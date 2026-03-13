"""Package-level logging helpers for Spyder AI Assistant."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from spyder.config.base import get_conf_path
except Exception:  # pragma: no cover - fallback outside Spyder envs
    get_conf_path = None


_LOGGER_NAME = "spyder_ai_assistant"
_HANDLER_NAME = "spyder_ai_assistant_file_handler"
_LOG_FILENAME = "spyder-ai-assistant.log"


def _resolve_log_path():
    """Return the package log file path."""
    if get_conf_path is not None:
        try:
            return Path(get_conf_path(_LOG_FILENAME))
        except Exception:
            pass

    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home) / "spyder-py3" / _LOG_FILENAME
    return Path.home() / ".config" / "spyder-py3" / _LOG_FILENAME


def configure_package_logging(level=logging.INFO):
    """Attach one rotating file handler to the package logger."""
    package_logger = logging.getLogger(_LOGGER_NAME)
    package_logger.setLevel(level)

    for handler in package_logger.handlers:
        if getattr(handler, "name", "") == _HANDLER_NAME:
            return package_logger

    log_path = _resolve_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
    except Exception:
        return package_logger

    handler.set_name(_HANDLER_NAME)
    handler.setLevel(level)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
        )
    )
    package_logger.addHandler(handler)
    package_logger.propagate = False
    package_logger.info("Package logging initialized at %s", log_path)
    return package_logger
