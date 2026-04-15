# =============================================================================
# api/tests/test_normalize_pipeline.py - 正規化パイプラインのテスト
# =============================================================================
#
# 【このファイルの役割】
# ダミーデータを使って、正規化パイプライン（ルールベース + AI辞書紐付け）が
# 正しく動くかを確認するテストスクリプト。
#
# 【実行方法】
# api/ ディレクトリで実行:
#   python tests/test_normalize_pipeline.py
#
# 【テスト方針】
# - MongoDBやAI（LiteLLM）には接続しない
# - 辞書（normalization_dict）をメモリ上で模擬する
# - _ai_match をモックして「AIがこう返した場合」のシナリオをテストする
# - 新スキーマ（点検タイトル / 機器 / 機器部品 / 測定物理量）で検証
#
# =============================================================================

import asyncio
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# __init__.py 経由だと normalize_ai → openai のimportが走るため直接読み込む
import importlib.util

_rules_path = os.path.join(os.path.dirname(__file__), "..", "services", "pipeline", "normalize_rules.py")
_spec = importlib.util.spec_from_file_location("normalize_rules", _rules_path)
_rules_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rules_mod)

normalize_text = _rules_mod.normalize_text
normalize_by_rules = _rules_mod.normalize_by_rules


# =========================================================================
# テスト1: normalize_text（個別の文字列正規化）
# フィールド非依存なので新スキーマでもそのまま使える
# =========================================================================

def test_normalize_text_fullwidth_alphanumeric():
    """全角英数字が半角に変換されるか"""
    assert normalize_text("２号機微粉炭機Ｄ") == "2号機微粉炭機D"
    assert normalize_text("Ｎｏ．１高圧ポンプＡ") == "No.1高圧ポンプA"
    assert normalize_text("３号ガスタービン") == "3号ガスタービン"
    assert normalize_text("主蒸気止弁Ｖ１０１") == "主蒸気止弁V101"
    assert normalize_text("Ｂ２号ボイラー") == "B2号ボイラー"


def test_normalize_text_halfwidth_kana():
    """半角カタカナが全角に変換されるか"""
    assert normalize_text("ｲﾝﾍﾟﾗ") == "インペラ"
    assert normalize_text("ﾍﾞｱﾘﾝｸﾞ") == "ベアリング"
    assert normalize_text("ｼｰﾙﾘﾝｸﾞ") == "シールリング"
    assert normalize_text("ﾀｰﾋﾞﾝﾛｰﾀ") == "タービンロータ"
    assert normalize_text("ﾌﾗﾝｼﾞ") == "フランジ"


def test_normalize_text_circled_numbers():
    """丸数字が通常数字に変換されるか"""
    assert normalize_text("タイヤ①") == "タイヤ1"
    assert normalize_text("軸受②") == "軸受2"
    assert normalize_text("A点③") == "A点3"
    assert normalize_text("計測点④⑤") == "計測点45"


def test_normalize_text_whitespace():
    """余分な空白が正規化されるか"""
    assert normalize_text("  2号機　微粉炭機  D  ") == "2号機 微粉炭機 D"
    assert normalize_text("高圧\tポンプA") == "高圧 ポンプA"
    assert normalize_text("外径     内径") == "外径 内径"


def test_normalize_text_middle_dot():
    """中点（・）が統一されるか"""
    assert normalize_text("摩耗量･タイヤ1") == "摩耗量・タイヤ1"
    assert normalize_text("摩耗量·タイヤ1") == "摩耗量・タイヤ1"
    assert normalize_text("振動値･A点·上") == "振動値・A点・上"


def test_normalize_text_none_and_empty():
    """None や空文字はそのまま返るか"""
    assert normalize_text(None) is None
    assert normalize_text("") == ""
    assert normalize_text(123) == 123


def test_normalize_text_combined_patterns():
    """複数パターンが同時に出現するケース"""
    assert normalize_text("Ｎｏ．２ﾎﾟﾝﾌﾟ･軸受①") == "No.2ポンプ・軸受1"
    assert normalize_text("Ｂ１号ﾎﾞｲﾗｰ　過熱器③") == "B1号ボイラー 過熱器3"


# =========================================================================
# テスト2: normalize_by_rules（新スキーマの構造化データ正規化）
# 新フィールド: 点検タイトル / 機器 / 機器部品 / 測定物理量
# =========================================================================

