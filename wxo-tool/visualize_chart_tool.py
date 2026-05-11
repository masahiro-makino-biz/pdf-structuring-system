"""
WxO ADK Python Tool: visualize_chart
検索結果データからグラフを生成する（chart_utils 統合版・単一ファイル）

【既存の visualize_data との違い】
- 別ツール名 'visualize_chart' で登録される（上書きしない）
- chart_utils.py の必要関数を1ファイルに統合済み（外部依存なし）

【インポート方法】
    cd /Users/masahiro/Business/test-cusor/wxo-tool
    orchestrate tools import -k python -f visualize_chart_tool.py -r requirements.txt

【AIエージェントへの紐付け】
WxO 管理画面で対象 Agent の Tools に visualize_chart を追加する。
古い flow 型のグラフツールや壊れている visualize_data は外す。
"""

import json
import os
import re
import tempfile
import traceback
import uuid

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from ibm_watsonx_orchestrate.agent_builder.tools import tool


# =============================================================================
# 定数
# =============================================================================
JAPANESE_FONT = "IPAGothic"

# グラフHTMLの出力先
# 【優先順位】
# 1. 環境変数 CHART_OUTPUT_DIR
# 2. /data/charts （ローカル MCP 互換）
# 3. システム標準 tmp（WxO実行環境では /tmp 等）
def _get_charts_dir() -> str:
    custom = os.environ.get("CHART_OUTPUT_DIR")
    if custom:
        try:
            os.makedirs(custom, exist_ok=True)
            return custom
        except OSError:
            pass
    # /data/charts に書ければそれを使う（ローカル開発時）
    try:
        os.makedirs("/data/charts", exist_ok=True)
        return "/data/charts"
    except OSError:
        pass
    # WxO 実行環境用フォールバック: tempfile
    fallback = os.path.join(tempfile.gettempdir(), "wxo_charts")
    os.makedirs(fallback, exist_ok=True)
    return fallback


# =============================================================================
# Plotly figure 出力ヘルパー
# =============================================================================

def _figure_to_html(fig) -> str:
    """PlotlyのfigureをHTMLテキストとして返す（CDN参照でファイル軽量化）"""
    return fig.to_html(
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": True, "displaylogo": False},
    )


def _figure_to_file(fig, filename: str = "chart.html") -> str:
    """PlotlyのfigureをHTMLファイルに保存して保存パスを返す"""
    charts_dir = _get_charts_dir()
    unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
    filepath = os.path.join(charts_dir, unique_name)
    fig.write_html(
        filepath,
        include_plotlyjs="cdn",
        full_html=True,
        config={"displayModeBar": True, "displaylogo": False},
    )
    return filepath


# =============================================================================
# 日付・基準値ユーティリティ
# =============================================================================

def _extract_year(date_str: str):
    """日付文字列から年を抽出"""
    if not date_str:
        return None
    try:
        if "-" in date_str:
            return int(date_str.split("-")[0])
        if "/" in date_str:
            return int(date_str.split("/")[0])
        return int(date_str[:4])
    except (ValueError, IndexError):
        return None


def _extract_year_month(date_str: str):
    """日付文字列から '2022-12' 形式を抽出"""
    if not date_str:
        return None
    try:
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts) >= 2:
                return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
        elif "/" in date_str:
            parts = date_str.split("/")
            if len(parts) >= 2:
                return f"{int(parts[0]):04d}-{int(parts[1]):02d}"
        elif len(date_str) >= 6 and date_str[:6].isdigit():
            return f"{int(date_str[:4]):04d}-{int(date_str[4:6]):02d}"
    except (ValueError, IndexError):
        return None
    return None


def _extract_reference_values(references: dict):
    """基準値辞書から数値を抽出（"≦0.5" → 0.5）"""
    result = {}
    for ref_key, ref_val in references.items():
        if isinstance(ref_val, (int, float)):
            result[ref_key] = float(ref_val)
        elif isinstance(ref_val, str):
            numbers = re.findall(r"[\d.]+", ref_val)
            if numbers:
                result[ref_key] = float(numbers[0])
    return result


# =============================================================================
# データを 機器+部品+物理量 単位でグルーピング
# =============================================================================

