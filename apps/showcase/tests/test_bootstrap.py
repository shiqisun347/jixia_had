import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ROOT, create_app, main, password_matches
from fastapi.testclient import TestClient
from ops.init_config import initialize


class BootstrapTests(unittest.TestCase):
    def test_private_credentials_are_new_random_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "private"
            password = secrets.token_urlsafe(18)
            initialize(directory, password)
            password_file, key_file = directory / "admin-hash", directory / "cookie-key"
            self.assertTrue(password_matches(password, password_file.read_text()))
            self.assertEqual(len(key_file.read_bytes()), 32)
            self.assertEqual(password_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
            before = key_file.read_bytes()
            with self.assertRaises(FileExistsError):
                initialize(directory, "another-synthetic-password")
            self.assertEqual(key_file.read_bytes(), before)

    def test_explicit_credentials_env_starts_empty_without_published_content(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            initialize(directory / "private", secrets.token_urlsafe(18))
            configuration = {
                "SHOWCASE_DATA_DIR": str(directory / "data"),
                "SHOWCASE_PASSWORD_FILE": str(directory / "private/admin-hash"),
                "SHOWCASE_COOKIE_KEY_FILE": str(directory / "private/cookie-key"),
                "SHOWCASE_ORIGIN": "http://127.0.0.1:8123",
                "SHOWCASE_PORT": "8123",
            }
            with (
                patch.dict(os.environ, configuration, clear=True),
                patch.object(sys, "argv", ["app.py", "--local-http"]),
                patch("uvicorn.run") as run,
            ):
                main()
            self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
            self.assertEqual(run.call_args.kwargs["port"], 8123)
            with TestClient(run.call_args.args[0]) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertFalse(client.get("/api/state").json()["published"])
                self.assertEqual(client.get("/api/admin").status_code, 401)
            self.assertFalse((directory / "data/cookie-key").exists())

    def test_cli_has_no_default_origin_or_private_credentials(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith("SHOWCASE_")}
        completed = subprocess.run(
            [sys.executable, str(ROOT / "app.py")],
            env=environment,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn(b"--cookie-key-file", completed.stderr)

    def test_explicit_cookie_key_is_required_valid_and_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            initialize(root / "private", secrets.token_urlsafe(18))
            key_file = root / "private/cookie-key"
            before = key_file.read_bytes()
            for _ in range(2):
                app = create_app(
                    root / "data",
                    "https://showcase.test",
                    root / "private/admin-hash",
                    cookie_key_file=key_file,
                )
                with TestClient(app) as client:
                    self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(key_file.read_bytes(), before)
            key_file.write_bytes(b"short")
            with self.assertRaises(ValueError):
                create_app(
                    root / "data",
                    "https://showcase.test",
                    root / "private/admin-hash",
                    cookie_key_file=key_file,
                )
