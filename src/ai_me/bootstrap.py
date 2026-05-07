from ai_me.config import AppSettings
from ai_me.services.food_analysis import DisabledFoodPhotoAnalyzer, OpenAIFoodPhotoAnalyzer
from ai_me.services.health_service import HealthService
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
    return HealthService(
        store=store,
        food_photo_analyzer=analyzer,
        admin_telegram_user_ids=settings.telegram.admin_user_ids,
        default_timezone_name=settings.telegram.timezone_name,
    )
