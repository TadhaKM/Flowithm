"""Structured JSON logging for Flowithm.

`get_logger("brain.scheduler")` returns a stdlib logger configured with a
JSON formatter writing to stdout. Every log line ends up parseable in
log-aggregation tools (Datadog, CloudWatch, etc.) without a separate
shipping config.

Add structured fields by passing `extra={...}`:

    log.info("ingest cycle done",
             extra={"org_id": org_id, "duration_ms": ms, "new_chunks": n})

Reserved attribute names (org_id, duration_ms, request_id, status_code,
endpoint) get top-level keys; unknown keys land in `extra`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# Names that get hoisted to top-level JSON keys when present on the record.
_RESERVED = {"org_id", "duration_ms", "request_id", "status_code", "endpoint"}


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        log: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Promote known structured fields. Anything else passed via
        # extra={...} lands under "extra".
        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in (
                "args", "asctime", "created", "exc_info", "exc_text", "filename",
                "funcName", "levelname", "levelno", "lineno", "message", "module",
                "msecs", "msg", "name", "pathname", "process", "processName",
                "relativeCreated", "stack_info", "thread", "threadName",
                "taskName",
            ):
                continue
            if key in _RESERVED:
                log[key] = value
            else:
                extra[key] = value
        if extra:
            log["extra"] = extra
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log, default=str)


def get_logger(name: str) -> logging.Logger:
    """Cached factory — repeated calls return the same logger without
    stacking duplicate handlers."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        # Don't let logs bubble to the root logger's default handler too —
        # avoids duplicate lines when the root has its own setup.
        logger.propagate = False
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    return logger
