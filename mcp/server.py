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
        x_axis: X軸に使うカラム。"year"(年度) / "year_month"(年月) / "key"(測定値キー)。未指定なら自動判定（同一年に複数月あれば年月、複数年で各年1点なら年度、単月なら測定値キー）
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

    # 機器+機器部品+測定物理量 の組み合わせごとにグラフ生成（オプションパラメータをそのまま透過）
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
    curvefit_predicted_data: str = None,
    curvefit_prediction_info: str = None,
) -> str:
    """
    実データ + AI予測 + Prophet予測 + カーブフィット予測 を1つのグラフに可視化する（予測グラフ専用）

    【使い方】
    1. MongoDB MCPのfindで実データを取得する
    2. AIが実データのトレンドを分析して予測データを生成する（predicted_data）
    3. forecast_time_seriesでProphet予測を実行する（prophet_predicted_data）
    4. forecast_curve_fitでカーブフィット予測を実行する（curvefit_predicted_data）
    5. actual_data + 各予測データ をこのツールに渡す

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
        curvefit_predicted_data: カーブフィット予測データ（JSON文字列、オプション）。形式:
            [{"year": 2025, "values": {"摩耗量・タイヤ①・上": 0.33}}, ...]
        curvefit_prediction_info: カーブフィット予測メタ情報（JSON文字列、オプション）。形式:
            {"method": "カーブフィット（指数）", "threshold_crossing": {"キー名": 2027}, "repair_years": [2022]}

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

    # --- curvefit_predicted_data のパース（オプション） ---
    curvefit_predictions = []
    if curvefit_predicted_data:
        try:
            curvefit_predictions = json.loads(curvefit_predicted_data)
        except json.JSONDecodeError as e:
            print(f"[visualize_prediction] curvefit_predicted_data JSONパースエラー: {e}", flush=True)
        if not isinstance(curvefit_predictions, list):
            curvefit_predictions = [curvefit_predictions]

    # --- curvefit_prediction_info のパース（オプション） ---
    curvefit_info = {}
    if curvefit_prediction_info:
        try:
            curvefit_info = json.loads(curvefit_prediction_info)
        except json.JSONDecodeError:
            pass

    print(
        f"[visualize_prediction] 実データ: {len(results)}件, "
        f"AI予測: {len(predictions)}年分, "
        f"Prophet予測: {len(prophet_predictions)}年分, "
        f"カーブフィット予測: {len(curvefit_predictions)}年分",
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
            curvefit_predictions=curvefit_predictions,
            curvefit_prediction_info=curvefit_info,
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
# 改修検出ヘルパー（全予測ツール共通）
# =============================================================================
def _detect_repair_indices(values: list, drop_ratio: float = 0.5) -> list[int]:
    """
    前年比で drop_ratio（デフォルト50%）以上の低下があったインデックスを返す。
    改修により値が急激に回復した年を検出する。
    """
    repair_indices = []
    for i in range(1, len(values)):
        if values[i - 1] != 0:
            drop = (values[i - 1] - values[i]) / abs(values[i - 1])
            if drop >= drop_ratio:
                repair_indices.append(i)
    return repair_indices


def _trim_to_last_cycle(dates: list, values: list, drop_ratio: float = 0.5) -> tuple[list, list, list[int]]:
    """
    改修を検出し、最後の改修以降のデータだけを返す。
    改修がなければ全データをそのまま返す。

    Returns:
        (trimmed_dates, trimmed_values, repair_years)
    """
    repair_indices = _detect_repair_indices(values, drop_ratio)
    years = [int(d[:4]) for d in dates]
    repair_years = [years[i] for i in repair_indices]

    if repair_indices:
        last_repair = repair_indices[-1]
        return dates[last_repair:], values[last_repair:], repair_years
    else:
        return dates, values, []


# =============================================================================
# 線形回帰予測ツール
# =============================================================================
@mcp.tool()
async def forecast_linear(
    ds: str,
    y: str,
    key_name: str,
    periods: int = 5,
    upper_limit: float = None,
    lower_limit: float = None,
) -> str:
    """
    線形回帰（最小二乗法）で予測する（改修サイクル自動除外）

    【特徴】
    - 改修（値の急激な回復）を自動検出し、最後の改修以降のデータで直線を当てはめる
    - 毎回同じ結果が出る（再現性がある）
    - データが少なくても動作する
    - visualize_prediction にそのまま渡せる形式で返す

    【使い方】
    1. MongoDB MCPのfindで年度別の測定値データを取得する
    2. ds（日付リスト）とy（測定値リスト）とkey_name（測定値キー名）を渡す
    3. 戻り値のpredicted_dataとprediction_infoをそのままvisualize_predictionに渡す

    Args:
        ds: 日付のリスト（JSON文字列）。例: '["2018-01-01", "2019-01-01", "2020-01-01"]'
        y: 測定値のリスト（JSON文字列）。dsと同じ長さ。例: '[0.10, 0.16, 0.22]'
        key_name: 測定値キー名。例: "摩耗量・タイヤ①・上"
        periods: 予測する将来の期間数（デフォルト: 5）
        upper_limit: 上限値（基準値）。超過する予測値にフラグを立てる
        lower_limit: 下限値。下回る予測値にフラグを立てる

    Returns:
        predicted_data（visualize_prediction用）とprediction_infoを含むJSON
    """
    import numpy as np
    from scipy.stats import linregress

    print(f"[forecast_linear] 開始: periods={periods}, upper_limit={upper_limit}", flush=True)

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

    # 改修検出 → 全サイクルを「改修後n年目」に正規化して全データ活用
    years = [int(d[:4]) for d in dates]
    repair_indices = _detect_repair_indices(values)
    repair_years_list = [years[i] for i in repair_indices]

    # サイクル分割 → 正規化（改修後の経過年数に揃える）
    cycle_starts = [0] + repair_indices
    normalized_x = []
    normalized_y = []
    for idx, start in enumerate(cycle_starts):
        end = cycle_starts[idx + 1] if idx + 1 < len(cycle_starts) else len(values)
        for i in range(start, end):
            normalized_x.append(i - start)
            normalized_y.append(values[i])

    if repair_years_list:
        print(f"[forecast_linear] 改修検出: {repair_years_list} → 正規化して{len(normalized_x)}点で回帰", flush=True)

    normalized_x = np.array(normalized_x, dtype=float)
    y_vals = np.array(normalized_y, dtype=float)

    # 線形回帰（正規化した経過年数 vs 値）
    result = linregress(normalized_x, y_vals)
    slope = result.slope
    intercept = result.intercept
    r_squared = result.rvalue ** 2

    print(f"[forecast_linear] 傾き={slope:.6f}/年, 切片={intercept:.4f}, R²={r_squared:.4f}", flush=True)

    # 将来を予測（最後のサイクルの経過年数を起点にする）
    last_cycle_start = cycle_starts[-1]
    current_elapsed = len(values) - 1 - last_cycle_start
    last_year = years[-1]
    forecast_results = []
    first_exceed_year = None

    for i in range(1, periods + 1):
        future_elapsed = current_elapsed + i
        future_year = last_year + i
        yhat = float(slope * future_elapsed + intercept)
        ds_str = f"{future_year}-01-01"

        status = "OK"
        if upper_limit is not None and yhat > upper_limit:
            status = "EXCEEDS_UPPER"
            if first_exceed_year is None:
                first_exceed_year = str(future_year)
        elif lower_limit is not None and yhat < lower_limit:
            status = "BELOW_LOWER"

        forecast_results.append({
            "ds": ds_str,
            "yhat": round(yhat, 4),
            "status": status,
        })

    # トレンド方向の判定
    hist_mean = float(np.mean(y_vals))
    future_values = [f["yhat"] for f in forecast_results]
    if len(future_values) > 0 and hist_mean != 0:
        fcst_mean = np.mean(future_values)
        change_pct = ((fcst_mean - hist_mean) / abs(hist_mean)) * 100
        if change_pct > 5:
            trend = f"上昇傾向（+{change_pct:.1f}%）"
        elif change_pct < -5:
            trend = f"下降傾向（{change_pct:.1f}%）"
        else:
            trend = "横ばい"
    else:
        trend = "判定不能"

    # visualize_prediction 用のフォーマットで返す
    # LLMによる変換を排除し、ツールの計算結果をそのまま渡せるようにする
    predicted_data = [
        {"year": int(f["ds"][:4]), "values": {key_name: f["yhat"]}}
        for f in forecast_results
    ]
    threshold_crossing = {}
    if first_exceed_year is not None:
        threshold_crossing[key_name] = int(first_exceed_year)

    prediction_info = {
        "method": "線形回帰（最小二乗法）",
        "threshold_crossing": threshold_crossing,
        "note": f"傾き: {slope:.6f}/年, R²: {r_squared:.4f}",
    }

    result_json = {
        "success": True,
        "predicted_data": predicted_data,
        "prediction_info": prediction_info,
    }

    print(f"[forecast_linear] 完了: slope={slope:.6f}, trend={trend}, exceed={first_exceed_year}, repairs={repair_years_list}", flush=True)
    return json.dumps(result_json, ensure_ascii=False)


# =============================================================================
# Prophet統計予測ツール
# =============================================================================
@mcp.tool()
async def forecast_time_series(
    ds: str,
    y: str,
    key_name: str,
    periods: int = 5,
    upper_limit: float = None,
    lower_limit: float = None,
) -> str:
    """
    Meta Prophetによる時系列予測を実行する（改修サイクル自動除外）

    【特徴】
    - 改修（値の急激な回復）を自動検出し、最後の改修以降のデータで予測する
    - 統計モデルベースで季節性やトレンド変化も考慮できる
    - visualize_prediction にそのまま渡せる形式で返す

    【使い方】
    1. MongoDB MCPのfindで年度別の測定値データを取得する
    2. ds（日付リスト）とy（測定値リスト）とkey_name（測定値キー名）を渡す
    3. 戻り値のpredicted_dataとprediction_infoをそのままvisualize_predictionに渡す

    Args:
        ds: 日付のリスト（JSON文字列）。例: '["2018-01-01", "2019-01-01", "2020-01-01"]'
        y: 測定値のリスト（JSON文字列）。dsと同じ長さ。例: '[0.10, 0.16, 0.22]'
        key_name: 測定値キー名。例: "摩耗量・タイヤ①・上"
        periods: 予測する将来の期間数（デフォルト: 5）
        upper_limit: 上限値（基準値）。超過する予測値にフラグを立てる
        lower_limit: 下限値。下回る予測値にフラグを立てる

    Returns:
        predicted_data（visualize_prediction用）とprediction_infoを含むJSON
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

    # 改修検出 → 最後の改修以降のデータだけ使う
    trimmed_dates, trimmed_values, repair_years = _trim_to_last_cycle(dates, values)
    if repair_years:
        print(f"[forecast_time_series] 改修検出: {repair_years} → 最後の改修以降 {len(trimmed_dates)}点で予測", flush=True)

    if len(trimmed_dates) < 2:
        return json.dumps({
            "success": False,
            "error": "改修後のデータが2点未満のため予測できません"
        }, ensure_ascii=False)

    # Prophet用DataFrameを作成
    df = pd.DataFrame({"ds": trimmed_dates, "y": trimmed_values})
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

    # visualize_prediction 用のフォーマットで返す
    # 将来分（実データより後の年度）だけ抽出
    last_hist_year = int(df["ds"].max().strftime("%Y"))
    future_records = [
        r for r in out.to_dict(orient="records")
        if int(r["ds"][:4]) > last_hist_year
    ]
    predicted_data = [
        {"year": int(r["ds"][:4]), "values": {key_name: r["yhat"]}}
        for r in future_records
    ]
    threshold_crossing = {}
    if first_exceed_year is not None:
        threshold_crossing[key_name] = int(first_exceed_year)

    prediction_info = {
        "method": "Prophet統計モデル",
        "threshold_crossing": threshold_crossing,
        "trend": trend,
    }

    result = {
        "success": True,
        "predicted_data": predicted_data,
        "prediction_info": prediction_info,
    }

    print(f"[forecast_time_series] 完了: trend={trend}, exceed={first_exceed_year}, repairs={repair_years}", flush=True)
    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# カーブフィット予測ツール（改修サイクル対応）
