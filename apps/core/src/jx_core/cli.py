"""Console entry point for the single-worker ``jx-core`` process."""

from __future__ import annotations

import sys

import uvicorn

from .app import create_app
from .config import load_settings
from .logging import configure_logging
from .runtime import CoreStartupError


def main() -> None:
    """Validate settings before handing lifecycle control to Uvicorn."""

    try:
        settings = load_settings()
    except Exception:
        # Never print pydantic's input values: they may contain DATABASE_URL.
        configure_logging("jx-core", "INFO").error(
            "core configuration is invalid",
            extra={"error_code": "configuration_invalid"},
        )
        raise SystemExit(2) from None

    try:
        app = create_app(settings)
    except CoreStartupError:
        raise SystemExit(1) from None

    uvicorn.run(
        app,
        host=settings.core_host,
        port=settings.core_port,
        workers=1,
        timeout_graceful_shutdown=2,
        log_config=None,
    )


if __name__ == "__main__":
    sys.exit(main())
