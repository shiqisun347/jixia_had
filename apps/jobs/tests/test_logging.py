from __future__ import annotations

import json
import logging
import sys

from jx_jobs.logging import JsonFormatter


def test_json_formatter_redacts_credentials_dsn_path_and_traceback() -> None:
    record = logging.LogRecord(
        name="jx-jobs",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg=(
            "failed postgresql+psycopg://jx:topsecret@localhost/jx "
            "password=hunter2 token=token-value at /private/project/job.py"
        ),
        args=(),
        exc_info=None,
    )
    serialized = JsonFormatter("jx-jobs").format(record)

    assert "topsecret" not in serialized
    assert "hunter2" not in serialized
    assert "token-value" not in serialized
    assert "/private/project" not in serialized

    try:
        raise RuntimeError("api_key=secret-key")
    except RuntimeError:
        exception_info = sys.exc_info()
    exception_record = logging.LogRecord(
        name="jx-jobs",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg="jobs operation failed",
        args=(),
        exc_info=exception_info,
    )
    payload = json.loads(JsonFormatter("jx-jobs").format(exception_record))

    assert payload["message"] == "jobs operation failed"
    assert payload["error_code"] == "internal_exception"


def test_json_formatter_redacts_json_quoted_sensitive_keys() -> None:
    record = logging.LogRecord(
        name="jx-jobs",
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

    serialized = JsonFormatter("jx-jobs").format(record)

    assert "hunter2" not in serialized
    assert "key-value" not in serialized
    assert "refresh-value" not in serialized
    assert "bearer-value" not in serialized
    assert serialized.count("[REDACTED]") == 4


def test_json_formatter_redacts_structured_sensitive_value_conservatively() -> None:
    record = logging.LogRecord(
        name="jx-jobs",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg='vendor payload={"token":{"value":"nested-token","scope":"all"}}',
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter("jx-jobs").format(record))

    assert payload["message"] == "sensitive log details redacted"
    assert "nested-token" not in json.dumps(payload)


def test_json_formatter_redacts_inline_traceback_text() -> None:
    record = logging.LogRecord(
        name="jx-jobs",
        level=logging.ERROR,
        pathname="/internal/path.py",
        lineno=10,
        msg=(
            'vendor error: Traceback (most recent call last): File '
            '"/private/project/job.py", line 4, token=token-value'
        ),
        args=(),
        exc_info=None,
    )

    payload = json.loads(JsonFormatter("jx-jobs").format(record))

    assert payload["message"] == "internal exception details redacted"
    assert payload["error_code"] == "internal_exception"
