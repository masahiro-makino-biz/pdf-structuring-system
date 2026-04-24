# =============================================================================
# api/main.py - FastAPI メインファイル
# =============================================================================
#
# 【ファイル概要】
# APIサーバーのエントリーポイント。docker compose up 時に uvicorn から起動される。
# 全てのAPIエンドポイント（URL）をここで定義し、リクエストを各サービスに振り分ける。
#
# 【処理フロー】
# 1. uvicorn が main:app を起動
# 2. @app.on_event("startup") で MongoDB に接続
# 3. 各 @app.get/post デコレータでエンドポイントを登録
# 4. リクエストを待ち受け、対応する関数を実行
#
# 【主要エンドポイント】
# - POST /admin/files       : PDFアップロード
# - POST /admin/process/{id}: PDF処理実行（AI構造化）
# - POST /chat              : チャット送信（AI検索）
#
# 【データベース】
# - documents コレクション: ファイル情報と処理結果を統合管理
#   （旧 files + structured_data を1つに統合）
#
# 【依存関係】
# - services/pdf_processor.py : PDF処理ロジック
# - services/chat_service.py  : チャット処理ロジック
# - core/config.py            : 設定管理
# - core/logging.py           : ログ管理
#
# =============================================================================

import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

# =============================================================================
# 設定とロギング
# =============================================================================
# 【一元管理された設定を使用】
# core/config.py で全ての環境変数を一元管理
# core/logging.py でログ出力を統一
from core.config import get_settings
from core.logging import setup_logging, get_logger

# =============================================================================
# サービス層
# =============================================================================
from services.pdf_processor import process_pdf
from services.chat_service import process_chat, clear_history

# ロギング初期化（アプリ起動時に1回だけ）
setup_logging()
logger = get_logger(__name__)

# 設定を取得
settings = get_settings()
MONGO_URL = settings.mongo_url
DATA_DIR = Path(settings.data_dir)

# =============================================================================
# FastAPIアプリケーションの作成
# =============================================================================
app = FastAPI(
    title="PDF構造化API",
    version="0.2.0",
    description="PDFをアップロードして構造化データに変換するAPI",
)

# =============================================================================
# CORSミドルウェアの設定
# =============================================================================
# 【CORSとは】
# Cross-Origin Resource Sharing（クロスオリジンリソース共有）
# 異なるドメイン（例：localhost:8501 → localhost:8000）からのアクセスを許可する設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# MongoDB接続
# =============================================================================
# 【グローバル変数として定義】
# アプリ起動時に接続を確立し、全エンドポイントで使い回す
#
# 【なぜ motor を使うか】
# - motor: MongoDBの非同期ドライバ
# - pymongo: 同期版。FastAPIは非同期なのでmotorの方が相性が良い
# - 非同期 = 重い処理を待っている間に他のリクエストを処理できる
mongo_client: AsyncIOMotorClient = None
db = None


