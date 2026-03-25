# =============================================================================
# tests/insert_test_data.py - カーブフィット予測テスト用ダミーデータ投入
# =============================================================================
#
# 【使い方】
#   docker compose up -d mongo  ← MongoDBを起動
#   python tests/insert_test_data.py  ← データ投入
#   Streamlit UIで「テスト機器」を検索して予測を試す
#
# 【テストデータの特徴】
#   - 機器名は「テスト機器A」「テスト機器B」で実データと混ざらない
#   - パターンA: 改修1回あり + 加速劣化（指数カーブが選ばれるはず）
#   - パターンB: 改修1回あり + 直線劣化（線形カーブが選ばれるはず）
#
# 【削除方法】
#   python tests/insert_test_data.py --delete
#
# =============================================================================

import sys
from pymongo import MongoClient

MONGO_URL = "mongodb://localhost:27017"
DATABASE = "pdf_system"
COLLECTION = "pages"

# =============================================================================
# パターンA: 改修1回 + 加速劣化（指数的に値が増加）
# 2018〜2022: 徐々に加速して悪化 → 2023: 改修 → 2024〜2025: また加速
# =============================================================================
PATTERN_A = [
    {"year": "2018-04-15", "values": {"摩耗量・テストA・①": 0.05, "摩耗量・テストA・②": 0.04}},
    {"year": "2019-04-15", "values": {"摩耗量・テストA・①": 0.09, "摩耗量・テストA・②": 0.07}},
    {"year": "2020-04-15", "values": {"摩耗量・テストA・①": 0.16, "摩耗量・テストA・②": 0.13}},
    {"year": "2021-04-15", "values": {"摩耗量・テストA・①": 0.28, "摩耗量・テストA・②": 0.22}},
    {"year": "2022-04-15", "values": {"摩耗量・テストA・①": 0.45, "摩耗量・テストA・②": 0.38}},
    # ↓ 改修（50%以上の低下）
    {"year": "2023-04-15", "values": {"摩耗量・テストA・①": 0.06, "摩耗量・テストA・②": 0.05}},
    {"year": "2024-04-15", "values": {"摩耗量・テストA・①": 0.12, "摩耗量・テストA・②": 0.10}},
    {"year": "2025-04-15", "values": {"摩耗量・テストA・①": 0.22, "摩耗量・テストA・②": 0.18}},
]

# =============================================================================
# パターンB: 改修1回 + 直線劣化（毎年ほぼ同じペースで悪化）
# 2016〜2020: 直線的に悪化 → 2021: 改修 → 2022〜2025: また直線的に悪化
# =============================================================================
PATTERN_B = [
    {"year": "2016-04-15", "values": {"振動値・テストB・上": 0.10}},
    {"year": "2017-04-15", "values": {"振動値・テストB・上": 0.15}},
    {"year": "2018-04-15", "values": {"振動値・テストB・上": 0.20}},
    {"year": "2019-04-15", "values": {"振動値・テストB・上": 0.25}},
    {"year": "2020-04-15", "values": {"振動値・テストB・上": 0.30}},
    # ↓ 改修（50%以上の低下）
    {"year": "2021-04-15", "values": {"振動値・テストB・上": 0.08}},
    {"year": "2022-04-15", "values": {"振動値・テストB・上": 0.13}},
    {"year": "2023-04-15", "values": {"振動値・テストB・上": 0.18}},
    {"year": "2024-04-15", "values": {"振動値・テストB・上": 0.23}},
    {"year": "2025-04-15", "values": {"振動値・テストB・上": 0.28}},
]


def build_documents(pattern, equipment, equipment_part, location, reference_values):
    """テストパターンからMongoDBドキュメントを生成"""
    docs = []
    for row in pattern:
        docs.append({
            "tenant": "default",
            "data": {
                "機器": equipment,
                "機器部品": equipment_part,
                "点検項目": "摩耗量測定",
                "点検年月日": row["year"],
                "計測箇所": location,
                "測定値": row["values"],
                "基準値": reference_values,
            },
            "image_path": "",
            "_test_data": True,  # テストデータ識別用フラグ
        })
    return docs


def insert_data():
    """テストデータをMongoDBに投入"""
    client = MongoClient(MONGO_URL)
    db = client[DATABASE]
    col = db[COLLECTION]

    # パターンA: 改修1回 + 加速劣化
    docs_a = build_documents(
        pattern=PATTERN_A,
        equipment="テスト機器A（加速劣化）",
        equipment_part="テスト部品A",
        location="テスト計測箇所A",
        reference_values={"摩耗量": 0.50},
    )

    # パターンB: 改修1回 + 直線劣化
    docs_b = build_documents(
        pattern=PATTERN_B,
        equipment="テスト機器B（直線劣化）",
        equipment_part="テスト部品B",
        location="テスト計測箇所B",
        reference_values={"振動値": 0.50},
    )

    all_docs = docs_a + docs_b
    result = col.insert_many(all_docs)
    print(f"投入完了: {len(result.inserted_ids)}件")
    print(f"  パターンA（加速劣化・改修1回）: {len(docs_a)}件 - 「テスト機器A」で検索")
    print(f"  パターンB（直線劣化・改修1回）: {len(docs_b)}件 - 「テスト機器B」で検索")
    print()
    print("Streamlit UIで「テスト機器Aの予測をして」と聞いてみてください")

    client.close()


def delete_data():
    """テストデータを削除"""
    client = MongoClient(MONGO_URL)
    db = client[DATABASE]
    col = db[COLLECTION]

    result = col.delete_many({"_test_data": True})
    print(f"削除完了: {result.deleted_count}件")

    client.close()


if __name__ == "__main__":
    if "--delete" in sys.argv:
        delete_data()
    else:
        # 既存のテストデータを先に削除（重複防止）
        client = MongoClient(MONGO_URL)
        db = client[DATABASE]
        col = db[COLLECTION]
        deleted = col.delete_many({"_test_data": True})
        if deleted.deleted_count > 0:
            print(f"既存テストデータを削除: {deleted.deleted_count}件")
        client.close()

        insert_data()
