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
#
# =============================================================================

import os
import re
import uuid

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import seaborn as sns


def setup_japanese_font():
    """日本語フォントを設定"""
    plt.rcParams['font.family'] = ['IPAGothic', 'DejaVu Sans']


def figure_to_file(fig, filename: str = "chart.png") -> str:
    """matplotlibのfigureをファイルに保存"""
    charts_dir = "/data/charts"
    os.makedirs(charts_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    filepath = os.path.join(charts_dir, unique_name)

    fig.savefig(filepath, format='png', dpi=200, bbox_inches='tight', facecolor='white')
    plt.close(fig)

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

    【なぜstripplotか】
    - 散布図と同じく個々のデータ点を表示できる
    - X軸がカテゴリ（年度）の場合に自動でジッターしてくれる
    - hueで測定値キーごとに色分け＋凡例が自動生成される
    - matplotlibのscatterで手動実装していた処理が1行で済む
    """
    setup_japanese_font()

    if not data_points:
        return {
            "success": False,
            "error": f"'{location}'のデータが見つかりません",
            "chart_path": ""
        }

    # data_pointsをDataFrameに変換
    # 【なぜDataFrameか】
    # seabornはpandasのDataFrame形式でデータを受け取る設計
    # data_points = [{"year": 2024, "key": "摩耗量", "value": 0.18}, ...] の形式
    df = pd.DataFrame(data_points)

    # 年を文字列に変換（seabornがカテゴリ＝離散値として扱うため）
    # 【注意】intのままだと連続値として扱われ、存在しない年も軸に表示されてしまう
    df["year"] = df["year"].astype(str)
    df = df.sort_values("year")

    # グラフ作成
    fig, ax = plt.subplots(figsize=(8, 4))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    # seabornのstripplotで描画
    # 【コード解説】
    #   x="year"    : X軸に年度を配置（カテゴリ軸）
    #   y="value"   : Y軸に測定値を配置
    #   hue="key"   : 測定値キーごとに色を自動で変える（凡例も自動生成）
    #   jitter=0.25 : 同じ年の点を左右にランダムにずらして重なりを防ぐ
    #   size=2.5    : 点の大きさ
    #   alpha=0.6   : 透明度（重なった点が見えるように）
    sns.stripplot(
        data=df,
        x="year",
        y="value",
        hue="key",
        jitter=0.25,
        size=5,
        alpha=0.6,
        ax=ax,
    )

    # 基準値の水平線（赤い破線）
    if reference_value is not None:
        ax.axhline(
            y=reference_value,
            color='red',
            linestyle='--',
            linewidth=2,
            label=f'基準値: {reference_value}',
        )

    # タイトル・ラベル設定
    title_parts = [p for p in [equipment, equipment_part, location] if p]
    chart_title = " / ".join(title_parts)

    ax.set_title(chart_title, fontsize=14)
    ax.set_xlabel("年度", fontsize=12)
    ax.set_ylabel("測定値", fontsize=12)

    # Y軸のみグリッド表示（データ点の読み取りやすさのため）
    ax.grid(True, axis="y", alpha=0.3)

    # 凡例を右外に配置（データ点と重ならないように）
    ax.legend(
        title="凡例",
        bbox_to_anchor=(1.02, 1),
        loc="upper left",
        fontsize=8,
    )

    plt.tight_layout()

    # ファイルに保存
    safe_location = re.sub(r'[^\w\-]', '_', location)
    chart_path = figure_to_file(fig, f"{safe_location}.png")

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
