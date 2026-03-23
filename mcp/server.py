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


@mcp.tool()
async def visualize_prediction(
    actual_data: str,
    predicted_data: str,
    prediction_info: str = None,
    prophet_predicted_data: str = None,
    prophet_prediction_info: str = None,
) -> str:
    """
    実データ + AI予測データ + Prophet予測データ を1つのグラフに可視化する（予測グラフ専用）

    【使い方】
    1. MongoDB MCPのfindで実データを取得する
    2. AIが実データのトレンドを分析して予測データを生成する（predicted_data）
    3. forecast_time_seriesでProphet予測を実行する（prophet_predicted_data）
    4. actual_data + predicted_data + prophet_predicted_data をこのツールに渡す

    Args:
        actual_data: MongoDB findの結果（JSON文字列）。visualize_dataと同じ形式
        predicted_data: AI予測データ（JSON文字列）。形式:
            [{"year": 2025, "values": {"摩耗量・タイヤ①・上": 0.32}}, ...]
        prediction_info: AI予測メタ情報（JSON文字列、オプション）。形式:
            {"method": "線形近似", "threshold_crossing": {"キー名": 2027}, "note": "..."}
        prophet_predicted_data: Prophet予測データ（JSON文字列、オプション）。形式:
            [{"year": 2025, "values": {"摩耗量・タイヤ①・上": 0.35}}, ...]
        prophet_prediction_info: Prophet予測メタ情報（JSON文字列、オプション）。形式:
            {"method": "Prophet統計モデル", "threshold_crossing": {"キー名": 2028}, "trend": "上昇傾向"}

    Returns:
        グラフHTMLファイルパスを含むJSON文字列
    """
    print(f"[visualize_prediction] 開始", flush=True)

    # --- actual_data のパース（visualize_data と同じロジック） ---
    try:
        documents = json.loads(actual_data)
    except json.JSONDecodeError as e:
        print(f"[visualize_prediction] actual_data JSONパースエラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"actual_dataのJSON解析エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    if not isinstance(documents, list):
        documents = [documents]

    results = []
    reference_images = []
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        doc_data = doc.get("data", {})
        if not doc_data:
            continue
        results.append({
            "matched_records": [{"data": doc_data}]
        })
        if doc.get("image_path"):
            reference_images.append(doc.get("image_path"))

    if not results:
        return json.dumps({
            "success": False,
            "error": "有効な実データがありません",
            "charts": []
        }, ensure_ascii=False)

    # --- predicted_data のパース ---
    try:
        predictions = json.loads(predicted_data)
    except json.JSONDecodeError as e:
        print(f"[visualize_prediction] predicted_data JSONパースエラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"predicted_dataのJSON解析エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    if not isinstance(predictions, list):
        predictions = [predictions]

    # --- prediction_info のパース（オプション） ---
    info = {}
    if prediction_info:
        try:
            info = json.loads(prediction_info)
        except json.JSONDecodeError:
            pass

    # --- prophet_predicted_data のパース（オプション） ---
    prophet_predictions = []
    if prophet_predicted_data:
        try:
            prophet_predictions = json.loads(prophet_predicted_data)
        except json.JSONDecodeError as e:
            print(f"[visualize_prediction] prophet_predicted_data JSONパースエラー: {e}", flush=True)
        if not isinstance(prophet_predictions, list):
            prophet_predictions = [prophet_predictions]

    # --- prophet_prediction_info のパース（オプション） ---
    prophet_info = {}
    if prophet_prediction_info:
        try:
            prophet_info = json.loads(prophet_prediction_info)
        except json.JSONDecodeError:
            pass

    print(
        f"[visualize_prediction] 実データ: {len(results)}件, "
        f"AI予測: {len(predictions)}年分, "
        f"Prophet予測: {len(prophet_predictions)}年分",
        flush=True
    )

    # --- グラフ生成 ---
    try:
        result = chart_utils.create_prediction_chart(
            results=results,
            predictions=predictions,
            prediction_info=info,
            prophet_predictions=prophet_predictions,
            prophet_prediction_info=prophet_info,
        )
        print(f"[visualize_prediction] 完了: {json.dumps(result, ensure_ascii=False)[:300]}", flush=True)
        result["reference_images"] = reference_images
    except Exception as e:
        print(f"[visualize_prediction] グラフ生成エラー: {e}", flush=True)
        traceback.print_exc()
        return json.dumps({
            "success": False,
            "error": f"予測グラフ生成エラー: {str(e)}",
            "charts": []
        }, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# Prophet統計予測ツール
# =============================================================================
@mcp.tool()
async def forecast_time_series(
    ds: str,
    y: str,
    periods: int = 5,
    upper_limit: float = None,
    lower_limit: float = None,
) -> str:
    """
    Meta Prophetによる時系列予測を実行する（統計モデルベース）

    【使い方】
    1. MongoDB MCPのfindで年度別の測定値データを取得する
    2. ds（日付リスト）とy（測定値リスト）を渡す
    3. 予測結果をvisualize_predictionのprophet_predicted_dataに渡してグラフ化する

    Args:
        ds: 日付のリスト（JSON文字列）。例: '["2018-01-01", "2019-01-01", "2020-01-01"]'
        y: 測定値のリスト（JSON文字列）。dsと同じ長さ。例: '[0.10, 0.16, 0.22]'
        periods: 予測する将来の期間数（デフォルト: 5）
        upper_limit: 上限値（基準値）。超過する予測値にフラグを立てる
        lower_limit: 下限値。下回る予測値にフラグを立てる

    Returns:
        予測結果のJSON。forecast配列（ds, yhat, yhat_lower, yhat_upper, status）と
        メタ情報（トレンド方向、超過年度など）を含む
    """
    import pandas as pd
    from prophet import Prophet

    print(f"[forecast_time_series] 開始: periods={periods}, upper_limit={upper_limit}", flush=True)

    # パラメータのパース
    try:
        dates = json.loads(ds)
        values = json.loads(y)
    except json.JSONDecodeError as e:
        return json.dumps({
            "success": False,
            "error": f"パラメータのJSON解析エラー: {str(e)}"
        }, ensure_ascii=False)

    if len(dates) != len(values):
        return json.dumps({
            "success": False,
            "error": f"dsとyの長さが一致しません（ds={len(dates)}, y={len(values)}）"
        }, ensure_ascii=False)

    if len(dates) < 2:
        return json.dumps({
            "success": False,
            "error": "データが2点未満のため予測できません"
        }, ensure_ascii=False)

    # Prophet用DataFrameを作成
    df = pd.DataFrame({"ds": dates, "y": values})
    df["ds"] = pd.to_datetime(df["ds"])

    # Prophetモデルで予測を実行
    try:
        model = Prophet()
        model.fit(df)
        future = model.make_future_dataframe(periods=periods, freq="YS")
        forecast = model.predict(future)
    except Exception as e:
        print(f"[forecast_time_series] Prophetエラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"Prophet予測エラー: {str(e)}"
        }, ensure_ascii=False)

    # 予測結果を整形
    out = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].copy()
    out["ds"] = out["ds"].dt.strftime("%Y-%m-%d")
    out["yhat"] = out["yhat"].round(4)
    out["yhat_lower"] = out["yhat_lower"].round(4)
    out["yhat_upper"] = out["yhat_upper"].round(4)

    # 基準値チェック
    first_exceed_year = None
    statuses = []
    for _, row in out.iterrows():
        yhat_val = row["yhat"]
        if upper_limit is not None and yhat_val > upper_limit:
            statuses.append("EXCEEDS_UPPER")
            if first_exceed_year is None:
                first_exceed_year = row["ds"][:4]
        elif lower_limit is not None and yhat_val < lower_limit:
            statuses.append("BELOW_LOWER")
        else:
            statuses.append("OK")
    out["status"] = statuses

    # トレンド方向の判定
    future_only = forecast.iloc[len(df):]
    hist_mean = df["y"].mean()
    if len(future_only) > 0 and hist_mean != 0:
        fcst_mean = future_only["yhat"].mean()
        change_pct = ((fcst_mean - hist_mean) / abs(hist_mean)) * 100
        if change_pct > 5:
            trend = f"上昇傾向（+{change_pct:.1f}%）"
        elif change_pct < -5:
            trend = f"下降傾向（{change_pct:.1f}%）"
        else:
            trend = "横ばい"
    else:
        trend = "判定不能"

    result = {
        "success": True,
        "forecast": out.to_dict(orient="records"),
        "meta": {
            "periods": periods,
            "n_history": len(df),
            "hist_start": df["ds"].min().strftime("%Y-%m-%d"),
            "hist_end": df["ds"].max().strftime("%Y-%m-%d"),
            "trend": trend,
            "first_exceed_year": first_exceed_year,
        }
    }

    print(f"[forecast_time_series] 完了: trend={trend}, exceed={first_exceed_year}", flush=True)
    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# iframeテスト用ツール（WxO検証用）
# =============================================================================
@mcp.tool()
async def test_iframe(
    url: str = "https://ja.wikipedia.org/wiki/%E3%83%A1%E3%82%A4%E3%83%B3%E3%83%9A%E3%83%BC%E3%82%B8",
) -> str:
    """
    Watson Assistant APIを呼び出し、iframe形式でURLを表示するテスト用ツール

    【検証の目的】
    Agent → MCPツール → Assistant API → iframe response_type
    この経路でiframeがWxOチャット上に表示されるかを確認する

    Args:
        url: iframe に埋め込むURL（デフォルト: Plotlyサンプルグラフ）

    Returns:
        Watson Assistant の応答（iframe response_type を含むJSON）
    """
    import httpx

    print(f"[test_iframe] URL: {url}", flush=True)

    # Watson Assistant API の設定（環境変数から取得）
    import os
    assistant_url = os.environ.get("WA_URL", "")
    assistant_api_key = os.environ.get("WA_API_KEY", "")
    assistant_id = os.environ.get("WA_ASSISTANT_ID", "")
    environment_id = os.environ.get("WA_ENVIRONMENT_ID", "")
    api_version = "2023-06-15"

    if not all([assistant_url, assistant_api_key, assistant_id, environment_id]):
        return json.dumps({
            "success": False,
            "error": "Watson Assistant の環境変数が未設定です（WA_URL, WA_API_KEY, WA_ASSISTANT_ID, WA_ENVIRONMENT_ID）",
        }, ensure_ascii=False)

    # Watson Assistant V2 API: メッセージ送信
    # セッション作成 → メッセージ送信 の2ステップ
    # 【注意】新しいAPI形式では environment_id が必要
    #   旧: /v2/assistants/{id}/sessions
    #   新: /v2/assistants/{id}/environments/{env_id}/sessions
    base_path = f"{assistant_url}/v2/assistants/{assistant_id}/environments/{environment_id}"
    try:
        async with httpx.AsyncClient() as client:
            # 1. セッション作成
            session_resp = await client.post(
                f"{base_path}/sessions",
                params={"version": api_version},
                auth=("apikey", assistant_api_key),
                headers={"Content-Type": "application/json"},
            )
            session_resp.raise_for_status()
            session_id = session_resp.json()["session_id"]
            print(f"[test_iframe] セッション作成: {session_id}", flush=True)

            # 2. メッセージ送信（show_chart + URLを1回で送信）
            message_resp = await client.post(
                f"{base_path}/sessions/{session_id}/message",
                params={"version": api_version},
                auth=("apikey", assistant_api_key),
                headers={"Content-Type": "application/json"},
                json={
                    "user_id": "mcp_test_user",
                    "input": {
                        "message_type": "text",
                        "text": f"show_chart {url}",
                    },
                },
            )
            message_resp.raise_for_status()
            response_data = message_resp.json()

            print(f"[test_iframe] Assistant応答: {json.dumps(response_data, ensure_ascii=False)[:500]}", flush=True)

            # 3. セッション削除
            await client.delete(
                f"{base_path}/sessions/{session_id}",
                params={"version": api_version},
                auth=("apikey", assistant_api_key),
            )

    except Exception as e:
        print(f"[test_iframe] エラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"Watson Assistant API エラー: {str(e)}",
        }, ensure_ascii=False)

    return json.dumps({
        "success": True,
        "assistant_response": response_data,
    }, ensure_ascii=False)


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
