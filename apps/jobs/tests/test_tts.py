import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast

from websockets.protocol import State

from jx_jobs.tts import DashScopeTTSClient


class FakeConnection:
    state = State.OPEN

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.task_id = ""
        self.messages: list[str | bytes] = []

    async def send(self, message: str) -> None:
        payload = json.loads(message)
        self.sent.append(payload)
        self.task_id = payload["header"]["task_id"]
        if payload["header"]["action"] == "run-task":
            self.messages.append(
                json.dumps({"header": {"task_id": self.task_id, "event": "task-started"}})
            )
        elif payload["header"]["action"] == "finish-task":
            self.messages.extend(
                [
                    b"OggS-probe",
                    json.dumps({"header": {"task_id": self.task_id, "event": "task-finished"}}),
                ]
            )

    async def recv(self) -> str | bytes:
        return self.messages.pop(0)

    async def close(self) -> None:
        self.state = State.CLOSED


def test_tts_duplex_protocol_writes_opus_and_reuses_connection() -> None:
    async def run() -> None:
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="test-workspace",
        )
        connection = FakeConnection()
        client._connection = cast(Any, connection)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "host.opus"
            await client.synthesize_to_file(
                text="主持测试文本。",
                voice="longanlingxi",
                rate=1.0,
                output_path=output,
            )
            assert output.read_bytes() == b"OggS-probe"
        assert [item["header"]["action"] for item in connection.sent] == [
            "run-task",
            "continue-task",
            "finish-task",
        ]
        assert connection.sent[0]["payload"]["parameters"]["format"] == "opus"
        assert connection.sent[0]["payload"]["parameters"]["sample_rate"] == 24000
        assert connection.sent[0]["payload"]["parameters"]["bit_rate"] == 32
        assert connection.sent[1]["header"]["task_id"] == connection.sent[0]["header"]["task_id"]

    asyncio.run(run())
