from ai_me.bootstrap import build_health_service
from ai_me.config import AppSettings
from ai_me.telegram import TelegramHealthBot


def main() -> None:
    settings = AppSettings.from_env()
    service = build_health_service(settings)
    bot = TelegramHealthBot(service=service, settings=settings.telegram)
    bot.run_forever()


if __name__ == "__main__":
    main()
