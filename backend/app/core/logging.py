import logging

from app.core.config import settings


def configure_logging() -> None:
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