def _group_by_location(documents: list) -> dict:
    """MongoDB findドキュメント群を 機器+機器部品+測定物理量 単位でまとめる"""
    location_groups = {}
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        data = doc.get("data", {})
        if not data:
            continue

        date_str = data.get("点検年月日", "")
        year = _extract_year(date_str)
        year_month = _extract_year_month(date_str)
        if year is None:
            continue

        equipment = data.get("機器") or "不明機器"
        equipment_part = data.get("機器部品") or "不明部品"
        measurement_type = data.get("測定物理量") or "不明物理量"
        measurements = data.get("測定値", {}) or {}
        references = data.get("基準値", {}) or {}

        ref_values = _extract_reference_values(references)
        group_key = f"{equipment} / {equipment_part} / {measurement_type}"

        if group_key not in location_groups:
            location_groups[group_key] = {
                "data_points": [],
                "equipment": equipment,
                "equipment_part": equipment_part,
                "measurement_type": measurement_type,
                "reference_values": {},
            }

        for k, v in ref_values.items():
            if k not in location_groups[group_key]["reference_values"]:
                location_groups[group_key]["reference_values"][k] = v

        for key, value in measurements.items():
            if isinstance(value, str):
                try:
                    value = float(value)
                except ValueError:
                    continue
            if not isinstance(value, (int, float)):
                continue
            location_groups[group_key]["data_points"].append({
                "year": year,
                "year_month": year_month or f"{year:04d}-01",
                "key": key,
                "value": value,
            })
    return location_groups


# =============================================================================
# 1グループ用のグラフを生成
# =============================================================================

def _create_chart(
    location: str,
    data_points: list,
    equipment: str,
    equipment_part: str,
    measurement_type: str,
    reference_values: dict,
    chart_type=None,
    color=None,
    year_from=None,
    year_to=None,
    min_value=None,
    max_value=None,
    show_reference=True,
    x_axis=None,
    key_filter=None,
    above_reference=False,
) -> dict:
    """1グループ分のグラフ生成（フィルタ・自動判定・基準値線描画）"""
    if not data_points:
        return {"success": False, "error": f"'{location}'のデータが見つかりません", "chart_path": ""}

    df = pd.DataFrame(data_points)

    if year_from is not None:
        df = df[df["year"] >= year_from]
    if year_to is not None:
        df = df[df["year"] <= year_to]
    if min_value is not None:
        df = df[df["value"] >= min_value]
    if max_value is not None:
        df = df[df["value"] <= max_value]
    if key_filter is not None:
        keywords = [k.strip() for k in key_filter.split(",")]
        df = df[df["key"].apply(lambda k: any(kw in k for kw in keywords))]
    if above_reference and reference_values:
        ref_min = min(reference_values.values())
        df = df[df["value"] > ref_min]

    if df.empty:
        return {"success": False, "error": f"'{location}'のフィルタ条件に合うデータがありません", "chart_path": ""}

    df["year"] = df["year"].astype(str)
    if "year_month" not in df.columns:
        df["year_month"] = df["year"].astype(str) + "-01"
    df = df.sort_values("year_month")

    unique_years = df["year"].nunique()
    unique_months = df["year_month"].nunique()

    if x_axis is not None:
        x_col = x_axis
        x_label = {"year": "年度", "year_month": "年月", "key": "測定値キー"}.get(x_axis, x_axis)
    elif unique_months <= 1:
        x_col = "key"
        x_label = "測定値キー"
    elif unique_months > unique_years:
        x_col = "year_month"
        x_label = "年月"
    else:
        x_col = "year"
        x_label = "年度"

    reference_values = reference_values or {}
    ref_min = min(reference_values.values()) if reference_values else None
    use_color_key = color is None and x_col != "key"
    warm_palette = [
        "#E74C3C", "#E67E22", "#D35400", "#C0392B",
        "#F39C12", "#E84393", "#D63031", "#F97F51",
    ]

    chart_common = dict(
        data_frame=df,
        x=x_col,
        y="value",
        hover_data={"key": True, "year_month": True, "value": ":.4f"},
        labels={"year": "年度", "year_month": "年月", "value": "測定値", "key": "測定値キー"},
    )
    if use_color_key:
        chart_common["color"] = "key"
        keys = df["key"].unique().tolist()
        chart_common["color_discrete_map"] = {
            k: warm_palette[i % len(warm_palette)] for i, k in enumerate(keys)
        }

    if chart_type is None:
        unique_keys = df["key"].nunique()
        multiple_time_points = unique_years >= 2 or unique_months >= 2
        chart_type = "line" if (unique_keys <= 5 and multiple_time_points) else "strip"

    chart_builders = {
        "strip": lambda: px.strip(**chart_common, stripmode="overlay"),
        "scatter": lambda: px.scatter(**chart_common),
        "bar": lambda: px.bar(**chart_common, barmode="group"),
        "line": lambda: px.line(**chart_common, markers=True),
    }
    fig = chart_builders.get(chart_type, chart_builders["strip"])()

    if color is not None:
        fig.update_traces(marker=dict(color=color))
    if chart_type in ("strip", "scatter"):
        fig.update_traces(marker=dict(size=6, opacity=0.55, line=dict(width=0)))
    if chart_type == "strip":
        fig.update_traces(jitter=0.35, pointpos=0)

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

    title_parts = [p for p in [equipment, equipment_part, measurement_type] if p]
    chart_title = " / ".join(title_parts) if title_parts else location
    xaxis_config = dict(showgrid=False)
    if x_col in ("key", "year_month"):
        xaxis_config["tickangle"] = -45

    fig.update_layout(
        template=None,
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
            yanchor="top", y=0.99, xanchor="left", x=1.02,
        ),
        width=700,
        height=440,
        margin=dict(l=60, r=160, t=60, b=100 if x_col == "key" else 50),
    )

    safe_location = re.sub(r"[^\w\-]", "_", location)
    chart_path = _figure_to_file(fig, f"{safe_location}.html")
    chart_html = _figure_to_html(fig)

    return {
        "success": True,
        "chart_path": chart_path,
        "chart_html": chart_html,
        "chart_title": chart_title,
        "location": location,
        "data_points": len(df),
        "reference_values": reference_values,
    }


