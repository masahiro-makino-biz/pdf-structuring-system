"""
forecast_curve_fit のテストスクリプト

5つのパターンでカーブフィット予測をテストし、
パターン1は予測チャートのパイプラインも通して検証する。

使い方（Docker コンテナ内で実行）:
    docker compose exec mcp python /app/../tests/test_curve_fit.py

    または mcp コンテナ内に tests/ をマウントして:
    python /tests/test_curve_fit.py

ローカル実行（scipy, numpy, plotly, pandas がインストール済みの場合）:
    pip install scipy numpy plotly pandas
    python tests/test_curve_fit.py
"""

import asyncio
import json
import os
import sys

# mcp/ ディレクトリをインポートパスに追加
# server.py 内の `import chart_utils` が解決できるようにする
MCP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mcp")
sys.path.insert(0, MCP_DIR)

# --- forecast_curve_fit のインポート ---
# server.py は FastMCP / FastAPI をモジュールレベルでインポートするため、
# それらがインストールされていない環境（ローカル）ではインポートに失敗する。
# その場合、forecast_curve_fit のロジックだけを直接コピーして使う。
try:
    from server import forecast_curve_fit
    print("[setup] server.py から forecast_curve_fit をインポート成功")
except ImportError as e:
    print(f"[setup] server.py インポート失敗 ({e})")
    print("[setup] forecast_curve_fit のロジックを直接定義して使用")

    import numpy as np
    from scipy.optimize import curve_fit

    async def forecast_curve_fit(
        ds: str,
        y: str,
        periods: int = 5,
        upper_limit: float = None,
        lower_limit: float = None,
        repair_drop_ratio: float = 0.5,
    ) -> str:
        """server.py の forecast_curve_fit と同一ロジック（FastMCPなしで動作）"""

        try:
            dates = json.loads(ds)
            values = json.loads(y)
        except json.JSONDecodeError as e:
            return json.dumps({"success": False, "error": f"JSON解析エラー: {e}"}, ensure_ascii=False)

        if len(dates) != len(values):
            return json.dumps({"success": False, "error": "dsとyの長さが不一致"}, ensure_ascii=False)
        if len(dates) < 2:
            return json.dumps({"success": False, "error": "データが2点未満"}, ensure_ascii=False)

        years = [int(d[:4]) for d in dates]

        # 改修年の検出
        repair_indices = []
        for i in range(1, len(values)):
            if values[i - 1] != 0:
                drop = (values[i - 1] - values[i]) / abs(values[i - 1])
                if drop >= repair_drop_ratio:
                    repair_indices.append(i)

        repair_years = [years[i] for i in repair_indices]

        # サイクル分割・正規化
        cycle_starts = [0] + repair_indices
        normalized_x = []
        normalized_y = []
        for idx, start in enumerate(cycle_starts):
            end = cycle_starts[idx + 1] if idx + 1 < len(cycle_starts) else len(values)
            for i in range(start, end):
                normalized_x.append(i - start)
                normalized_y.append(values[i])

        normalized_x = np.array(normalized_x, dtype=float)
        normalized_y = np.array(normalized_y, dtype=float)

        # カーブフィット
        def linear(x, a, b):
            return a * x + b

        def exponential(x, a, b, c):
            return a * np.exp(b * x) + c

        def logarithmic(x, a, b):
            return a * np.log(x + 1) + b

        best_curve = None
        best_sse = float("inf")
        best_params = None
        best_name = ""

        for name, func, p0 in [
            ("linear", linear, [1.0, 0.0]),
            ("exponential", exponential, [0.01, 0.1, 0.0]),
            ("logarithmic", logarithmic, [1.0, 0.0]),
        ]:
            try:
                params, _ = curve_fit(func, normalized_x, normalized_y, p0=p0, maxfev=5000)
                predicted = func(normalized_x, *params)
                sse = np.sum((normalized_y - predicted) ** 2)
                if sse < best_sse:
                    best_sse = sse
                    best_curve = func
                    best_params = params
                    best_name = name
            except Exception:
                continue

        if best_curve is None:
            return json.dumps({"success": False, "error": "フィット失敗"}, ensure_ascii=False)

        curve_name_jp = {
            "linear": "線形（直線）",
            "exponential": "指数（加速劣化）",
            "logarithmic": "対数（減速劣化）",
        }

        last_cycle_start = cycle_starts[-1]
        current_elapsed = len(values) - 1 - last_cycle_start

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

            forecast_results.append({"ds": ds_str, "yhat": round(yhat, 4), "status": status})

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

        return json.dumps({
            "success": True,
            "forecast": forecast_results,
            "meta": {
                "method": f"カーブフィット（{curve_name_jp.get(best_name, best_name)}）",
                "curve_type": best_name,
                "periods": periods,
                "n_history": len(values),
                "n_cycles": len(cycle_starts),
                "repair_years": repair_years,
                "trend": trend,
                "first_exceed_year": first_exceed_year,
            }
        }, ensure_ascii=False)

