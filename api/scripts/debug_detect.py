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

    # まず detect_inconsistent_groups の結果を全表示
    groups = await detect_inconsistent_groups(db, "default")
    print(f"=== detect_inconsistent_groups: {len(groups)} groups ===")
    for i, g in enumerate(groups):
        print(f"[{i}] {g['group']} / minority={len(g['minority_samples'])}")

    # 次にaggregateを直接叩いて、何が集計されているか見る
    print("\n=== raw aggregate (total_records > 1) ===")
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
            "records": {"$sum": 1},
            "keysets": {"$addToSet": "$measurement_keys"},
        }},
        {"$match": {"records": {"$gt": 1}}},
    ]
    async for g in db.pages.aggregate(pipeline):
        kiki = g["_id"].get("機器")
        buhin = g["_id"].get("機器部品")
        butsuryo = g["_id"].get("測定物理量")
        print(f"  {kiki!r} / {buhin!r} / {butsuryo!r}: records={g['records']}, keysets={len(g['keysets'])}")
        if buhin and "ライナ" in buhin:
            print(f"    キーセット内訳:")
            for i, ks in enumerate(g["keysets"]):
                print(f"      [{i}] {len(ks)}個: 先頭={ks[:2]}")


if __name__ == "__main__":
    asyncio.run(main())
