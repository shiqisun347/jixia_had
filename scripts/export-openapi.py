"""Export the core OpenAPI document without starting a server."""

from __future__ import annotations

import json
import os

from jx_core.app import create_app
from jx_core.config import Settings


def main() -> None:
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://jx:change-me@127.0.0.1:5432/jx_debate",
    )
    app = create_app(Settings(database_url=database_url))
    print(json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
