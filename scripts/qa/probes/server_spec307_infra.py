"""Owned, loopback-only LiveKit for the Spec307 single-App qualification.

No production containers, configuration, ports or credentials are reused.
The private configuration is removed together with this exact labelled owner.
"""

from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import SecretStr


class OwnedLiveKit:
    image = "livekit/livekit-server:v1.13.5"

    def __init__(self, *, core_port: int = 8027) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="jx-spec307-rtc-")
        self.root = Path(self.temporary.name)
        self.root.chmod(0o700)
        self.owner = secrets.token_hex(12)
        self.name = f"jx-spec307-rtc-{self.owner}"
        self.api_key = f"QA307{secrets.token_hex(8)}"
        self.api_secret = secrets.token_hex(32)
        self.core_port = core_port
        self.started = False

    @staticmethod
    def _command(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(arguments, capture_output=True, text=True, timeout=30, check=False)

    def start(self) -> dict[str, Any]:
        if os.geteuid() != 0:
            raise RuntimeError("qa_owned_livekit_requires_root")
        if self._command(["docker", "image", "inspect", self.image]).returncode:
            raise RuntimeError("qa_exact_livekit_image_unavailable")
        for port, kind in (
            (7887, socket.SOCK_STREAM),
            (7888, socket.SOCK_STREAM),
            (7889, socket.SOCK_DGRAM),
        ):
            with socket.socket(socket.AF_INET, kind) as probe:
                probe.bind(("127.0.0.1", port))
        # JSON is valid YAML. Secret values are never command arguments or logs.
        config = self.root / "livekit.yaml"
        config.write_text(
            json.dumps(
                {
                    "port": 7887,
                    "bind_addresses": ["127.0.0.1"],
                    "logging": {"level": "error"},
                    "rtc": {
                        "tcp_port": 7888,
                        "udp_port": 7889,
                        "node_ip": "127.0.0.1",
                        "use_external_ip": False,
                        "enable_loopback_candidate": True,
                    },
                    "keys": {self.api_key: self.api_secret},
                    "webhook": {
                        "api_key": self.api_key,
                        "urls": [f"http://127.0.0.1:{self.core_port}/api/livekit/webhook"],
                    },
                }
            ),
            encoding="utf-8",
        )
        config.chmod(0o600)
        started = self._command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                self.name,
                "--label",
                f"jx.qa.owner={self.owner}",
                "--label",
                "jx.qa.spec=307",
                "--network",
                "host",
                "--memory",
                "256m",
                "--memory-swap",
                "256m",
                "--mount",
                f"type=bind,src={config},dst=/qa/livekit.yaml,readonly",
                self.image,
                "--config",
                "/qa/livekit.yaml",
            ]
        )
        if started.returncode:
            raise RuntimeError("qa_owned_livekit_start_failed")
        self.started = True
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", 7887), timeout=0.2):
                    return {
                        "livekit_url": "ws://127.0.0.1:7887",
                        "livekit_api_key": SecretStr(self.api_key),
                        "livekit_api_secret": SecretStr(self.api_secret),
                    }
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("qa_owned_livekit_not_ready")

    def close(self) -> None:
        if self.started:
            owner = self._command(
                [
                    "docker",
                    "inspect",
                    "--format",
                    '{{index .Config.Labels "jx.qa.owner"}}',
                    self.name,
                ]
            )
            if owner.returncode or owner.stdout.strip() != self.owner:
                raise RuntimeError("qa_livekit_cleanup_owner_changed")
            if self._command(["docker", "rm", "--force", self.name]).returncode:
                raise RuntimeError("qa_owned_livekit_cleanup_failed")
            self.started = False
        self.temporary.cleanup()


__all__ = ["OwnedLiveKit"]