# =============================================================================
@mcp.tool()
async def forecast_curve_fit(
    ds: str,
    y: str,
    key_name: str,
    periods: int = 5,
    upper_limit: float = None,
    lower_limit: float = None,
    repair_drop_ratio: float = 0.5,
) -> str:
    """
    改修サイクルを自動検出し、最適カーブで予測する（カーブフィッティング）

    【特徴】
    - 改修（値の急激な回復）を自動検出し、改修年のデータを除外する
    - 各改修サイクルを「改修後n年目」に正規化して全データを活用する
    - 線形・指数・対数の3種類のカーブから最もデータに合うものを自動選択する
    - visualize_prediction にそのまま渡せる形式で返す

    【使い方】
    1. MongoDB MCPのfindで年度別の測定値データを取得する
    2. ds（日付リスト）とy（測定値リスト）とkey_name（測定値キー名）を渡す
    3. 戻り値のpredicted_dataとprediction_infoをそのままvisualize_predictionに渡す

    Args:
        ds: 日付のリスト（JSON文字列）。例: '["2018-01-01", "2019-01-01", "2020-01-01"]'
        y: 測定値のリスト（JSON文字列）。dsと同じ長さ。例: '[0.10, 0.16, 0.22]'
        key_name: 測定値キー名。例: "摩耗量・タイヤ①・上"
        periods: 予測する将来の期間数（デフォルト: 5）
        upper_limit: 上限値（基準値）。超過する予測値にフラグを立てる
        lower_limit: 下限値。下回る予測値にフラグを立てる
        repair_drop_ratio: 改修検出の閾値。前年比でこの割合以上下がったら改修と判定（デフォルト: 0.5 = 50%）

    Returns:
        predicted_data（visualize_prediction用）とprediction_infoを含むJSON
    """
    import numpy as np
    from scipy.optimize import curve_fit

    print(f"[forecast_curve_fit] 開始: periods={periods}, upper_limit={upper_limit}, repair_drop_ratio={repair_drop_ratio}", flush=True)

    # --- パラメータのパース ---
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

    # 年を抽出
    years = [int(d[:4]) for d in dates]

    # --- ステップ1: 改修年の検出（共通ヘルパー使用） ---
    repair_indices = _detect_repair_indices(values, repair_drop_ratio)
    repair_years = [years[i] for i in repair_indices]
    print(f"[forecast_curve_fit] 検出した改修年: {repair_years}", flush=True)

    # --- ステップ2: サイクルに分割して「改修後n年目」に正規化 ---
    # サイクルの開始点: データの先頭 + 各改修年
    cycle_starts = [0] + repair_indices
    normalized_x = []  # 改修後の経過年数
    normalized_y = []  # 測定値

    for idx, start in enumerate(cycle_starts):
        end = cycle_starts[idx + 1] if idx + 1 < len(cycle_starts) else len(values)
        for i in range(start, end):
            elapsed = i - start  # 改修後の経過年数
            normalized_x.append(elapsed)
            normalized_y.append(values[i])

    normalized_x = np.array(normalized_x, dtype=float)
    normalized_y = np.array(normalized_y, dtype=float)

    print(f"[forecast_curve_fit] 正規化後データ点数: {len(normalized_x)}", flush=True)

    # --- ステップ3: 3種類のカーブをフィットして最適なものを選択 ---
    # 各カーブの定義
    def linear(x, a, b):
        return a * x + b

    def exponential(x, a, b, c):
        return a * np.exp(b * x) + c

    def logarithmic(x, a, b):
        return a * np.log(x + 1) + b

    # 各カーブをフィットしてSSE（残差平方和）で比較
    best_curve = None
    best_sse = float("inf")
    best_params = None
    best_name = ""

    candidates = [
        ("linear", linear, [1.0, 0.0]),
        ("exponential", exponential, [0.01, 0.1, 0.0]),
        ("logarithmic", logarithmic, [1.0, 0.0]),
    ]

    for name, func, p0 in candidates:
        try:
            params, _ = curve_fit(func, normalized_x, normalized_y, p0=p0, maxfev=5000)
            predicted = func(normalized_x, *params)
            sse = np.sum((normalized_y - predicted) ** 2)
            print(f"[forecast_curve_fit] {name}: SSE={sse:.6f}, params={params}", flush=True)
            if sse < best_sse:
                best_sse = sse
                best_curve = func
                best_params = params
                best_name = name
        except Exception as e:
            print(f"[forecast_curve_fit] {name} フィット失敗: {e}", flush=True)
            continue

    if best_curve is None:
        return json.dumps({
            "success": False,
            "error": "どのカーブもフィットできませんでした"
        }, ensure_ascii=False)

    curve_name_jp = {
        "linear": "線形（直線）",
        "exponential": "指数（加速劣化）",
        "logarithmic": "対数（減速劣化）",
    }
    print(f"[forecast_curve_fit] 最適カーブ: {best_name}", flush=True)

    # --- ステップ4: 最後のサイクルの経過年数を起点に将来を予測 ---
    last_cycle_start = cycle_starts[-1]
    current_elapsed = len(values) - 1 - last_cycle_start  # 最後のサイクルでの現在の経過年数

    forecast_results = []
    last_year = years[-1]
    first_exceed_year = None

    for i in range(1, periods + 1):
        future_elapsed = current_elapsed + i
        yhat = float(best_curve(future_elapsed, *best_params))
        forecast_year = last_year + i
        ds_str = f"{forecast_year}-01-01"

        status = "OK"
        if upper_limit is not None and yhat > upper_limit:
            status = "EXCEEDS_UPPER"
            if first_exceed_year is None:
                first_exceed_year = str(forecast_year)
        elif lower_limit is not None and yhat < lower_limit:
            status = "BELOW_LOWER"

        forecast_results.append({
            "ds": ds_str,
            "yhat": round(yhat, 4),
            "status": status,
        })

    # --- トレンド方向の判定 ---
    future_values = [f["yhat"] for f in forecast_results]
    hist_mean = float(np.mean(normalized_y))
    if len(future_values) > 0 and hist_mean != 0:
        fcst_mean = np.mean(future_values)
        change_pct = ((fcst_mean - hist_mean) / abs(hist_mean)) * 100
        if change_pct > 5:
            trend = f"上昇傾向（+{change_pct:.1f}%）"
        elif change_pct < -5:
            trend = f"下降傾向（{change_pct:.1f}%）"
        else:
            trend = "横ばい"
    else:
        trend = "判定不能"

    # visualize_prediction 用のフォーマットで返す
    predicted_data = [
        {"year": int(f["ds"][:4]), "values": {key_name: f["yhat"]}}
        for f in forecast_results
    ]
    threshold_crossing = {}
    if first_exceed_year is not None:
        threshold_crossing[key_name] = int(first_exceed_year)

    method_name = f"カーブフィット（{curve_name_jp.get(best_name, best_name)}）"
    prediction_info = {
        "method": method_name,
        "threshold_crossing": threshold_crossing,
        "repair_years": repair_years,
        "curve_type": best_name,
        "trend": trend,
    }

    result = {
        "success": True,
        "predicted_data": predicted_data,
        "prediction_info": prediction_info,
    }

    print(f"[forecast_curve_fit] 完了: curve={best_name}, trend={trend}, exceed={first_exceed_year}", flush=True)
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