# 新スキーマ準拠のダミーデータ
DUMMY_RECORDS = {
    "微粉炭機_全角": {
        "点検タイトル": "微粉炭機定期点検記録",
        "機器": "２号機微粉炭機Ｄ",
        "機器部品": "ｲﾝﾍﾟﾗ・外周部",
        "測定物理量": "摩耗量",
        "測定値": {"ﾀｲﾔ①": 0.18, "ﾀｲﾔ②": 0.22},
        "基準値": {"摩耗量": "≦0.50"},
    },
    "微粉炭機_半角": {
        "点検タイトル": "微粉炭機定期点検記録",
        "機器": "2号機微粉炭機D",
        "機器部品": "インペラ・外周部",
        "測定物理量": "摩耗量",
        "測定値": {"タイヤ1": 0.25, "タイヤ2": 0.30},
        "基準値": {"摩耗量": "≦0.50"},
    },
    "ポンプ": {
        "点検タイトル": "高圧ポンプ月次点検",
        "機器": "Ｎｏ．１高圧ポンプＡ",
        "機器部品": "ﾍﾞｱﾘﾝｸﾞ・ドライブ側",
        "測定物理量": "振動値",
        "測定値": {"A点": 0.12, "B点": 0.15},
        "基準値": {"振動値": "≦0.30"},
    },
    "タービン": {
        "点検タイトル": "ガスタービン年次点検",
        "機器": "３号ｶﾞｽﾀｰﾋﾞﾝ",
        "機器部品": "ﾀｰﾋﾞﾝﾛｰﾀ・第①段",
        "測定物理量": "肉厚",
        "測定値": {"ﾌﾞﾚｰﾄﾞ①": 2.35, "ﾌﾞﾚｰﾄﾞ②": 2.41},
        "基準値": {"肉厚": "≧2.00"},
    },
    "バルブ": {
        "点検タイトル": "主蒸気止弁開放点検",
        "機器": "主蒸気止弁Ｖ１０１",
        "機器部品": "弁体・ｼｰﾄ面",
        "測定物理量": "当たり幅",
        "測定値": {"A": 3.2, "B": 3.5},
        "基準値": {"当たり幅": "2.5~4.0"},
    },
    "フィールド欠損": {
        "点検タイトル": None,
        "機器": None,
        "機器部品": "",
        "測定物理量": "摩耗量",
        "測定値": {"外径": 0.10},
        "基準値": {},
    },
}


def test_normalize_by_rules_pump():
    """ポンプデータの正規化"""
    result = normalize_by_rules(DUMMY_RECORDS["ポンプ"])

    assert result["機器"] == "No.1高圧ポンプA"
    assert result["機器部品"] == "ベアリング・ドライブ側"
    assert result["測定物理量"] == "振動値"
    assert "A点" in result["測定値"]
    assert "B点" in result["測定値"]


def test_normalize_by_rules_turbine():
    """タービンデータの正規化"""
    result = normalize_by_rules(DUMMY_RECORDS["タービン"])

    assert result["機器"] == "3号ガスタービン"
    assert result["機器部品"] == "タービンロータ・第1段"
    assert result["測定物理量"] == "肉厚"
    assert "ブレード1" in result["測定値"]
    assert "ブレード2" in result["測定値"]


def test_normalize_by_rules_valve():
    """バルブデータの正規化"""
    result = normalize_by_rules(DUMMY_RECORDS["バルブ"])

    assert result["機器"] == "主蒸気止弁V101"
    assert result["機器部品"] == "弁体・シート面"
    assert result["測定物理量"] == "当たり幅"
    assert "A" in result["測定値"]
    assert "B" in result["測定値"]


def test_normalize_by_rules_missing_fields():
    """None・空文字のフィールドがエラーにならないか"""
    result = normalize_by_rules(DUMMY_RECORDS["フィールド欠損"])

    assert result["機器"] is None
    assert result["機器部品"] == ""
    assert result["測定物理量"] == "摩耗量"
    assert result["基準値"] == {}


