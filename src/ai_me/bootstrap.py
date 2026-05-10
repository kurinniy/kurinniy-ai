from ai_me.config import AppSettings
from ai_me.services.food_analysis import DisabledFoodPhotoAnalyzer, OpenAIFoodPhotoAnalyzer
from ai_me.services.google_drive_import import DisabledGoogleDriveClient, GoogleDriveHealthImportService, ServiceAccountGoogleDriveClient
from ai_me.services.health_service import HealthService
from ai_me.services.media_storage import DisabledMediaStorage, RailwayBucketMediaStorage
from ai_me.storage.mysql import MySQLStore


def build_health_service(settings: AppSettings) -> HealthService:
    store = MySQLStore(
        **settings.database.as_mysql_connector_kwargs(),
        owner_telegram_user_id=settings.telegram.owner_telegram_user_id,
    )
    analyzer = DisabledFoodPhotoAnalyzer()
    if settings.food_vision_api_key and settings.food_vision_model:
        analyzer = OpenAIFoodPhotoAnalyzer(
            api_key=settings.food_vision_api_key,
            model=settings.food_vision_model,
        )
    google_drive_client = DisabledGoogleDriveClient()
    if settings.google_drive.enabled:
        google_drive_client = ServiceAccountGoogleDriveClient(
            service_account_json=settings.google_drive.service_account_json,
            service_account_file=settings.google_drive.service_account_file,
        )
    media_storage = DisabledMediaStorage()
    if settings.media_bucket.enabled:
        media_storage = RailwayBucketMediaStorage(settings.media_bucket)
    return HealthService(
        store=store,
        food_photo_analyzer=analyzer,
        media_storage=media_storage,
        google_drive_import_service=GoogleDriveHealthImportService(
            store=store,
            google_drive_client=google_drive_client,
            lookback_days=settings.google_drive.lookback_days,
        ),
        admin_telegram_user_ids=settings.telegram.admin_user_ids,
        default_timezone_name=settings.telegram.timezone_name,
    )
