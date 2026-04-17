# =============================================================================
# api/services/pipeline/normalize_rules.py - ルールベース正規化
# =============================================================================
#
# 【このファイルの役割】
# GPT-4oが出力した構造化データの表記ゆれを、ルールベースで統一する。
# AIやDBを使わず、純粋な文字列処理だけで行う。
#
# 【なぜ必要か】
# 同じ機器「2号機微粉炭機D」が、PDFによって「２号機微粉炭機Ｄ」（全角）や
# 「2号機微粉炭機D」（半角）で出力されることがある。
# これらを統一しないと、検索・グラフで別データとして扱われてしまう。
#
# 【NFKC正規化とは】
# Unicode正規化の一種。以下を自動変換する：
#   - 全角英数字 → 半角（Ｄ→D、２→2）
#   - 半角カナ → 全角（ｲﾝﾍﾟﾗ→インペラ）
#   - 丸数字 → 通常数字（①→1）
#   - その他の互換文字の統一
# Python標準ライブラリの unicodedata.normalize() で実行できる。
#
# =============================================================================

import re
import unicodedata


# 正規化対象フィールド（文字列値を正規化）
TEXT_FIELDS = ["点検タイトル", "機器", "機器部品", "測定物理量"]

# 正規化対象フィールド（dictのキーを正規化）
DICT_KEY_FIELDS = ["測定値", "基準値"]


# 丸数字（①〜⑳）の一覧。NFKCで通常数字に変換されるのを防ぐ。
# PDFでは①と1は異なる測定点を指すため、区別を維持する必要がある。
CIRCLED_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_CIRCLE_PLACEHOLDER = "__CIRCLE_{}_"


def normalize_text(text: str) -> str:
    """
    1つの文字列に対してルールベース正規化を適用する。

    【処理順序と理由】
    1. 丸数字を退避 → ①と1の区別を維持するため、NFKC変換から保護
    2. NFKC正規化 → 全角/半角・カナの統一（最も効果が大きい）
    3. 丸数字を復元
    4. 空白の正規化 → 余分なスペースを除去
    5. 中点の統一 → 測定値キーのパス区切り「・」を統一

    Args:
        text: 正規化する文字列

    Returns:
        正規化済みの文字列。None や空文字はそのまま返す。

    【この関数がないとどうなるか】
    「２号機微粉炭機Ｄ」と「2号機微粉炭機D」が別データとして扱われ、
    グラフが分かれてしまう。
    """
    if not text or not isinstance(text, str):
        return text

    # 1. 丸数字を退避（NFKCで①→1に変換されるのを防ぐ）
    #    PDFでは①と1は異なる測定点を指すため、区別を維持する
    for i, c in enumerate(CIRCLED_NUMBERS):
        text = text.replace(c, _CIRCLE_PLACEHOLDER.format(i))

    # 2. NFKC正規化
    #    全角英数→半角、半角カナ→全角 等（丸数字は退避済みなので変換されない）
    text = unicodedata.normalize("NFKC", text)

    # 3. 丸数字を復元
    for i, c in enumerate(CIRCLED_NUMBERS):
        text = text.replace(_CIRCLE_PLACEHOLDER.format(i), c)

    # 4. 連続する空白（全角スペース含む）を半角スペース1つに統一し、前後の空白を除去
    text = re.sub(r"\s+", " ", text).strip()

    # 5. 中点の統一
    #    半角中点(･)やMiddle Dot(·)を全角中点(・)に統一
    #    測定値キーのパス区切り「摩耗量・タイヤ1」で使われるため重要
    text = re.sub(r"[･·]", "・", text)

    return text


def normalize_by_rules(record_data: dict) -> dict:
    """
    構造化データ（record_data）全体にルールベース正規化を適用する。

    【処理対象】
    - 文字列フィールド: 機器、機器部品、計測箇所、点検項目 → 値を正規化
    - dictフィールド: 測定値、基準値 → キー名を正規化（値はそのまま）

    Args:
        record_data: GPT-4oが出力した1レコード分の構造化データ
            例: {"機器": "２号機微粉炭機Ｄ", "測定値": {"摩耗量・ﾀｲﾔ①": 0.18}, ...}

    Returns:
        正規化済みの新しいdict（元のdictは変更しない）
            例: {"機器": "2号機微粉炭機D", "測定値": {"摩耗量・タイヤ1": 0.18}, ...}

    【なぜ元のdictを変更しないか】
    元データ（data_raw）として保存するため。正規化で問題が起きた場合に
    元に戻せるようにしておく。
    """
    result = dict(record_data)

    # 文字列フィールドの正規化
    for field in TEXT_FIELDS:
        if field in result and isinstance(result[field], str):
            result[field] = normalize_text(result[field])

    # dictフィールドのキー正規化（値は触らない）
    for field in DICT_KEY_FIELDS:
        if field in result and isinstance(result[field], dict):
            result[field] = {
                normalize_text(k): v for k, v in result[field].items()
            }

    return result
