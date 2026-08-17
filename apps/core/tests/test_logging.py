from __future__ import annotations

import json
import logging
import sys

from jx_core.logging import JsonFormatter


def test_json_formatter_redacts_traceback_text() -> None:
    record = logging.LogRecord(
        name="uvicorn.error",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg="Traceback (most recent call last):\nsecret stack details",
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter("jx-core").format(record))

    assert payload["message"] == "internal exception details redacted"
    assert payload["error_code"] == "internal_exception"
    assert "secret stack" not in json.dumps(payload)


def test_json_formatter_redacts_dsn_credentials_and_internal_path() -> None:
    record = logging.LogRecord(
        name="jx-core",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg=(
            "failed postgresql+psycopg://jx:topsecret@127.0.0.1:5432/jx "
            "password=hunter2 api_key='key-value' at /Users/private/project/main.py"
        ),
        args=(),
        exc_info=None,
    )

    serialized = JsonFormatter("jx-core").format(record)
    payload = json.loads(serialized)

    assert "topsecret" not in serialized
    assert "hunter2" not in serialized
    assert "key-value" not in serialized
    assert "/Users/private" not in serialized
    assert "[REDACTED_URL]" in payload["message"]


def test_json_formatter_redacts_json_quoted_sensitive_keys() -> None:
    record = logging.LogRecord(
        name="jx-core",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg=(
            'vendor payload={"password":"hunter2","api_key":"key-value",'
            '"refresh_token":"refresh-value",'
            '"authorization":"Bearer bearer-value"}'
        ),
        args=(),
        exc_info=None,
    )

    serialized = JsonFormatter("jx-core").format(record)

    assert "hunter2" not in serialized
    assert "key-value" not in serialized
    assert "refresh-value" not in serialized
    assert "bearer-value" not in serialized
    assert serialized.count("[REDACTED]") == 4


def test_json_formatter_redacts_structured_sensitive_value_conservatively() -> None:
    record = logging.LogRecord(
        name="jx-core",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg='vendor payload={"token":{"value":"nested-token","scope":"all"}}',
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter("jx-core").format(record))

    assert payload["message"] == "sensitive log details redacted"
    assert "nested-token" not in json.dumps(payload)


def test_json_formatter_redacts_inline_traceback_text() -> None:
    record = logging.LogRecord(
        name="jx-core",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg=(
            'vendor error: Traceback (most recent call last): File '
            '"/Users/private/project/main.py", line 4, password=hunter2'
        ),
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter("jx-core").format(record))

    assert payload["message"] == "internal exception details redacted"
    assert payload["error_code"] == "internal_exception"


def test_json_formatter_does_not_serialize_exception_info() -> None:
    try:
        raise RuntimeError("password=hunter2 postgresql+psycopg://jx:secret@localhost/jx")
    except RuntimeError:
        exception_info = sys.exc_info()

    record = logging.LogRecord(
        name="jx-core",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg="database operation failed",
        args=(),
        exc_info=exception_info,
    )
    serialized = JsonFormatter("jx-core").format(record)
    payload = json.loads(serialized)

    assert payload["message"] == "database operation failed"
    assert payload["error_code"] == "internal_exception"
    assert "hunter2" not in serialized
    assert "secret" not in serialized
