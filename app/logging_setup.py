"""Structured JSON logging with a correlation ID carried in a contextvar (D5)."""
import contextvars
import json
import logging
import sys
import time

from .config import cfg

_cid = contextvars.ContextVar("correlation_id", default="-")
_rid = contextvars.ContextVar("run_id", default="-")


def set_context(correlation_id=None, run_id=None):
    if correlation_id is not None:
        _cid.set(correlation_id)
    if run_id is not None:
        _rid.set(run_id)


def get_correlation_id():
    return _cid.get()


class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": cfg.SERVICE_NAME,
            "logger": record.name,
            "event": record.getMessage(),
            "correlation_id": _cid.get(),
            "run_id": _rid.get(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            d.update(extra)
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def setup_logging():
    root = logging.getLogger()
    if getattr(root, "_grievance_configured", False):
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    root.setLevel(cfg.LOG_LEVEL.upper())
    root._grievance_configured = True
    logging.getLogger("uvicorn.access").disabled = True  # we log requests ourselves, structured


def log(logger, event, level="info", **fields):
    logger.log(getattr(logging, level.upper()), event, extra={"fields": fields})
