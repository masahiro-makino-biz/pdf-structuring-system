# =============================================================================
# mcp/server.py - 検索サーバー
# =============================================================================
#
# 【ファイル概要】
# 点検記録の検索APIを提供するサーバー。
# chat_service.py からHTTP経由で呼び出される。
#
# 【処理フロー】
# 1. chat_service.py が POST /api/search にリクエスト
# 2. _search_documents_async() でMongoDBを検索
# 3. 検索条件に部分一致するレコードを返す
#
# 【依存関係】
# - MongoDB : documents コレクションを検索（processed: true のみ）
# - FastAPI : HTTPエンドポイント提供
# - FastMCP : 将来的にClaudeから直接呼び出す用（現在は未使用）
#
# =============================================================================

import os
from fastmcp import FastMCP
from motor.motor_asyncio import AsyncIOMotorClient

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
MONGO_URL = os.getenv("MONGO_URL", "mongodb://mongo:27017")

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


async def _search_documents_async(
    equipment: str = "",
    equipment_part: str = "",
    inspection_item: str = "",
    inspection_date: str = "",
    tenant: str = "default",
    limit: int = 5
) -> dict:
    """
    MongoDBから構造化データを検索する内部関数

    【処理フロー】
    1. 検索条件をリストに格納（空でないもののみ）
    2. MongoDBから全件取得
    3. 3重ループ（ファイル→ページ→レコード）でマッチング
    4. 部分一致でマッチしたレコードを収集
    5. マッチ数が多い順にソートして返す

    【なぜこの実装か】
    - 全件取得→Pythonフィルタ方式はシンプルで理解しやすい
    - データ量が増えたらMongoDBの$regexクエリに移行を検討

    Args:
        equipment: 機器名
        equipment_part: 機器部品名
        inspection_item: 点検項目
        inspection_date: 点検年月日
        tenant: テナントID
        limit: 返す結果の最大数

    Returns:
        {"success": bool, "results": [...], "total_documents": int}
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
        return {"success": False, "error": "検索条件を1つ以上指定してください", "results": []}

    # pages コレクションから処理済みページを検索（page_number が null でないもの）
    all_pages = await db.pages.find({
        "tenant": tenant,
        "page_number": {"$ne": None}
    }).to_list(500)

    # ファイルごとにマッチしたページをグループ化
    file_results = {}

    for page in all_pages:
        if "error" in page:
            continue

        # data 直下にフィールドがある新構造に対応
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

    return {
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


from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class SearchRequest(BaseModel):
    """検索リクエスト"""
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
    検索APIエンドポイント

    【処理フロー】
    1. chat_service.py からHTTPリクエストを受信
    2. _search_documents_async() で検索実行
    3. 結果をJSONで返す

    【なぜこの実装か】
    - HTTP経由にすることで、サービス間の疎結合を維持
    - 将来的に検索サーバーを別マシンに分離することも可能
    """
    result = await _search_documents_async(
        equipment=request.equipment,
        equipment_part=request.equipment_part,
        inspection_item=request.inspection_item,
        inspection_date=request.inspection_date,
        tenant=request.tenant,
        limit=request.limit
    )
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
