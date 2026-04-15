# =============================================================================
# api/services/pipeline/normalize_ai.py - AI自動マッチング
# =============================================================================
#
# 【このファイルの役割】
# ルールベース正規化（normalize_rules.py）では対応できない表記ゆれを、
# AIが自動判定して辞書に登録する。
# 例: 「No.2微粉炭機D」と「2号機微粉炭機D」は同じ機器だが、
#      文字列の変換ルールだけでは統一できない。
#
# 【辞書の仕組み】
# MongoDB の normalization_dict コレクションに「正規名（canonical）」と
# 「表記ゆれ（variants）」のペアを保存する。
# 初回登場時だけAIが判定し、2回目以降は辞書で即解決される。
#
# 【なぜ gpt-4o-mini か】
# 類似判定は軽いタスクなので、高性能モデルは不要。
# gpt-4o-mini はコストが安く応答も速い。
#
# =============================================================================

import json
from datetime import datetime

from openai import OpenAI

from core.config import get_settings
from core.logging import get_logger

logger = get_logger(__name__)
settings = get_settings()

# 正規化対象フィールド（normalize_rules.py の TEXT_FIELDS と同じ）
TEXT_FIELDS = ["機器", "機器部品", "計測箇所", "点検項目"]

# AI判定の確信度閾値
# これ以上なら同一と判定し辞書に追加。これ未満なら新規として登録。
# 低すぎると誤統合（別物を同じにしてしまう）のリスクがある。
# 高すぎると辞書が育たない。0.8は安全寄りの設定。
CONFIDENCE_THRESHOLD = 0.8

# AI判定に使用するモデル（LiteLLM経由）
AI_MODEL = "azure-gpt-4o"


def _get_client():
    """LiteLLM経由のOpenAIクライアントを取得"""
    return OpenAI(
        api_key=settings.litellm_api_key,
        base_url=f"{settings.litellm_url}/v1",
    )


async def _find_in_dict(db, field: str, value: str) -> str | None:
    """
    辞書から値を検索し、canonical（正規名）を返す。

    canonical または variants に完全一致すればヒット。
    見つからなければ None を返す。

    【なぜ完全一致か】
    部分一致だと「微粉炭機C」が「微粉炭機D」にマッチしてしまう。
    あいまいな判定はAIに任せる。
    """
    doc = await db.normalization_dict.find_one({
        "field": field,
        "$or": [
            {"canonical": value},
            {"variants": value},
        ]
    })
    if doc:
        return doc["canonical"]
    return None


async def _get_candidates(db, field: str) -> list[str]:
    """指定フィールドの全 canonical をリストで返す"""
    cursor = db.normalization_dict.find(
        {"field": field},
        {"canonical": 1, "_id": 0}
    )
    docs = await cursor.to_list(length=None)
    return [d["canonical"] for d in docs]


async def _register_canonical(db, field: str, value: str):
    """新しい canonical を辞書に登録する"""
    now = datetime.utcnow()
    await db.normalization_dict.insert_one({
        "field": field,
        "canonical": value,
        "variants": [],
        "created_at": now,
        "updated_at": now,
    })
    logger.info(f"辞書に新規登録: [{field}] {value}")


async def _add_variant(db, field: str, canonical: str, variant: str):
    """既存の canonical に variant（表記ゆれ）を追加する"""
    await db.normalization_dict.update_one(
        {"field": field, "canonical": canonical},
        {
            "$addToSet": {"variants": variant},
            "$set": {"updated_at": datetime.utcnow()},
        }
    )
    logger.info(f"辞書に表記ゆれ追加: [{field}] {variant} → {canonical}")


