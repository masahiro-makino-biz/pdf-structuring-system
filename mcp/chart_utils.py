# =============================================================================
# mcp/chart_utils.py - グラフ生成ユーティリティ
# =============================================================================
#
# 【ファイル概要】
# 検索結果から年次推移グラフを生成するヘルパー関数群。
# visualize_dataツールから呼び出される。
#
# =============================================================================

import base64
import io
from datetime import datetime

import matplotlib
matplotlib.use('Agg')  # GUIなしで描画するため
import matplotlib.pyplot as plt


def setup_japanese_font():
    """
    日本語フォントを設定

    【なぜ必要か】
    matplotlibはデフォルトで日本語フォントを持っていない。
    Dockerfileでインストールしたフォントを指定する。
    """
    plt.rcParams['font.family'] = ['IPAGothic', 'DejaVu Sans']


def figure_to_base64(fig) -> str:
    """
    matplotlibのfigureをBase64文字列に変換

    【なぜBase64か】
    - JSONで返せる
    - Streamlitで直接表示できる
    """
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=100, bbox_inches='tight')
    buf.seek(0)
    img_base64 = base64.b64encode(buf.read()).decode('utf-8')
    buf.close()
    plt.close(fig)
    return img_base64


def extract_yearly_data(results: list, measurement_key: str) -> dict:
    """
    検索結果から年ごとのデータを抽出

    Args:
        results: search_documentsの結果（resultsリスト）
        measurement_key: 抽出する測定値のキー（例: "摩耗量"）

    Returns:
        {年: [測定値リスト], ...} の辞書
    """
    yearly_data = {}

    for file_result in results:
        for record in file_result.get("matched_records", []):
            data = record.get("data", {})

            # 点検年月日から年を抽出
            date_str = data.get("点検年月日", "")
            if not date_str:
                continue

            try:
                # 様々な日付形式に対応
                if "-" in date_str:
                    year = int(date_str.split("-")[0])
                elif "/" in date_str:
                    year = int(date_str.split("/")[0])
                else:
                    year = int(date_str[:4])
            except (ValueError, IndexError):
                continue

            # 測定値を取得
            measurements = data.get("測定値", {})
            if measurement_key in measurements:
                value = measurements[measurement_key]
                # 文字列の場合は数値に変換を試みる
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        continue
                if isinstance(value, (int, float)):
                    if year not in yearly_data:
                        yearly_data[year] = []
                    yearly_data[year].append(value)

    return yearly_data


def create_yearly_trend(
    results: list,
    measurement_key: str,
    chart_type: str = "line",
    title: str = ""
) -> dict:
    """
    年次推移グラフを生成

    Args:
        results: search_documentsの結果（resultsリスト）
        measurement_key: 表示する測定値のキー（例: "摩耗量"）
        chart_type: "line"(折れ線) or "bar"(棒)
        title: グラフタイトル（省略時は自動生成）

    Returns:
        {
            "success": bool,
            "chart_image": "Base64エンコード画像",
            "chart_title": str,
            "data_points": int
        }
    """
    setup_japanese_font()

    # 年ごとのデータを抽出
    yearly_data = extract_yearly_data(results, measurement_key)

    if not yearly_data:
        return {
            "success": False,
            "error": f"'{measurement_key}'のデータが見つかりません",
            "chart_image": "",
            "data_points": 0
        }

    # 年でソートして平均値を計算
    years = sorted(yearly_data.keys())
    values = [sum(yearly_data[y]) / len(yearly_data[y]) for y in years]

    # グラフ作成
    fig, ax = plt.subplots(figsize=(10, 6))

    if chart_type == "bar":
        ax.bar(years, values, color='steelblue')
    else:
        ax.plot(years, values, marker='o', linewidth=2, markersize=8, color='steelblue')

    # タイトル設定
    chart_title = title if title else f"{measurement_key}の年次推移"
    ax.set_title(chart_title, fontsize=14)
    ax.set_xlabel("年", fontsize=12)
    ax.set_ylabel(measurement_key, fontsize=12)

    # X軸を整数表示
    ax.set_xticks(years)
    ax.set_xticklabels([str(y) for y in years])

    # グリッド追加
    ax.grid(True, linestyle='--', alpha=0.7)

    # Base64変換
    chart_image = figure_to_base64(fig)

    return {
        "success": True,
        "chart_image": chart_image,
        "chart_title": chart_title,
        "data_points": sum(len(v) for v in yearly_data.values())
    }
