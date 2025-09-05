import logging
import os


def init_logging(default_level: str | None = None) -> None:
    """Initialize root logging once, honoring LOG_LEVEL env.

    Safe to call multiple times; subsequent calls are no-ops if handlers exist.
    """
    if logging.getLogger().handlers:
        return
    level_name = (default_level or os.getenv("LOG_LEVEL") or "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)
    fmt = "%(asctime)s | %(levelname)5s | %(name)s | %(message)s"
    logging.basicConfig(level=level, format=fmt)
    logging.getLogger(__name__).debug("logging initialized at level %s", level_name)