def test_normalize_by_rules_same_equipment_different_encoding():
    """異なるエンコーディングの同一機器が正規化後に一致するか"""
    result1 = normalize_by_rules(DUMMY_RECORDS["微粉炭機_全角"])
    result2 = normalize_by_rules(DUMMY_RECORDS["微粉炭機_半角"])

    assert result1["機器"] == result2["機器"]
    assert result1["機器部品"] == result2["機器部品"]
    assert result1["測定物理量"] == result2["測定物理量"]


def test_normalize_by_rules_immutability():
    """元データが変更されないか（全機器タイプで検証）"""
    for name, record in DUMMY_RECORDS.items():
        original_kiki = record.get("機器")
        normalize_by_rules(record)
        assert record.get("機器") == original_kiki, f"{name}: 元データが書き換わった"


# =========================================================================
# テスト3: AI辞書紐付け（DBモック + _ai_matchモック）
# =========================================================================

class MockCollection:
    """MongoDBコレクションのモック"""
    def __init__(self, initial_docs=None):
        self.docs = list(initial_docs) if initial_docs else []

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    def find(self, query, projection=None):
        results = [d for d in self.docs if self._matches(d, query)]
        return MockCursor(results, projection)

    async def insert_one(self, doc):
        self.docs.append(dict(doc))

    async def update_one(self, filter_query, update):
        for doc in self.docs:
            if self._matches(doc, filter_query):
                if "$addToSet" in update:
                    for key, val in update["$addToSet"].items():
                        if key not in doc:
                            doc[key] = []
                        if val not in doc[key]:
                            doc[key].append(val)
                if "$set" in update:
                    for key, val in update["$set"].items():
                        doc[key] = val
                break

    def _matches(self, doc, query):
        for key, val in query.items():
            if key == "$or":
                if not any(self._matches(doc, cond) for cond in val):
                    return False
            elif isinstance(val, dict):
                continue
            else:
                if doc.get(key) != val:
                    if key == "variants" and val in doc.get("variants", []):
                        continue
                    return False
        return True


class MockCursor:
    def __init__(self, results, projection=None):
        self.results = results
        self.projection = projection

    async def to_list(self, length=None):
        if self.projection:
            return [
                {k: d[k] for k in self.projection if k in d and self.projection[k]}
                for d in self.results
            ]
        return self.results


class MockDB:
    def __init__(self, dict_docs=None):
        self.normalization_dict = MockCollection(dict_docs)


# --- 辞書テストのヘルパー ---

# 新スキーマの正規化対象フィールド（ヘルパー関数でデフォルト値として使用）
NORMALIZATION_FIELDS = ["点検タイトル", "機器", "機器部品", "測定物理量"]


def print_db_state(db, label="辞書状態"):
    """辞書の中身を見やすく出力"""
    print(f"\n  【{label}】 {len(db.normalization_dict.docs)}件")
    for doc in db.normalization_dict.docs:
        variants = doc.get("variants", [])
        v_str = f" variants={variants}" if variants else ""
        print(f"    [{doc['field']}] 「{doc['canonical']}」{v_str}")


def print_normalize_result(record, result, fields=None):
    """正規化前後を見やすく出力"""
    fields = fields or NORMALIZATION_FIELDS
    print("\n  【正規化結果】")
    for f in fields:
        if f not in record:
            continue
        before = record[f]
        after = result[f]
        if before != after:
            print(f"    {f}: 「{before}」 → 「{after}」 ★置換")
        else:
            print(f"    {f}: 「{before}」 (変更なし)")


def _empty_record():
    """正規化対象フィールドだけの空レコードを作るヘルパー"""
    return {f: "" for f in NORMALIZATION_FIELDS}


# =========================================================================
# 3-A: 辞書の完全一致ルックアップ（AI呼び出しなし）
# =========================================================================

