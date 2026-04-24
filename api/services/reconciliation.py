# =============================================================================
# api/services/reconciliation.py - 測定値キー突合サービス
# =============================================================================
#
# 【このファイルの役割】
# 同じ機器+部品+測定物理量グループ内で、測定値のキーが不一致なレコードを検出し、
# AIで画像比較して「A = タイヤ1」のようなマッピングを生成する。
#
# 【処理フロー】
# 1. detect_inconsistent_groups(): MongoDB aggregate でキー不一致グループを検出
# 2. ai_judge_key_mappings_batch(): Vision API で画像比較し、レコード単位でキー対応を判定
# 3. apply_key_mappings(): 承認済みマッピングを使って測定値キーを書き換え
#
# 【なぜ必要か】
# 同じ測定点が PDF ごとに違うラベル（例: "A" vs "1" vs "タイヤ1"）で記載される場合、
# 文字列の類似性がゼロなので、ルールベース正規化やAI辞書では対処不可能。
# 元のPDF画像を見て初めて「同じ測定点」と判定できる。
#
# =============================================================================

import base64
import json
from datetime import datetime
from collections import Counter
from io import BytesIO

from PIL import Image
from openai import OpenAI

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()


def _get_openai_client():
    """LiteLLM経由のOpenAIクライアントを取得"""
    return OpenAI(
        api_key=settings.litellm_api_key,
        base_url=f"{settings.litellm_url}/v1",
    )


def _image_to_base64(image: Image.Image, format: str = "PNG") -> str:
    """PIL ImageをBase64文字列に変換"""
    buffer = BytesIO()
    image.save(buffer, format=format)
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


# =============================================================================
# 検出: キー不一致グループの検出
# =============================================================================

async def detect_inconsistent_groups(db, tenant: str = "default") -> list[dict]:
    """
    機器+機器部品+測定物理量 のグループごとに、測定値キーの不一致を検出する。

    【処理】
    1. pages コレクションから全レコードを集約
    2. グループごとに測定値キーの出現頻度をカウント
    3. 少数派キーを持つグループを返す

    Returns:
        [
            {
                "group": {"機器": "...", "機器部品": "...", "測定物理量": "..."},
                "majority_keys": ["タイヤ1", "タイヤ2"],
                "minority_keys": ["A", "B"],
                "majority_sample": {"page_id": ObjectId, "image_path": "..."},
                "minority_samples": [{"key": "A", "page_id": ObjectId, "image_path": "..."}],
                "total_records": 10,
            },
            ...
        ]
    """
    # 全レコードを取得（グループごとにキー情報を集める）
    pipeline = [
        {"$match": {
            "tenant": tenant,
            "page_number": {"$ne": None},
            "error": {"$exists": False},
            "data.測定値": {"$exists": True},
        }},
        {"$addFields": {
            "measurement_keys": {
                "$map": {
                    "input": {"$objectToArray": "$data.測定値"},
                    "as": "kv",
                    "in": "$$kv.k",
                }
            }
        }},
        {"$group": {
            "_id": {
                "機器": "$data.機器",
                "機器部品": "$data.機器部品",
                "測定物理量": "$data.測定物理量",
            },
            "records": {
                "$push": {
                    "keys": "$measurement_keys",
                    "page_id": "$_id",
                    "image_path": "$image_path",
                    "measurements": "$data.測定値",
                }
            },
            "total_records": {"$sum": 1},
        }},
        {"$match": {"total_records": {"$gt": 1}}},
    ]

    cursor = db.pages.aggregate(pipeline)
    groups = await cursor.to_list(length=None)

    inconsistent_groups = []

    for group in groups:
        group_id = group["_id"]
        records = group["records"]

        if len(records) < 2:
            continue  # 1レコードしかないグループは比較対象なし

        # キーセット（ソート済みタプル）ごとにレコードをグルーピング
        keyset_to_records = {}  # tuple(sorted keys) → [record, ...]
        for record in records:
            keys = record.get("keys", [])
            if not keys:
                continue
            keyset = tuple(sorted(keys))
            keyset_to_records.setdefault(keyset, []).append(record)

        # キーセットの種類が1つ以下 → 全員同じキー、照合不要
        if len(keyset_to_records) < 2:
            continue

        # キーセット件数の多い順に並べる。同数の場合はキー数が多い方を優先（情報量が多い側を多数派にする）
        sorted_keysets = sorted(
            keyset_to_records.items(),
            key=lambda kv: (len(kv[1]), len(kv[0])),
            reverse=True,
        )

        # 最初のキーセットを「多数派候補」として扱う（2件でも検出対象）
        majority_keyset, majority_records = sorted_keysets[0]
        majority_keys = list(majority_keyset)
        majority_record = majority_records[0]
        majority_set = set(majority_keyset)

        # 残りのキーセットのキーを少数派として扱う
        # 多数派と重複するキーは除外（「違いのあるキーだけ」を突合対象にする）
        minority_samples = []
        for keyset, recs in sorted_keysets[1:]:
            sample_record = recs[0]
            for mk in keyset:
                if mk in majority_set:
                    continue  # 多数派にも存在するキーは突合不要
                minority_samples.append({
                    "key": mk,
                    "page_id": sample_record.get("page_id"),
                    "image_path": sample_record.get("image_path"),
                    "measurements": sample_record.get("measurements", {}),
                })

        if not minority_samples:
            continue

        inconsistent_groups.append({
            "group": {
                "機器": group_id.get("機器"),
                "機器部品": group_id.get("機器部品"),
                "測定物理量": group_id.get("測定物理量"),
            },
            "majority_keys": majority_keys,
            "minority_keys": [s["key"] for s in minority_samples],
            "majority_sample": {
                "page_id": majority_record.get("page_id"),
                "image_path": majority_record.get("image_path"),
                "measurements": majority_record.get("measurements", {}),
            },
            "minority_samples": minority_samples,
            "total_records": group["total_records"],
        })

    return inconsistent_groups


