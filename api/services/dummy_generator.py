# =============================================================================
# api/services/dummy_generator.py - ダミーデータ生成サービス
# =============================================================================
#
# 【ファイル概要】
# テンプレートとなる点検記録から、経年劣化をシミュレーションした
# ダミーデータを生成し、pages コレクションに保存する。
#
# 【処理フロー】
# 1. テンプレートレコードを pages コレクションから取得
# 2. 基準値キーマッチングで各測定値の劣化方向を決定
# 3. 年度ごとに劣化・修繕を考慮した値を計算
# 4. pages コレクションに保存（file_id = dummy_group_id でグループ化）
#
# 【なぜこの設計か】
# - 同じ pages コレクションに入れることで、チャット検索・可視化がそのまま使える
# - 既存のスキーマと同じ構造にすることで、MCP側の変更が不要
# - dummy_group_id で一括管理（一覧表示・削除）できる
#
# =============================================================================

import re
import uuid
import random
from datetime import datetime
from copy import deepcopy

from core.logging import get_logger

logger = get_logger(__name__)


# =============================================================================
# 基準値パース
# =============================================================================
def parse_reference_value(value) -> float | None:
    """
    基準値を数値に変換する。

    基準値はデータによって形式が様々なので、複数のパターンに対応する。

    【対応パターン】
    - 数値: 0.5 → 0.5
    - 文字列(不等号): "≦0.30" → 0.30
    - 文字列(範囲): "0.1~0.5" → 0.5（上限値を採用）
    - 文字列(プラスマイナス): "±0.5" → 0.5
    - 文字列(数値): "0.5" → 0.5

    【この行がないとどうなるか】
    基準値が文字列の場合、劣化計算ができずダミーデータの品質が下がる。
    """
    if isinstance(value, (int, float)):
        return float(value)

    if not isinstance(value, str):
        return None

    # "≦0.30", "≤0.30", "<=0.30" パターン
    match = re.match(r"[≦≤<]=?\s*([\d.]+)", value)
    if match:
        return float(match.group(1))

    # "≧0.10", "≥0.10", ">=0.10" パターン
    match = re.match(r"[≧≥>]=?\s*([\d.]+)", value)
    if match:
        return float(match.group(1))

    # "0.1~0.5", "0.1～0.5" パターン → 上限値を採用
    match = re.match(r"[\d.]+[~～]([\d.]+)", value)
    if match:
        return float(match.group(1))

    # "±0.5" パターン
    match = re.match(r"[±]\s*([\d.]+)", value)
    if match:
        return float(match.group(1))

    # 単純な数値文字列: "0.5" → 0.5
    try:
        return float(value)
    except ValueError:
        pass

    # 文字列内の最後の数値を取得（"基準値 0.5mm" → 0.5）
    numbers = re.findall(r"[\d.]+", value)
    if numbers:
        try:
            return float(numbers[-1])
        except ValueError:
            pass

    return None


# =============================================================================
# 基準値キーマッチング
# =============================================================================
def find_matching_reference(measurement_key: str, parsed_references: dict) -> float | None:
    """
    測定値キーに対応する基準値を探す。

    【なぜマッチングが必要か】
    測定値のキーは詳細（例: "摩耗量・A（上部）・測定箇所①"）だが、
    基準値のキーは簡潔（例: "摩耗量"）。
    「・」で分割した最初のセグメントで前方一致マッチングする。

    【マッチング戦略】
    1. 測定値キーの最初のセグメント（「・」の前）を取得
    2. 各基準値キーと比較
    3. 最も長い一致を優先（より具体的な基準値を使う）

    Args:
        measurement_key: 測定値のキー（例: "摩耗量・A（上部）・測定箇所①"）
        parsed_references: パース済み基準値の辞書（例: {"摩耗量": 0.5}）

    Returns:
        マッチした基準値（数値）。見つからなければ None
    """
    first_segment = measurement_key.split("・")[0]

    best_match = None
    best_length = 0

    for ref_key, ref_val in parsed_references.items():
        # 基準値キーと測定値の最初のセグメントで前方一致
        if first_segment.startswith(ref_key) or ref_key.startswith(first_segment):
            if len(ref_key) > best_length:
                best_match = ref_val
                best_length = len(ref_key)

    return best_match


