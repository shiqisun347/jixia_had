"""Import-safe app factory for OpenAPI tooling and tests.

The runnable server entry point is the ``jx-core`` console script.  Keeping
this module free of a module-level ``app`` prevents a direct
``uvicorn jx_core.main:app`` launch from bypassing the bounded shutdown
configuration in :mod:`jx_core.cli`.
"""

from fastapi import FastAPI

from .app import create_app
from .config import load_settings
from .logging import configure_logging
from .runtime import CoreStartupError


def build_app() -> FastAPI:
    """Build the ASGI app while keeping invalid secret inputs out of logs."""

    try:
        settings = load_settings()
    except Exception:
        configure_logging("jx-core", "INFO").error(
            "core configuration is invalid",
            extra={"error_code": "configuration_invalid"},
        )
        raise SystemExit(2) from None
    try:
        return create_app(settings)
    except CoreStartupError:
        raise SystemExit(1) from None


__all__ = ["build_app", "create_app"]