# --- chart_utils のインポート ---
try:
    import chart_utils
    HAS_CHART_UTILS = True
    print("[setup] chart_utils をインポート成功")
except ImportError as e:
    HAS_CHART_UTILS = False
    print(f"[setup] chart_utils インポート失敗 ({e}) - チャートテストはスキップ")


# =========================================================================
# ヘルパー関数
# =========================================================================

def print_header(title: str):
    """テストのヘッダーを見やすく表示"""
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(result: dict):
    """forecast_curve_fit の結果を整形表示"""
    success = result.get("success", False)
    print(f"  成功: {success}")

    if not success:
        print(f"  エラー: {result.get('error', '不明')}")
        return

    meta = result.get("meta", {})
    print(f"  改修年: {meta.get('repair_years', [])}")
    print(f"  カーブ種類: {meta.get('curve_type', '不明')} ({meta.get('method', '')})")
    print(f"  サイクル数: {meta.get('n_cycles', 0)}")
    print(f"  トレンド: {meta.get('trend', '不明')}")
    print(f"  基準超過年: {meta.get('first_exceed_year', 'なし')}")
    print(f"  予測値:")
    for f in result.get("forecast", []):
        status_mark = " !!!" if f["status"] != "OK" else ""
        print(f"    {f['ds']}: {f['yhat']:.4f} [{f['status']}]{status_mark}")


def make_ds(years: list) -> str:
    """年のリストを ds 用 JSON 文字列に変換"""
    return json.dumps([f"{y}-01-01" for y in years])


def make_y(values: list) -> str:
    """値のリストを y 用 JSON 文字列に変換"""
    return json.dumps(values)


# =========================================================================
# テストデータ定義
# =========================================================================

def pattern_1_repair_exponential():
    """パターン1: 改修1回 + 加速劣化（指数カーブが選ばれるべき）"""
    years = list(range(2015, 2026))
    values = [
        0.05, 0.08, 0.14, 0.25, 0.50,  # 2015-2019: 指数的に増加
        0.06,                            # 2020: 改修（0.50→0.06 = 88%低下）
        0.10, 0.18, 0.33, 0.55, 0.90,   # 2021-2025: 再び指数的に増加
    ]
    return make_ds(years), make_y(values), 1.0


def pattern_2_repair_linear():
    """パターン2: 改修1回 + 直線劣化（線形カーブが選ばれるべき）"""
    # 完全に等間隔で増加するデータを多めに用意し、線形フィットが有利になるようにする
    years = list(range(2010, 2026))
    values = [
        0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45,  # 2010-2017: 毎年+0.05
        0.05,                                               # 2018: 改修（0.45→0.05 = 89%低下）
        0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40,          # 2019-2025: 毎年+0.05
    ]
    return make_ds(years), make_y(values), 0.50


def pattern_3_no_repair():
    """パターン3: 改修なし（単一サイクル）"""
    years = list(range(2018, 2026))
    values = [0.10, 0.14, 0.18, 0.22, 0.26, 0.30, 0.34, 0.38]
    return make_ds(years), make_y(values), 0.50


