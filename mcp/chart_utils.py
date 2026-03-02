# =============================================================================
# mcp/chart_utils.py - グラフ生成ユーティリティ
# =============================================================================
#
# 【グラフ仕様】
# - グループ化単位: 計測箇所（インペラ外周部など）
# - 同じ計測箇所のデータは同じグラフに複数年分をプロット
# - 異なる計測箇所は別のグラフ
# - X軸: 年度
# - Y軸: 測定値
# - 凡例: 測定値キー
# - 基準値: 赤い水平線
# - ホバー: カーソルを合わせるとx,y値と凡例が表示される
#
# 【フロント非依存設計】
# - Plotlyで生成したグラフをHTMLファイルとして保存
# - どのフロント（Streamlit, React, Vue, 素のHTML）でも表示可能
# - MCP側はStreamlit等のフロントを一切知らない
#
# =============================================================================

import os
import re
import uuid

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

# 日本語フォント設定
# Docker環境ではIPAGothicがインストール済み（Dockerfileでfonts-ipafont-gothicを指定）
# Plotlyはフォントが見つからない場合、自動でデフォルトフォントにフォールバックする
JAPANESE_FONT = "IPAGothic"


def figure_to_file(fig, filename: str = "chart.html") -> str:
    """
    PlotlyのfigureをHTMLファイルに保存

    【なぜHTMLか】
    - ブラウザで直接開ける → フロントエンド非依存
    - ホバー、ズーム、パン等のインタラクティブ機能がそのまま使える
    - PNGだとカーソルを合わせて値を見る機能が使えない

    【include_plotlyjs="cdn"について】
    HTMLにplotly.jsを埋め込まず、CDN（インターネット上のライブラリ）を参照する。
    ファイルサイズが約3MBから数KBに削減される。
    オフライン環境の場合は include_plotlyjs=True にする（ファイルが大きくなる）。
    """
    charts_dir = "/data/charts"
    os.makedirs(charts_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    filepath = os.path.join(charts_dir, unique_name)

    fig.write_html(
        filepath,
        include_plotlyjs="cdn",
        full_html=True,
        config={
            "displayModeBar": True,
            "displaylogo": False,
        },
    )

    return filepath


def extract_year_from_date(date_str: str) -> int | None:
    """日付文字列から年を抽出"""
    if not date_str:
        return None
    try:
        if "-" in date_str:
            return int(date_str.split("-")[0])
        elif "/" in date_str:
            return int(date_str.split("/")[0])
        else:
            return int(date_str[:4])
    except (ValueError, IndexError):
        return None


def extract_reference_values(references: dict) -> dict[str, float]:
    """基準値をすべて抽出（キー名→数値の辞書）"""
    result = {}
    for ref_key, ref_val in references.items():
        if isinstance(ref_val, (int, float)):
            result[ref_key] = float(ref_val)
        elif isinstance(ref_val, str):
            numbers = re.findall(r'[\d.]+', ref_val)
            if numbers:
                result[ref_key] = float(numbers[0])
    return result


def group_by_measurement_location(results: list) -> dict:
    """
    計測箇所ごとにグループ化
    同じ計測箇所のデータは1つのグラフにまとめる（複数年対応）

    Returns:
        {
            "インペラ外周部": {
                "data_points": [...],
                "equipment": "機器名",
                "equipment_part": "機器部品名",
                "reference_values": {"摩耗量": 0.5, "振動値": 1.0}
            },
            ...
        }
    """
    location_groups = {}

    for file_result in results:
        matched_records = file_result.get("matched_records", [])
        for record in matched_records:
            data = record.get("data", {})

            year = extract_year_from_date(data.get("点検年月日", ""))
            if year is None:
                continue

            location = data.get("計測箇所") or "不明"
            measurements = data.get("測定値", {})
            references = data.get("基準値", {})
            equipment = data.get("機器", "")
            equipment_part = data.get("機器部品", "")

            ref_values = extract_reference_values(references)

            # 計測箇所でグループ化
            if location not in location_groups:
                location_groups[location] = {
                    "data_points": [],
                    "equipment": equipment,
                    "equipment_part": equipment_part,
                    "reference_values": {}
                }

            # 基準値をマージ（同じキーは上書きしない）
            for k, v in ref_values.items():
                if k not in location_groups[location]["reference_values"]:
                    location_groups[location]["reference_values"][k] = v

            # 各測定値を追加（年度情報付き）
            for key, value in measurements.items():
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        continue
                if not isinstance(value, (int, float)):
                    continue

                location_groups[location]["data_points"].append({
                    "year": year,
                    "key": key,
                    "value": value
                })

    return location_groups


def create_chart_for_location(
    location: str,
    data_points: list,
    equipment: str = "",
    equipment_part: str = "",
    reference_values: dict = None,
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
) -> dict:
    """
    1つの計測箇所用のグラフを生成（複数年対応）

    【動的パラメータ】
    chart_type, color, year_from/to, min/max_value, show_reference で
    グラフの種類・色・データ範囲をチャットから動的に指定できる。
    すべてオプションで、指定しなければデフォルト（自動判定, 自動色分け, 全データ）。
    """
    if not data_points:
        return {
            "success": False,
            "error": f"'{location}'のデータが見つかりません",
            "chart_path": ""
        }

    df = pd.DataFrame(data_points)

    # 年度フィルタリング（intの状態でフィルタしてから文字列変換）
    if year_from is not None:
        df = df[df["year"] >= year_from]
    if year_to is not None:
        df = df[df["year"] <= year_to]

    # 値フィルタリング
    if min_value is not None:
        df = df[df["value"] >= min_value]
    if max_value is not None:
        df = df[df["value"] <= max_value]

    # キー名フィルタ（部分一致、カンマ区切りで複数キーワード対応）
    if key_filter is not None:
        keywords = [k.strip() for k in key_filter.split(",")]
        df = df[df["key"].apply(lambda k: any(kw in k for kw in keywords))]

    # 基準値超過フィルタ（基準値を超えているデータだけ残す）
    if above_reference and reference_values:
        ref_min = min(reference_values.values())
        df = df[df["value"] > ref_min]

    if df.empty:
        return {
            "success": False,
            "error": f"'{location}'のフィルタ条件に合うデータがありません",
            "chart_path": ""
        }

    # 年を文字列に変換（カテゴリ＝離散値として扱うため）
    df["year"] = df["year"].astype(str)
    df = df.sort_values("year")

    # X軸の決定: 指定があればそれを優先、なければ自動判定
    unique_years = df["year"].nunique()
    if x_axis is not None:
        x_col = x_axis
        x_label = "年度" if x_axis == "year" else "点検項目"
    elif unique_years <= 1:
        x_col = "key"
        x_label = "点検項目"
    else:
        x_col = "year"
        x_label = "年度"

    # 凡例は点検項目（key）で色分け
    # デフォルトはグレー統一、基準値超過のみ赤枠で目立たせる
    if reference_values is None:
        reference_values = {}
    ref_min = min(reference_values.values()) if reference_values else None

    # 凡例用にkeyで色分け
    # x_colが"key"の場合はcolorにkeyを使うとPlotlyが混乱するためスキップ
    use_color_key = color is None and x_col != "key"

    # グレー系グラデーション（キーごとに異なるグレー色を割り当て）
    # これにより strip plot のグループ分けが視覚的に横にばらける
    gray_palette = [
        "#808080", "#999999", "#6b6b6b", "#b0b0b0",
        "#707070", "#a0a0a0", "#585858", "#c0c0c0",
    ]

    chart_common = dict(
        data_frame=df,
        x=x_col,
        y="value",
        hover_data={"key": True, "year": True, "value": ":.4f"},
        labels={"year": "年度", "value": "測定値", "key": "点検項目"},
    )
    if use_color_key:
        chart_common["color"] = "key"
        keys = df["key"].unique().tolist()
        chart_common["color_discrete_map"] = {
            k: gray_palette[i % len(gray_palette)] for i, k in enumerate(keys)
        }
    # chart_type が未指定（None）の場合、データに応じて自動判定
    # 【なぜこの判定か】
    # - 線グラフは「同じキーを年度でつなぐ」ので、複数年データが必要
    # - 1年分しかないデータで線グラフにすると、点が1つだけで意味がない
    # - 複数年 かつ 測定値キーが5個以下: 線グラフで経年変化を追いやすい
    # - それ以外: 散布図（strip）で全体の分布を見る
    if chart_type is None:
        unique_keys = df["key"].nunique()
        if unique_keys <= 5 and unique_years >= 2:
            chart_type = "line"
        else:
            chart_type = "strip"

    chart_builders = {
        "strip": lambda: px.strip(**chart_common),
        "scatter": lambda: px.scatter(**chart_common),
        "bar": lambda: px.bar(**chart_common, barmode="group"),
        "line": lambda: px.line(**chart_common, markers=True),
    }

    builder = chart_builders.get(chart_type, chart_builders["strip"])
    fig = builder()

    # 単一色指定がある場合、全トレースの色を上書き
    if color is not None:
        fig.update_traces(marker=dict(color=color))

    # 点のスタイル調整（scatter/stripのみ有効）
    if chart_type in ("strip", "scatter"):
        fig.update_traces(marker=dict(size=8, opacity=0.7))

    # 基準値超過のデータ点に赤枠をつけて目立たせる
    if ref_min is not None:
        for trace in fig.data:
            if not hasattr(trace, "marker") or trace.y is None:
                continue
            try:
                border_colors = [
                    "red" if float(v) > ref_min else "rgba(0,0,0,0)"
                    for v in trace.y
                ]
                trace.marker.line = dict(color=border_colors, width=2)
            except (TypeError, ValueError):
                pass

    # 基準値の水平線（キーごとに1本ずつ赤い破線）
    # go.Scatterで追加することで凡例に表示され、クリックで表示/非表示を切り替えられる
    if show_reference and reference_values:
        x_values = df[x_col].unique().tolist()
        for ref_key, ref_val in reference_values.items():
            fig.add_trace(go.Scatter(
                x=[x_values[0], x_values[-1]],
                y=[ref_val, ref_val],
                mode="lines",
                line=dict(dash="dash", color="red", width=2),
                name=f"基準値({ref_key}): {ref_val}",
            ))

    # タイトル・ラベル・レイアウト設定
    title_parts = [p for p in [equipment, equipment_part, location] if p]
    chart_title = " / ".join(title_parts)

    # X軸が点検項目（key）の場合はラベルが長いので斜め表示
    # 年度（year）の場合は短いので斜めにしない
    xaxis_config = dict(showgrid=False)
    if x_col == "key":
        xaxis_config["tickangle"] = -45

    fig.update_layout(
        title=dict(text=chart_title, font=dict(size=16, family=JAPANESE_FONT)),
        xaxis_title=dict(text=x_label, font=dict(size=13, family=JAPANESE_FONT)),
        yaxis_title=dict(text="測定値", font=dict(size=13, family=JAPANESE_FONT)),
        font=dict(family=JAPANESE_FONT),
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(gridcolor="rgba(0,0,0,0.1)", gridwidth=1),
        xaxis=xaxis_config,
        showlegend=True,
        legend=dict(
            font=dict(size=10, family=JAPANESE_FONT),
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=1.02,
        ),
        width=900,
        height=500,
        margin=dict(l=60, r=160, t=60, b=100 if x_col == "key" else 50),
    )

    # HTMLファイルに保存
    safe_location = re.sub(r'[^\w\-]', '_', location)
    chart_path = figure_to_file(fig, f"{safe_location}.html")

    return {
        "success": True,
        "chart_path": chart_path,
        "chart_title": chart_title,
        "location": location,
        "data_points": len(df),
        "reference_values": reference_values,
    }


# =============================================================================
# 予測グラフ生成
# =============================================================================
#
# 【予測グラフの仕様】
# - 実データ: グレー系の実線 + 丸マーカー（既存グラフと同じ）
# - 予測データ: オレンジ系の破線 + ダイヤマーカー
# - 実データの最終点と予測の最初の点は線でつながる
# - 基準値: 赤い水平破線（既存グラフと同じ）
# - 基準値超過予測年: 赤い垂直点線 + アノテーション（注釈）
# - タイトル末尾に「【予測】」を付与
#
# 【なぜ create_chart_for_location() を再利用しないか】
# 既存関数は px.line() 等で自動描画するが、予測グラフは
# 実線と破線を明確に分ける必要がある。go.Scatter() で手動描画する方が
# スタイルの制御が容易で、実データと予測データの区別が明確になる。
#
# =============================================================================

# オレンジ系グラデーション（予測データ用）
# グレー（実績）とオレンジ（予測）は色覚的にも区別しやすい
PREDICTION_PALETTE = [
    "#FF8C00", "#FFA500", "#FF7F50", "#FF6347",
    "#E67E22", "#D2691E", "#CD853F", "#DEB887",
]


def create_prediction_chart(
    results: list,
    predictions: list,
    prediction_info: dict = None,
) -> dict:
    """
    実データ + 予測データ を1つのグラフに可視化する（メイン関数）

    【処理の流れ】
    1. group_by_measurement_location() で実データを計測箇所ごとにグループ化
    2. predictions の測定値キー名で、どの計測箇所に属するか振り分ける
    3. 計測箇所ごとに、実線（実データ）+ 破線（予測）+ 基準値線 のグラフを生成

    Args:
        results: MongoDB findの結果を変換したリスト（visualize_data と同じ形式）
        predictions: AI予測データのリスト
            [{"year": 2025, "values": {"摩耗量・タイヤ①・上": 0.32, ...}}, ...]
        prediction_info: 予測メタ情報（オプション）
            {"method": "線形近似", "threshold_crossing": {"キー名": 2027}, "note": "..."}

    Returns:
        {"success": bool, "charts": [...], "total_locations": int, "prediction_method": str}
    """
    if prediction_info is None:
        prediction_info = {}

    # 1. 実データを計測箇所ごとにグループ化（既存関数を再利用）
    location_groups = group_by_measurement_location(results)

    if not location_groups:
        return {
            "success": False,
            "error": "実データが見つかりません",
            "charts": []
        }

    # 2. 予測データを計測箇所ごとに振り分ける
    #    各 location_group の data_points に含まれるキー名と、
    #    predictions の values のキー名をマッチングする
    prediction_by_location = _assign_predictions_to_locations(
        location_groups, predictions
    )

    # 3. 計測箇所ごとにグラフを生成
    threshold_crossing = prediction_info.get("threshold_crossing", {})
    charts = []

    for location, group in location_groups.items():
        pred_points = prediction_by_location.get(location, [])

        result = _create_single_prediction_chart(
            location=location,
            data_points=group["data_points"],
            pred_points=pred_points,
            equipment=group["equipment"],
            equipment_part=group["equipment_part"],
            reference_values=group["reference_values"],
            threshold_crossing=threshold_crossing,
        )
        if result["success"]:
            charts.append(result)

    if not charts:
        return {
            "success": False,
            "error": "予測グラフを生成できませんでした",
            "charts": []
        }

    return {
        "success": True,
        "charts": charts,
        "total_locations": len(charts),
        "prediction_method": prediction_info.get("method", ""),
    }


def _assign_predictions_to_locations(
    location_groups: dict,
    predictions: list,
) -> dict:
    """
    予測データを計測箇所ごとに振り分ける

    【マッチング方法】
    各計測箇所の実データに含まれる測定値キー名を集め、
    予測データの values に同じキー名があれば、その計測箇所に紐づける。

    Returns:
        {"タイヤ外周部": [{"year": 2025, "key": "摩耗量・タイヤ①・上", "value": 0.32}, ...]}
    """
    # 各計測箇所が持つ測定値キー名のセットを作る
    location_keys = {}
    for location, group in location_groups.items():
        keys = set()
        for dp in group["data_points"]:
            keys.add(dp["key"])
        location_keys[location] = keys

    # 予測データをフラットなポイント列に変換して計測箇所に振り分け
    result = {loc: [] for loc in location_groups}

    for pred in predictions:
        year = pred.get("year")
        values = pred.get("values", {})
        if year is None:
            continue

        for key, value in values.items():
            if not isinstance(value, (int, float)):
                continue
            # このキーがどの計測箇所に属するか探す
            for location, keys in location_keys.items():
                if key in keys:
                    result[location].append({
                        "year": year,
                        "key": key,
                        "value": value,
                    })
                    break

    return result


def _create_single_prediction_chart(
    location: str,
    data_points: list,
    pred_points: list,
    equipment: str = "",
    equipment_part: str = "",
    reference_values: dict = None,
    threshold_crossing: dict = None,
) -> dict:
    """
    1つの計測箇所の予測グラフを生成

    【なぜ go.Scatter を直接使うか】
    px.line() だと全データが同じスタイルで描画される。
    実データ（実線）と予測データ（破線）でスタイルを分けるには、
    go.Scatter() で個別にトレースを追加する必要がある。

    【線のつなげ方】
    実データの最終年の値を予測データの先頭にもコピーすることで、
    実線と破線が途切れずにつながるようにする。
    """
    if not data_points:
        return {
            "success": False,
            "error": f"'{location}'のデータが見つかりません",
            "chart_path": ""
        }

    if reference_values is None:
        reference_values = {}
    if threshold_crossing is None:
        threshold_crossing = {}

    fig = go.Figure()

    # --- 実データを測定値キーごとにグループ化して描画 ---
    actual_df = pd.DataFrame(data_points)
    actual_df = actual_df.sort_values("year")
    actual_keys = actual_df["key"].unique().tolist()

    # --- 予測期間の上限チェック ---
    # 予測が長すぎるとX軸が引き延ばされて実データが潰れてしまう。
    # 実データの年数を超えない範囲に予測データをカットする。
    if pred_points:
        actual_years = actual_df["year"].unique()
        actual_span = int(actual_years.max()) - int(actual_years.min()) + 1
        max_pred_year = int(actual_years.max()) + max(actual_span, 3)
        pred_points = [p for p in pred_points if p["year"] <= max_pred_year]

    # グレー系パレット（既存グラフと同じ）
    gray_palette = [
        "#808080", "#999999", "#6b6b6b", "#b0b0b0",
        "#707070", "#a0a0a0", "#585858", "#c0c0c0",
    ]

    for i, key in enumerate(actual_keys):
        key_data = actual_df[actual_df["key"] == key].sort_values("year")
        color = gray_palette[i % len(gray_palette)]

        fig.add_trace(go.Scatter(
            x=key_data["year"].astype(str).tolist(),
            y=key_data["value"].tolist(),
            mode="lines+markers",
            name=key,
            line=dict(color=color, width=2),
            marker=dict(size=7, color=color),
            hovertemplate=f"{key}<br>年度: %{{x}}<br>測定値: %{{y:.4f}}<extra></extra>",
        ))

    # --- 予測データを測定値キーごとにグループ化して描画 ---
    if pred_points:
        pred_df = pd.DataFrame(pred_points)
        pred_df = pred_df.sort_values("year")
        pred_keys = pred_df["key"].unique().tolist()

        for i, key in enumerate(pred_keys):
            key_pred = pred_df[pred_df["key"] == key].sort_values("year")
            color = PREDICTION_PALETTE[i % len(PREDICTION_PALETTE)]

            # 実データの最終点を予測線の先頭に追加（線をつなげるため）
            #
            # 【この処理がないとどうなるか】
            # 実線の最後と破線の最初の間に隙間ができてしまい、
            # グラフが途切れて見える。接続点を追加して自然につなげる。
            x_vals = key_pred["year"].astype(str).tolist()
            y_vals = key_pred["value"].tolist()

            actual_key_data = actual_df[actual_df["key"] == key].sort_values("year")
            if not actual_key_data.empty:
                last_year = str(int(actual_key_data["year"].iloc[-1]))
                last_value = actual_key_data["value"].iloc[-1]
                x_vals = [last_year] + x_vals
                y_vals = [last_value] + y_vals

            fig.add_trace(go.Scatter(
                x=x_vals,
                y=y_vals,
                mode="lines+markers",
                name=f"予測: {key}",
                line=dict(color=color, width=2, dash="dash"),
                marker=dict(size=7, symbol="diamond", color=color),
                hovertemplate=f"予測: {key}<br>年度: %{{x}}<br>予測値: %{{y:.4f}}<extra></extra>",
            ))

    # --- 基準値の水平線（既存グラフと同じ赤い破線） ---
    if reference_values:
        # X軸の全範囲（実データ + 予測データ）を計算
        all_years = sorted(set(
            actual_df["year"].astype(str).tolist()
            + ([str(p["year"]) for p in pred_points] if pred_points else [])
        ))
        for ref_key, ref_val in reference_values.items():
            fig.add_trace(go.Scatter(
                x=[all_years[0], all_years[-1]],
                y=[ref_val, ref_val],
                mode="lines",
                line=dict(dash="dash", color="red", width=2),
                name=f"基準値({ref_key}): {ref_val}",
            ))

    # --- 基準値超過予測年の表示 ---
    #
    # 【なぜ垂直線を使わないか】
    # add_shape() で垂直線を描くと、X軸にその年度がカテゴリとして追加され、
    # グラフ全体が引き延ばされて実データが潰れてしまう。
    # そのため、常にグラフ右上のテキスト注釈として表示する。
    if threshold_crossing:
        # 複数キーの超過予測年をまとめて表示（重複年はまとめる）
        unique_years = sorted(set(threshold_crossing.values()))
        crossing_text = "  ".join(f"超過予測: {y}年" for y in unique_years)
        fig.add_annotation(
            xref="paper", yref="paper",
            x=0.98, y=0.98,
            text=crossing_text,
            showarrow=False,
            font=dict(size=12, color="red", family=JAPANESE_FONT),
            xanchor="right", yanchor="top",
            bgcolor="rgba(255,255,255,0.8)",
            bordercolor="red",
            borderwidth=1,
            borderpad=4,
        )

    # --- レイアウト設定（既存グラフとほぼ同じ） ---
    title_parts = [p for p in [equipment, equipment_part, location] if p]
    chart_title = " / ".join(title_parts) + " 【予測】"

    fig.update_layout(
        title=dict(text=chart_title, font=dict(size=16, family=JAPANESE_FONT)),
        xaxis_title=dict(text="年度", font=dict(size=13, family=JAPANESE_FONT)),
        yaxis_title=dict(text="測定値", font=dict(size=13, family=JAPANESE_FONT)),
        font=dict(family=JAPANESE_FONT),
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(gridcolor="rgba(0,0,0,0.1)", gridwidth=1),
        xaxis=dict(showgrid=False),
        showlegend=True,
        legend=dict(
            font=dict(size=10, family=JAPANESE_FONT),
            yanchor="top",
            y=0.99,
            xanchor="left",
            x=1.02,
        ),
        width=900,
        height=500,
        margin=dict(l=60, r=160, t=60, b=50),
    )

    # HTMLファイルに保存
    safe_location = re.sub(r'[^\w\-]', '_', location)
    chart_path = figure_to_file(fig, f"{safe_location}_prediction.html")

    actual_count = len(actual_df)
    pred_count = len(pred_points) if pred_points else 0

    return {
        "success": True,
        "chart_path": chart_path,
        "chart_title": chart_title,
        "location": location,
        "actual_data_points": actual_count,
        "predicted_data_points": pred_count,
        "threshold_crossing": threshold_crossing,
        "reference_values": reference_values,
    }


def create_charts_by_location(results: list, **options) -> dict:
    """
    計測箇所ごとにグラフを生成（メイン関数）
    - 同じ計測箇所 → 同じグラフ（複数年をX軸にプロット）
    - 異なる計測箇所 → 別々のグラフ
    - **options: chart_type, color, year_from/to, min/max_value, show_reference
    """
    location_groups = group_by_measurement_location(results)

    if not location_groups:
        return {
            "success": False,
            "error": "データが見つかりません",
            "charts": []
        }

    charts = []
    for location, group in location_groups.items():
        result = create_chart_for_location(
            location=location,
            data_points=group["data_points"],
            equipment=group["equipment"],
            equipment_part=group["equipment_part"],
            reference_values=group["reference_values"],
            **options,
        )
        if result["success"]:
            charts.append(result)

    if not charts:
        return {
            "success": False,
            "error": "グラフを生成できませんでした",
            "charts": []
        }

    return {
        "success": True,
        "charts": charts,
        "total_locations": len(charts)
    }
