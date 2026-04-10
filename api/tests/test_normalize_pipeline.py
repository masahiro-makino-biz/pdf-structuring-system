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
#   python -m pytest tests/test_normalize_pipeline.py -v
#
# または pytest なしで直接実行:
#   python tests/test_normalize_pipeline.py
#
# 【ポイント】
# - MongoDBやAI（LiteLLM）には接続しない
# - 辞書（normalization_dict）をメモリ上で模擬する
# - ルールベース正規化は実際のコードをそのまま使う
# - AI辞書紐付けはDBをモック（偽物）にして動作確認する
#
# =============================================================================

import asyncio
import sys
import os

# api/ をモジュール検索パスに追加（テスト実行時にimportできるようにする）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 【importの工夫】
# 通常の from services.pipeline.normalize_rules import ... だと、
# Pythonが __init__.py を自動実行 → normalize_ai.py → openai のimportが走る。
# openai がインストールされていない環境でもルールベーステストを動かすため、
# importlib で normalize_rules.py を直接読み込む。
import importlib.util

_rules_path = os.path.join(os.path.dirname(__file__), "..", "services", "pipeline", "normalize_rules.py")
_spec = importlib.util.spec_from_file_location("normalize_rules", _rules_path)
_rules_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_rules_mod)

normalize_text = _rules_mod.normalize_text
normalize_by_rules = _rules_mod.normalize_by_rules


# =========================================================================
# テスト1: normalize_text（個別の文字列正規化）
# =========================================================================

def test_normalize_text_fullwidth_to_halfwidth():
    """全角英数字が半角に変換されるか"""
    assert normalize_text("２号機微粉炭機Ｄ") == "2号機微粉炭機D"


def test_normalize_text_halfwidth_kana():
    """半角カタカナが全角に変換されるか"""
    assert normalize_text("ｲﾝﾍﾟﾗ") == "インペラ"


def test_normalize_text_circled_numbers():
    """丸数字が通常数字に変換されるか"""
    assert normalize_text("タイヤ①") == "タイヤ1"
    assert normalize_text("タイヤ②") == "タイヤ2"


def test_normalize_text_whitespace():
    """余分な空白が正規化されるか"""
    assert normalize_text("  2号機　微粉炭機  D  ") == "2号機 微粉炭機 D"


def test_normalize_text_middle_dot():
    """中点（・）が統一されるか"""
    # 半角中点（U+FF65）→ 全角中点
    assert normalize_text("摩耗量･タイヤ1") == "摩耗量・タイヤ1"
    # Middle Dot（U+00B7）→ 全角中点
    assert normalize_text("摩耗量·タイヤ1") == "摩耗量・タイヤ1"


def test_normalize_text_none_and_empty():
    """None や空文字はそのまま返るか"""
    assert normalize_text(None) is None
    assert normalize_text("") == ""


# =========================================================================
# テスト2: normalize_by_rules（構造化データ全体のルールベース正規化）
# =========================================================================

# ダミーデータ: GPT-4oが出力する構造化データを模擬
DUMMY_RECORDS = [
    {
        "機器": "２号機微粉炭機Ｄ",         # 全角英数
        "機器部品": "ｲﾝﾍﾟﾗ",               # 半角カタカナ
        "計測箇所": "タイヤ① 外径",          # 丸数字+余分な空白
        "点検項目": "定期点検",
        "点検年月日": "2024-03-15",
        "測定者": "田中",
        "測定値": {
            "摩耗量･ﾀｲﾔ①": 0.18,           # 半角中点+半角カナ+丸数字
            "摩耗量･ﾀｲﾔ②": 0.22,
        },
        "基準値": {
            "摩耗量･ﾀｲﾔ①": "≦0.50",
            "摩耗量･ﾀｲﾔ②": "≦0.50",
        },
    },
    {
        "機器": "2号機微粉炭機D",            # 半角（正規化済み相当）
        "機器部品": "インペラ",               # 全角（正規化済み相当）
        "計測箇所": "タイヤ1 外径",
        "点検項目": "定期点検",
        "点検年月日": "2024-09-20",
        "測定者": "佐藤",
        "測定値": {
            "摩耗量・タイヤ1": 0.25,
            "摩耗量・タイヤ2": 0.30,
        },
        "基準値": {
            "摩耗量・タイヤ1": "≦0.50",
            "摩耗量・タイヤ2": "≦0.50",
        },
    },
]


