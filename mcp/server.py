# =============================================================================
# mcp/server.py - MCP検索サーバー
# =============================================================================
#
# 【ファイル概要】
# 点検記録の検索機能をMCPツールとして提供するサーバー。
# chat_service.py から MCPStreamableHTTPTool 経由で呼び出される。
#
# 【処理フロー】
# 1. chat_service.py が MCPStreamableHTTPTool で /mcp に接続
# 2. AIが search_documents ツールを呼び出す
# 3. MongoDBを検索して結果を返す
#
# 【依存関係】
# - MongoDB : pages コレクションを検索
# - FastMCP : MCPプロトコルでツールを提供
# - FastAPI : HTTPエンドポイント提供
#
# =============================================================================

import json
import os
from fastmcp import FastMCP
from motor.motor_asyncio import AsyncIOMotorClient

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017")

# MCPサーバーを作成
mcp = FastMCP("pdf-tools")

mongo_client: AsyncIOMotorClient = None
db = None


def init_mongo():
    """
    MongoDB接続を初期化

    【なぜこの実装か】
    - 遅延初期化: 最初のリクエスト時に接続を確立
    - グローバル変数で接続を保持し、再利用
    """
    global mongo_client, db
    if mongo_client is None:
        mongo_client = AsyncIOMotorClient(MONGO_URL)
        db = mongo_client.pdf_system


# =============================================================================
# MCPツール定義
# =============================================================================
@mcp.tool()
async def search_documents(
    equipment: str = "",
    equipment_part: str = "",
    inspection_item: str = "",
    inspection_date: str = "",
    tenant: str = "default",
    limit: int = 5
) -> str:
    """
    ミル機器の点検記録を検索する

    【処理フロー】
    1. 検索条件をリストに格納（空でないもののみ）
    2. MongoDBから全件取得
    3. 部分一致でマッチしたレコードを収集
    4. マッチ数が多い順にソートして返す

    Args:
        equipment: 機器名（例: '2号機微粉炭機D'）
        equipment_part: 機器部品名（例: 'リンクサポート'）
        inspection_item: 点検項目（例: '隙間計測'）
        inspection_date: 点検年月日（例: '2024-01-15', '2024'）
        tenant: テナントID
        limit: 返す結果の最大数

    Returns:
        検索結果のJSON文字列
    """
    init_mongo()

    # 検索条件をリストに格納（空でないもののみ）
    search_conditions = []
    if equipment.strip():
        search_conditions.append(("機器", equipment.strip()))
    if equipment_part.strip():
        search_conditions.append(("機器部品", equipment_part.strip()))
    if inspection_item.strip():
        search_conditions.append(("点検項目", inspection_item.strip()))
    if inspection_date.strip():
        search_conditions.append(("点検年月日", inspection_date.strip()))

    if not search_conditions:
        return json.dumps({
            "success": False,
            "error": "検索条件を1つ以上指定してください",
            "results": []
        }, ensure_ascii=False)

    # pages コレクションから処理済みページを検索
    all_pages = await db.pages.find({
        "tenant": tenant,
        "page_number": {"$ne": None}
    }).to_list(500)

    # ファイルごとにマッチしたページをグループ化
    file_results = {}

    for page in all_pages:
        if "error" in page:
            continue

        page_data = page.get("data", {})

        # 検索条件とのマッチをチェック
        match_count = 0
        matched_fields = []

        for field_name, search_value in search_conditions:
            search_lower = search_value.lower()
            stored_value = (page_data.get(field_name) or "").lower()

            if search_lower in stored_value:
                match_count += 1
                matched_fields.append(field_name)

        if match_count > 0:
            file_id = page.get("file_id")

            if file_id not in file_results:
                file_results[file_id] = {
                    "file_id": file_id,
                    "filename": page.get("filename", "不明"),
                    "matched_records": [],
                    "total_matches": 0
                }

            file_results[file_id]["matched_records"].append({
                "page_number": page.get("page_number"),
                "table_index": page.get("table_index"),
                "inspection_item": page_data.get("点検項目"),
                "image_path": page.get("image_path", ""),
                "data": page_data,
                "matched_fields": matched_fields,
                "match_score": match_count / len(search_conditions)
            })
            file_results[file_id]["total_matches"] += 1

    # 結果をリスト化してマッチ数でソート
    results = list(file_results.values())
    results.sort(key=lambda x: x["total_matches"], reverse=True)

    # matched_records を最大5件に制限
    for r in results:
        r["matched_records"] = r["matched_records"][:5]

    result = {
        "success": True,
        "search_conditions": {
            "equipment": equipment,
            "equipment_part": equipment_part,
            "inspection_item": inspection_item,
            "inspection_date": inspection_date
        },
        "total_documents": len(results),
        "results": results[:limit]
    }

    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# FastAPI + MCPマウント
# =============================================================================
from fastapi import FastAPI
from pydantic import BaseModel

# MCPのHTTPアプリを取得
# 【なぜ先に取得するか】
# FastMCPのlifespanをFastAPIに渡す必要があるため、先にアプリを作成
mcp_http_app = mcp.http_app()

# FastAPIアプリを作成（MCPのlifespanを渡す）
# 【重要】lifespan を渡さないと "Task group is not initialized" エラーになる
app = FastAPI(lifespan=mcp_http_app.lifespan)

# MCPエンドポイントをマウント（HTTP）
# 【注意】http_app() は既に /mcp パスを持っているので、ルート("") にマウント
app.mount("", mcp_http_app)


class SearchRequest(BaseModel):
    """検索リクエスト（後方互換用）"""
    equipment: str = ""
    equipment_part: str = ""
    inspection_item: str = ""
    inspection_date: str = ""
    tenant: str = "default"
    limit: int = 5


@app.get("/api/health")
async def api_health():
    """ヘルスチェック"""
    return {"status": "ok", "openai_configured": bool(OPENAI_API_KEY)}


@app.post("/api/search")
async def api_search(request: SearchRequest):
    """
    検索APIエンドポイント（後方互換用）

    【なぜ残しているか】
    - 既存のHTTP呼び出しとの互換性を維持
    - MCP非対応のクライアントからも利用可能
    """
    result_json = await search_documents(
        equipment=request.equipment,
        equipment_part=request.equipment_part,
        inspection_item=request.inspection_item,
        inspection_date=request.inspection_date,
        tenant=request.tenant,
        limit=request.limit
    )
    return json.loads(result_json)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
