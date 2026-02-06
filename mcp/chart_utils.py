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

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def setup_japanese_font():
    """日本語フォントを設定"""
    plt.rcParams['font.family'] = ['IPAGothic', 'DejaVu Sans']


def figure_to_file(fig, filename: str = "chart.png") -> str:
    """matplotlibのfigureをファイルに保存"""
    charts_dir = "/data/charts"
    os.makedirs(charts_dir, exist_ok=True)

    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    filepath = os.path.join(charts_dir, unique_name)

    fig.savefig(filepath, format='png', dpi=100, bbox_inches='tight', facecolor='white')
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
    1つの計測箇所用のグラフを生成（複数年対応）
    """
    setup_japanese_font()

    if not data_points:
        return {
            "success": False,
            "error": f"'{location}'のデータが見つかりません",
            "chart_path": ""
        }

    # キーごとに (年, 値) を収集
    key_data = {}
    all_years = set()

    for dp in data_points:
        year = dp["year"]
        key = dp["key"]
        value = dp["value"]
        all_years.add(year)

        if key not in key_data:
            key_data[key] = []
        key_data[key].append((year, value))

    # グラフ作成
    fig, ax = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor('white')
    ax.set_facecolor('white')

    # 色とマーカーの定義
    colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
              '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf']
    markers = ['o', 's', '^', 'D', 'v', 'p', 'h', '8', '*', 'X']

    total_points = 0

    # キーごとにプロット
    for i, (key, year_value_pairs) in enumerate(sorted(key_data.items())):
        color = colors[i % len(colors)]
        marker = markers[i % len(markers)]

        years = [pair[0] for pair in year_value_pairs]
        values = [pair[1] for pair in year_value_pairs]

        # 同じ年に複数の値がある場合、少しずらす
        jitter = (i - len(key_data) / 2) * 0.03
        x_values = [y + jitter for y in years]

        ax.scatter(
            x_values, values,
            c=color, marker=marker, s=80, alpha=0.7,
            label=key, edgecolors='white', linewidths=0.5
        )
        total_points += len(values)

    # 基準値の水平線
    if reference_value is not None:
        ax.axhline(
            y=reference_value,
            color='red',
            linestyle='--',
            linewidth=2,
            label=f'基準値: {reference_value}'
        )

    # タイトル設定
    title_parts = [p for p in [equipment, equipment_part, location] if p]
    chart_title = " / ".join(title_parts)

    ax.set_title(chart_title, fontsize=14)
    ax.set_xlabel("年度", fontsize=12)
    ax.set_ylabel("測定値", fontsize=12)

    # X軸を年度で表示
    if all_years:
        years_sorted = sorted(all_years)
        ax.set_xticks(years_sorted)
        ax.set_xticklabels([str(y) for y in years_sorted])
        ax.set_xlim(min(years_sorted) - 0.5, max(years_sorted) + 0.5)

    # 凡例（多い場合は外に配置）
    if len(key_data) > 6:
        ax.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize=8)
    else:
        ax.legend(loc='upper right', fontsize=9)

    ax.grid(True, linestyle='--', alpha=0.7)

    # ファイルに保存
    safe_location = re.sub(r'[^\w\-]', '_', location)
    chart_path = figure_to_file(fig, f"{safe_location}.png")

    return {
        "success": True,
        "chart_path": chart_path,
        "chart_title": chart_title,
        "location": location,
        "data_points": total_points,
        "reference_value": reference_value
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