@app.on_event("startup")
async def startup_db_client():
    """
    アプリ起動時にMongoDBに接続

    【@app.on_event("startup") とは】
    FastAPIのライフサイクルイベント
    アプリが起動した時に一度だけ実行される

    【なぜ startup で接続するか】
    - 毎リクエストで接続すると遅い
    - 一度接続して使い回す方が効率的
    """
    global mongo_client, db
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    db = mongo_client.pdf_system  # データベース名: pdf_system

    # 【接続テスト】
    # サーバー情報を取得して接続確認
    try:
        await mongo_client.server_info()
        logger.info(f"MongoDB接続成功: {MONGO_URL}")
    except Exception as e:
        logger.error(f"MongoDB接続失敗: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    """
    アプリ終了時にMongoDB接続を閉じる
    """
    global mongo_client
    if mongo_client:
        mongo_client.close()
        logger.info("MongoDB接続クローズ")


# =============================================================================
# レスポンスモデル（Pydantic）
# =============================================================================
# 【Pydanticとは】
# データの型チェックとバリデーションを行うライブラリ
# FastAPIと組み合わせて、APIのリクエスト/レスポンスの形式を定義する
#
# 【なぜモデルを定義するか】
# - APIドキュメントに型情報が表示される
# - 間違った形式のデータを弾ける
# - コードの可読性が上がる


class FileUploadResponse(BaseModel):
    """ファイルアップロードのレスポンス"""
    file_id: str
    filename: str
    path: str
    size: int
    uploaded_at: str


class FileInfo(BaseModel):
    """ファイル情報"""
    file_id: str
    filename: str
    path: str
    size: Optional[int] = None
    tenant: str
    uploaded_at: str
    processed: bool = False
    processed_at: Optional[str] = None


# =============================================================================
# エンドポイント
# =============================================================================


@app.get("/")
async def root():
    """ルートエンドポイント（動作確認用）"""
    return {"message": "PDF構造化API is running", "status": "ok", "version": "0.2.0"}


@app.get("/health")
async def health_check():
    """ヘルスチェックエンドポイント"""
    # MongoDB接続状態も確認
    mongo_status = "connected"
    try:
        await mongo_client.server_info()
    except Exception:
        mongo_status = "disconnected"

    return {"status": "healthy", "mongodb": mongo_status}


# =============================================================================
# Admin エンドポイント
# =============================================================================


@app.post("/admin/files", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    PDFファイルをアップロード

    【UploadFile とは】
    FastAPIが提供するファイルアップロード用の型
    - file.filename: 元のファイル名
    - file.content_type: MIMEタイプ（例: application/pdf）
    - file.read(): ファイル内容を読み込む

    【File(...) とは】
    - File(): ファイルパラメータであることを示す
    - ... (Ellipsis): 必須パラメータであることを示す

    【Query() とは】
    URLのクエリパラメータ（例: ?tenant=xxx）

    Args:
        file: アップロードするファイル
        tenant: テナントID（データを分けるための識別子）

    Returns:
        アップロード結果（ファイルID、パス等）
    """
    # -------------------------------------------------------------------------
    # バリデーション
    # -------------------------------------------------------------------------
    # 【なぜバリデーションが必要か】
    # 不正なファイルをアップロードされないようにする
    # PDFでないファイルは拒否する
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="PDFファイルのみアップロード可能です",
        )

    # -------------------------------------------------------------------------
    # ファイルIDを生成
    # -------------------------------------------------------------------------
    # 【UUIDとは】
    # Universally Unique Identifier（普遍的に一意な識別子）
    # ランダムな文字列を生成して、ファイルを一意に識別する
    # 例: "550e8400-e29b-41d4-a716-446655440000"
    file_id = str(uuid.uuid4())

    # -------------------------------------------------------------------------
    # 保存先パスを作成
    # -------------------------------------------------------------------------
    # 仕様書に従った命名規則: /data/{tenant}/raw/{job_id}/{filename}.pdf
    # ここではjob_idはまだないので、file_idを使う
    save_dir = DATA_DIR / tenant / "raw" / file_id
    save_dir.mkdir(parents=True, exist_ok=True)
    # 【mkdir の引数】
    # - parents=True: 親ディレクトリも一緒に作成
    # - exist_ok=True: 既に存在してもエラーにしない

    save_path = save_dir / file.filename

    # -------------------------------------------------------------------------
    # ファイルを保存
    # -------------------------------------------------------------------------
    content = await file.read()  # ファイル内容を読み込み
    with open(save_path, "wb") as f:
        f.write(content)
    # 【"wb" とは】
    # - w: write（書き込み）
    # - b: binary（バイナリモード）
    # PDFはバイナリファイルなので "wb" を使う

    # -------------------------------------------------------------------------
    # MongoDBに記録（pages コレクションに統合）
    # -------------------------------------------------------------------------
    # アップロード時は page_number: null で「未処理」状態を示す
    # 処理後は各ページごとのドキュメントに置き換わる
    file_doc = {
        "file_id": file_id,
        "filename": file.filename,
        "path": str(save_path),
        "size": len(content),
        "tenant": tenant,
        "content_type": file.content_type,
        "uploaded_at": datetime.utcnow(),
        "processed": False,
        "page_number": None,  # 未処理状態
    }
    await db.pages.insert_one(file_doc)

    # -------------------------------------------------------------------------
    # レスポンス
    # -------------------------------------------------------------------------
    return FileUploadResponse(
        file_id=file_id,
        filename=file.filename,
        path=str(save_path),
        size=len(content),
        uploaded_at=datetime.utcnow().isoformat(),
    )


