from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ai_me.config import MediaBucketSettings

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None


logger = logging.getLogger(__name__)


def build_meal_media_object_key(
    *,
    key_prefix: str,
    user_id: int,
    media_id: str,
    occurred_at: datetime,
    mime_type: str,
) -> str:
    extension = "bin"
    if mime_type == "image/jpeg":
        extension = "jpg"
    elif mime_type == "image/png":
        extension = "png"
    normalized_prefix = key_prefix.strip("/") or "meal-media"
    return "%s/%s/%s/%s.%s" % (
        normalized_prefix,
        user_id,
        occurred_at.strftime("%Y-%m"),
        media_id,
        extension,
    )


@dataclass(frozen=True)
class StoredMediaObject:
    storage_kind: str
    storage_key: str
    bucket_name: str
    byte_size: int


class MediaStorage(Protocol):
    def store_image(self, *, object_key: str, image_bytes: bytes, mime_type: str) -> StoredMediaObject:
        ...

    def load_image(self, *, object_key: str) -> bytes:
        ...

    @property
    def enabled(self) -> bool:
        ...

    @property
    def key_prefix(self) -> str:
        ...


class DisabledMediaStorage:
    @property
    def enabled(self) -> bool:
        return False

    @property
    def key_prefix(self) -> str:
        return "meal-media"

    def store_image(self, *, object_key: str, image_bytes: bytes, mime_type: str) -> StoredMediaObject:
        raise RuntimeError("Bucket storage is not configured.")

    def load_image(self, *, object_key: str) -> bytes:
        raise RuntimeError("Bucket storage is not configured.")


class RailwayBucketMediaStorage:
    STORAGE_KIND = "railway_bucket"

    def __init__(self, settings: MediaBucketSettings) -> None:
        if boto3 is None:
            raise RuntimeError("boto3 is not installed. Add boto3 to use Railway Bucket media storage.")
        self.settings = settings
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.endpoint,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            region_name=settings.region,
        )

    @property
    def enabled(self) -> bool:
        return True

    @property
    def key_prefix(self) -> str:
        return self.settings.key_prefix

    def store_image(self, *, object_key: str, image_bytes: bytes, mime_type: str) -> StoredMediaObject:
        self._client.put_object(
            Bucket=self.settings.bucket_name,
            Key=object_key,
            Body=image_bytes,
            ContentType=mime_type,
        )
        return StoredMediaObject(
            storage_kind=self.STORAGE_KIND,
            storage_key=object_key,
            bucket_name=self.settings.bucket_name,
            byte_size=len(image_bytes),
        )

    def load_image(self, *, object_key: str) -> bytes:
        response = self._client.get_object(Bucket=self.settings.bucket_name, Key=object_key)
        body = response["Body"]
        try:
            return body.read()
        finally:  # pragma: no cover
            try:
                body.close()
            except Exception:
                logger.debug("Failed to close bucket response body for key=%s", object_key)
