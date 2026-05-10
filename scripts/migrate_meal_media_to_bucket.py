#!/usr/bin/env python3
from __future__ import annotations

import argparse
import logging
import os
from io import BytesIO

from PIL import Image

from ai_me.config import DatabaseSettings, MediaBucketSettings
from ai_me.services.media_storage import RailwayBucketMediaStorage, build_meal_media_object_key
from ai_me.storage.mysql import MySQLStore


logger = logging.getLogger("ai_me.bucket_migration")


def resolve_image_size(image_bytes: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            return image.size
    except Exception:
        return (0, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Перенос meal_media.image_bytes в Railway Bucket")
    parser.add_argument("--batch-size", type=int, default=100, help="Размер батча legacy media rows")
    parser.add_argument("--max-batches", type=int, default=0, help="Максимум батчей за один прогон, 0 = без лимита")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что будет перенесено")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    env = os.environ
    database_settings = DatabaseSettings.from_env(env)
    bucket_settings = MediaBucketSettings.from_env(env)
    if not bucket_settings.enabled:
        logger.error("Bucket не настроен. Проверь BUCKET / ENDPOINT / ACCESS_KEY_ID / SECRET_ACCESS_KEY.")
        return 1

    store = MySQLStore(
        **database_settings.as_mysql_connector_kwargs(),
    )
    media_storage = RailwayBucketMediaStorage(bucket_settings)

    migrated = 0
    failed = 0
    batch_no = 0

    try:
        while True:
            if args.max_batches > 0 and batch_no >= args.max_batches:
                break
            batch_no += 1
            media_items = store.list_legacy_meal_media_for_migration(limit=args.batch_size)
            if not media_items:
                break

            logger.info("Обрабатываю батч %s: %s media rows", batch_no, len(media_items))
            for media in media_items:
                object_key = build_meal_media_object_key(
                    key_prefix=media_storage.key_prefix,
                    user_id=media.user_id,
                    media_id=media.media_id,
                    occurred_at=media.occurred_at,
                    mime_type=media.mime_type,
                )
                try:
                    width, height = (media.width, media.height)
                    if not width or not height:
                        width, height = resolve_image_size(media.image_bytes)
                    if args.dry_run:
                        logger.info(
                            "[dry-run] media_id=%s bytes=%s key=%s",
                            media.media_id,
                            len(media.image_bytes),
                            object_key,
                        )
                        migrated += 1
                        continue

                    media_storage.store_image(
                        object_key=object_key,
                        image_bytes=media.image_bytes,
                        mime_type=media.mime_type,
                    )
                    store.mark_meal_media_bucket_migrated(
                        media.media_id,
                        storage_key=object_key,
                        bucket_name=media_storage.settings.bucket_name,
                        width=width,
                        height=height,
                    )
                    migrated += 1
                except Exception:
                    failed += 1
                    logger.exception("Не удалось мигрировать media_id=%s", media.media_id)
    finally:
        store.close()

    logger.info("Migration finished: migrated=%s failed=%s dry_run=%s", migrated, failed, args.dry_run)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
