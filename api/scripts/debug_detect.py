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
    groups = await detect_inconsistent_groups(db, "default")
    print(f"total groups: {len(groups)}")

    for g in groups:
        buhin = g["group"].get("機器部品") or ""
        if "テーブルライナ" in buhin:
            print("\n=== テーブルライナ ===")
            print(f"group: {g['group']}")
            print(f"majority_keys (count): {len(g['majority_keys'])}")
            print(f"minority_keys (count): {len(g['minority_keys'])}")
            print(f"minority_samples (count): {len(g['minority_samples'])}")
            mi = g["majority_sample"].get("image_path")
            print(f"majority_image: {mi}")
            if g["minority_samples"]:
                s = g["minority_samples"][0]
                print(f"first sample: key={s.get('key')} image={s.get('image_path')} page_id={s.get('page_id')}")
            if g["minority_keys"]:
                print(f"minority先頭3: {g['minority_keys'][:3]}")
                print(f"minority末尾3: {g['minority_keys'][-3:]}")


if __name__ == "__main__":
    asyncio.run(main())
