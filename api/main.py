# =============================================================================
# api/main.py - FastAPI メインファイル
# =============================================================================
# 【このファイルの役割】
# APIサーバーのエントリーポイント（起動時に最初に読み込まれるファイル）
# ここでAPIのエンドポイント（URL）を定義する
# =============================================================================

import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel

# =============================================================================
# 設定
# =============================================================================
# 【環境変数から設定を読み込む】
# os.getenv("変数名", "デフォルト値") で環境変数を取得
# docker-compose.yml の environment で設定した値が入る
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DATA_DIR = Path("/data")  # ファイル保存先（docker-compose.ymlでマウント）

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
        print(f"✅ MongoDB connected: {MONGO_URL}")
    except Exception as e:
        print(f"❌ MongoDB connection failed: {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    """
    アプリ終了時にMongoDB接続を閉じる
    """
    global mongo_client
    if mongo_client:
        mongo_client.close()
        print("MongoDB connection closed")


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
    size: int
    tenant: str
    uploaded_at: str


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
