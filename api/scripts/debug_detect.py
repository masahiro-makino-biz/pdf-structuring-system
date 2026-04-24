"""
テーブルライナの検出結果を直接デバッグするスクリプト
使い方: docker compose exec api python -m scripts.debug_detect
"""
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from services.reconciliation import detect_inconsistent_groups


async def main():
    client = AsyncIOMotorClient("mongodb://mongo:27017")
    db = client.pdf_system

    # detect_inconsistent_groups の内部を手動でトレース
    print("=== detect_inconsistent_groups トレース ===")
    pipeline = [
        {"$match": {
            "tenant": "default",
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
    agg_groups = await cursor.to_list(length=None)
    print(f"aggregateから {len(agg_groups)} グループ")

    for g in agg_groups:
        group_id = g["_id"]
        buhin = group_id.get("機器部品") or ""
        is_target = "ライナ" in buhin

        records = g["records"]
        mark = ">>> " if is_target else "    "

        if len(records) < 2:
            if is_target:
                print(f"{mark}{group_id}: SKIP (records<2)")
            continue

        keyset_to_records = {}
        empty_keys = 0
        for r in records:
            keys = r.get("keys", [])
            if not keys:
                empty_keys += 1
                continue
            keyset = tuple(sorted(keys))
            keyset_to_records.setdefault(keyset, []).append(r)

        if is_target:
            print(f"{mark}{group_id}")
            print(f"    records={len(records)}, empty_keys={empty_keys}")
            print(f"    keyset_to_records: {len(keyset_to_records)}種類")
            for ks, rs in keyset_to_records.items():
                print(f"      keyset({len(ks)}個): {len(rs)}レコード 先頭2={ks[:2]}")

        if len(keyset_to_records) < 2:
            if is_target:
                print(f"{mark}SKIP (keyset数<2)")
            continue

        sorted_keysets = sorted(
            keyset_to_records.items(),
            key=lambda kv: (len(kv[1]), len(kv[0])),
            reverse=True,
        )
        majority_keyset = sorted_keysets[0][0]
        majority_set = set(majority_keyset)

        minority_samples = []
        for keyset, recs in sorted_keysets[1:]:
            for mk in keyset:
                if mk in majority_set:
                    continue
                minority_samples.append(mk)

        if is_target:
            print(f"    majority keys={len(majority_keyset)}, minority計算後={len(minority_samples)}")

        if not minority_samples:
            if is_target:
                print(f"{mark}SKIP (minority空)")
            continue

        if is_target:
            print(f"{mark}✓ 検出対象: minority={len(minority_samples)}個")


if __name__ == "__main__":
    asyncio.run(main())