async def test_dict_lookup_variant_to_canonical():
    """
    variants にある表記 → canonical に置換される。
    AI呼び出しは不要（辞書だけで解決）。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "2号機微粉炭機D",
         "variants": ["No.2微粉炭機D", "No2微粉炭機D", "二号微粉炭機D"]},
        {"field": "機器", "canonical": "No.1高圧ポンプA",
         "variants": ["1号高圧ポンプA", "#1高圧ポンプA"]},
        {"field": "機器部品", "canonical": "インペラ",
         "variants": ["impeller"]},
        {"field": "機器部品", "canonical": "ベアリング",
         "variants": ["bearing", "軸受"]},
        {"field": "測定物理量", "canonical": "摩耗量",
         "variants": ["wear", "wear_amount"]},
    ])

    print("\n  --- ケース1: 微粉炭機の表記ゆれ ---")
    record1 = _empty_record()
    record1.update({"機器": "No.2微粉炭機D", "機器部品": "インペラ"})
    result1 = await normalize_by_ai(record1, db)
    print_normalize_result(record1, result1, ["機器", "機器部品"])
    assert result1["機器"] == "2号機微粉炭機D"

    print("\n  --- ケース2: ポンプの表記ゆれ ---")
    record2 = _empty_record()
    record2.update({"機器": "1号高圧ポンプA", "機器部品": "bearing"})
    result2 = await normalize_by_ai(record2, db)
    print_normalize_result(record2, result2, ["機器", "機器部品"])
    assert result2["機器"] == "No.1高圧ポンプA"
    assert result2["機器部品"] == "ベアリング"

    print("\n  --- ケース3: 測定物理量の表記ゆれ（英→日） ---")
    record3 = _empty_record()
    record3["測定物理量"] = "wear"
    result3 = await normalize_by_ai(record3, db)
    print_normalize_result(record3, result3, ["測定物理量"])
    assert result3["測定物理量"] == "摩耗量"

    print_db_state(db)
    print("\n  [PASS] variants → canonical 置換OK（3パターン）")


async def test_dict_lookup_canonical_as_is():
    """canonical そのものが入力された場合、変更なし"""
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "3号ガスタービン", "variants": []},
        {"field": "機器部品", "canonical": "タービンロータ", "variants": []},
        {"field": "測定物理量", "canonical": "肉厚", "variants": []},
    ])

    record = _empty_record()
    record.update({
        "機器": "3号ガスタービン",
        "機器部品": "タービンロータ",
        "測定物理量": "肉厚",
    })
    result = await normalize_by_ai(record, db)
    print_normalize_result(record, result, ["機器", "機器部品", "測定物理量"])

    assert result["機器"] == "3号ガスタービン"
    assert result["機器部品"] == "タービンロータ"
    assert result["測定物理量"] == "肉厚"
    print("\n  [PASS] canonical そのものは変更なしOK")


# =========================================================================
# 3-B: 辞書が空 / 候補なし → 新規登録
# =========================================================================

async def test_empty_dict_registers_all():
    """辞書が空のとき、全フィールドが新規 canonical として登録される"""
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[])

    record = {
        "点検タイトル": "主蒸気止弁開放点検",
        "機器": "主蒸気止弁V101",
        "機器部品": "弁体・シート面",
        "測定物理量": "当たり幅",
    }
    result = await normalize_by_ai(record, db)
    print_normalize_result(record, result)
    print_db_state(db, "辞書（登録後）")

    # 値は変わらず、辞書に4件登録される
    assert result["機器"] == "主蒸気止弁V101"
    assert result["測定物理量"] == "当たり幅"
    assert len(db.normalization_dict.docs) == 4
    print("\n  [PASS] 空辞書: 全フィールド新規登録OK")


# =========================================================================
# 3-C: AI判定パス（_ai_match をモックしてテスト）
# =========================================================================

async def test_ai_match_same_equipment_different_notation():
    """
    AIが「同じ機器」と判定 → variants に追加 & canonical に置換。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "2号機微粉炭機D", "variants": []},
    ])

    record = _empty_record()
    record["機器"] = "No.2微粉炭機D"

    mock_response = {"matched": True, "canonical": "2号機微粉炭機D", "confidence": 0.95}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器"])
    print_db_state(db, "辞書（AI判定後）")

    assert result["機器"] == "2号機微粉炭機D"
    kiki_doc = next(d for d in db.normalization_dict.docs if d["field"] == "機器")
    assert "No.2微粉炭機D" in kiki_doc["variants"]
    print("\n  [PASS] AI判定: 号機表記違い → 同一判定 → variant追加OK")