# =============================================================================
# AI判定: 画像比較による測定値キーの対応付け
# =============================================================================

# AI判定の確信度閾値（これ未満は人間レビュー必須）
RECONCILIATION_CONFIDENCE_THRESHOLD = 0.7


async def ai_judge_key_mappings_batch(
    minority_keys: list[str],
    majority_keys: list[str],
    minority_image_path: str,
    majority_image_path: str,
    minority_measurements: dict = None,
    majority_measurements: dict = None,
) -> dict[str, dict]:
    """
    同一レコード内の複数の少数派キーをまとめてAIに判定させる（バッチ判定）。

    【なぜバッチ化するか】
    1キーずつ判定するとAIは他のキーとの位置関係を把握できない。
    レコード内の全少数派キーを一度に見せた方が、表の行列配置から対応関係を推定しやすい。

    Args:
        minority_keys: 同じレコード内の少数派キーのリスト（例: ["A", "B", "C"]）
        majority_keys: 多数派キーリスト（例: ["タイヤ1", "タイヤ2", "タイヤ3"]）
        minority_image_path: 少数派キーを含むページの画像
        majority_image_path: 多数派キーを含むページの画像
        minority_measurements: 少数派レコードの測定値dict全体
        majority_measurements: 多数派レコードの測定値dict全体

    Returns:
        {
            "A": {"matched_key": "タイヤ1", "confidence": 0.92, "reasoning": "..."},
            "B": {"matched_key": "タイヤ2", "confidence": 0.88, "reasoning": "..."},
            ...
        }
    """
    client = _get_openai_client()

    # 画像を読み込んでBase64変換
    try:
        img1 = Image.open(minority_image_path)
        max_size = 2048
        if max(img1.size) > max_size:
            img1.thumbnail((max_size, max_size), Image.LANCZOS)
        b64_minority = _image_to_base64(img1)

        img2 = Image.open(majority_image_path)
        if max(img2.size) > max_size:
            img2.thumbnail((max_size, max_size), Image.LANCZOS)
        b64_majority = _image_to_base64(img2)
    except Exception as e:
        logger.warning(f"画像読み込みエラー: {e}")
        return {
            k: {"matched_key": None, "confidence": 0.0, "reasoning": f"画像読み込みエラー: {e}"}
            for k in minority_keys
        }

    # JSON データを判断材料としてプロンプトに含める
    json_context = ""
    if minority_measurements:
        json_context += f"【画像1の構造化データ】{json.dumps(minority_measurements, ensure_ascii=False)}\n"
    if majority_measurements:
        json_context += f"【画像2の構造化データ】{json.dumps(majority_measurements, ensure_ascii=False)}\n"

    prompt = (
        f"2枚の点検記録画像と構造化データを比較して、測定値キーの対応を判定してください。\n\n"
        f"【画像1】少数派キー {minority_keys} を含む表\n"
        f"【画像2】多数派キー {majority_keys} を含む表\n\n"
        f"{json_context}\n"
        f"画像1の各キー（{minority_keys}）が、画像2の {majority_keys} のどれに対応するかを**それぞれ**判定してください。\n"
        f"判断材料:\n"
        f"- 画像内の表の位置関係、行列の配置（同じ行/列の位置なら対応する可能性が高い）\n"
        f"- 構造化データの値の近さ（同じ測定点なら値が近い傾向がある）\n"
        f"- レコード内の並び順の一貫性（A,B,C と タイヤ1,タイヤ2,タイヤ3 が同じ並びなら順番に対応）\n\n"
        f"JSON形式で回答（mappings配列に各少数派キーごとの判定を入れる）:\n"
        f'{{"mappings": ['
        f'{{"minority_key": "A", "matched_key": "タイヤ1 or null", "confidence": 0.0-1.0, "reasoning": "..."}}, ...'
        f"]}}\n"
    )

    try:
        response = client.chat.completions.create(
            model=settings.litellm_model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_minority}",
                            "detail": "auto",
                        },
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{b64_majority}",
                            "detail": "auto",
                        },
                    },
                ],
            }],
            response_format={"type": "json_object"},
            max_tokens=1500,
            temperature=0,
        )
        raw = json.loads(response.choices[0].message.content)
        mappings_list = raw.get("mappings", []) if isinstance(raw, dict) else []

        # minority_key をキーにしたdictに変換 + 候補外キー検証
        result: dict[str, dict] = {}
        for item in mappings_list:
            if not isinstance(item, dict):
                continue
            mk = item.get("minority_key")
            if mk not in minority_keys:
                continue  # プロンプト外のキーは無視
            matched = item.get("matched_key")
            if matched and matched not in majority_keys:
                logger.warning(f"AIが不正なキーを返却: {matched} (候補: {majority_keys})")
                result[mk] = {
                    "matched_key": None,
                    "confidence": 0.0,
                    "reasoning": f"AIが候補外のキーを返却: {matched}",
                }
            else:
                result[mk] = {
                    "matched_key": matched,
                    "confidence": float(item.get("confidence", 0.0) or 0.0),
                    "reasoning": item.get("reasoning", ""),
                }

        # AIが返さなかったキーは「判定不能」として埋める
        for mk in minority_keys:
            if mk not in result:
                result[mk] = {
                    "matched_key": None,
                    "confidence": 0.0,
                    "reasoning": "AIが該当キーを応答しなかった",
                }

        return result

    except Exception as e:
        logger.warning(f"AI突合判定エラー: {e}")
        return {
            k: {"matched_key": None, "confidence": 0.0, "reasoning": f"AI判定エラー: {e}"}
            for k in minority_keys
        }