@app.get("/admin/files")
async def list_files(
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    アップロード済みファイル一覧を取得

    Args:
        tenant: テナントID

    Returns:
        ファイル一覧
    """
    # pages コレクションから file_id ごとにグループ化してファイル一覧を取得
    # aggregate を使って重複を除去
    # 【なぜ $match に file_id 条件を入れるか】
    # 過去の不完全な処理で file_id/filename/path が null のドキュメントがDBに
    # 残ることがある。これらは FileInfo の必須フィールドを満たせず Pydantic で
    # バリデーションエラーになるため、aggregate段階で除外しておく。
    pipeline = [
        {"$match": {
            "tenant": tenant,
            "file_id": {"$ne": None},
            "filename": {"$ne": None},
            "path": {"$ne": None},
        }},
        {"$group": {
            "_id": "$file_id",
            "file_id": {"$first": "$file_id"},
            "filename": {"$first": "$filename"},
            "path": {"$first": "$path"},
            "size": {"$first": "$size"},
            "tenant": {"$first": "$tenant"},
            "uploaded_at": {"$first": "$uploaded_at"},
            "processed": {"$max": "$processed"},  # 1つでも処理済みなら True
            "processed_at": {"$max": "$processed_at"},
        }},
        {"$sort": {"uploaded_at": -1}}
    ]
    files = await db.pages.aggregate(pipeline).to_list(100)

    # 集約後にも file_id 欠損が残る可能性は低いが念のため防御的にスキップ
    return [
        FileInfo(
            file_id=f["file_id"],
            filename=f["filename"],
            path=f["path"],
            size=f.get("size", 0) or 0,
            tenant=f.get("tenant", tenant),
            uploaded_at=f["uploaded_at"].isoformat() if f.get("uploaded_at") else "",
            processed=bool(f.get("processed")),
            processed_at=f["processed_at"].isoformat() if f.get("processed_at") else None,
        )
        for f in files
        if f.get("file_id") and f.get("filename") and f.get("path")
    ]


# =============================================================================
# ファイル配信エンドポイント
# =============================================================================


@app.get("/files")
async def get_file(
    path: str = Query(..., description="ファイルパス（/data/...）"),
):
    """
    ファイルを配信

    【セキュリティ: Path Traversal対策】
    ユーザーが "../../../etc/passwd" のようなパスを指定して
    意図しないファイルにアクセスすることを防ぐ

    Args:
        path: ファイルパス

    Returns:
        ファイル
    """
    # -------------------------------------------------------------------------
    # Path Traversal対策
    # -------------------------------------------------------------------------
    # 【resolve() とは】
    # パスを絶対パスに変換し、".." などを解決する
    # 例: "/data/../etc/passwd" → "/etc/passwd"
    file_path = Path(path).resolve()

    # /data 配下かチェック
    if not str(file_path).startswith(str(DATA_DIR.resolve())):
        raise HTTPException(
            status_code=403,
            detail="アクセスが許可されていないパスです",
        )

    # ファイル存在チェック
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="ファイルが見つかりません")

    if not file_path.is_file():
        raise HTTPException(status_code=400, detail="ディレクトリは取得できません")

    # -------------------------------------------------------------------------
    # ファイルを返す
    # -------------------------------------------------------------------------
    # 【FileResponse とは】
    # ファイルをHTTPレスポンスとして返すためのクラス
    # 自動でContent-Typeを設定してくれる
    return FileResponse(file_path)


# =============================================================================
# PDF処理エンドポイント
# =============================================================================


class ProcessResponse(BaseModel):
    """PDF処理のレスポンス"""
    success: bool
    file_id: str
    filename: str = None
    total_pages: int = None
    records_processed: int = None
    pages_with_errors: int = None
    error: str = None


@app.post("/admin/process/{file_id}", response_model=ProcessResponse)
async def process_pdf_endpoint(
    file_id: str,
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    アップロード済みPDFをAI処理

    【処理の流れ】
    1. ファイル情報をMongoDBから取得
    2. PDFを画像に変換
    3. 各ページをGPT-4oで解析
    4. 構造化データをMongoDBに保存

    Args:
        file_id: 処理するファイルのID
        tenant: テナントID

    Returns:
        処理結果
    """
    # ファイルの存在確認（pages コレクションから）
    file_doc = await db.pages.find_one({"file_id": file_id, "tenant": tenant})
    if not file_doc:
        raise HTTPException(
            status_code=404,
            detail=f"ファイルが見つかりません: {file_id}"
        )

    try:
        # PDF処理サービスを呼び出し
        result = await process_pdf(db, file_id, tenant)

        if result.get("success"):
            return ProcessResponse(
                success=True,
                file_id=file_id,
                filename=result.get("filename"),
                total_pages=result.get("total_pages"),
                records_processed=result.get("records_processed"),
                pages_with_errors=result.get("pages_with_errors"),
            )
        else:
            return ProcessResponse(
                success=False,
                file_id=file_id,
                error=result.get("error", "処理に失敗しました")
            )

    except Exception as e:
        return ProcessResponse(
            success=False,
            file_id=file_id,
            error=f"処理エラー: {str(e)}"
        )


# =============================================================================
# 構造化データ取得エンドポイント
# =============================================================================


@app.get("/admin/structured/{file_id}")
async def get_structured_data(
    file_id: str,
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    構造化データを取得

    【用途】
    AI処理が完了した後、その結果を取得するためのエンドポイント

    Args:
        file_id: ファイルID
        tenant: テナントID

    Returns:
        構造化データ
    """
    # pages コレクションから処理済みのレコードを取得
    # page_number が null でないものが処理済み
    raw_records = await db.pages.find(
        {"file_id": file_id, "tenant": tenant, "page_number": {"$ne": None}},
    ).sort([("page_number", 1), ("table_index", 1)]).to_list(500)
    # _id を文字列に変換（JSONシリアライズ用）
    records = []
    for r in raw_records:
        r["_id"] = str(r["_id"])
        records.append(r)

    if not records:
        # レコードがない = 未処理またはファイルが存在しない
        file_exists = await db.pages.find_one({"file_id": file_id, "tenant": tenant})
        if not file_exists:
            raise HTTPException(
                status_code=404,
                detail=f"ファイルが見つかりません: {file_id}"
            )
        raise HTTPException(
            status_code=404,
            detail=f"構造化データが見つかりません（未処理）: {file_id}"
        )

    # 最初のレコードから processed_at を取得
    processed_at = records[0].get("processed_at") if records else None

    return {
        "records": records,
        "total_records": len(records),
        "processed_at": processed_at,
    }


class StructuredDataUpdate(BaseModel):
    """構造化データ更新リクエスト"""
    data: dict


@app.put("/admin/structured/{record_id}")
async def update_structured_data(record_id: str, request: StructuredDataUpdate):
    """
    構造化データを手動で修正する。

    測定値キーの変更、機器名の修正、基準値の訂正などに使用。
    """
    try:
        obj_id = ObjectId(record_id)
    except Exception:
        raise HTTPException(status_code=400, detail="不正なID形式")

    existing = await db.pages.find_one({"_id": obj_id})
    if not existing:
        raise HTTPException(status_code=404, detail="レコードが見つかりません")

    await db.pages.update_one(
        {"_id": obj_id},
        {"$set": {"data": request.data}}
    )
    return {"success": True}


@app.delete("/admin/files/{file_id}")
async def delete_file(
    file_id: str,
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    ファイルと関連データを削除

    【削除対象】
    1. pages コレクションのレコード（該当ファイルの全ページ）
    2. 実ファイル（PDF、画像）

    Args:
        file_id: 削除するファイルのID
        tenant: テナントID

    Returns:
        削除結果
    """
    # ファイル情報を取得（pages コレクションから）
    file_doc = await db.pages.find_one({"file_id": file_id, "tenant": tenant})
    if not file_doc:
        raise HTTPException(
            status_code=404,
            detail=f"ファイルが見つかりません: {file_id}"
        )

    # 実ファイルを削除（PDFと画像）
    try:
        # PDFファイルのディレクトリを削除
        pdf_dir = DATA_DIR / tenant / "raw" / file_id
        if pdf_dir.exists():
            shutil.rmtree(pdf_dir)

        # 画像ファイルのディレクトリを削除
        images_dir = DATA_DIR / tenant / "images" / file_id
        if images_dir.exists():
            shutil.rmtree(images_dir)
    except Exception as e:
        # ファイル削除に失敗してもDB削除は続行
        logger.warning(f"ファイル削除エラー: {e}")

    # MongoDBから削除（pages コレクションのみ）
    await db.pages.delete_many({"file_id": file_id, "tenant": tenant})

    return {
        "success": True,
        "message": f"ファイル {file_doc['filename']} を削除しました",
        "file_id": file_id
    }


# =============================================================================
# 正規化辞書管理エンドポイント
# =============================================================================
#
# 【このセクションの役割】
# normalization_dict コレクションを管理画面から編集できるようにする。
# 辞書は PDF 処理時の表記ゆれ統一に使われる（canonical + variants）。
#
# 【正規化対象フィールド】
# 機器 / 機器部品 / 計測箇所 / 点検項目 の4つ。
# 他のフィールドは登録できないようにバリデーションする。
# =============================================================================

from bson import ObjectId
from services.pipeline.normalize_rules import TEXT_FIELDS as NORMALIZATION_FIELDS


def _serialize_dict_entry(doc: dict) -> dict:
    """MongoDB ドキュメントをJSONシリアライズ可能な形式に変換"""
    return {
        "id": str(doc["_id"]),
        "field": doc["field"],
        "canonical": doc["canonical"],
        "variants": doc.get("variants", []),
        "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        "updated_at": doc.get("updated_at").isoformat() if doc.get("updated_at") else None,
    }


class NormalizationDictCreate(BaseModel):
    """新規canonical登録リクエスト"""
    field: str
    canonical: str
    variants: list[str] = []


class NormalizationDictUpdate(BaseModel):
    """canonical/variants 更新リクエスト"""
    canonical: str | None = None
    variants: list[str] | None = None


@app.get("/admin/normalization-dict")
async def list_normalization_dict(
    field: str | None = Query(default=None, description="絞り込みフィールド名"),
):
    """正規化辞書の一覧を取得"""
    query = {}
    if field:
        if field not in NORMALIZATION_FIELDS:
            raise HTTPException(status_code=400, detail=f"無効なフィールド: {field}")
        query["field"] = field

    cursor = db.normalization_dict.find(query).sort([("field", 1), ("canonical", 1)])
    docs = await cursor.to_list(length=None)
    return {
        "success": True,
        "entries": [_serialize_dict_entry(d) for d in docs],
    }


@app.post("/admin/normalization-dict")
async def create_normalization_entry(request: NormalizationDictCreate):
    """新規canonicalを辞書に登録"""
    if request.field not in NORMALIZATION_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"フィールドは {NORMALIZATION_FIELDS} のいずれか"
        )
    if not request.canonical.strip():
        raise HTTPException(status_code=400, detail="canonicalは必須")

    # 重複チェック（同一fieldで同じcanonicalは作れない）
    existing = await db.normalization_dict.find_one({
        "field": request.field,
        "canonical": request.canonical,
    })
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"既に登録されています: [{request.field}] {request.canonical}"
        )

    now = datetime.utcnow()
    doc = {
        "field": request.field,
        "canonical": request.canonical,
        "variants": request.variants,
        "created_at": now,
        "updated_at": now,
    }
    result = await db.normalization_dict.insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "entry": _serialize_dict_entry(doc)}