async def test_ai_match_abbreviation():
    """
    AIが「同じ部品」と判定 → 略称の統一（BRG → ベアリング）。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器部品", "canonical": "ベアリング", "variants": []},
    ])

    record = _empty_record()
    record["機器部品"] = "BRG"

    mock_response = {"matched": True, "canonical": "ベアリング", "confidence": 0.90}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器部品"])

    assert result["機器部品"] == "ベアリング"
    print("\n  [PASS] AI判定: 英略称 → カタカナ統一OK")


async def test_ai_match_different_number_should_not_match():
    """
    AIが「別物」と判定 → 新規 canonical として登録。
    例: 高圧ポンプA ≠ 高圧ポンプB
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "高圧ポンプA", "variants": []},
    ])

    record = _empty_record()
    record["機器"] = "高圧ポンプB"

    mock_response = {"matched": False}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器"])
    print_db_state(db, "辞書（AI判定後）")

    assert result["機器"] == "高圧ポンプB"
    canonicals = [d["canonical"] for d in db.normalization_dict.docs if d["field"] == "機器"]
    assert "高圧ポンプA" in canonicals
    assert "高圧ポンプB" in canonicals
    print("\n  [PASS] AI判定: 番号違い → 別物として新規登録OK")


async def test_ai_match_different_physical_quantity():
    """
    AIが「別物」と判定 → 物理量の違い。
    例: 摩耗量 ≠ 振動値（単位も意味も違う）
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "測定物理量", "canonical": "摩耗量", "variants": []},
    ])

    record = _empty_record()
    record["測定物理量"] = "振動値"

    mock_response = {"matched": False}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["測定物理量"])

    assert result["測定物理量"] == "振動値"
    canonicals = [d["canonical"] for d in db.normalization_dict.docs if d["field"] == "測定物理量"]
    assert "摩耗量" in canonicals
    assert "振動値" in canonicals
    print("\n  [PASS] AI判定: 物理量違い → 別物として新規登録OK")


async def test_ai_match_low_confidence_treated_as_new():
    """
    AIが「同一」と返しても confidence が閾値未満 → 新規扱い。

    【なぜ _ai_match ではなく _get_client をモックするか】
    confidence閾値チェックは _ai_match の内部で行われる。
    _ai_match 自体をモックするとそのチェックもバイパスされるため、
    OpenAIクライアント（LLMの応答）をモックする必要がある。
    """
    from services.pipeline.normalize_ai import normalize_by_ai
    import json

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "給水ポンプ", "variants": []},
    ])

    record = _empty_record()
    record["機器"] = "循環水ポンプ"

    mock_message = type("Msg", (), {
        "content": json.dumps({"matched": True, "canonical": "給水ポンプ", "confidence": 0.6})
    })()
    mock_choice = type("Choice", (), {"message": mock_message})()
    mock_completion = type("Completion", (), {"choices": [mock_choice]})()

    mock_client = type("MockClient", (), {
        "chat": type("Chat", (), {
            "completions": type("Completions", (), {
                "create": staticmethod(lambda **kwargs: mock_completion)
            })()
        })()
    })()

    with patch("services.pipeline.normalize_ai._get_client", return_value=mock_client):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器"])
    print_db_state(db, "辞書（低confidence判定後）")

    assert result["機器"] == "循環水ポンプ"
    canonicals = [d["canonical"] for d in db.normalization_dict.docs if d["field"] == "機器"]
    assert "給水ポンプ" in canonicals
    assert "循環水ポンプ" in canonicals
    print("\n  [PASS] 低confidence: 誤統合を防止OK")


async def test_ai_match_invalid_canonical_response():
    """
    AIが辞書に存在しない canonical を返した場合 → 新規扱い（ハルシネーション対策）。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "高圧ポンプA", "variants": []},
    ])

    record = _empty_record()
    record["機器"] = "HP Pump A"

    mock_response = {"matched": True, "canonical": "高圧ポンプ1号", "confidence": 0.85}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器"])
    print_db_state(db, "辞書（不正応答後）")

    assert result["機器"] == "HP Pump A"
    print("\n  [PASS] AI不正応答: ハルシネーション防止OK")


