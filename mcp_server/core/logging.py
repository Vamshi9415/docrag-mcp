"""Structured JSON logging with request-context tracking.

Every log record is emitted as a single-line JSON object to *stderr*
AND to a rotating log file under ``request_logs/``.  This keeps
``stdout`` free for MCP protocol traffic (important for the stdio
transport).  A ``ContextVar`` carries the current request-id so that
concurrent tool invocations can be correlated.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from contextvars import ContextVar

# ---------------------------------------------------------------------------
# Request-scoped context
# ---------------------------------------------------------------------------
request_id_var: ContextVar[str] = ContextVar("request_id", default="system")


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------
class StructuredFormatter(logging.Formatter):
    """Emit each log record as a compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "rid": request_id_var.get("system"),
        }
        for key in ("tool", "elapsed", "url", "code", "detail",
                     "attempt", "wait", "bytes",
                     "tool_args", "result_keys", "result_preview", "error_detail"):
            val = getattr(record, key, None)
            if val is not None:
                entry[key] = val
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------
def _get_log_file_path() -> str:
    """Return the path to today's log file in request_logs/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logs_dir = os.path.join(base_dir, "request_logs")
    os.makedirs(logs_dir, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    return os.path.join(logs_dir, f"server_{today}.log")


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root ``mcp_server`` logger (idempotent).

    Adds two handlers:
    - StreamHandler → stderr (console)
    - FileHandler   → request_logs/server_YYYY-MM-DD.log
    """
    root = logging.getLogger("mcp_server")
    if root.handlers:
        return
    root.setLevel(level)

    formatter = StructuredFormatter()

    # Console handler (stderr)
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # File handler (request_logs/)
    try:
        file_handler = logging.FileHandler(
            _get_log_file_path(), mode="a", encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except Exception:
        # If file logging fails, continue with console only
        root.warning("Could not set up file logging — using console only")

    # Silence noisy third-party loggers
    for name in ("httpx", "httpcore", "urllib3", "sentence_transformers", "filelock"):
        logging.getLogger(name).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a namespaced logger under ``mcp_server.<name>``."""
    return logging.getLogger(f"mcp_server.{name}")


def make_request_logger(request_id: str) -> logging.Logger:
    """Return a logger tagged with a request ID (used in the RAG pipeline)."""
    return get_logger(f"request.{request_id[:8]}")