@app.put("/admin/normalization-dict/{entry_id}")
async def update_normalization_entry(entry_id: str, request: NormalizationDictUpdate):
    """
    canonical をリネーム、または variants を差し替え。

    【canonicalリネーム時の注意】
    このエンドポイント自体は辞書を書き換えるだけで、
    既存の pages コレクションの正規化済みデータは更新されない。
    既存データに反映したい場合は個別にレコードを削除・再処理する。
    """
    try:
        obj_id = ObjectId(entry_id)
    except Exception:
        raise HTTPException(status_code=400, detail="不正なID形式")

    existing = await db.normalization_dict.find_one({"_id": obj_id})
    if not existing:
        raise HTTPException(status_code=404, detail="エントリが見つかりません")

    update_fields: dict = {"updated_at": datetime.utcnow()}

    if request.canonical is not None:
        new_canonical = request.canonical.strip()
        if not new_canonical:
            raise HTTPException(status_code=400, detail="canonicalは空にできません")
        # リネーム先が同一fieldで重複していないか確認
        if new_canonical != existing["canonical"]:
            duplicate = await db.normalization_dict.find_one({
                "field": existing["field"],
                "canonical": new_canonical,
                "_id": {"$ne": obj_id},
            })
            if duplicate:
                raise HTTPException(
                    status_code=409,
                    detail=f"リネーム先が既に存在: {new_canonical}"
                )
        update_fields["canonical"] = new_canonical

    if request.variants is not None:
        update_fields["variants"] = request.variants

    await db.normalization_dict.update_one({"_id": obj_id}, {"$set": update_fields})
    updated = await db.normalization_dict.find_one({"_id": obj_id})
    return {"success": True, "entry": _serialize_dict_entry(updated)}


