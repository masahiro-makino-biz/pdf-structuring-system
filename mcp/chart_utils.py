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
    chart_type: str = "strip",
    color: str = None,
    year_from: int = None,
    year_to: int = None,
    min_value: float = None,
    max_value: float = None,
    show_reference: bool = True,
    x_axis: str = None,
) -> dict:
    """
    1つの計測箇所用のグラフを生成（複数年対応）

    【動的パラメータ】
    chart_type, color, year_from/to, min/max_value, show_reference で
    グラフの種類・色・データ範囲をチャットから動的に指定できる。
    すべてオプションで、指定しなければデフォルト（strip, 自動色分け, 全データ）。
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
    elif color is not None:
        pass  # 単一色指定: 後で上書き
    else:
        pass  # x_col=="key"の場合: colorなし

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
