from ai_me.config import AppSettings
from ai_me.services.health_service import HealthService
from ai_me.storage.mysql import MySQLStore


def build_health_service(settings: AppSettings) -> HealthService:
    store = MySQLStore(**settings.database.as_mysql_connector_kwargs())
    return HealthService(store=store)
