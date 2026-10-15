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


class HeartbeatConnection(FakeConnection):
    async def recv(self) -> str | bytes:
        await asyncio.sleep(0.005)
        return json.dumps({"header": {"task_id": self.task_id, "event": "heartbeat"}})


class HangingCloseConnection(FakeConnection):
    async def close(self) -> None:
        await asyncio.Future()


class FailingCancelConnection(FakeConnection):
    async def send(self, message: str) -> None:
        payload = json.loads(message)
        if payload["payload"]["input"].get("directive") == "cancel":
            raise RuntimeError("provider connection is already closed")
        await super().send(message)


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


def test_tts_cancel_is_best_effort_when_provider_rejects_cancel() -> None:
    async def run() -> None:
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="test-workspace",
        )
        connection = FailingCancelConnection()
        cast(Any, client)._connection = connection
        await client.cancel("task-id")
        assert cast(Any, client)._connection is connection

    asyncio.run(run())


def test_tts_close_is_bounded_when_provider_close_hangs(monkeypatch) -> None:
    async def run() -> None:
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="test-workspace",
        )
        connection = HangingCloseConnection()
        cast(Any, client)._connection = connection
        monkeypatch.setattr(DashScopeTTSClient, "_CLOSE_TIMEOUT_SECONDS", 0.01)
        await asyncio.wait_for(client.close(), timeout=0.2)
        assert cast(Any, client)._connection is None

    asyncio.run(run())


def test_tts_heartbeat_cannot_extend_start_deadline() -> None:
    async def run() -> None:
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="test-workspace",
            timeout_seconds=0.02,
            task_timeout_seconds=0.025,
        )
        connection = HeartbeatConnection()
        client._connection = cast(Any, connection)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "host.opus"
            try:
                await client.synthesize_to_file(
                    text="主持测试文本。",
                    voice="longanlingxi",
                    rate=1.0,
                    output_path=output,
                )
            except Exception as error:
                assert getattr(error, "code", None) == "tts_task_timeout"
            else:
                raise AssertionError("heartbeat stream unexpectedly completed")

    asyncio.run(run())


def test_tts_audio_budget_removes_oversized_output() -> None:
    async def run() -> None:
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="test-workspace",
            max_audio_bytes=4,
        )
        connection = FakeConnection()
        client._connection = cast(Any, connection)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "host.opus"
            try:
                await client.synthesize_to_file(
                    text="主持测试文本。",
                    voice="longanlingxi",
                    rate=1.0,
                    output_path=output,
                )
            except Exception as error:
                assert getattr(error, "code", None) == "tts_audio_too_large"
            else:
                raise AssertionError("oversized audio unexpectedly completed")
            assert not output.exists()

    asyncio.run(run())


def test_tts_optional_capture_has_shared_budget_and_close_releases_unsubmitted_payload() -> None:
    from jx_core.data_capture.provider_writer import ProviderCaptureWriter

    async def run() -> None:
        writer = ProviderCaptureWriter(
            "postgresql+psycopg://jx:synthetic@localhost/jx_debate_qa_agent_faults",
            service="jx-jobs",
        )
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="",
            capture_factory=writer.new_capture,
        )
        client._connection = cast(Any, FakeConnection())
        try:
            with TemporaryDirectory() as directory:
                await client.synthesize_to_file(
                    text="合成采集测试",
                    voice="synthetic",
                    rate=1.0,
                    output_path=Path(directory) / "audio.ogg",
                )
            assert writer.stats()["items"] == 1
            assert writer.stats()["retained_bytes"] > 0
            capture = client.take_capture()
            assert capture is not None and capture.request and capture.response
            assert len(capture.request.payload["body"]["messages"]) == 3
            assert len(capture.response.payload["events"]) == 2
            capture.close()
            assert writer.stats()["items"] == writer.stats()["retained_bytes"] == 0
        finally:
            await client.close()
            await writer.close()

    asyncio.run(run())


def test_tts_continues_authority_output_when_capture_budget_rejects_reservation() -> None:
    from jx_core.data_capture.provider_writer import ProviderCaptureWriter

    async def run() -> None:
        writer = ProviderCaptureWriter(
            "postgresql+psycopg://jx:synthetic@localhost/jx_debate_qa_agent_faults",
            service="jx-jobs",
        )
        writer.budget.accepting = False
        client = DashScopeTTSClient(
            websocket_url="wss://example.invalid/inference",
            api_key="test-key",
            workspace="",
            capture_factory=writer.new_capture,
        )
        client._connection = cast(Any, FakeConnection())
        try:
            with TemporaryDirectory() as directory:
                output = Path(directory) / "audio.ogg"
                await client.synthesize_to_file(
                    text="合成采集测试",
                    voice="synthetic",
                    rate=1.0,
                    output_path=output,
                )
                assert output.read_bytes() == b"OggS-probe"
            capture = client.take_capture()
            assert capture is not None and capture.request and capture.response
            assert capture.request.status == capture.response.status == "UNAVAILABLE"
            assert writer.stats()["items"] == writer.stats()["retained_bytes"] == 0
            capture.close()
        finally:
            await client.close()
            await writer.close()

    asyncio.run(run())