def test_normalize_by_rules_fullwidth():
    """ダミーレコード1: 全角・半角カナ・丸数字が正規化されるか"""
    record = DUMMY_RECORDS[0]
    result = normalize_by_rules(record)

    # 文字列フィールドの正規化
    assert result["機器"] == "2号機微粉炭機D"
    assert result["機器部品"] == "インペラ"
    assert result["計測箇所"] == "タイヤ1 外径"

    # dictフィールド（測定値）のキーが正規化されるか
    assert "摩耗量・タイヤ1" in result["測定値"]
    assert "摩耗量・タイヤ2" in result["測定値"]

    # 値は変わっていないか
    assert result["測定値"]["摩耗量・タイヤ1"] == 0.18
    assert result["測定値"]["摩耗量・タイヤ2"] == 0.22

    # 基準値のキーも正規化されるか
    assert "摩耗量・タイヤ1" in result["基準値"]

    # 元のデータが変更されていないか（イミュータビリティ）
    assert record["機器"] == "２号機微粉炭機Ｄ"


def test_normalize_by_rules_already_normalized():
    """ダミーレコード2: 既に正規化済みのデータは変わらないか"""
    record = DUMMY_RECORDS[1]
    result = normalize_by_rules(record)

    assert result["機器"] == "2号機微粉炭機D"
    assert result["機器部品"] == "インペラ"
    assert result["測定値"]["摩耗量・タイヤ1"] == 0.25


def test_normalize_by_rules_both_records_match():
    """2つのレコードが正規化後に同じキーになるか（最重要テスト）"""
    result1 = normalize_by_rules(DUMMY_RECORDS[0])
    result2 = normalize_by_rules(DUMMY_RECORDS[1])

    # 正規化後、同じ機器は同じ文字列になるべき
    assert result1["機器"] == result2["機器"], (
        f"機器名が一致しない: '{result1['機器']}' != '{result2['機器']}'"
    )
    assert result1["機器部品"] == result2["機器部品"], (
        f"機器部品が一致しない: '{result1['機器部品']}' != '{result2['機器部品']}'"
    )

    # 測定値のキーも一致するべき
    keys1 = set(result1["測定値"].keys())
    keys2 = set(result2["測定値"].keys())
    assert keys1 == keys2, (
        f"測定値のキーが一致しない: {keys1} != {keys2}"
    )


# =========================================================================
# テスト3: AI辞書紐付け（MongoDBをモックして検証）
# =========================================================================

class MockCollection:
    """
    MongoDBコレクションのモック（偽物）。

    【なぜモックを使うか】
    テスト実行時に本物のMongoDBに接続したくないため。
    実際のDBと同じメソッド名（find_one, find, insert_one, update_one）を持ち、
    データをメモリ上のリスト（self.docs）に保存する。
    """
    def __init__(self, initial_docs=None):
        self.docs = list(initial_docs) if initial_docs else []

    async def find_one(self, query):
        """クエリに一致する最初のドキュメントを返す"""
        for doc in self.docs:
            if self._matches(doc, query):
                return doc
        return None

    def find(self, query, projection=None):
        """クエリに一致するドキュメントを返すカーソル風オブジェクト"""
        results = [d for d in self.docs if self._matches(d, query)]
        return MockCursor(results, projection)

    async def insert_one(self, doc):
        """ドキュメントを追加"""
        self.docs.append(dict(doc))

    async def update_one(self, filter_query, update):
        """一致するドキュメントを更新"""
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
        """簡易的なクエリマッチング"""
        for key, val in query.items():
            if key == "$or":
                if not any(self._matches(doc, cond) for cond in val):
                    return False
            elif isinstance(val, dict):
                continue
            else:
                if doc.get(key) != val:
                    # variants 配列内のチェック
                    if key == "variants" and val in doc.get("variants", []):
                        continue
                    return False
        return True


class MockCursor:
    """MongoDB カーソルのモック"""
    def __init__(self, results, projection=None):
        self.results = results
        self.projection = projection

    async def to_list(self, length=None):
        if self.projection:
            projected = []
            for doc in self.results:
                projected.append({
                    k: doc[k] for k in self.projection if k in doc and self.projection[k]
                })
            return projected
        return self.results


class MockDB:
    """MongoDBデータベースのモック"""
    def __init__(self, dict_docs=None):
        self.normalization_dict = MockCollection(dict_docs)


