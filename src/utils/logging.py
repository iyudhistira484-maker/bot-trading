"""JSON-line logging with rotation; never log raw exchange errors or secrets."""
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def create_logger(path: str, max_bytes: int, backup_count: int) -> logging.Logger:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    logger = logging.Logger('indodax_bot', level=logging.INFO)
    handler = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    logger.addHandler(handler)
    return logger


def event(logger: logging.Logger, name: str, **fields) -> None:
    """Only callers' explicitly chosen safe fields are serialized."""
    logger.info(json.dumps({'time': datetime.now(timezone.utc).isoformat(), 'event': name, **fields}, default=str))
