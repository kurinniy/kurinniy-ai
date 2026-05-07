import logging

from ai_me.bootstrap import build_health_service
from ai_me.config import AppSettings
from ai_me.telegram import TelegramHealthBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = AppSettings.from_env()
    logging.getLogger(__name__).info("Starting ai-me environment=%s", settings.environment_name)
    service = build_health_service(settings)
    bot = TelegramHealthBot(service=service, settings=settings.telegram)
    bot.run_forever()


if __name__ == "__main__":
    main()