def pattern_4_two_repairs():
    """パターン4: 改修2回"""
    years = list(range(2012, 2026))
    values = [
        0.05, 0.12, 0.20, 0.30, 0.42,  # 2012-2016: 劣化
        0.07,                            # 2017: 改修1回目（0.42→0.07 = 83%低下）
        0.14, 0.22, 0.32, 0.45,         # 2018-2021: 劣化
        0.06,                            # 2022: 改修2回目（0.45→0.06 = 87%低下）
        0.13, 0.21,                      # 2023-2024: 劣化
        0.30,                            # 2025
    ]
    return make_ds(years), make_y(values), 0.50


def pattern_5_small_drop():
    """パターン5: ギリギリ50%未満の低下（改修と判定されないべき）"""
    years = list(range(2018, 2026))
    # 2021年: 0.40→0.22 = 45%低下 → 閾値50%未満なので改修とみなさない
    values = [0.10, 0.20, 0.30, 0.40, 0.22, 0.30, 0.38, 0.46]
    return make_ds(years), make_y(values), 0.60


# =========================================================================
# メインテスト実行
# =========================================================================

async def run_tests():
    patterns = [
        ("パターン1: 改修1回 + 加速劣化", pattern_1_repair_exponential, "exponential"),
        ("パターン2: 改修1回 + 直線劣化", pattern_2_repair_linear, "linear"),
        ("パターン3: 改修なし", pattern_3_no_repair, None),
        ("パターン4: 改修2回", pattern_4_two_repairs, None),
        ("パターン5: ギリギリ50%未満（改修なし判定）", pattern_5_small_drop, None),
    ]

    all_results = {}

    for title, data_func, expected_curve in patterns:
        print_header(title)
        ds, y, upper_limit = data_func()

        # forecast_curve_fit は JSON 文字列を返す async 関数
        raw = await forecast_curve_fit(
            ds=ds,
            y=y,
            periods=5,
            upper_limit=upper_limit,
        )
        result = json.loads(raw)
        print_result(result)
        all_results[title] = result

        # 期待カーブの検証（指定がある場合）
        if expected_curve and result.get("success"):
            actual_curve = result["meta"]["curve_type"]
            if actual_curve == expected_curve:
                print(f"  [OK] 期待通り {expected_curve} が選択された")
            else:
                print(f"  [WARN] 期待: {expected_curve}, 実際: {actual_curve}")

    # --- パターン別の追加チェック ---

    # パターン3: 改修が0件であること
    r3 = all_results["パターン3: 改修なし"]
    if r3.get("success"):
        repairs = r3["meta"]["repair_years"]
        cycles = r3["meta"]["n_cycles"]
        if len(repairs) == 0 and cycles == 1:
            print(f"\n  [OK] パターン3: 改修0件、サイクル1（正常）")
        else:
            print(f"\n  [WARN] パターン3: 改修={repairs}, サイクル={cycles}")

    # パターン4: 改修が2件であること
    r4 = all_results["パターン4: 改修2回"]
    if r4.get("success"):
        repairs = r4["meta"]["repair_years"]
        cycles = r4["meta"]["n_cycles"]
        if len(repairs) == 2 and cycles == 3:
            print(f"  [OK] パターン4: 改修2件 {repairs}、サイクル3（正常）")
        else:
            print(f"  [WARN] パターン4: 改修={repairs}, サイクル={cycles}")

    # パターン5: 改修が0件であること
    r5 = all_results["パターン5: ギリギリ50%未満（改修なし判定）"]
    if r5.get("success"):
        repairs = r5["meta"]["repair_years"]
        if len(repairs) == 0:
            print(f"  [OK] パターン5: 改修0件（50%未満なので正しく無視）")
        else:
            print(f"  [WARN] パターン5: 改修が誤検出された: {repairs}")

    # =================================================================
    # パターン1 の予測チャートパイプラインテスト
    # =================================================================
    print_header("チャートパイプラインテスト（パターン1のデータで予測グラフ生成）")

    if not HAS_CHART_UTILS:
        print("  chart_utils が利用できないためスキップ")
        print("  （plotly, pandas がインストールされた環境で実行してください）")
    else:
        r1 = all_results["パターン1: 改修1回 + 加速劣化"]
        if not r1.get("success"):
            print("  パターン1が失敗しているためスキップ")
        else:
            _run_chart_pipeline_test(r1)

    print()
    print("=" * 70)
    print("  全テスト完了")
    print("=" * 70)