# =============================================================================
# スキャン実行: 検出 + AI判定 + DB保存
# =============================================================================

async def run_reconciliation_scan(db, tenant: str = "default") -> dict:
    """
    突合スキャンを実行し、結果を key_mappings コレクションに保存する。

    Returns:
        {"groups_found": int, "mappings_created": int, "stale_deleted": int}
    """
    # スキャン前に「もう該当レコードが存在しないマッピング」を削除
    # 手動で構造化データのキーを修正した場合など、variant_keyが実在しなくなったマッピングを掃除
    stale_deleted = await _cleanup_stale_mappings(db, tenant)

    groups = await detect_inconsistent_groups(db, tenant)
    mappings_created = 0

    # 1グループあたりの少数派キー上限（多すぎる場合は構造化自体が不正）
    MAX_MINORITY_KEYS_PER_GROUP = 20

    for group_info in groups:
        group = group_info["group"]
        majority_keys = group_info["majority_keys"]
        majority_image = group_info["majority_sample"].get("image_path")
        majority_page_id = group_info["majority_sample"].get("page_id")
        majority_measurements = group_info["majority_sample"].get("measurements")

        if not majority_image:
            continue

        minority_samples = group_info["minority_samples"]

        # 少数派キーが多すぎる場合はスキップ（構造化品質の問題）
        if len(minority_samples) > MAX_MINORITY_KEYS_PER_GROUP:
            logger.warning(
                f"少数派キーが{len(minority_samples)}件で上限{MAX_MINORITY_KEYS_PER_GROUP}件超過、スキップ: "
                f"[{group.get('機器')}]"
            )
            continue

        # 少数派を「同じページ（=同じレコード）」ごとにまとめる。
        # AIに「1ページ分の少数派キー群」をまとめて見せることで行列対応の推定精度を上げる。
        samples_by_page: dict = {}
        for s in minority_samples:
            samples_by_page.setdefault(s.get("page_id"), []).append(s)

        for page_id, samples_in_page in samples_by_page.items():
            # ページ単位で: 不正キーと既存マッピングを除外したうえで残ったものだけAIに渡す
            new_samples = []
            for s in samples_in_page:
                mk = s["key"]
                # 明らかに不正なキー（数字だけ、「基準値」「判定」を含む）はスキップ
                if mk.isdigit() or "基準値" in mk or "判定" in mk:
                    logger.info(f"不正キーをスキップ: [{group.get('機器')}] {mk}")
                    continue

                # 既に同じマッピングが存在するかチェック（applied は履歴なので対象外）
                existing = await db.key_mappings.find_one({
                    "group.機器": group["機器"],
                    "group.機器部品": group["機器部品"],
                    "group.測定物理量": group["測定物理量"],
                    "variant_key": mk,
                    "status": {"$in": ["pending", "approved", "rejected"]},
                })
                if existing:
                    continue  # 既にこのステータスで登録済み

                new_samples.append(s)

            if not new_samples:
                continue

            first = new_samples[0]
            minority_image = first.get("image_path")
            minority_measurements = first.get("measurements")
            if not minority_image:
                continue

            new_keys = [s["key"] for s in new_samples]

            # AIバッチ判定（画像2枚 + 構造化データ + レコード内の全少数派キー）
            ai_results = await ai_judge_key_mappings_batch(
                minority_keys=new_keys,
                majority_keys=majority_keys,
                minority_image_path=minority_image,
                majority_image_path=majority_image,
                minority_measurements=minority_measurements,
                majority_measurements=majority_measurements,
            )

            # 結果をDBに保存（キーごとに1レコード）
            now = datetime.utcnow()
            for s in new_samples:
                mk = s["key"]
                ai_result = ai_results.get(mk, {})
                mapping_doc = {
                    "group": group,
                    "canonical_key": ai_result.get("matched_key"),
                    "variant_key": mk,
                    "ai_confidence": ai_result.get("confidence", 0.0),
                    "ai_reasoning": ai_result.get("reasoning", ""),
                    "canonical_page_id": majority_page_id,
                    "variant_page_id": page_id,
                    "status": "pending",
                    "created_at": now,
                    "updated_at": now,
                }
                await db.key_mappings.insert_one(mapping_doc)
                mappings_created += 1

                logger.info(
                    f"突合マッピング生成: [{group['機器']}] "
                    f"{mk} → {ai_result.get('matched_key')} "
                    f"(confidence: {ai_result.get('confidence', 0)})"
                )

    return {
        "groups_found": len(groups),
        "mappings_created": mappings_created,
        "stale_deleted": stale_deleted,
    }