# =============================================================================
# 経年劣化計算
# =============================================================================
def generate_degradation_series(
    initial_value: float,
    reference_value: float | None,
    start_year: int,
    end_year: int,
    repair_years: list[int],
    noise_ratio: float = 0.05,
) -> dict[int, float]:
    """
    経年劣化データ（年度 → 測定値）を生成する。

    【劣化モデルの設計思想】
    - 基準値は「これ以上劣化すると問題」の閾値
    - 値が初期値から基準値に向かって毎年少しずつ近づく（線形劣化 + ノイズ）
    - 修繕年には初期値方向に60%回復（完全リセットではない）
    - 修繕後、同じ速度で再び劣化が進行する

    【基準値がない場合】
    劣化方向が不明なので、初期値を中心に年3%の微小ランダムウォークを行う。
    これにより「変動はあるが方向性のない」データになる。

    Args:
        initial_value: テンプレートの測定値（劣化の起点）
        reference_value: 基準値（劣化の終点方向）。None なら方向性なし
        start_year: 生成開始年
        end_year: 生成終了年
        repair_years: 修繕を行った年のリスト
        noise_ratio: ノイズの比率（値の変動幅 = |基準値 - 初期値| * noise_ratio）

    Returns:
        {year: value} の辞書
    """
    total_years = end_year - start_year + 1
    results = {}

    # --- 基準値がない場合: ランダムウォーク ---
    if reference_value is None:
        current = initial_value
        walk_amplitude = abs(initial_value) * 0.03 if initial_value != 0 else 0.01
        for year in range(start_year, end_year + 1):
            if year in repair_years:
                # 修繕: 初期値方向に60%回復
                current = current - (current - initial_value) * 0.6
            noise = random.uniform(-walk_amplitude, walk_amplitude)
            results[year] = round(current + noise, 4)
            current += random.uniform(-walk_amplitude, walk_amplitude) * 0.5
        return results

    # --- 基準値がある場合: 線形劣化 ---
    degradation_range = reference_value - initial_value

    # 基準値到達予定年数（生成期間の1.5倍 → 期間内では基準値の2/3程度まで劣化）
    years_to_reference = max(total_years * 1.5, 3)
    annual_degradation = degradation_range / years_to_reference

    # ノイズの幅
    noise_amplitude = abs(degradation_range) * noise_ratio

    current_value = initial_value

    for year in range(start_year, end_year + 1):
        # 修繕年: 初期値方向に60%回復
        if year in repair_years:
            recovery = (current_value - initial_value) * 0.6
            current_value = current_value - recovery

        # ノイズを加えた出力値
        noise = random.uniform(-noise_amplitude, noise_amplitude)
        output_value = current_value + noise

        # 基準値を超えないようにクランプ
        if degradation_range > 0:
            # 値が増加する劣化（例: 摩耗量が増える）
            output_value = max(output_value, 0)
            output_value = min(output_value, reference_value)
        elif degradation_range < 0:
            # 値が減少する劣化（例: 厚みが減る）
            output_value = max(output_value, reference_value)
        # degradation_range == 0 の場合は初期値のままノイズのみ

        results[year] = round(output_value, 4)

        # 劣化を進行
        current_value += annual_degradation

    return results


# =============================================================================
# 日付の年度差し替え
# =============================================================================
def replace_year_in_date(date_str: str | None, new_year: int) -> str:
    """
    日付文字列の年だけを差し替える。

    例: "2024-11-14" → "2020-11-14"（new_year=2020 の場合）

    【なぜ月日を変えないか】
    点検は毎年同じ時期に行うのが一般的。年だけ変えることで
    リアリティのあるデータになる。
    """
    if not date_str:
        return f"{new_year}-01-01"

    try:
        if "-" in date_str:
            parts = date_str.split("-")
            parts[0] = str(new_year)
            return "-".join(parts)
        elif "/" in date_str:
            parts = date_str.split("/")
            parts[0] = str(new_year)
            return "/".join(parts)
    except (ValueError, IndexError):
        pass

    return f"{new_year}-01-01"