def _ai_match(value: str, candidates: list[str], field: str) -> dict:
    """
    AIに類似判定を依頼する。

    【プロンプト設計】
    - フィールド名を伝えることで、文脈を理解させる
    - 既存リストを渡して「どれと同じか」を聞く
    - JSON形式で応答させ、確信度（confidence）も返させる

    Args:
        value: 新しい表記（例: "No.2微粉炭機D"）
        candidates: 既存の正規名リスト（例: ["2号機微粉炭機D", "高圧ポンプA"]）
        field: フィールド名（例: "機器"）

    Returns:
        {"matched": True, "canonical": "2号機微粉炭機D", "confidence": 0.95}
        or {"matched": False}
    """
    client = _get_client()

    prompt = (
        f"発電所の点検記録における「{field}」フィールドの表記ゆれを判定してください。\n\n"
        f"新しい表記: 「{value}」\n"
        f"既存リスト: {candidates}\n\n"
        f"「新しい表記」が既存リストのどれかと同じものを指しているか判定してください。\n\n"
        f"【同一とみなすもの】\n"
        f"- 号機の表記違い: No.2 / 2号 / #2 / 二号\n"
        f"- 全角半角の違い: Ａ / A\n"
        f"- 英略称とカタカナ: BRG / ベアリング、IMP / インペラ\n"
        f"- 和名とカタカナ: 軸受 / ベアリング\n"
        f"- 語順の違い: 高圧ポンプA / HP-A ポンプ\n\n"
        f"【別物として扱うもの】\n"
        f"- 番号・記号が違う: 機器C ≠ 機器D、ポンプA ≠ ポンプB\n"
        f"- 計測位置が違う: A点 ≠ B点、ドライブ側 ≠ フリー側\n"
        f"- 名前が似ているが別の機器: 給水ポンプ ≠ 循環水ポンプ\n\n"
        f"JSON形式で回答してください:\n"
        f'- 同じものがある場合: {{"matched": true, "canonical": "既存リストの値", "confidence": 0.0-1.0}}\n'
        f'- 新規の場合: {{"matched": false}}\n'
    )

    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=200,
            temperature=0,  # 再現性を高めるため
        )
        result = json.loads(response.choices[0].message.content)

        # 確信度が閾値未満なら新規扱い（誤統合を防ぐ）
        if result.get("matched") and result.get("confidence", 0) < CONFIDENCE_THRESHOLD:
            logger.info(
                f"AI判定: [{field}] {value} → {result.get('canonical')} "
                f"(confidence={result.get('confidence')}, 閾値未満のため新規扱い)"
            )
            return {"matched": False}

        return result

    except Exception as e:
        # AI呼び出し失敗時は新規扱い（正規化できないだけでデータは保存される）
        logger.warning(f"AI判定エラー: [{field}] {value} - {e}")
        return {"matched": False}


async def normalize_by_ai(record_data: dict, db) -> dict:
    """
    AI自動マッチングによる正規化を適用する。

    【処理フロー】
    各テキストフィールド（機器、機器部品、計測箇所、点検項目）に対して:
    1. 辞書で完全一致検索 → ヒットすればcanonicalに置換（AI不要）
    2. 辞書にない → 既存candidatesを取得
    3. candidatesが空 → 新規canonicalとして登録
    4. candidatesがある → AIが類似判定
    5. AIが同一と判定 → variantsに追加 & canonicalに置換
    6. AIが新規と判定 → 新規canonicalとして登録

    Args:
        record_data: ルールベース正規化済みの構造化データ
        db: MongoDBデータベースインスタンス

    Returns:
        AI正規化済みの構造化データ
    """
    result = dict(record_data)

    for field in TEXT_FIELDS:
        value = result.get(field)
        if not value or not isinstance(value, str):
            continue

        # 1. 辞書から検索（AI不要、高速）
        canonical = await _find_in_dict(db, field, value)
        if canonical:
            result[field] = canonical
            continue

        # 2. 候補を取得
        candidates = await _get_candidates(db, field)

        if not candidates:
            # 3. 辞書が空 → 最初のエントリとして登録
            await _register_canonical(db, field, value)
            continue

        # 4. AIに類似判定を依頼
        match_result = _ai_match(value, candidates, field)

        if match_result.get("matched"):
            # 5. 同一と判定 → variantsに追加してcanonicalに置換
            matched_canonical = match_result["canonical"]
            # AI応答のcanonicalが実際にcandidatesに存在するか確認
            if matched_canonical in candidates:
                await _add_variant(db, field, matched_canonical, value)
                result[field] = matched_canonical
            else:
                # AIが存在しないcanonicalを返した場合は新規扱い
                logger.warning(
                    f"AIが不正なcanonicalを返却: [{field}] {matched_canonical}"
                )
                await _register_canonical(db, field, value)
        else:
            # 6. 新規 → 辞書に登録
            await _register_canonical(db, field, value)

    return result
