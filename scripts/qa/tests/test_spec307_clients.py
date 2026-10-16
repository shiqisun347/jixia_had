"""Pure controls for the normal transport QA client; no Core App or provider."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

spec = importlib.util.spec_from_file_location(
    "spec307_clients", Path(__file__).parents[1] / "probes/server_spec307_clients.py"
)
assert spec is not None and spec.loader is not None

module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ConferenceClients, Person, ClientFailure = (
    module.ConferenceClients,
    module.Person,
    module.ClientFailure,
)


@pytest.mark.asyncio
async def test_long_event_stream_keeps_bounded_metadata_and_delivers_last_ack() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    person = Person("a1", None, None, uuid4(), "owned", "memory-only", httpx.AsyncClient())

    class Socket:
        async def __aiter__(self):
            for sequence in range(5000):
                yield json.dumps({"type": "match.event", "sequence": sequence, "text": "unused"})
            yield json.dumps({"type": "command.ack", "message_id": "last-command"})

    person.socket = cast(Any, Socket())
    future = person.pending["last-command"] = asyncio.get_running_loop().create_future()
    try:
        await clients._receive(person)
        assert future.result()["type"] == "command.ack"
        assert len(person.events) == 512
        assert person.events_seen == 5001 and person.events_evicted == 4489
        assert all("text" not in item for item in person.events)
    finally:
        await person.http.aclose()


@pytest.mark.parametrize(
    "url",
    [
        "https://debate.vsagents.online",
        "http://127.0.0.1",
        "http://user:password@127.0.0.1:8027",
        "http://127.0.0.1:8027/path",
    ],
)
def test_live_or_nonowned_app_is_rejected_without_io(url: str) -> None:
    with pytest.raises(ClientFailure, match="qa_loopback_app_required"):
        ConferenceClients(url, SimpleNamespace(cors_origin_list=["http://localhost:3000"]))


@pytest.mark.asyncio
async def test_production_database_guard_fails_before_factory_or_http() -> None:
    def forbidden() -> Any:
        raise AssertionError("factory entered before database guard")

    settings = SimpleNamespace(app_env="development", database_url_value="same")
    with pytest.raises(ClientFailure, match="qa_owned_app_settings_required"):
        await ConferenceClients.create(
            "http://127.0.0.1:8027",
            settings,
            forbidden,
            "owned",
            uuid4(),
            owned_database_url="same",
            production_database_url="same",
        )


@pytest.mark.asyncio
async def test_one_ws_sequence_conflict_keeps_id_and_authority_fields() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    sent: list[dict[str, Any]] = []
    person = Person(
        "a1",
        "AFFIRMATIVE",
        1,
        uuid4(),
        "public-owned-user",
        "temporary-only",
        httpx.AsyncClient(),
        epoch=4,
    )

    async def snapshot(_person: Any) -> dict[str, Any]:
        return {"sequence": 10 + len(sent)}

    async def send(raw: str) -> None:
        value = json.loads(raw)
        sent.append(value)
        answer = (
            {"type": "command.error", "code": "match_state_conflict"}
            if len(sent) == 1
            else {"type": "command.ack", "snapshot": {"sequence": 12}}
        )
        person.pending[value["message_id"]].set_result(answer)

    clients.snapshot = snapshot
    person.socket = cast(Any, SimpleNamespace(send=send))
    try:
        result = await clients.command(person, "speech.start", message_id="owned-stable-id")
        assert result == {"sequence": 12}
        assert [item["expected_sequence"] for item in sent] == [10, 11]
        assert {item["message_id"] for item in sent} == {"owned-stable-id"}
        assert {item["connection_epoch"] for item in sent} == {4}
        with pytest.raises(ClientFailure, match="qa_command_payload_forbidden"):
            await clients.command(person, "speech.start", {"message_id": "overridden"})
        assert len(sent) == 2 and not person.pending
    finally:
        await person.http.aclose()


@pytest.mark.asyncio
async def test_http_provider_message_is_not_copied_to_error() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    http = httpx.AsyncClient(
        base_url=clients.base_url,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                409,
                json={
                    "error": {
                        "code": "asr_finalization_pending",
                        "message": "private vendor detail",
                    }
                },
            )
        ),
    )
    person = Person("a1", None, None, uuid4(), "public-owned-user", "temporary-only", http)
    try:
        with pytest.raises(ClientFailure) as caught:
            await clients.request(person, "POST", "/api/owned", {})
        assert str(caught.value) == "asr_finalization_pending"
        assert caught.value.status == 409 and "private" not in repr(caught.value)
        assert "temporary-only" not in repr(person)
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_control_uses_exact_target_without_silent_redirect() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    clients.admin = Person(
        "admin", None, None, uuid4(), "public-admin", "temporary-only", httpx.AsyncClient()
    )
    clients.match_id = uuid4()
    calls: list[dict[str, Any]] = []

    async def request(
        _person: Any, _method: str, _path: str, payload: Any = None
    ) -> dict[str, Any]:
        calls.append(payload)
        return {"status": "accepted"}

    clients.request = request
    target = {
        "match_id": str(clients.match_id),
        "sequence": 9,
        "action_key": "action",
        "stage_generation": 2,
        "speech_id": str(uuid4()),
        "context_version": 5,
    }
    op = uuid4()
    try:
        await clients.control("retry_asr", target, operation_id=op)
        assert calls == [
            {
                "action": "retry_asr",
                "operation_id": str(op),
                **{"expected_" + key: value for key, value in target.items() if key != "match_id"},
            }
        ]
        with pytest.raises(ClientFailure, match="qa_control_target_mismatch"):
            await clients.control("retry_asr", {**target, "match_id": str(uuid4())})
        assert len(calls) == 1
    finally:
        await clients.admin.http.aclose()


@pytest.mark.asyncio
async def test_cleanup_continues_to_other_owned_clients_and_logout_after_media_failure() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    people = [
        Person(str(n), None, None, uuid4(), "owned", "temporary-only", httpx.AsyncClient())
        for n in range(2)
    ]
    clients.accounts = people
    calls: list[str] = []

    async def request(person: Any, *_args: Any) -> dict[str, Any]:
        calls.append(person.label)
        return {"status": "logged_out"}

    clients.request = request
    people[1].publisher = cast(Any, SimpleNamespace(close=AsyncMock(side_effect=TimeoutError())))
    with pytest.raises(ClientFailure, match="qa_client_cleanup_failed"):
        await clients.close()
    assert calls == ["1", "0"] and all(person.http.is_closed for person in people)
    await clients.close()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_existing_admin_reuses_identity_with_fresh_http_session() -> None:
    clients = ConferenceClients(
        "http://127.0.0.1:8027", SimpleNamespace(cors_origin_list=["http://localhost:3000"])
    )
    old = Person(
        "admin", None, None, uuid4(), "qa307-owned-admin", "memory-only", httpx.AsyncClient()
    )

    class Session:
        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def scalar(self, query: Any, params: dict[str, Any]) -> Any:
            assert "UPDATE" not in str(query) and params == {
                "id": old.user_id,
                "name": old.username,
            }
            return old.user_id

    clients._login = AsyncMock()
    try:
        await clients._reuse_admin(old, Session)
        assert clients.admin.user_id == old.user_id
        assert clients.admin.password == old.password
        assert clients.admin.http is not old.http
        assert clients.accounts == [clients.admin]
        clients._login.assert_awaited_once_with(clients.admin)
    finally:
        await old.http.aclose()
        if clients.admin is not None:
            await clients.admin.http.aclose()
