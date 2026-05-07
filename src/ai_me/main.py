import logging

from ai_me.bootstrap import build_health_service
from ai_me.config import AppSettings
from ai_me.digest_worker import DigestSchedulerWorker
from ai_me.telegram import TelegramHealthBot


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = AppSettings.from_env()
    logging.getLogger(__name__).info(
        "Starting ai-me environment=%s runtime_mode=%s",
        settings.environment_name,
        settings.runtime_mode,
    )
    service = build_health_service(settings)
    bot = TelegramHealthBot(service=service, settings=settings.telegram)
    if settings.runtime_mode == "digest_worker":
        worker = DigestSchedulerWorker(
            service=service,
            bot=bot,
            poll_interval_seconds=settings.scheduler_poll_interval_seconds,
        )
        worker.run_forever()
        return
    bot.run_forever()


if __name__ == "__main__":
    main()