# =============================================================================
# ダミーデータ生成メイン処理
# =============================================================================
async def generate_dummy_data(
    db,
    source_file_id: str,
    tenant: str,
    start_year: int,
    end_year: int,
    repair_years: list[int],
) -> dict:
    """
    テンプレートレコードからダミーデータを生成して pages コレクションに保存する。

    【処理フロー】
    1. source_file_id のレコードを pages コレクションから全件取得
    2. 各レコードについて:
       a. data.基準値から各キーの基準値を抽出・パース
       b. data.測定値の各キーについて、対応する基準値を探す
       c. generate_degradation_series() で年度ごとの値を生成
       d. 年度ごとにドキュメントを作成して pages に挿入
    3. 結果サマリーを返す

    【なぜこの実装か】
    - pages コレクションに直接入れるので、チャット検索・可視化がそのまま使える
    - file_id を dummy_group_id にすることで、Admin 一覧に表示される
    - is_dummy フラグで実データとの区別が可能

    Args:
        db: MongoDBデータベースオブジェクト
        source_file_id: テンプレートとなるファイルのID
        tenant: テナントID
        start_year: 生成開始年
        end_year: 生成終了年
        repair_years: 修繕を行った年のリスト

    Returns:
        {success, dummy_group_id, source_filename, total_records, error}
    """
    # 1. テンプレートレコードを取得
    template_records = await db.pages.find(
        {
            "file_id": source_file_id,
            "tenant": tenant,
            "page_number": {"$ne": None},
            "error": {"$exists": False},
        }
    ).to_list(500)

    if not template_records:
        return {"success": False, "error": "テンプレートレコードが見つかりません"}

    dummy_group_id = str(uuid.uuid4())
    source_filename = template_records[0].get("filename", "unknown.pdf")
    generated_at = datetime.utcnow()
    total_records = 0
    docs_to_insert = []

    logger.info(
        f"ダミーデータ生成開始: source={source_file_id}, "
        f"years={start_year}-{end_year}, repairs={repair_years}, "
        f"templates={len(template_records)}件"
    )

    # 2. 各テンプレートレコードについて年度ごとのダミーデータを生成
    for template in template_records:
        template_data = template.get("data", {})
        measurements = template_data.get("測定値", {})
        references = template_data.get("基準値", {})
        original_date = template_data.get("点検年月日", "")

        # テンプレートの年度を取得（この年は実データが存在するのでスキップする）
        template_year = None
        if original_date:
            try:
                template_year = int(original_date.split("-")[0].split("/")[0])
            except (ValueError, IndexError):
                pass

        if not measurements:
            continue

        # 基準値を事前にパース
        parsed_references = {}
        for ref_key, ref_val in references.items():
            parsed = parse_reference_value(ref_val)
            if parsed is not None:
                parsed_references[ref_key] = parsed

        # 各測定値キーの年度別劣化系列を計算
        degradation_series = {}
        for meas_key, meas_val in measurements.items():
            # 数値でない測定値（null や文字列）はスキップ
            if not isinstance(meas_val, (int, float)):
                degradation_series[meas_key] = None
                continue

            ref_val = find_matching_reference(meas_key, parsed_references)
            series = generate_degradation_series(
                initial_value=float(meas_val),
                reference_value=ref_val,
                start_year=start_year,
                end_year=end_year,
                repair_years=repair_years,
            )
            degradation_series[meas_key] = series

        # 3. 年度ごとにドキュメントを作成（テンプレート年は実データがあるのでスキップ）
        for year in range(start_year, end_year + 1):
            if year == template_year:
                continue
            # 測定値を年度の値に差し替え
            new_measurements = {}
            for meas_key, series in degradation_series.items():
                if series is None:
                    # 数値でない測定値はそのまま保持
                    new_measurements[meas_key] = measurements[meas_key]
                else:
                    new_measurements[meas_key] = series[year]

            # テンプレートの data を deep copy して測定値と日付を差し替え
            new_data = deepcopy(template_data)
            new_data["測定値"] = new_measurements
            new_data["点検年月日"] = replace_year_in_date(original_date, year)

            dummy_doc = {
                # --- 既存スキーマと同じフィールド ---
                "file_id": dummy_group_id,
                "filename": f"【ダミー】{source_filename}",
                "path": template.get("path", ""),
                "tenant": tenant,
                "uploaded_at": generated_at,
                "processed": True,
                "processed_at": generated_at,
                "page_number": template.get("page_number"),
                "table_index": template.get("table_index"),
                "table_title": template.get("table_title"),
                "image_path": template.get("image_path"),
                "data": new_data,
                # --- ダミーデータ固有フィールド ---
                "dummy_group_id": dummy_group_id,
                "source_file_id": source_file_id,
                "is_dummy": True,
                "is_repair_year": year in repair_years,
                "generated_at": generated_at,
            }

            docs_to_insert.append(dummy_doc)
            total_records += 1

    # 4. 一括挿入（パフォーマンスのため insert_many を使用）
    if docs_to_insert:
        await db.pages.insert_many(docs_to_insert)

    logger.info(f"ダミーデータ生成完了: group={dummy_group_id}, records={total_records}")

    return {
        "success": True,
        "dummy_group_id": dummy_group_id,
        "source_file_id": source_file_id,
        "source_filename": source_filename,
        "total_records": total_records,
    }