async def test_ai_match_error_fallback():
    """
    AI呼び出しでエラー発生 → 新規扱い（データは失われない）。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "ボイラー1号", "variants": []},
    ])

    record = _empty_record()
    record["機器"] = "B1号ボイラー"

    mock_client = type("MockClient", (), {
        "chat": type("Chat", (), {
            "completions": type("Completions", (), {
                "create": staticmethod(lambda **kwargs: (_ for _ in ()).throw(Exception("API timeout")))
            })()
        })()
    })()

    with patch("services.pipeline.normalize_ai._get_client", return_value=mock_client):
        result = await normalize_by_ai(record, db)

    print_normalize_result(record, result, ["機器"])

    assert result["機器"] == "B1号ボイラー"
    canonicals = [d["canonical"] for d in db.normalization_dict.docs if d["field"] == "機器"]
    assert "B1号ボイラー" in canonicals
    print("\n  [PASS] AIエラー時: データ保持 & 新規登録OK")


# =========================================================================
# 3-D: 複数レコードの連続処理（辞書が育つ過程）
# =========================================================================

async def test_dict_grows_over_multiple_records():
    """
    レコードを順に処理すると辞書が育ち、後のレコードで活用される。

    1件目: 「高圧ポンプA」 → 辞書に新規登録
    2件目: 「HP-A ポンプ」 → AI判定で同一 → variants追加
    3件目: 「HP-A ポンプ」 → 辞書にvariantとして存在 → AI不要で即解決
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[])

    # 1件目
    r1 = _empty_record()
    r1["機器"] = "高圧ポンプA"
    result1 = await normalize_by_ai(r1, db)
    print("\n  --- 1件目 ---")
    print_normalize_result(r1, result1, ["機器"])
    print_db_state(db, "1件目処理後")
    assert result1["機器"] == "高圧ポンプA"
    assert len(db.normalization_dict.docs) == 1

    # 2件目
    r2 = _empty_record()
    r2["機器"] = "HP-A ポンプ"
    mock_response = {"matched": True, "canonical": "高圧ポンプA", "confidence": 0.92}
    with patch("services.pipeline.normalize_ai._ai_match", return_value=mock_response):
        result2 = await normalize_by_ai(r2, db)
    print("\n  --- 2件目 ---")
    print_normalize_result(r2, result2, ["機器"])
    print_db_state(db, "2件目処理後")
    assert result2["機器"] == "高圧ポンプA"

    # 3件目: variants にヒット → AI不要
    r3 = _empty_record()
    r3["機器"] = "HP-A ポンプ"
    with patch("services.pipeline.normalize_ai._ai_match") as mock_ai:
        result3 = await normalize_by_ai(r3, db)
        assert not mock_ai.called, "辞書にvariantがあるのにAIが呼ばれた"
    print("\n  --- 3件目 ---")
    print_normalize_result(r3, result3, ["機器"])
    assert result3["機器"] == "高圧ポンプA"

    print("\n  [PASS] 辞書成長: 1件目新規 → 2件目AI判定 → 3件目辞書即解決OK")


# =========================================================================
# テスト4: パイプライン統合テスト（ルールベース → AI辞書）
# =========================================================================

async def test_full_pipeline_pump():
    """ポンプデータのパイプライン統合テスト"""
    from services.pipeline import run_pipeline

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "No.1高圧ポンプA", "variants": []},
        {"field": "機器部品", "canonical": "ベアリング・ドライブ側", "variants": []},
        {"field": "測定物理量", "canonical": "振動値", "variants": []},
    ])

    record = DUMMY_RECORDS["ポンプ"]

    print("\n  【入力】")
    for f in NORMALIZATION_FIELDS:
        print(f"    {f}: 「{record.get(f, '')}」")

    rules_result = normalize_by_rules(record)
    print("\n  【Step1: ルールベース後】")
    for f in NORMALIZATION_FIELDS:
        print(f"    {f}: 「{rules_result.get(f, '')}」")

    result = await run_pipeline(record, db)
    print("\n  【Step2: パイプライン最終結果】")
    for f in NORMALIZATION_FIELDS:
        print(f"    {f}: 「{result.get(f, '')}」")

    assert result["機器"] == "No.1高圧ポンプA"
    assert result["機器部品"] == "ベアリング・ドライブ側"
    assert result["測定物理量"] == "振動値"
    print("\n  [PASS] ポンプ: パイプライン統合OK")