async def _cleanup_stale_mappings(db, tenant: str = "default") -> int:
    """
    もう該当レコードが存在しないマッピングを削除する。

    例: 手動で構造化データの variant_key を修正すると、
    key_mappings には残るが pages に該当キーがなくなる。
    そういった「用済みマッピング」を掃除する。

    applied ステータスのものは履歴として保持するため対象外。
    """
    # pending/approved/rejected のマッピングだけ対象
    mappings = await db.key_mappings.find(
        {"status": {"$in": ["pending", "approved", "rejected"]}}
    ).to_list(length=None)

    deleted = 0
    for m in mappings:
        group = m.get("group", {})
        variant_key = m.get("variant_key")
        if not variant_key:
            continue

        # このグループで variant_key を含むレコードが残っているか
        query = {
            "tenant": tenant,
            "data.機器": group.get("機器"),
            "data.機器部品": group.get("機器部品"),
            "data.測定物理量": group.get("測定物理量"),
            f"data.測定値.{variant_key}": {"$exists": True},
        }
        exists = await db.pages.find_one(query)

        if not exists:
            await db.key_mappings.delete_one({"_id": m["_id"]})
            deleted += 1
            logger.info(f"古いマッピング削除: [{group.get('機器')}] {variant_key}")

    return deleted


# =============================================================================
# 適用: 承認済みマッピングをDBに反映
# =============================================================================

