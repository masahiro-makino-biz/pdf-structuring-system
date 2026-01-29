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
# 【依存関係】
# - services/pdf_processor.py : PDF処理ロジック
# - services/chat_service.py  : チャット処理ロジック
# - core/config.py            : 設定管理
# - core/logging.py           : ログ管理
#
# =============================================================================

import uuid
from datetime import datetime
from pathlib import Path

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


from typing import Optional

class FileInfo(BaseModel):
    """ファイル情報"""
    file_id: str
    filename: str
    path: str
    size: int
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
    # MongoDBに記録
    # -------------------------------------------------------------------------
    file_doc = {
        "file_id": file_id,
        "filename": file.filename,
        "path": str(save_path),
        "size": len(content),
        "tenant": tenant,
        "content_type": file.content_type,
        "uploaded_at": datetime.utcnow(),
    }
    await db.files.insert_one(file_doc)
    # 【db.files とは】
    # - db: pdf_system データベース
    # - files: コレクション（テーブルのようなもの）
    # MongoDBは事前にコレクションを作成しなくてもOK（自動作成）

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
    # 【find() とは】
    # MongoDBで条件に合うドキュメントを検索
    # to_list(100): 最大100件をリストとして取得
    files = await db.files.find({"tenant": tenant}).to_list(100)

    # 【_id を除外】
    # MongoDBが自動で追加する _id はObjectId型でJSONに変換できないので除外
    return [
        FileInfo(
            file_id=f["file_id"],
            filename=f["filename"],
            path=f["path"],
            size=f["size"],
            tenant=f["tenant"],
            uploaded_at=f["uploaded_at"].isoformat(),
            processed=f.get("processed", False),
            processed_at=f["processed_at"].isoformat() if f.get("processed_at") else None,
        )
        for f in files
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
    pages_processed: int = None
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
    from services.pdf_processor import process_pdf

    # ファイルの存在確認
    file_doc = await db.files.find_one({"file_id": file_id, "tenant": tenant})
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
                pages_processed=result.get("pages_processed"),
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
    structured = await db.structured_data.find_one(
        {"file_id": file_id, "tenant": tenant}
    )

    if not structured:
        raise HTTPException(
            status_code=404,
            detail=f"構造化データが見つかりません: {file_id}"
        )

    # MongoDBの_idを除外して返す
    structured.pop("_id", None)
    return structured


@app.delete("/admin/files/{file_id}")
async def delete_file(
    file_id: str,
    tenant: str = Query(default="default", description="テナントID"),
):
    """
    ファイルと関連データを削除

    【削除対象】
    1. files コレクションのレコード
    2. structured_data コレクションのレコード
    3. 実ファイル（PDF、画像）

    Args:
        file_id: 削除するファイルのID
        tenant: テナントID

    Returns:
        削除結果
    """
    import shutil

    # ファイル情報を取得
    file_doc = await db.files.find_one({"file_id": file_id, "tenant": tenant})
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

    # MongoDBから削除
    await db.files.delete_one({"file_id": file_id, "tenant": tenant})
    await db.structured_data.delete_one({"file_id": file_id, "tenant": tenant})

    return {
        "success": True,
        "message": f"ファイル {file_doc['filename']} を削除しました",
        "file_id": file_id
    }


# =============================================================================
# チャットエンドポイント
# =============================================================================


class ChatRequest(BaseModel):
    """チャットリクエスト"""
    message: str
    tenant: str = "default"


class ChatResponse(BaseModel):
    """チャットレスポンス"""
    success: bool
    response: str
    search_performed: bool = False
    search_results: dict | None = None  # 検索結果（画像パス含む）


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    チャットエンドポイント

    【処理の流れ】
    1. ユーザーメッセージを受信
    2. OpenAI GPT-4oが検索が必要か判断
    3. 必要ならMCP経由でMongoDBを検索
    4. 検索結果を元に回答を生成

    Args:
        request: チャットリクエスト（メッセージ、テナントID）

    Returns:
        AIの回答
    """
    from services.chat_service import process_chat

    result = await process_chat(
        message=request.message,
        tenant=request.tenant
    )

    return ChatResponse(
        success=result.get("success", False),
        response=result.get("response", "エラーが発生しました"),
        search_performed=result.get("search_performed", False),
        search_results=result.get("search_results")  # 検索結果（画像パス含む）
    )
