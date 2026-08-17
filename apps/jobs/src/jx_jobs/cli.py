"""Console entry point for ``jx-jobs``."""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from .audio_worker import process_one_file_cleanup, process_one_postmatch_audio
from .config import Settings, load_settings
from .database import Database
from .export_worker import process_one_match_export
from .host_tts_worker import process_one_host_tts
from .leaderboard_worker import (
    ensure_daily_tasks,
    process_one_leaderboard,
    process_one_transcript_archive,
)
from .logging import configure_logging
from .runner import DatabaseProbe, run_jobs
from .tts import DashScopeTTSClient

SettingsFactory = Callable[[], Settings]
DatabaseFactory = Callable[[str], DatabaseProbe]


def _stop_event() -> asyncio.Event:
    event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for termination_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(termination_signal, event.set)
        except (NotImplementedError, RuntimeError):
            # Windows and embedded event loops may not expose signal handlers;
            # the supervisor can still cancel the task.
            continue
    return event


def main(
    argv: Sequence[str] | None = None,
    *,
    settings_factory: SettingsFactory = load_settings,
    database_factory: DatabaseFactory = Database,
) -> None:
    parser = argparse.ArgumentParser(prog="jx-jobs")
    parser.add_argument(
        "--once",
        action="store_true",
        help="validate configuration/database once and exit without claiming work",
    )
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    # pnpm forwards arguments written as `pnpm dev:jobs -- --once` with an
    # additional separator. Treat that one leading separator as transport
    # syntax, while keeping the console script's normal `--once` behavior.
    if raw_argv and raw_argv[0] == "--":
        raw_argv = raw_argv[1:]
    args = parser.parse_args(raw_argv)
    try:
        settings = settings_factory()
    except Exception:
        configure_logging("jx-jobs", "INFO").error(
            "jobs configuration is invalid",
            extra={"error_code": "configuration_invalid"},
        )
        raise SystemExit(2) from None

    logger = configure_logging("jx-jobs", settings.log_level)
    try:
        database = database_factory(settings.database_url_value)
    except Exception:
        logger.error(
            "jobs database initialization failed",
            extra={"error_code": "database_unavailable"},
        )
        raise SystemExit(1) from None

    async def execute() -> int:
        tts_client: DashScopeTTSClient | None = None
        task_processor = None
        if settings.tts_ws_url and settings.dashscope_api_key is not None:
            concrete_database = cast(Database, database)
            tts_client = DashScopeTTSClient(
                websocket_url=settings.tts_ws_url,
                api_key=settings.dashscope_api_key.get_secret_value(),
                workspace=settings.dashscope_workspace,
            )

            async def process_tasks() -> bool:
                await ensure_daily_tasks(concrete_database.session_factory)
                if await process_one_host_tts(
                    concrete_database.session_factory,
                    client=tts_client,
                    storage_root=Path(settings.host_audio_storage_dir),
                ):
                    return True
                if await process_one_leaderboard(concrete_database.session_factory):
                    return True
                if await process_one_transcript_archive(concrete_database.session_factory):
                    return True
                if await process_one_postmatch_audio(
                    concrete_database.session_factory,
                    storage_root=Path(settings.match_audio_storage_dir),
                    host_storage_root=Path(settings.host_audio_storage_dir),
                ):
                    return True
                if await process_one_match_export(
                    concrete_database.session_factory,
                    storage_root=Path(settings.export_storage_dir),
                    audio_roots=[
                        Path(settings.match_audio_storage_dir),
                        Path(settings.human_audio_storage_dir),
                        Path(settings.host_audio_storage_dir),
                    ],
                ):
                    return True
                return await process_one_file_cleanup(
                    concrete_database.session_factory,
                    storage_roots=[
                        Path(settings.match_audio_storage_dir),
                        Path(settings.human_audio_storage_dir),
                    ],
                )

            task_processor = process_tasks
        elif not args.once:
            concrete_database = cast(Database, database)

            async def process_tasks_without_tts() -> bool:
                await ensure_daily_tasks(concrete_database.session_factory)
                if await process_one_leaderboard(concrete_database.session_factory):
                    return True
                if await process_one_transcript_archive(concrete_database.session_factory):
                    return True
                if await process_one_postmatch_audio(
                    concrete_database.session_factory,
                    storage_root=Path(settings.match_audio_storage_dir),
                    host_storage_root=Path(settings.host_audio_storage_dir),
                ):
                    return True
                if await process_one_match_export(
                    concrete_database.session_factory,
                    storage_root=Path(settings.export_storage_dir),
                    audio_roots=[
                        Path(settings.match_audio_storage_dir),
                        Path(settings.human_audio_storage_dir),
                        Path(settings.host_audio_storage_dir),
                    ],
                ):
                    return True
                return await process_one_file_cleanup(
                    concrete_database.session_factory,
                    storage_roots=[
                        Path(settings.match_audio_storage_dir),
                        Path(settings.human_audio_storage_dir),
                    ],
                )

            task_processor = process_tasks_without_tts
        # Signal handlers must be installed while the event loop is running.
        try:
            return await run_jobs(
                once=args.once,
                database=database,
                stop_event=None if args.once else _stop_event(),
                task_processor=task_processor,
            )
        finally:
            if tts_client is not None:
                await tts_client.close()

    try:
        exit_code = asyncio.run(execute())
    except Exception:
        logger.error(
            "jobs startup failed",
            extra={"error_code": "jobs_startup_failed"},
        )
        raise SystemExit(1) from None
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