# =============================================================================
# WxO Tool 本体
# =============================================================================

@tool(
    name="visualize_chart",
    description="検索結果データ(MongoDB find結果のJSON文字列)から、機器+機器部品+測定物理量ごとにグラフを生成する。",
)
def visualize_chart(
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
    MongoDB findの検索結果データからグラフを生成する。

    Args:
        data: MongoDB findの検索結果（JSON文字列）。dictまたはdictのリストを JSON.stringify した文字列。
        chart_type: グラフ種類。"strip" / "scatter" / "bar" / "line"。
                    未指定なら自動判定（測定値キーが5個以下で複数時点ある→line、それ以外→strip）
        color: データ点の色を統一する場合に指定（例: "red"）。未指定なら測定値キーで自動色分け。
        year_from: この年度以降のデータだけ表示（例: 2024）
        year_to: この年度以前のデータだけ表示（例: 2025）
        min_value: この値以上のデータだけ表示
        max_value: この値以下のデータだけ表示
        show_reference: 基準値の赤い線を表示するか（デフォルト: True）
        x_axis: X軸に使うカラム。"year" / "year_month" / "key"。未指定なら自動判定。
        key_filter: 測定値キー名で絞る（部分一致、カンマ区切りで複数可）
        above_reference: Trueにすると基準値を超えているデータだけ表示

    Returns:
        グラフHTMLパス・タイトル等を含むJSON文字列
    """
    print(f"[visualize_chart] 開始: data 長さ={len(data) if data else 0}", flush=True)

    # JSONパース
    try:
        documents = json.loads(data) if isinstance(data, str) else data
    except json.JSONDecodeError as e:
        print(f"[visualize_chart] JSONパースエラー: {e}", flush=True)
        return json.dumps({
            "success": False,
            "error": f"データのJSON解析エラー: {str(e)}",
            "charts": [],
        }, ensure_ascii=False)

    if not isinstance(documents, list):
        documents = [documents]
    if not documents:
        return json.dumps({"success": False, "error": "データが空です", "charts": []}, ensure_ascii=False)

    # image_path 収集
    reference_images = [doc["image_path"] for doc in documents if isinstance(doc, dict) and doc.get("image_path")]

    # グルーピング
    groups = _group_by_location(documents)
    if not groups:
        return json.dumps({
            "success": False,
            "error": "有効なデータがありません（点検年月日と機器情報を含むdata.測定値が必要）",
            "charts": [],
        }, ensure_ascii=False)

    # グループごとにグラフ生成
    charts = []
    try:
        for group_key, group in groups.items():
            chart = _create_chart(
                location=group_key,
                data_points=group["data_points"],
                equipment=group["equipment"],
                equipment_part=group["equipment_part"],
                measurement_type=group["measurement_type"],
                reference_values=group["reference_values"],
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
            if chart.get("success"):
                charts.append(chart)
    except Exception as e:
        print(
            f"[visualize_chart] グラフ生成エラー: {type(e).__name__}: {e}\n{traceback.format_exc()}",
            flush=True,
        )
        return json.dumps({
            "success": False,
            "error": f"グラフ生成エラー: {type(e).__name__}: {str(e)}",
            "charts": [],
        }, ensure_ascii=False)

    if not charts:
        return json.dumps({
            "success": False,
            "error": "グラフを生成できませんでした",
            "charts": [],
        }, ensure_ascii=False)

    result = {
        "success": True,
        "charts": charts,
        "total_locations": len(charts),
        "reference_images": reference_images,
    }
    print(f"[visualize_chart] 完了: {len(charts)}グラフ生成", flush=True)
    return json.dumps(result, ensure_ascii=False)