async def test_ai_normalize_dict_lookup():
    """
    辞書に既存エントリがある場合、正しくcanonicalに置換されるか。

    【シナリオ】
    辞書に「2号機微粉炭機D」が canonical として登録済み。
    variants に「No.2微粉炭機D」が登録済み。
    → 「No.2微粉炭機D」を入力すると「2号機微粉炭機D」に置換される。
    """
    # normalize_ai は openai に依存するため、ここで遅延importする
    from services.pipeline.normalize_ai import normalize_by_ai

    # 辞書の初期状態を設定
    db = MockDB(dict_docs=[
        {
            "field": "機器",
            "canonical": "2号機微粉炭機D",
            "variants": ["No.2微粉炭機D", "No2微粉炭機D"],
        },
        {
            "field": "機器部品",
            "canonical": "インペラ",
            "variants": ["ｲﾝﾍﾟﾗ", "impeller"],
        },
        {
            "field": "計測箇所",
            "canonical": "タイヤ1 外径",
            "variants": ["タイヤ① 外径"],
        },
    ])

    # variants にある表記を入力
    record = {
        "機器": "No.2微粉炭機D",
        "機器部品": "インペラ",     # canonical そのもの
        "計測箇所": "タイヤ1 外径", # canonical そのもの
        "点検項目": "定期点検",     # 辞書にないフィールド値（新規登録される）
    }

    # --- 入力データの表示 ---
    print("\n  【入力データ】")
    for field in ["機器", "機器部品", "計測箇所", "点検項目"]:
        print(f"    {field}: 「{record[field]}」")

    print("\n  【辞書の初期状態】")
    for doc in db.normalization_dict.docs:
        print(f"    [{doc['field']}] canonical=「{doc['canonical']}」 variants={doc['variants']}")

    # --- 正規化を実行 ---
    result = await normalize_by_ai(record, db)

    # --- 結果の表示 ---
    print("\n  【正規化結果】")
    for field in ["機器", "機器部品", "計測箇所", "点検項目"]:
        before = record[field]
        after = result[field]
        changed = "→ 置換された!" if before != after else "(変更なし)"
        print(f"    {field}: 「{before}」 → 「{after}」 {changed}")

    # --- 辞書の変化を表示 ---
    print("\n  【辞書の最終状態】")
    for doc in db.normalization_dict.docs:
        print(f"    [{doc['field']}] canonical=「{doc['canonical']}」 variants={doc.get('variants', [])}")

    # --- 検証 ---
    # variants → canonical に置換される
    assert result["機器"] == "2号機微粉炭機D", (
        f"辞書紐付け失敗: '{result['機器']}' (期待: '2号機微粉炭機D')"
    )

    # canonical そのものはそのまま
    assert result["機器部品"] == "インペラ"
    assert result["計測箇所"] == "タイヤ1 外径"

    # 辞書になかった「点検項目: 定期点検」が新規登録されているか
    new_entries = [
        d for d in db.normalization_dict.docs
        if d.get("field") == "点検項目" and d.get("canonical") == "定期点検"
    ]
    assert len(new_entries) == 1, "新規canonicalが辞書に登録されていない"

    print("\n  [PASS] 辞書紐付け: variants → canonical 置換OK")
    print("  [PASS] 辞書紐付け: canonical そのものは変更なしOK")
    print("  [PASS] 辞書紐付け: 新規エントリ登録OK")


async def test_ai_normalize_empty_dict():
    """
    辞書が空の場合、全フィールドが新規 canonical として登録されるか。

    【シナリオ】
    初めてデータを処理する場合（辞書が空）。
    全フィールドの値が新規 canonical として辞書に登録される。
    """
    from services.pipeline.normalize_ai import normalize_by_ai

    db = MockDB(dict_docs=[])

    record = {
        "機器": "2号機微粉炭機D",
        "機器部品": "インペラ",
        "計測箇所": "タイヤ1 外径",
        "点検項目": "定期点検",
    }

    print("\n  【入力データ】")
    for field in ["機器", "機器部品", "計測箇所", "点検項目"]:
        print(f"    {field}: 「{record[field]}」")
    print(f"\n  【辞書の初期状態】 エントリ数: {len(db.normalization_dict.docs)}（空）")

    result = await normalize_by_ai(record, db)

    print("\n  【正規化結果】")
    for field in ["機器", "機器部品", "計測箇所", "点検項目"]:
        print(f"    {field}: 「{record[field]}」 → 「{result[field]}」 (変更なし＝新規登録)")

    print(f"\n  【辞書の最終状態】 エントリ数: {len(db.normalization_dict.docs)}")
    for doc in db.normalization_dict.docs:
        print(f"    [{doc['field']}] canonical=「{doc['canonical']}」")

    # 値は変わらない（新規登録されるだけ）
    assert result["機器"] == "2号機微粉炭機D"
    assert result["機器部品"] == "インペラ"

    # 4つのフィールドすべてが辞書に登録されているか
    assert len(db.normalization_dict.docs) == 4, (
        f"辞書エントリ数が想定と異なる: {len(db.normalization_dict.docs)} (期待: 4)"
    )

    print("\n  [PASS] 空の辞書: 全フィールドが新規登録OK")
    print("  [PASS] 空の辞書: 値は変更なしOK")