@app.delete("/admin/normalization-dict/{entry_id}")
async def delete_normalization_entry(entry_id: str):
    """辞書エントリを削除"""
    try:
        obj_id = ObjectId(entry_id)
    except Exception:
        raise HTTPException(status_code=400, detail="不正なID形式")

    result = await db.normalization_dict.delete_one({"_id": obj_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="エントリが見つかりません")
    return {"success": True}


# =============================================================================
# 測定値キー突合エンドポイント
# =============================================================================

from services.reconciliation import (
    run_reconciliation_scan,
    run_ai_judgment_for_pending,
    apply_approved_mappings,
    invalidate_mapping_cache,
)


@app.post("/admin/reconciliation/scan")
async def reconciliation_scan(
    tenant: str = Query(default="default", description="テナントID"),
    run_ai: bool = Query(default=False, description="Trueなら検出と同時にAI判定も走る（遅い）"),
):
    """
    測定値キーの突合スキャンを実行（検出のみ、デフォルトは高速）。

    機器+部品+物理量が同じレコード群でキーが不一致のものを検出し、
    key_mappings に canonical_key=null の pending として保存する。

    AI判定は別エンドポイント /admin/reconciliation/ai_judge で実行。
    run_ai=true を渡すと検出と同時にAI判定も走る（従来動作）。
    """
    result = await run_reconciliation_scan(db, tenant, run_ai=run_ai)
    return {"success": True, **result}


@app.post("/admin/reconciliation/ai_judge")
async def reconciliation_ai_judge(
    tenant: str = Query(default="default", description="テナントID"),
    kiki: str | None = Query(default=None, description="機器名でフィルタ"),
    buhin: str | None = Query(default=None, description="機器部品でフィルタ"),
    butsuryo: str | None = Query(default=None, description="測定物理量でフィルタ"),
):
    """
    既に検出済みで canonical_key=null の pending マッピングに AI判定を実行。

    検出スキャン(/scan)でまず全グループを記録しておき、
    AI判定が必要なものだけこのエンドポイントで処理する。
    フィルタ指定で対象グループを絞れる。
    """
    group_filter: dict = {}
    if kiki:
        group_filter["機器"] = kiki
    if buhin:
        group_filter["機器部品"] = buhin
    if butsuryo:
        group_filter["測定物理量"] = butsuryo

    result = await run_ai_judgment_for_pending(db, tenant, group_filter or None)
    return {"success": True, **result}


@app.get("/admin/reconciliation/report")
async def reconciliation_report(
    status: str = Query(default="pending_approved", description="ステータスフィルタ"),
):
    """突合レポートを取得

    status: pending / approved / rejected / applied / pending_approved / all
    pending_approved は pending と approved の両方を含む（デフォルト表示）
    """
    query = {}
    if status == "pending_approved":
        query["status"] = {"$in": ["pending", "approved"]}
    elif status != "all":
        query["status"] = status

    docs = await db.key_mappings.find(query).sort("created_at", -1).to_list(length=None)

    # ページ情報を取得（画像パス + 測定値JSON用）
    # ページIDごとにキャッシュして重複クエリを避ける
    page_cache = {}
    mappings = []
    for doc in docs:
        canonical_image = None
        variant_image = None
        canonical_data = None
        variant_data = None

        for page_id_field, is_canonical in [("canonical_page_id", True), ("variant_page_id", False)]:
            pid = doc.get(page_id_field)
            if not pid:
                continue
            pid_str = str(pid)
            if pid_str not in page_cache:
                page = await db.pages.find_one(
                    {"_id": pid},
                    {"image_path": 1, "data.測定値": 1, "data.基準値": 1}
                )
                page_cache[pid_str] = page
            page = page_cache.get(pid_str)
            if page:
                if is_canonical:
                    canonical_image = page.get("image_path")
                    canonical_data = page.get("data", {}).get("測定値")
                else:
                    variant_image = page.get("image_path")
                    variant_data = page.get("data", {}).get("測定値")

        mappings.append({
            "id": str(doc["_id"]),
            "group": doc.get("group", {}),
            "canonical_key": doc.get("canonical_key"),
            "variant_key": doc.get("variant_key"),
            "ai_confidence": doc.get("ai_confidence", 0),
            "ai_reasoning": doc.get("ai_reasoning", ""),
            "status": doc.get("status", "pending"),
            "canonical_image_path": canonical_image,
            "variant_image_path": variant_image,
            "canonical_measurements": canonical_data,
            "variant_measurements": variant_data,
            "variant_page_id": str(doc.get("variant_page_id", "")),
            "created_at": doc.get("created_at").isoformat() if doc.get("created_at") else None,
        })

    return {"success": True, "mappings": mappings, "total": len(mappings)}


class ReconciliationAction(BaseModel):
    """突合マッピングの承認/却下/修正リクエスト"""
    action: str  # "approve" | "reject" | "modify"
    modified_key: str | None = None  # action="modify" の場合のみ


@app.put("/admin/reconciliation/{mapping_id}")
async def update_reconciliation_mapping(mapping_id: str, request: ReconciliationAction):
    """突合マッピングを承認/却下/修正する"""
    try:
        obj_id = ObjectId(mapping_id)
    except Exception:
        raise HTTPException(status_code=400, detail="不正なID形式")

    existing = await db.key_mappings.find_one({"_id": obj_id})
    if not existing:
        raise HTTPException(status_code=404, detail="マッピングが見つかりません")

    update_fields = {"updated_at": datetime.utcnow()}

    if request.action == "approve":
        update_fields["status"] = "approved"
    elif request.action == "reject":
        update_fields["status"] = "rejected"
    elif request.action == "modify":
        if not request.modified_key or not request.modified_key.strip():
            raise HTTPException(status_code=400, detail="修正キーは必須です")
        update_fields["status"] = "approved"
        update_fields["canonical_key"] = request.modified_key.strip()
    else:
        raise HTTPException(status_code=400, detail=f"不正なアクション: {request.action}")

    await db.key_mappings.update_one({"_id": obj_id}, {"$set": update_fields})

    # マッピングキャッシュを無効化（パイプラインが最新を使うように）
    invalidate_mapping_cache()

    return {"success": True}


@app.post("/admin/reconciliation/reject_all")
async def reconciliation_reject_all(
    status: str = Query(default="pending", description="対象ステータス（通常はpending）"),
    variant_page_id: str | None = Query(default=None, description="指定があればそのページの却下のみ"),
):
    """
    指定ステータス(デフォルト: pending)のマッピングを却下に変更する。

    variant_page_id 指定なし → 全件却下
    variant_page_id 指定あり → そのページのマッピングだけ却下
    """
    query: dict = {"status": status}
    if variant_page_id:
        try:
            query["variant_page_id"] = ObjectId(variant_page_id)
        except Exception:
            raise HTTPException(status_code=400, detail="不正な variant_page_id")

    result = await db.key_mappings.update_many(
        query,
        {"$set": {"status": "rejected", "updated_at": datetime.utcnow()}},
    )
    invalidate_mapping_cache()
    return {"success": True, "rejected_count": result.modified_count}


@app.post("/admin/reconciliation/apply")
async def apply_reconciliation(
    tenant: str = Query(default="default", description="テナントID"),
):
    """承認済みマッピングを pages コレクションに適用する"""
    result = await apply_approved_mappings(db, tenant)
    return {"success": True, **result}


# =============================================================================
# チャットエンドポイント
# =============================================================================


class ChatRequest(BaseModel):
    """チャットリクエスト"""
    message: str
    tenant: str = "default"
    session_id: str = "default"


class ChatResponse(BaseModel):
    """チャットレスポンス"""
    success: bool
    response: str
    search_performed: bool = False
    search_results: dict | None = None


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    チャットエンドポイント

    【処理フロー】
    1. ユーザーメッセージを受信
    2. セッションの履歴を取得して文脈を構築
    3. OpenAI GPT-4oが検索が必要か判断
    4. 必要ならMCP経由でMongoDBを検索
    5. 検索結果を元に回答を生成
    6. 履歴に追加

    Args:
        request: チャットリクエスト（メッセージ、テナントID、セッションID）

    Returns:
        AIの回答
    """
    result = await process_chat(
        message=request.message,
        tenant=request.tenant,
        session_id=request.session_id
    )

    return ChatResponse(
        success=result.get("success", False),
        response=result.get("response", "エラーが発生しました"),
        search_performed=result.get("search_performed", False),
        search_results=result.get("search_results")
    )


@app.post("/chat/clear")
async def clear_chat_history(session_id: str = Query(default="default")):
    """
    チャット履歴をクリア

    【なぜこのAPIが必要か】
    - ユーザーが新しい話題を始めたいとき
    - 履歴が長くなりすぎてトークン消費が増えたとき

    Args:
        session_id: クリアするセッションのID

    Returns:
        成功メッセージ
    """
    clear_history(session_id)
    return {"success": True, "message": f"セッション {session_id} の履歴をクリアしました"}
