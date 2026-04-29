"""Logging configuration module.

Purpose: Centralized logging setup for the moomoo-bot application.
Related: All modules that need logging.
"""

import logging
import sys
from pathlib import Path


import logging
import sys
from pathlib import Path

# python-json-logger がインストールされていることを前提とし、利用可能なら使用する
try:
    from pythonjsonlogger import jsonlogger
    HAS_JSON_LOGGER = True
except ImportError:
    HAS_JSON_LOGGER = False

def setup_logging(log_level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure logging with console and optional file output.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR). Defaults to INFO.
        log_file: Optional file path for file-based logging.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    
    if HAS_JSON_LOGGER:
        formatter = jsonlogger.JsonFormatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s"
        )

    for handler in handlers:
        handler.setFormatter(formatter)
        logging.getLogger().addHandler(handler)

    logging.getLogger().setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given name.

    Args:
        name: Logger name, typically __name__ of the module.

    Returns:
        Configured logger instance.
    """
    return logging.getLogger(name)
