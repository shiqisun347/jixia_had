"""Structured, redacted logging for ``jx-jobs``."""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

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


class JsonFormatter(logging.Formatter):
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
        error_code = getattr(record, "error_code", None)
        if isinstance(error_code, str) and _SAFE_ERROR_CODE_RE.fullmatch(error_code):
            payload["error_code"] = error_code
        elif has_internal_exception:
            payload["error_code"] = "internal_exception"
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class FoundationStreamHandler(logging.StreamHandler[TextIO]):
    """Marker handler so repeated CLI tests stay idempotent."""


def configure_logging(service: str, level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        if isinstance(handler, FoundationStreamHandler):
            root.removeHandler(handler)
            handler.close()
    handler = FoundationStreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    return logging.getLogger(service)