async def test_full_pipeline_with_dummy_data():
    """
    パイプライン全体（ルールベース → AI辞書紐付け）の統合テスト。

    【シナリオ】
    全角・半角カナ混在のデータが入力される。
    1. ルールベースで全角/半角を統一
    2. AI辞書紐付けで canonical に置換
    → 最終的に正規化された文字列が返る。
    """
    from services.pipeline import run_pipeline

    # 辞書に canonical を事前登録
    db = MockDB(dict_docs=[
        {
            "field": "機器",
            "canonical": "2号機微粉炭機D",
            "variants": [],
        },
        {
            "field": "機器部品",
            "canonical": "インペラ",
            "variants": [],
        },
    ])

    # 全角混在のデータ（ルールベースで正規化 → AI辞書で確認）
    record = {
        "機器": "２号機微粉炭機Ｄ",     # 全角 → ルールで半角化 → 辞書でcanonical一致
        "機器部品": "ｲﾝﾍﾟﾗ",           # 半角カナ → ルールで全角化 → 辞書でcanonical一致
        "計測箇所": "タイヤ① 外径",      # 丸数字 → ルールで通常数字化 → 辞書に新規登録
        "点検項目": "定期点検",
        "測定値": {"摩耗量･ﾀｲﾔ①": 0.18},
    }

    print("\n  【入力データ（全角混在）】")
    for field in ["機器", "機器部品", "計測箇所", "点検項目"]:
        print(f"    {field}: 「{record[field]}」")
    print(f"    測定値キー: {list(record['測定値'].keys())}")

    # Step 1: ルールベースだけの結果を見る
    rules_result = normalize_by_rules(record)
    print("\n  【Step1: ルールベース正規化後】")
    for field in ["機器", "機器部品", "計測箇所"]:
        print(f"    {field}: 「{record[field]}」 → 「{rules_result[field]}」")
    print(f"    測定値キー: {list(rules_result['測定値'].keys())}")

    # Step 2: パイプライン全体（ルール + 辞書）
    result = await run_pipeline(record, db)
    print("\n  【Step2: ルール + AI辞書紐付け後（最終結果）】")
    for field in ["機器", "機器部品", "計測箇所"]:
        print(f"    {field}: 「{result[field]}」 ← 辞書canonical一致")

    print("\n  【辞書の最終状態】")
    for doc in db.normalization_dict.docs:
        print(f"    [{doc['field']}] canonical=「{doc['canonical']}」 variants={doc.get('variants', [])}")

    assert result["機器"] == "2号機微粉炭機D"
    assert result["機器部品"] == "インペラ"
    assert result["計測箇所"] == "タイヤ1 外径"
    assert "摩耗量・タイヤ1" in result["測定値"]

    print("\n  [PASS] 統合テスト: ルールベース→辞書紐付けの連携OK")
    print("  [PASS] 統合テスト: 測定値キーも正規化OK")


# =========================================================================
# テスト実行
# =========================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("正規化パイプライン テスト")
    print("=" * 60)

    # --- ルールベース正規化テスト ---
    print("\n[1] normalize_text テスト")
    test_normalize_text_fullwidth_to_halfwidth()
    print("  [PASS] 全角→半角変換OK")
    test_normalize_text_halfwidth_kana()
    print("  [PASS] 半角カナ→全角変換OK")
    test_normalize_text_circled_numbers()
    print("  [PASS] 丸数字→通常数字変換OK")
    test_normalize_text_whitespace()
    print("  [PASS] 空白正規化OK")
    test_normalize_text_middle_dot()
    print("  [PASS] 中点統一OK")
    test_normalize_text_none_and_empty()
    print("  [PASS] None/空文字のハンドリングOK")

    print("\n[2] normalize_by_rules テスト")
    test_normalize_by_rules_fullwidth()
    print("  [PASS] 全角混在レコードの正規化OK")
    test_normalize_by_rules_already_normalized()
    print("  [PASS] 正規化済みレコードは変更なしOK")
    test_normalize_by_rules_both_records_match()
    print("  [PASS] 2つのレコードが正規化後に一致OK（最重要）")

    # AI辞書紐付けテストは openai がインストールされている場合のみ実行
    try:
        import openai  # noqa: F401
        has_openai = True
    except ImportError:
        has_openai = False

    if has_openai:
        print("\n[3] AI辞書紐付けテスト（DBモック使用）")
        asyncio.run(test_ai_normalize_dict_lookup())
        asyncio.run(test_ai_normalize_empty_dict())

        print("\n[4] パイプライン統合テスト")
        asyncio.run(test_full_pipeline_with_dummy_data())
    else:
        print("\n[3] AI辞書紐付けテスト → SKIP（openai未インストール）")
        print("    pip install openai でインストール後に再実行してください")
        print("\n[4] パイプライン統合テスト → SKIP（openai未インストール）")

    print("\n" + "=" * 60)
    if has_openai:
        print("全テスト PASS!")
    else:
        print("ルールベーステスト PASS!（AI辞書テストはスキップ）")
    print("=" * 60)
