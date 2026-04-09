# =============================================================================
# api/services/pipeline/batch.py - 既存データ一括正規化スクリプト
# =============================================================================
#
# 【このスクリプトの役割】
# MongoDBに保存済みの構造化データに対して、正規化パイプラインを一括適用する。
# ルールベース正規化 + AI自動マッチングの両方を実行する。
#
# 【使い方】
# Docker内で実行:
#   docker exec pdf-api python -m services.pipeline.batch
#
# AI正規化を含めて再実行する場合（Phase 1済みのデータも対象）:
#   docker exec pdf-api python -m services.pipeline.batch --force
#
# 【安全性】
# - 元データは data_raw に自動バックアップされる
# - 通常実行: data_raw が既にあるドキュメントはスキップ（冪等性）
# - --force: data_raw があっても再処理（AI正規化を後から適用する時に使用）
#
# =============================================================================

import asyncio
import sys

from motor.motor_asyncio import AsyncIOMotorClient

from core.config import get_settings
from services.pipeline import run_pipeline


async def migrate_all(force: bool = False):
    """
    全処理済みドキュメントに正規化パイプラインを適用する。

    Args:
        force: Trueの場合、正規化済みのドキュメントも再処理する
    """
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongo_url)
    db = client[settings.mongo_database]

    query = {
        "processed": True,
        "data": {"$exists": True},
    }

    if not force:
        # 通常: まだ正規化していないドキュメントのみ
        query["data_raw"] = {"$exists": False}

    cursor = db.pages.find(query)

    count = 0
    async for doc in cursor:
        # --force の場合は data_raw（元データ）から正規化し直す
        original_data = doc.get("data_raw", doc["data"])
        normalized = await run_pipeline(original_data, db)

        await db.pages.update_one(
            {"_id": doc["_id"]},
            {"$set": {
                "data": normalized,
                "data_raw": original_data,
            }}
        )
        count += 1
        if count % 100 == 0:
            print(f"  {count}件処理完了...")

    print(f"完了: 計{count}件を正規化しました")
    client.close()


if __name__ == "__main__":
    force = "--force" in sys.argv
    if force:
        print("--force: 正規化済みデータも再処理します")
    asyncio.run(migrate_all(force=force))