async def apply_approved_mappings(db, tenant: str = "default") -> dict:
    """
    承認済みの key_mappings を pages コレクションに適用する。

    測定値・基準値のキーを canonical_key に書き換える。

    Returns:
        {"records_updated": int}
    """
    # 承認済みマッピングを取得
    approved = await db.key_mappings.find({"status": "approved"}).to_list(length=None)

    if not approved:
        return {"records_updated": 0}

    records_updated = 0

    for mapping in approved:
        group = mapping["group"]
        variant_key = mapping["variant_key"]
        canonical_key = mapping.get("canonical_key")

        if not canonical_key:
            continue

        # 対象レコードを検索
        query = {
            "tenant": tenant,
            "data.機器": group["機器"],
            "data.機器部品": group["機器部品"],
            "data.測定物理量": group["測定物理量"],
            f"data.測定値.{variant_key}": {"$exists": True},
        }

        cursor = db.pages.find(query)
        async for doc in cursor:
            data = doc.get("data", {})
            measurements = data.get("測定値", {})
            references = data.get("基準値", {})
            updated = False

            # 測定値のキー書き換え
            if variant_key in measurements:
                measurements[canonical_key] = measurements.pop(variant_key)
                updated = True

            # 基準値のキー書き換え（あれば）
            if variant_key in references:
                references[canonical_key] = references.pop(variant_key)
                updated = True

            if updated:
                await db.pages.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {
                        "data.測定値": measurements,
                        "data.基準値": references,
                    }}
                )
                records_updated += 1

        # 適用完了したマッピングのステータスを "applied" に変更
        # これにより次回のレポートからは除外される（自動適用は継続）
        await db.key_mappings.update_one(
            {"_id": mapping["_id"]},
            {"$set": {"status": "applied", "applied_at": datetime.utcnow()}}
        )

    return {"records_updated": records_updated}


# =============================================================================
# パイプライン用: 新規レコードに承認済みマッピングを自動適用
# =============================================================================

# 承認済みマッピングのメモリキャッシュ（毎回DBクエリを避ける）
_mapping_cache = {"loaded": False, "mappings": []}


async def _load_mapping_cache(db):
    """承認済み & 適用済みマッピングをキャッシュに読み込む"""
    if not _mapping_cache["loaded"]:
        approved = await db.key_mappings.find(
            {"status": {"$in": ["approved", "applied"]}}
        ).to_list(length=None)
        _mapping_cache["mappings"] = approved
        _mapping_cache["loaded"] = True
    return _mapping_cache["mappings"]


def invalidate_mapping_cache():
    """キャッシュを無効化（マッピング承認時に呼ぶ）"""
    _mapping_cache["loaded"] = False
    _mapping_cache["mappings"] = []


async def apply_key_mappings(record_data: dict, db) -> dict:
    """
    新規レコードの測定値キーに、承認済みマッピングを自動適用する。

    pipeline/__init__.py の run_pipeline() から呼ばれる。

    Args:
        record_data: 正規化済みの構造化データ
        db: MongoDBデータベースインスタンス

    Returns:
        マッピング適用済みのデータ（新しいdictを返す）
    """
    result = dict(record_data)
    # AIがnullを返す場合に備えて、dict以外は空dict扱い
    measurements = result.get("測定値") or {}
    references = result.get("基準値") or {}
    if not isinstance(measurements, dict):
        measurements = {}
    if not isinstance(references, dict):
        references = {}

    if not measurements:
        return result

    # このレコードのグループ情報
    kiki = result.get("機器")
    buhin = result.get("機器部品")
    butsuryo = result.get("測定物理量")

    # 承認済みマッピングを取得（キャッシュ使用）
    mappings = await _load_mapping_cache(db)

    # このグループに該当するマッピングを適用
    new_measurements = dict(measurements)
    new_references = dict(references)

    for mapping in mappings:
        group = mapping.get("group", {})
        if (group.get("機器") == kiki and
                group.get("機器部品") == buhin and
                group.get("測定物理量") == butsuryo):

            variant = mapping.get("variant_key")
            canonical = mapping.get("canonical_key")
            if variant and canonical and variant in new_measurements:
                new_measurements[canonical] = new_measurements.pop(variant)
            if variant and canonical and variant in new_references:
                new_references[canonical] = new_references.pop(variant)

    result["測定値"] = new_measurements
    result["基準値"] = new_references
    return result