async def test_full_pipeline_turbine():
    """タービンデータのパイプライン統合テスト"""
    from services.pipeline import run_pipeline

    db = MockDB(dict_docs=[
        {"field": "機器", "canonical": "3号ガスタービン", "variants": []},
    ])

    record = DUMMY_RECORDS["タービン"]
    result = await run_pipeline(record, db)

    print("\n  【結果】")
    for f in ["機器", "機器部品", "測定物理量"]:
        print(f"    {f}: 「{record.get(f, '')}」 → 「{result.get(f, '')}」")
    print(f"    測定値キー: {list(result['測定値'].keys())}")

    assert result["機器"] == "3号ガスタービン"
    assert result["機器部品"] == "タービンロータ・第1段"
    assert result["測定物理量"] == "肉厚"
    assert "ブレード1" in result["測定値"]
    print("\n  [PASS] タービン: パイプライン統合OK")


# =========================================================================
# テスト実行
# =========================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("正規化パイプライン テスト（新スキーマ）")
    print("=" * 70)

    # --- 1. ルールベース: normalize_text ---
    print("\n[1] normalize_text テスト")
    test_normalize_text_fullwidth_alphanumeric()
    print("  [PASS] 全角→半角変換OK（ポンプ,タービン,バルブ,ボイラー含む）")
    test_normalize_text_halfwidth_kana()
    print("  [PASS] 半角カナ→全角変換OK（ベアリング,シールリング等）")
    test_normalize_text_circled_numbers()
    print("  [PASS] 丸数字→通常数字変換OK")
    test_normalize_text_whitespace()
    print("  [PASS] 空白正規化OK")
    test_normalize_text_middle_dot()
    print("  [PASS] 中点統一OK（多段パス含む）")
    test_normalize_text_none_and_empty()
    print("  [PASS] None/空文字/非文字列のハンドリングOK")
    test_normalize_text_combined_patterns()
    print("  [PASS] 複合パターンOK")

    # --- 2. ルールベース: normalize_by_rules ---
    print("\n[2] normalize_by_rules テスト（多様な機器タイプ）")
    test_normalize_by_rules_pump()
    print("  [PASS] ポンプ: 正規化OK")
    test_normalize_by_rules_turbine()
    print("  [PASS] タービン: 正規化OK")
    test_normalize_by_rules_valve()
    print("  [PASS] バルブ: 正規化OK")
    test_normalize_by_rules_missing_fields()
    print("  [PASS] フィールド欠損: エラーなしOK")
    test_normalize_by_rules_same_equipment_different_encoding()
    print("  [PASS] 同一機器の異エンコーディング: 正規化後一致OK")
    test_normalize_by_rules_immutability()
    print("  [PASS] イミュータビリティ: 全機器タイプで元データ不変OK")

    # --- 3. AI辞書紐付け ---
    try:
        import openai  # noqa: F401
        has_openai = True
    except ImportError:
        has_openai = False

    if has_openai:
        print("\n[3] AI辞書紐付けテスト")

        print("\n  === 3-A: 辞書ルックアップ（AI不要） ===")
        asyncio.run(test_dict_lookup_variant_to_canonical())
        asyncio.run(test_dict_lookup_canonical_as_is())

        print("\n  === 3-B: 辞書が空 → 新規登録 ===")
        asyncio.run(test_empty_dict_registers_all())

        print("\n  === 3-C: AI判定パス（_ai_matchモック） ===")
        asyncio.run(test_ai_match_same_equipment_different_notation())
        asyncio.run(test_ai_match_abbreviation())
        asyncio.run(test_ai_match_different_number_should_not_match())
        asyncio.run(test_ai_match_different_physical_quantity())
        asyncio.run(test_ai_match_low_confidence_treated_as_new())
        asyncio.run(test_ai_match_invalid_canonical_response())
        asyncio.run(test_ai_match_error_fallback())

        print("\n  === 3-D: 辞書の成長テスト ===")
        asyncio.run(test_dict_grows_over_multiple_records())

        print("\n[4] パイプライン統合テスト")
        asyncio.run(test_full_pipeline_pump())
        asyncio.run(test_full_pipeline_turbine())
    else:
        print("\n[3] AI辞書紐付けテスト → SKIP（openai未インストール）")
        print("[4] パイプライン統合テスト → SKIP（openai未インストール）")

    print("\n" + "=" * 70)
    print("全テスト PASS!" if has_openai else "ルールベーステスト PASS!（AI辞書テストはスキップ）")
    print("=" * 70)
