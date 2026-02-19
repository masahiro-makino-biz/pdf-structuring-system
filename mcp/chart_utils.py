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


def extract_reference_value(references: dict) -> float | None:
    """基準値を抽出"""
    for ref_key, ref_val in references.items():
        if isinstance(ref_val, (int, float)):
            return float(ref_val)
        elif isinstance(ref_val, str):
            numbers = re.findall(r'[\d.]+', ref_val)
            if numbers:
                return float(numbers[0])
    return None


def group_by_measurement_location(results: list) -> dict:
    """
    計測箇所ごとにグループ化
    同じ計測箇所のデータは1つのグラフにまとめる（複数年対応）

    Returns:
        {
            "インペラ外周部": {
                "data_points": [
                    {"year": 2024, "key": "摩耗量・A（上部）・測定箇所①", "value": 0.18},
                    {"year": 2025, "key": "振動値・A・測定箇所①", "value": 0.32},
                    ...
                ],
                "equipment": "機器名",
                "equipment_part": "機器部品名",
                "reference_value": 0.5
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

            reference_value = extract_reference_value(references)

            # 計測箇所でグループ化
            if location not in location_groups:
                location_groups[location] = {
                    "data_points": [],
                    "equipment": equipment,
                    "equipment_part": equipment_part,
                    "reference_value": reference_value
                }

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

            # 基準値を更新（未設定の場合）
            if location_groups[location]["reference_value"] is None and reference_value is not None:
                location_groups[location]["reference_value"] = reference_value

    return location_groups


def create_chart_for_location(
    location: str,
    data_points: list,
    equipment: str = "",
    equipment_part: str = "",
    reference_value: float = None
) -> dict:
    """
    1つの計測箇所用のストリッププロットを生成（複数年対応）

    【なぜplotly.express.stripか】
    - seaborn.stripplotと同じ「ストリッププロット」をPlotlyで描画できる
    - カーソルを合わせるとx値、y値、凡例カテゴリが自動表示される（ホバー機能）
    - color パラメータで測定値キーごとに色分け（seabornのhue相当）
    - HTMLファイルとして保存するため、フロントエンド非依存

    【seabornとの対応関係】
    - sns.stripplot(hue="key") → px.strip(color="key")
    - ax.axhline() → fig.add_hline()
    - fig.savefig(.png) → fig.write_html(.html)
    """
    if not data_points:
        return {
            "success": False,
            "error": f"'{location}'のデータが見つかりません",
            "chart_path": ""
        }

    # data_pointsをDataFrameに変換
    # plotly.expressもpandasのDataFrame形式でデータを受け取る設計（seabornと同じ）
    df = pd.DataFrame(data_points)

    # 年を文字列に変換（カテゴリ＝離散値として扱うため）
    # intのままだと連続値として扱われ、存在しない年も軸に表示されてしまう
    df["year"] = df["year"].astype(str)
    df = df.sort_values("year")

    # Plotly Expressでストリッププロットを生成
    #   x="year"     : X軸に年度（カテゴリ軸）
    #   y="value"    : Y軸に測定値
    #   color="key"  : 測定値キーごとに色分け + 凡例自動生成
    #   stripmode="overlay" : 同じカテゴリの点を重ねて表示
    #   hover_data   : ホバー時に表示する情報のカスタマイズ
    fig = px.strip(
        df,
        x="year",
        y="value",
        color="key",
        stripmode="overlay",
        hover_data={"key": True, "year": True, "value": ":.4f"},
        labels={"year": "年度", "value": "測定値", "key": "凡例"},
    )

    # 点のスタイル調整
    fig.update_traces(
        marker=dict(size=8, opacity=0.7),
    )

    # 基準値の水平線（赤い破線）
    if reference_value is not None:
        fig.add_hline(
            y=reference_value,
            line_dash="dash",
            line_color="red",
            line_width=2,
            annotation_text=f"基準値: {reference_value}",
            annotation_position="top left",
            annotation_font_color="red",
        )

    # タイトル・ラベル・レイアウト設定
    title_parts = [p for p in [equipment, equipment_part, location] if p]
    chart_title = " / ".join(title_parts)

    fig.update_layout(
        title=dict(text=chart_title, font=dict(size=16, family=JAPANESE_FONT)),
        xaxis_title=dict(text="年度", font=dict(size=13, family=JAPANESE_FONT)),
        yaxis_title=dict(text="測定値", font=dict(size=13, family=JAPANESE_FONT)),
        font=dict(family=JAPANESE_FONT),
        plot_bgcolor="white",
        paper_bgcolor="white",
        yaxis=dict(gridcolor="rgba(0,0,0,0.1)", gridwidth=1),
        xaxis=dict(showgrid=False),
        showlegend=False,
        width=900,
        height=500,
        margin=dict(l=60, r=60, t=60, b=50),
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
        "reference_value": reference_value,
    }


def create_charts_by_location(results: list) -> dict:
    """
    計測箇所ごとにグラフを生成（メイン関数）
    - 同じ計測箇所 → 同じグラフ（複数年をX軸にプロット）
    - 異なる計測箇所 → 別々のグラフ
    """
    # 計測箇所ごとにグループ化
    location_groups = group_by_measurement_location(results)

    if not location_groups:
        return {
            "success": False,
            "error": "データが見つかりません",
            "charts": []
        }

    # 各計測箇所のグラフを生成
    charts = []
    for location, group in location_groups.items():
        result = create_chart_for_location(
            location=location,
            data_points=group["data_points"],
            equipment=group["equipment"],
            equipment_part=group["equipment_part"],
            reference_value=group["reference_value"]
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
