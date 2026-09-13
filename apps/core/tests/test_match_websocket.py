from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.websockets import WebSocketDisconnect, WebSocketState

from jx_core.matches import routes
from jx_core.matches.routes import _SerializedWebSocketSender, _SocketClosed

# pyright: reportPrivateUsage=false, reportPrivateImportUsage=false, reportUnknownLambdaType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false


class _FakeWebSocket:
    def __init__(self) -> None:
        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.calls: list[object] = []
        self.active_sends = 0
        self.max_active_sends = 0
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.error: Exception | None = None

    async def send_json(self, value: object) -> None:
        self.calls.append(value)
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        self.started.set()
        try:
            if self.error is not None:
                raise self.error
            await self.release.wait()
        finally:
            self.active_sends -= 1

    async def close(self, *, code: int) -> None:
        self.application_state = WebSocketState.DISCONNECTED
        self.calls.append({"close": code})


class _SessionContext(AbstractAsyncContextManager[object]):
    def __init__(self, session: object | None = None) -> None:
        self.session = session or object()

    async def __aenter__(self) -> object:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _Dumpable:
    def __init__(self, value: dict[str, object]) -> None:
        self.value = value

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return self.value


@pytest.mark.asyncio
async def test_websocket_sender_serializes_event_and_command_messages() -> None:
    websocket = _FakeWebSocket()
    sender = _SerializedWebSocketSender(cast(Any, websocket))

    first = asyncio.create_task(sender.send_json({"type": "match.event"}))
    await websocket.started.wait()
    second = asyncio.create_task(sender.send_json({"type": "command.ack"}))
    await asyncio.sleep(0)

    assert websocket.calls == [{"type": "match.event"}]
    assert websocket.max_active_sends == 1

    websocket.release.set()
    await asyncio.gather(first, second)

    assert websocket.calls == [
        {"type": "match.event"},
        {"type": "command.ack"},
    ]
    assert websocket.max_active_sends == 1


@pytest.mark.asyncio
async def test_websocket_sender_stops_after_transport_disconnect() -> None:
    websocket = _FakeWebSocket()
    websocket.error = OSError("client disconnected")
    sender = _SerializedWebSocketSender(cast(Any, websocket))

    with pytest.raises(_SocketClosed):
        await sender.send_json({"type": "command.ack"})
    with pytest.raises(_SocketClosed):
        await sender.send_json({"type": "match.event"})

    assert websocket.calls == [{"type": "command.ack"}]


