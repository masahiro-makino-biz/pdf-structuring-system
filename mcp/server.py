# =============================================================================
# mcp/server.py - MCPグラフ生成サーバー
# =============================================================================
#
# 【ファイル概要】
# 点検データの可視化機能をMCPツールとして提供するサーバー。
# chat_service.py から MCPServerStreamableHttp 経由で呼び出される。
#
# 【処理フロー】
# 1. AIがMongoDB MCPのfindでデータを検索
# 2. AIがその結果をvisualize_dataツールにJSON文字列として渡す
# 3. chart_utilsでグラフを生成してファイルパスを返す
#
# 【依存関係】
# - FastMCP : MCPプロトコルでツールを提供
# - FastAPI : HTTPエンドポイント提供
# - chart_utils : グラフ生成処理
#
# =============================================================================

import json
from fastmcp import FastMCP
import chart_utils

# MCPサーバーを作成
mcp = FastMCP("pdf-tools")


# =============================================================================
# MCPツール定義
# =============================================================================
@mcp.tool()
async def visualize_data(data: str) -> str:
    """
    検索結果データから計測箇所ごとに散布図を生成する（グラフ生成専用）

    【使い方】
    1. 先にMongoDB MCPのfindで検索データを取得する
    2. その結果をdataパラメータにJSON文字列として渡す

    【グラフ仕様】
    - 可視化単位: 計測箇所ごとに1グラフ
    - X軸: 年度（複数年のデータを表示）
    - Y軸: 測定値
    - 凡例: 測定値キー
    - 基準値: 赤い水平線

    Args:
        data: MongoDB findの検索結果（JSON文字列）
              配列形式で、各要素は以下の構造:
              { "data": { "機器": "...", "機器部品": "...", "計測箇所": "...",
                          "点検年月日": "...", "測定値": {...}, "基準値": {...} },
                "image_path": "..." }

    Returns:
        計測箇所ごとのグラフ画像パスを含むJSON文字列
    """
    print(f"[visualize_data] 開始: データ受信, 長さ={len(data)}")

    # JSON文字列をパース
    try:
        documents = json.loads(data)
    except json.JSONDecodeError as e:
        return json.dumps({
            "success": False,
            "error": f"データのJSON解析エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    if not isinstance(documents, list):
        # 単一オブジェクトの場合は配列に変換
        documents = [documents]

    if not documents:
        return json.dumps({
            "success": False,
            "error": "データが空です",
            "charts": []
        }, ensure_ascii=False)

    # MongoDB findの結果を chart_utils が期待する形式に変換
    results = []
    reference_images = []
    for doc in documents:
        doc_data = doc.get("data", {})
        results.append({
            "matched_records": [{
                "data": doc_data
            }]
        })
        if doc.get("image_path"):
            reference_images.append(doc.get("image_path"))

    print(f"[visualize_data] データ件数: {len(results)}件")

    # 計測箇所ごとにグラフ生成
    try:
        result = chart_utils.create_charts_by_location(results)
        print(f"[visualize_data] グラフ生成完了: {result.get('total_locations')}箇所")
        result["reference_images"] = reference_images
    except Exception as e:
        print(f"[visualize_data] グラフ生成エラー: {e}")
        import traceback
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"グラフ生成エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# FastAPI + MCPマウント
# =============================================================================
from fastapi import FastAPI

# MCPのHTTPアプリを取得
# 【なぜ先に取得するか】
# FastMCPのlifespanをFastAPIに渡す必要があるため、先にアプリを作成
mcp_http_app = mcp.http_app()

# FastAPIアプリを作成（MCPのlifespanを渡す）
# 【重要】lifespan を渡さないと "Task group is not initialized" エラーになる
app = FastAPI(lifespan=mcp_http_app.lifespan)


@app.get("/api/health")
async def api_health():
    """ヘルスチェック"""
    return {"status": "ok"}


# MCPエンドポイントをマウント（HTTP）
# 【重要】FastAPIルートの後にマウント
# 【注意】http_app() は既に /mcp パスを持っているので、ルート("") にマウント
app.mount("", mcp_http_app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
