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
# 【参考】https://github.com/jlowin/fastmcp
# =============================================================================

import os
import re
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
        "phase": 4,
        "openai_configured": bool(OPENAI_API_KEY),
        "available_tools": [
            "hello",
            "get_server_status",
            "search_documents",
        ],
    }


# =============================================================================
# 検索ツール
# =============================================================================


async def _search_documents_async(
    query: str,
    tenant: str = "default",
    limit: int = 5
) -> dict:
    """
    MongoDBから構造化データを検索する内部関数

    【検索ロジック】
    1. クエリをキーワードに分割
    2. 各ページのtitle, summary, key_pointsを検索
    3. マッチしたページを含むドキュメントを返す
    """
    init_mongo()

    # クエリをキーワードに分割（日本語対応）
    keywords = [k.strip() for k in re.split(r'[\s　]+', query) if k.strip()]

    if not keywords:
        return {"success": False, "error": "検索キーワードが空です", "results": []}

    # 構造化データを取得
    cursor = db.structured_data.find({"tenant": tenant})
    all_docs = await cursor.to_list(100)

    results = []

    for doc in all_docs:
        matched_pages = []

        for page in doc.get("pages", []):
            if "error" in page:
                continue

            page_data = page.get("data", {})
            title = page_data.get("title") or ""
            summary = page_data.get("summary") or ""
            key_points = page_data.get("key_points") or []

            # 検索対象テキストを結合
            searchable_text = f"{title} {summary} {' '.join(key_points)}".lower()

            # キーワードがマッチするかチェック
            match_count = sum(1 for kw in keywords if kw.lower() in searchable_text)

            if match_count > 0:
                matched_pages.append({
                    "page_number": page.get("page_number"),
                    "title": title,
                    "summary": summary,
                    "key_points": key_points,
                    "match_score": match_count / len(keywords)
                })

        if matched_pages:
            # マッチスコアでソート
            matched_pages.sort(key=lambda x: x["match_score"], reverse=True)

            results.append({
                "file_id": doc.get("file_id"),
                "filename": doc.get("filename"),
                "matched_pages": matched_pages[:3],  # 上位3ページ
                "total_matches": len(matched_pages)
            })

    # 結果をマッチ数でソート
    results.sort(key=lambda x: x["total_matches"], reverse=True)

    return {
        "success": True,
        "query": query,
        "keywords": keywords,
        "total_documents": len(results),
        "results": results[:limit]
    }


@mcp.tool()
def search_documents(query: str, tenant: str = "default", limit: int = 5) -> dict:
    """
    構造化データからキーワード検索

    PDFから抽出した構造化データ（タイトル、要約、重要ポイント）を検索し、
    関連するページを返します。

    Args:
        query: 検索クエリ（スペース区切りでAND検索）
        tenant: テナントID
        limit: 返す結果の最大数

    Returns:
        検索結果（マッチしたドキュメントとページ）
    """
    import asyncio
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_search_documents_async(query, tenant, limit))
    finally:
        loop.close()


# =============================================================================
# HTTPエンドポイント（API連携用）
# =============================================================================
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class SearchRequest(BaseModel):
    """検索リクエスト"""
    query: str
    tenant: str = "default"
    limit: int = 5


@app.get("/api/health")
async def api_health():
    """ヘルスチェック"""
    return {
        "status": "ok",
        "openai_configured": bool(OPENAI_API_KEY)
    }


@app.post("/api/search")
async def api_search(request: SearchRequest):
    """
    検索APIエンドポイント

    APIサーバーからMCPの検索ツールを呼び出すためのエンドポイント
    """
    result = await _search_documents_async(
        query=request.query,
        tenant=request.tenant,
        limit=request.limit
    )
    return result


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