@pytest.mark.asyncio
async def test_match_route_sender_disconnect_wakes_receiver_and_cleans_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match_id = uuid4()
    room_id = uuid4()
    user_id = uuid4()
    connection_id = uuid4()
    event_queue: asyncio.Queue[object] = asyncio.Queue()
    event_queue.put_nowait(object())
    manager = SimpleNamespace(
        snapshot_view=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(status="RUNNING"))),
        subscribe=AsyncMock(return_value=event_queue),
        unsubscribe=AsyncMock(),
    )
    presence = SimpleNamespace(websocket_joined=AsyncMock(), websocket_left=AsyncMock())
    release = AsyncMock(return_value=True)
    acquire = AsyncMock(
        return_value=SimpleNamespace(connection_epoch=3, connection_id=connection_id)
    )
    monkeypatch.setattr(routes, "_websocket_context", AsyncMock(return_value=SimpleNamespace(
        user_id=user_id, session_id=uuid4(), role="USER"
    )))
    monkeypatch.setattr(routes, "_manager", lambda _app: manager)
    monkeypatch.setattr(
        routes,
        "_member",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=match_id),
                SimpleNamespace(id=room_id),
                SimpleNamespace(member_role="DEBATER"),
            )
        ),
    )
    monkeypatch.setattr(routes, "_snapshot_response", lambda *_args, **_kwargs: _Dumpable({}))
    monkeypatch.setattr(routes, "_event_response", lambda *_args, **_kwargs: _Dumpable({}))
    monkeypatch.setattr(routes.RoomConnectionService, "acquire", acquire)
    monkeypatch.setattr(routes.RoomConnectionService, "release", release)

    receive_cancelled = asyncio.Event()

    class _RouteWebSocket(_FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.app = SimpleNamespace(
                state=SimpleNamespace(
                    runtime=SimpleNamespace(
                        database=SimpleNamespace(session_factory=lambda: _SessionContext())
                    ),
                    presence=presence,
                )
            )
            self.send_count = 0

        async def accept(self) -> None:
            return None

        async def send_json(self, value: object) -> None:
            self.send_count += 1
            if self.send_count == 2:
                raise OSError("client disconnected")
            self.calls.append(value)

        async def receive_json(self) -> object:
            try:
                await asyncio.Event().wait()
            finally:
                receive_cancelled.set()

    websocket = _RouteWebSocket()

    await routes.match_events(cast(Any, websocket), UUID(str(match_id)))

    assert receive_cancelled.is_set()
    manager.unsubscribe.assert_awaited_once_with(match_id, event_queue)
    release.assert_awaited_once()
    presence.websocket_joined.assert_awaited_once()
    presence.websocket_left.assert_awaited_once()


@pytest.mark.asyncio
async def test_match_route_human_start_returns_ack_with_authoritative_speech_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match_id = uuid4()
    room_id = uuid4()
    user_id = uuid4()
    speech_id = uuid4()
    event_queue: asyncio.Queue[object] = asyncio.Queue()
    initial_view = SimpleNamespace(state=SimpleNamespace(status="RUNNING"))
    committed_view = SimpleNamespace(
        state=SimpleNamespace(status="RUNNING", current_speech_id=speech_id)
    )
    manager = SimpleNamespace(
        snapshot_view=AsyncMock(side_effect=[initial_view, initial_view, committed_view]),
        subscribe=AsyncMock(return_value=event_queue),
        unsubscribe=AsyncMock(),
        get_actor=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(sequence=7))),
        submit=AsyncMock(
            return_value=SimpleNamespace(
                duplicate=False,
                state=SimpleNamespace(sequence=8, current_speech_id=speech_id),
            )
        ),
    )
    presence = SimpleNamespace(websocket_joined=AsyncMock(), websocket_left=AsyncMock())
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(
        routes,
        "_websocket_context",
        AsyncMock(
            return_value=SimpleNamespace(user_id=user_id, session_id=uuid4(), role="USER")
        ),
    )
    monkeypatch.setattr(routes, "_manager", lambda _app: manager)
    monkeypatch.setattr(
        routes,
        "_member",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=match_id),
                SimpleNamespace(id=room_id),
                SimpleNamespace(member_role="DEBATER"),
            )
        ),
    )
    monkeypatch.setattr(
        routes,
        "_snapshot_response",
        lambda view, *_args, **_kwargs: _Dumpable(
            {
                "current_speech_id": str(
                    getattr(view.state, "current_speech_id", None) or speech_id
                )
            }
        ),
    )
    monkeypatch.setattr(
        routes.RoomConnectionService,
        "acquire",
        AsyncMock(return_value=SimpleNamespace(connection_epoch=3)),
    )
    monkeypatch.setattr(routes.RoomConnectionService, "release", release)
    monkeypatch.setattr(routes, "_match_page_permissions", AsyncMock(return_value=(False, True)))

    scalar_session = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()))
    receive_count = 0

    class _RouteWebSocket(_FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.app = SimpleNamespace(
                state=SimpleNamespace(
                    runtime=SimpleNamespace(
                        database=SimpleNamespace(
                            session_factory=lambda: _SessionContext(scalar_session)
                        )
                    ),
                    presence=presence,
                )
            )

        async def accept(self) -> None:
            return None

        async def send_json(self, value: object) -> None:
            self.calls.append(value)

        async def receive_json(self) -> object:
            nonlocal receive_count
            receive_count += 1
            if receive_count == 1:
                return {
                    "type": "speech.start",
                    "message_id": "human-start",
                    "expected_sequence": 7,
                    "connection_epoch": 3,
                }
            raise WebSocketDisconnect(code=1000)

    websocket = _RouteWebSocket()

    await routes.match_events(cast(Any, websocket), match_id)

    submitted = manager.submit.await_args.args[1]
    assert submitted.type == "speech.start"
    assert submitted.actor_user_id == user_id
    assert submitted.payload["connection_epoch"] == 3
    ack = next(
        cast(dict[str, object], item)
        for item in websocket.calls
        if isinstance(item, dict) and item.get("type") == "command.ack"
    )
    assert ack["message_id"] == "human-start"
    assert ack["sequence"] == 8
    assert cast(dict[str, object], ack["snapshot"])["current_speech_id"] == str(speech_id)
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_match_route_keeps_socket_after_unexpected_command_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    match_id = uuid4()
    room_id = uuid4()
    user_id = uuid4()
    connection_id = uuid4()
    event_queue: asyncio.Queue[object] = asyncio.Queue()
    view = SimpleNamespace(state=SimpleNamespace(status="RUNNING"))
    manager = SimpleNamespace(
        snapshot_view=AsyncMock(return_value=view),
        subscribe=AsyncMock(return_value=event_queue),
        unsubscribe=AsyncMock(),
        get_actor=AsyncMock(return_value=SimpleNamespace(state=SimpleNamespace(sequence=7))),
        submit=AsyncMock(
            side_effect=[
                RuntimeError("database details must stay private"),
                SimpleNamespace(duplicate=False, state=SimpleNamespace(sequence=8)),
            ]
        ),
    )
    presence = SimpleNamespace(websocket_joined=AsyncMock(), websocket_left=AsyncMock())
    release = AsyncMock(return_value=True)
    monkeypatch.setattr(
        routes,
        "_websocket_context",
        AsyncMock(return_value=SimpleNamespace(user_id=user_id, session_id=uuid4(), role="USER")),
    )
    monkeypatch.setattr(routes, "_manager", lambda _app: manager)
    monkeypatch.setattr(
        routes,
        "_member",
        AsyncMock(
            return_value=(
                SimpleNamespace(id=match_id),
                SimpleNamespace(id=room_id),
                SimpleNamespace(member_role="DEBATER"),
            )
        ),
    )
    monkeypatch.setattr(routes, "_snapshot_response", lambda *_args, **_kwargs: _Dumpable({}))
    monkeypatch.setattr(
        routes.RoomConnectionService,
        "acquire",
        AsyncMock(return_value=SimpleNamespace(connection_epoch=3, connection_id=connection_id)),
    )
    monkeypatch.setattr(routes.RoomConnectionService, "release", release)
    monkeypatch.setattr(routes, "_match_page_permissions", AsyncMock(return_value=(False, True)))
    scalar_session = SimpleNamespace(scalar=AsyncMock(return_value=uuid4()))
    commands = iter(
        [
            {
                "type": "speech.start",
                "message_id": "failed-start",
                "expected_sequence": 7,
                "connection_epoch": 3,
            },
            {
                "type": "speech.start",
                "message_id": "retried-start",
                "expected_sequence": 7,
                "connection_epoch": 3,
            },
        ]
    )

    class _RouteWebSocket(_FakeWebSocket):
        def __init__(self) -> None:
            super().__init__()
            self.app = SimpleNamespace(
                state=SimpleNamespace(
                    runtime=SimpleNamespace(
                        database=SimpleNamespace(
                            session_factory=lambda: _SessionContext(scalar_session)
                        )
                    ),
                    presence=presence,
                )
            )

        async def accept(self) -> None:
            return None

        async def send_json(self, value: object) -> None:
            self.calls.append(value)

        async def receive_json(self) -> object:
            try:
                return next(commands)
            except StopIteration as error:
                raise WebSocketDisconnect(code=1000) from error

    websocket = _RouteWebSocket()
    await routes.match_events(cast(Any, websocket), match_id)

    messages = [item for item in websocket.calls if isinstance(item, dict)]
    command_error = next(item for item in messages if item.get("type") == "command.error")
    command_ack = next(item for item in messages if item.get("type") == "command.ack")
    assert command_error == {
        "type": "command.error",
        "message_id": "failed-start",
        "code": "internal_server_error",
        "message": "服务暂时不可用，请稍后重试",
    }
    assert "database details" not in str(messages)
    assert command_ack["message_id"] == "retried-start"
    assert manager.submit.await_count == 2
    release.assert_awaited_once()
