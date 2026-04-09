# =============================================================================
# api/services/pipeline/__init__.py - 正規化パイプライン
# =============================================================================
#
# 【このモジュールの役割】
# PDF構造化データをMongoDB保存前に正規化するパイプライン。
# 表記ゆれ（全角/半角、カナ表記の違い等）を統一し、
# 同じ機器・部品のデータが同一グラフで可視化できるようにする。
#
# 【なぜパイプライン構成か】
# 将来、ページ分類AI・表紙構造化・難易度判定AI等のステップを追加する予定。
# 各ステップを独立した関数にしておけば、1行追加するだけで拡張できる。
#
# =============================================================================

from services.pipeline.normalize_rules import normalize_by_rules
from services.pipeline.normalize_ai import normalize_by_ai


async def run_pipeline(record_data: dict, db=None) -> dict:
    """
    構造化データに正規化パイプラインを適用する。

    【処理順序】
    1. ルールベース正規化: 全角/半角、カナ、丸数字等を機械的に統一
    2. AI自動マッチング: ルールで対応できない表記ゆれをAIが辞書で統一

    Args:
        record_data: GPT-4oが出力した構造化データ（data フィールドの中身）
        db: MongoDBデータベースインスタンス（AI正規化の辞書参照・更新に使用）

    Returns:
        正規化済みの構造化データ（新しいdictを返す。元データは変更しない）
    """
    result = normalize_by_rules(record_data)

    if db is not None:
        result = await normalize_by_ai(result, db)

    return result
