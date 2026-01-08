# =============================================================================
# mcp/server.py - FastMCP ツールサーバー
# =============================================================================
# 【このファイルの役割】
# AIエージェントが使用するツールを提供するMCPサーバー
#
# 【MCPとは】
# Model Context Protocol の略
# AIモデル（Claude等）に「ツール」を提供するための標準規格
#
# 【フェーズ4で実装予定のツール】
# - search_documents: MongoDB内のドキュメントを検索
# - visualize_data: データをグラフ化
# - predict_trend: 線形予測
#
# 【参考】https://github.com/jlowin/fastmcp
# =============================================================================

import os
from fastmcp import FastMCP
from motor.motor_asyncio import AsyncIOMotorClient

# =============================================================================
# 設定
# =============================================================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017")

# =============================================================================
# MCPサーバーの作成
# =============================================================================
mcp = FastMCP("pdf-tools")

# =============================================================================
# MongoDB接続
# =============================================================================
mongo_client: AsyncIOMotorClient = None
db = None


def init_mongo():
    """MongoDB接続を初期化"""
    global mongo_client, db
    if mongo_client is None:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client.pdf_system


# =============================================================================
# MCPツール（基本）
# =============================================================================


@mcp.tool()
def hello(name: str = "World") -> str:
    """
    動作確認用のシンプルなツール

    Args:
        name: 挨拶する相手の名前

    Returns:
        挨拶メッセージ
    """
    return f"Hello, {name}! MCP server is working."


@mcp.tool()
def get_server_status() -> dict:
    """
    サーバーの状態を返す

    Returns:
        サーバーの状態情報
    """
    return {
        "status": "running",
        "phase": 3,
        "openai_configured": bool(OPENAI_API_KEY),
        "available_tools": [
            "hello",
            "get_server_status",
            # フェーズ4で追加予定:
            # "search_documents",
            # "visualize_data",
            # "predict_trend",
        ],
    }


# =============================================================================
# フェーズ4用ツール（プレースホルダー）
# =============================================================================
# 以下はフェーズ4で実装予定
#
# @mcp.tool()
# async def search_documents(query: str, tenant: str = "default") -> dict:
#     """
#     MongoDBから構造化データを検索
#
#     Args:
#         query: 検索クエリ
#         tenant: テナントID
#
#     Returns:
#         検索結果
#     """
#     init_mongo()
#     # MongoDB検索ロジック
#     pass
#
#
# @mcp.tool()
# def visualize_data(data: list, chart_type: str = "bar") -> dict:
#     """
#     データをグラフ化
#
#     Args:
#         data: グラフ化するデータ
#         chart_type: グラフの種類（bar, line, pie等）
#
#     Returns:
#         グラフデータ（base64画像等）
#     """
#     pass
#
#
# @mcp.tool()
# def predict_trend(data: list, periods: int = 3) -> dict:
#     """
#     線形予測を実行
#
#     Args:
#         data: 予測の元となるデータ
#         periods: 予測する期間数
#
#     Returns:
#         予測結果
#     """
#     pass


# =============================================================================
# HTTPエンドポイント（ヘルスチェック用）
# =============================================================================
from fastapi import FastAPI

app = FastAPI()


@app.get("/api/health")
async def api_health():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "openai_configured": bool(OPENAI_API_KEY)
    }


# =============================================================================
# サーバー起動
# =============================================================================
if __name__ == "__main__":
    import uvicorn

    if OPENAI_API_KEY:
        print("✅ OpenAI API Key configured")
    else:
        print("⚠️ OpenAI API Key not set. Set OPENAI_API_KEY in .env file.")

    uvicorn.run(app, host="0.0.0.0", port=8001)