def _run_chart_pipeline_test(r1: dict):
    """パターン1の結果を使って予測チャートパイプラインをテスト"""
    # --- 実データ用のドキュメントを作成 ---
    # create_prediction_chart が期待する形式:
    #   results = [{"matched_records": [{"data": {...}}]}, ...]
    # data に必要なフィールド: 機器, 計測箇所, 点検年月日, 測定値, 基準値
    key_name = "劣化量・テスト①"
    ds_list = json.loads(pattern_1_repair_exponential()[0])
    y_list = json.loads(pattern_1_repair_exponential()[1])

    actual_results = []
    for date_str, value in zip(ds_list, y_list):
        actual_results.append({
            "matched_records": [{
                "data": {
                    "機器": "テスト機器A",
                    "機器部品": "テスト部品",
                    "計測箇所": "テスト計測箇所",
                    "点検年月日": date_str,
                    "測定値": {key_name: value},
                    "基準値": {key_name: 1.0},
                }
            }]
        })

    # --- カーブフィット予測データを predictions 形式に変換 ---
    curvefit_predictions = []
    for f in r1["forecast"]:
        year = int(f["ds"][:4])
        curvefit_predictions.append({
            "year": year,
            "values": {key_name: f["yhat"]},
        })

    curvefit_info = {
        "method": r1["meta"]["method"],
        "threshold_crossing": {},
        "repair_years": r1["meta"]["repair_years"],
    }
    if r1["meta"].get("first_exceed_year"):
        curvefit_info["threshold_crossing"][key_name] = int(r1["meta"]["first_exceed_year"])

    # --- AI予測データ（ダミー: 単純線形で作る） ---
    last_y = y_list[-1]
    ai_predictions = []
    for i in range(1, 6):
        ai_predictions.append({
            "year": 2025 + i,
            "values": {key_name: round(last_y + 0.05 * i, 4)},
        })
    ai_info = {"method": "線形近似（ダミー）"}

    # --- チャート出力ディレクトリの準備 ---
    # chart_utils.figure_to_file は /data/charts に書き込む。
    # Docker 外では /data/charts が存在しない可能性があるので作成を試みる。
    try:
        os.makedirs("/data/charts", exist_ok=True)
    except PermissionError:
        print("  /data/charts に書き込めないためチャート保存をスキップ")
        return

    # --- チャート生成 ---
    try:
        chart_result = chart_utils.create_prediction_chart(
            results=actual_results,
            predictions=ai_predictions,
            prediction_info=ai_info,
            curvefit_predictions=curvefit_predictions,
            curvefit_prediction_info=curvefit_info,
        )
        print(f"  チャート生成成功: {chart_result.get('success')}")
        if chart_result.get("success"):
            for c in chart_result.get("charts", []):
                print(f"    タイトル: {c.get('chart_title')}")
                print(f"    パス: {c.get('chart_path')}")
                print(f"    実データ点数: {c.get('actual_data_points')}")
                print(f"    AI予測点数: {c.get('predicted_data_points')}")
                print(f"    カーブフィット予測点数: {c.get('curvefit_predicted_data_points')}")
            print(f"  予測手法: {chart_result.get('prediction_method')}")
            print(f"  [OK] チャートパイプライン正常完了")
        else:
            print(f"  [FAIL] チャート生成失敗: {chart_result.get('error')}")
    except Exception as e:
        print(f"  [FAIL] チャート生成中にエラー: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(run_tests())
