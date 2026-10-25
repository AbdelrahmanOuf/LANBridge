from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        # Passwords/credential objects never enter the logger. Tracebacks are local only.
        payload = {"time": self.formatTime(record), "level": record.levelname,
                   "component": record.name, "message": record.getMessage()}
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / "application.jsonl", maxBytes=5_000_000,
                                  backupCount=4, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("lanbridge")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
