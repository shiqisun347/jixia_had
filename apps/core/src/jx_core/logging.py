"""Small, dependency-free structured logging boundary for the core process."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import uuid
from datetime import UTC, datetime
from typing import Any, TextIO

_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jx_request_id", default=None
)

_CONNECTION_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^\s\"'<>]+")
_SENSITIVE_KEY_NAMES = (
    r"password|passwd|pwd|secret|api[_-]?key|access[_-]?token|"
    r"refresh[_-]?token|token|authorization"
)
_QUOTED_SENSITIVE_PAIR_RE = re.compile(
    r"""(?ix)
    (?P<prefix>
      (?P<key_quote>["'])
      (?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|
         refresh[_-]?token|token|authorization)
      (?P=key_quote)
      \s*(?:=|:)\s*
    )
    (?P<value>
      "(?:\\.|[^"\\])*"
      |'(?:\\.|[^'\\])*'
      |[^\s,;}\]]+
    )
    """
)
_STRUCTURED_SENSITIVE_PAIR_RE = re.compile(
    r"""(?ix)
    (?:(?P<key_quote>["'])
       (?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|
          refresh[_-]?token|token|authorization)
       (?P=key_quote)
       |\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|
            refresh[_-]?token|token|authorization)\b)
    \s*(?:=|:)\s*[{\[]
    """
)
_SENSITIVE_PAIR_RE = re.compile(
    rf"(?i)(?P<prefix>\b({_SENSITIVE_KEY_NAMES})\b"
    r"\s*(?:=|:)\s*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![\w:])(?:/[a-z0-9_.-]+){2,}|\b[a-z]:\\(?:[^\s\\]+\\)+[^\s\\]+"
)
_TRACEBACK_RE = re.compile(
    r"(?i)\btraceback\s*\(\s*most recent call last\s*\)\s*:"
    r"|\bfile\s+[\"'][^\"']+[\"'],\s*line\s+\d+"
)
_SAFE_ERROR_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_CANONICAL_REQUEST_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_GENERATED_REQUEST_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _redact_sensitive_pair(match: re.Match[str]) -> str:
    value = match.group("value")
    if len(value) >= 2 and value[0] in {"'", '"'} and value[-1] == value[0]:
        redacted_value = f"{value[0]}[REDACTED]{value[-1]}"
    else:
        redacted_value = "[REDACTED]"
    return f"{match.group('prefix')}{redacted_value}"


def redact_log_text(value: str) -> str:
    """Remove common credential, DSN and internal-path shapes."""

    if _STRUCTURED_SENSITIVE_PAIR_RE.search(value):
        return "sensitive log details redacted"
    redacted = _CONNECTION_URL_RE.sub("[REDACTED_URL]", value)
    redacted = _QUOTED_SENSITIVE_PAIR_RE.sub(_redact_sensitive_pair, redacted)
    redacted = _SENSITIVE_PAIR_RE.sub(_redact_sensitive_pair, redacted)
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", redacted)
    redacted = _ABSOLUTE_PATH_RE.sub("[REDACTED_PATH]", redacted)
    return redacted


def normalize_request_id(value: str | None) -> str:
    """Accept only canonical UUIDs from HTTP clients; generate the rest."""

    if isinstance(value, str) and _CANONICAL_REQUEST_ID_RE.fullmatch(value):
        return value
    return uuid.uuid4().hex


def _is_safe_request_id(value: str) -> bool:
    return bool(
        _CANONICAL_REQUEST_ID_RE.fullmatch(value) or _GENERATED_REQUEST_ID_RE.fullmatch(value)
    )


def set_request_id(value: str | None) -> contextvars.Token[str | None]:
    if value is None:
        safe_value = None
    elif _is_safe_request_id(value):
        safe_value = value
    else:
        safe_value = uuid.uuid4().hex
    return _request_id.set(safe_value)


def reset_request_id(token: contextvars.Token[str | None]) -> None:
    _request_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


class JsonFormatter(logging.Formatter):
    """Emit one redacted JSON object per log line.

    Exception objects and tracebacks are intentionally not serialized.  The
    service logs a stable ``error_code`` instead of leaking a connection URL,
    vendor response, request body, or stack trace to the operator-facing
    stream.
    """

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        try:
            raw_message = record.getMessage()
        except Exception:
            raw_message = "log message unavailable"
            has_internal_exception = True
        else:
            has_internal_exception = bool(
                record.exc_info or record.exc_text or record.stack_info
            ) or bool(_TRACEBACK_RE.search(raw_message))
        message = (
            "internal exception details redacted"
            if _TRACEBACK_RE.search(raw_message)
            else redact_log_text(raw_message)
        )
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "message": message,
        }
        request_id = current_request_id()
        if request_id and _is_safe_request_id(request_id):
            payload["request_id"] = request_id
        error_code = getattr(record, "error_code", None)
        if isinstance(error_code, str) and _SAFE_ERROR_CODE_RE.fullmatch(error_code):
            payload["error_code"] = error_code
        elif has_internal_exception:
            payload["error_code"] = "internal_exception"
        for key in (
            "match_id",
            "speech_id",
            "generation_id",
            "decision_round_id",
            "connection_epoch",
            "incident_id",
        ):
            value = getattr(record, key, None)
            if isinstance(value, (str, int)) and str(value):
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class FoundationStreamHandler(logging.StreamHandler[TextIO]):
    """Marker handler so repeated app construction stays idempotent."""


def configure_logging(service: str, level: str = "INFO") -> logging.Logger:
    """Configure the process root logger once and return the service logger."""

    root = logging.getLogger()
    root.setLevel(level)
    # The foundation process owns its stdout handler.  Replacing only handlers
    # created by this function keeps repeated test/app construction idempotent.
    for handler in list(root.handlers):
        if isinstance(handler, FoundationStreamHandler):
            root.removeHandler(handler)
            handler.close()
    handler = FoundationStreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    # Uvicorn normally installs text handlers.  Route its records through the
    # same JSON formatter even when the service is started via ``uvicorn``.
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    return logging.getLogger(service)


logger = logging.getLogger("jx-core")
