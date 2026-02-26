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
import traceback
from fastmcp import FastMCP
import chart_utils

# MCPサーバーを作成
mcp = FastMCP("pdf-tools")


# =============================================================================
# MCPツール定義
# =============================================================================
@mcp.tool()
async def visualize_data(
    data: str,
    chart_type: str = None,
    color: str = None,
    year_from: int = None,
    year_to: int = None,
    min_value: float = None,
    max_value: float = None,
    show_reference: bool = True,
    x_axis: str = None,
    key_filter: str = None,
    above_reference: bool = False,
) -> str:
    """
    検索結果データからグラフを生成する（グラフ生成専用）

    【使い方】
    1. 先にMongoDB MCPのfindで検索データを取得する
    2. その結果をdataパラメータにJSON文字列として渡す
    3. 必要に応じてオプションパラメータでグラフをカスタマイズする

    Args:
        data: MongoDB findの検索結果（JSON文字列）
        chart_type: グラフの種類。"strip", "scatter", "bar", "line"。未指定なら自動判定（測定値キーが5個以下→line、6個以上→strip）
        color: データ点の色を統一する場合に指定（例: "red", "#FF6600"）。未指定なら測定値キーで自動色分け
        year_from: この年度以降のデータだけ表示（例: 2024）
        year_to: この年度以前のデータだけ表示（例: 2025）
        min_value: この値以上のデータだけ表示（例: 0.5）
        max_value: この値以下のデータだけ表示（例: 1.0）
        show_reference: 基準値の赤い線を表示するか（デフォルト: True）
        x_axis: X軸に使うカラム。"year"(年度) or "key"(点検項目)。未指定なら自動判定（複数年→年度、単年→点検項目）
        key_filter: 測定値キー名で絞る（部分一致、カンマ区切りで複数可）。例: "タイヤ・上・①" や "振動値・A,振動値・B"
        above_reference: Trueにすると基準値を超えているデータだけ表示（デフォルト: False）

    Returns:
        グラフHTMLファイルパスを含むJSON文字列
    """
    print(f"[visualize_data] 開始: 長さ={len(data)}, 先頭200文字={data[:200]}", flush=True)

    # JSON文字列をパース
    try:
        documents = json.loads(data)
    except json.JSONDecodeError as e:
        print(f"[visualize_data] JSONパースエラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"データのJSON解析エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    if not isinstance(documents, list):
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
        # docがdictでない場合（AIが予期しない形式で渡した場合）のガード
        if not isinstance(doc, dict):
            print(f"[visualize_data] 警告: docがdictでない: type={type(doc).__name__}", flush=True)
            continue
        doc_data = doc.get("data", {})
        if not doc_data:
            print(f"[visualize_data] 警告: dataフィールドが空: keys={list(doc.keys())}", flush=True)
            continue
        results.append({
            "matched_records": [{
                "data": doc_data
            }]
        })
        if doc.get("image_path"):
            reference_images.append(doc.get("image_path"))

    print(f"[visualize_data] 有効データ: {len(results)}件 / {len(documents)}件中", flush=True)

    if not results:
        return json.dumps({
            "success": False,
            "error": "有効なデータがありません（dataフィールドを持つドキュメントが必要）",
            "charts": []
        }, ensure_ascii=False)

    # 計測箇所ごとにグラフ生成（オプションパラメータをそのまま透過）
    try:
        result = chart_utils.create_charts_by_location(
            results,
            chart_type=chart_type,
            color=color,
            year_from=year_from,
            year_to=year_to,
            min_value=min_value,
            max_value=max_value,
            show_reference=show_reference,
            x_axis=x_axis,
            key_filter=key_filter,
            above_reference=above_reference,
        )
        print(f"[visualize_data] 完了: {json.dumps(result, ensure_ascii=False)[:300]}", flush=True)
        result["reference_images"] = reference_images
    except Exception as e:
        print(f"[visualize_data] グラフ生成エラー: {e}", flush=True)
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
