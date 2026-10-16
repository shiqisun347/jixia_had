"""Normal HTTP/WS/RTC clients for one isolated Spec307 App; import performs no IO.

Only the caller's exact fresh owned PostgreSQL URL may receive QA fixture role
updates. All rooms, seats, commands, sessions and media tokens use Core APIs.
Credentials/cookies/tokens stay in memory and must never enter result artifacts.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from livekit import rtc
from sqlalchemy import text
from sqlalchemy.engine import make_url
from websockets.asyncio.client import ClientConnection, connect


class ClientFailure(RuntimeError):
    def __init__(self, code: str, status: int | None = None) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def local_app_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise ClientFailure("qa_loopback_app_required")
    return value.rstrip("/")


def safe_code(value: Any) -> str:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value)
        else "qa_http_failed"
    )


@dataclass(repr=False)
class Person:
    label: str
    side: str | None
    seat: int | None
    user_id: UUID
    username: str
    password: str = field(repr=False)
    http: httpx.AsyncClient = field(repr=False)
    epoch: int | None = None
    socket: ClientConnection | None = field(default=None, repr=False)
    receiver: asyncio.Task[None] | None = field(default=None, repr=False)
    pending: dict[str, asyncio.Future[dict[str, Any]]] = field(
        default_factory=lambda: dict[str, asyncio.Future[dict[str, Any]]](), repr=False
    )
    command_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    publisher: HumanPublisher | None = field(default=None, repr=False)
    events: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=512))
    events_seen: int = 0
    events_evicted: int = 0


class HumanPublisher:
    def __init__(self) -> None:
        self.room = rtc.Room()
        self.source = rtc.AudioSource(48000, 1, queue_size_ms=100)
        self.track = rtc.LocalAudioTrack.create_audio_track("spec307-public-material", self.source)
        self.closed = False

    async def open(self, token: dict[str, Any], match_id: UUID) -> None:
        if token["room_name"] != f"jx-match-{match_id}":
            raise ClientFailure("qa_media_room_mismatch")
        await asyncio.wait_for(
            self.room.connect(
                token["server_url"],
                token["participant_token"],
                rtc.RoomOptions(auto_subscribe=False),
            ),
            15,
        )
        await asyncio.wait_for(
            self.room.local_participant.publish_track(
                self.track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
            ),
            10,
        )

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.source.clear_queue()
        try:
            await asyncio.wait_for(self.room.disconnect(), 15)
        finally:
            await self.source.aclose()


class ConferenceClients:
    def __init__(self, base_url: str, settings: Any) -> None:
        self.base_url = local_app_url(base_url)
        self.settings = settings
        self.origin = settings.cors_origin_list[0]
        self.people: tuple[Person, ...] = ()
        self.accounts: list[Person] = []
        self.admin: Person | None = None
        self.audience: Person | None = None
        self.room_id: UUID | None = None
        self.match_id: UUID | None = None
        self.rule_id: UUID | None = None
        self.room_snapshot: dict[str, Any] = {}
        self.events: list[dict[str, Any]] = []
        self.closed = False

    async def request(
        self, person: Person, method: str, path: str, payload: Any = None
    ) -> dict[str, Any]:
        response = await person.http.request(
            method, path, json=payload if method != "GET" else None
        )
        try:
            body = response.json()
        except ValueError:
            raise ClientFailure("qa_response_not_json", response.status_code) from None
        if not response.is_success:
            raise ClientFailure(safe_code(body.get("error", {}).get("code")), response.status_code)
        if not isinstance(body, dict):
            raise ClientFailure("qa_response_not_object", response.status_code)
        return cast(dict[str, Any], body)

    async def _register(
        self, tag: str, label: str, *, side: str | None = None, seat: int | None = None
    ) -> Person:
        client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Origin": self.origin, "Referer": self.origin + "/"},
            follow_redirects=False,
            trust_env=False,
            timeout=30,
        )
        username = f"qa307-{tag[:8]}-{label}"
        password = secrets.token_urlsafe(24)
        person = Person(label, side, seat, uuid4(), username, password, client)
        self.accounts.append(person)
        terms = await self.request(person, "GET", "/api/legal/platform-terms/current")
        registered = await self.request(
            person,
            "POST",
            "/api/auth/register",
            {
                "username": username,
                "real_name": f"公开测试{label}",
                "password": password,
                "platform_terms_version": terms["version"],
            },
        )
        person.user_id = UUID(registered["user"]["id"])
        # Revoke the registration session before creating the measured login session.
        await self.request(person, "POST", "/api/auth/logout", {})
        await self._login(person)
        return person

    async def _login(self, person: Person) -> None:
        result = await self.request(
            person,
            "POST",
            "/api/auth/login",
            {"username": person.username, "password": person.password},
        )
        if UUID(result["user"]["id"]) != person.user_id:
            raise ClientFailure("qa_login_identity_changed")

    async def _reuse_admin(self, identity: Person, factory: Any) -> None:
        # The previous client's HTTP session is never shared across matches.
        async with factory() as session:
            found = await session.scalar(
                text(
                    "SELECT id FROM users WHERE id=:id AND username=:name "
                    "AND role='ADMIN' AND status='ACTIVE' AND must_change_password=false"
                ),
                {"id": identity.user_id, "name": identity.username},
            )
            if found != identity.user_id:
                raise ClientFailure("qa_existing_admin_fixture_not_exact")
        client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Origin": self.origin, "Referer": self.origin + "/"},
            follow_redirects=False,
            trust_env=False,
            timeout=30,
        )
        self.admin = Person(
            "admin", None, None, identity.user_id, identity.username, identity.password, client
        )
        self.accounts.append(self.admin)
        await self._login(self.admin)

    @classmethod
    async def create(
        cls,
        base_url: str,
        settings: Any,
        factory: Any,
        tag: str,
        rule_id: UUID,
        *,
        owned_database_url: str,
        production_database_url: str,
        existing_admin: Person | None = None,
        before_room: Callable[[ConferenceClients], Awaitable[None]] | None = None,
    ) -> ConferenceClients:
        if (
            settings.app_env == "production"
            or owned_database_url == production_database_url
            or settings.database_url_value != owned_database_url
        ):
            raise ClientFailure("qa_owned_app_settings_required")
        owner_db = make_url(owned_database_url)
        if owner_db.database not in {"jxqa", "jx_debate_qa_agent_faults"} or owner_db.host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ClientFailure("qa_owned_database_required")
        async with factory() as session:
            actual = session.bind.url
            if actual.render_as_string(hide_password=False) != owner_db.render_as_string(
                hide_password=False
            ):
                raise ClientFailure("qa_factory_database_mismatch")
            if await session.scalar(text("SELECT current_database()")) != owner_db.database:
                raise ClientFailure("qa_owned_database_name_mismatch")
        value = cls(base_url, settings)
        try:
            value.rule_id = rule_id
            if existing_admin is None:
                value.admin = await value._register(tag, "admin")
                # Only fresh owned bootstrap/registered QA fixture roles change.
                async with factory() as session:
                    async with session.begin():
                        # Migration 0016 seeds a dormant administrator. In this
                        # fresh owned fixture, retire its role without reading
                        # its password, then use the normally registered user.
                        bootstrap = (
                            await session.execute(
                                text("SELECT id, username_normalized FROM users WHERE role='ADMIN'")
                            )
                        ).all()
                        if bootstrap:
                            if (
                                len(bootstrap) != 1
                                or bootstrap[0][1] != "admin"
                                or await session.scalar(text("SELECT count(*) FROM matches")) != 0
                                or await session.scalar(
                                    text("SELECT count(*) FROM sessions WHERE user_id=:id"),
                                    {"id": bootstrap[0][0]},
                                )
                                != 0
                            ):
                                raise ClientFailure("qa_bootstrap_admin_not_dormant")
                            retired = await session.execute(
                                text(
                                    "UPDATE users SET role='USER' WHERE id=:id "
                                    "AND username_normalized='admin' AND role='ADMIN' RETURNING id"
                                ),
                                {"id": bootstrap[0][0]},
                            )
                            if retired.scalar_one_or_none() != bootstrap[0][0]:
                                raise ClientFailure("qa_bootstrap_admin_changed")
                        changed = await session.execute(
                            text(
                                "UPDATE users SET role='ADMIN' WHERE id=:id AND username=:name "
                                "AND role='USER' AND status='ACTIVE' "
                                "AND must_change_password=false RETURNING id"
                            ),
                            {"id": value.admin.user_id, "name": value.admin.username},
                        )
                        if changed.scalar_one_or_none() != value.admin.user_id:
                            raise ClientFailure("qa_new_admin_fixture_not_exact")
                await value.request(value.admin, "POST", "/api/auth/logout", {})
                await value._login(value.admin)
            else:
                await value._reuse_admin(existing_admin, factory)
            assert value.admin is not None
            if before_room is not None:
                await before_room(value)
            terms = await value.request(
                value.admin, "GET", "/api/legal/human-participation/current"
            )
            room = await value.request(
                value.admin,
                "POST",
                "/api/rooms",
                {
                    "title": f"Spec307公开合成全赛-{tag[:8]}",
                    "label": "隔离完整链测试",
                    "rule_id": str(rule_id),
                    "conference_roster": True,
                    "custom_topic_title": "公共决策应更重视过程还是结果",
                    "affirmative_text": "公共决策应更重视过程",
                    "negative_text": "公共决策应更重视结果",
                    "human_participation_terms_version": terms["version"],
                },
            )
            value.room_id = UUID(room["id"])
            await value.request(
                value.admin,
                "POST",
                f"/api/rooms/{value.room_id}/role",
                {"member_role": "SPECTATOR"},
            )
            people: list[Person] = []
            for side in ("AFFIRMATIVE", "NEGATIVE"):
                for seat in (1, 3):
                    person = await value._register(
                        tag,
                        ("a" if side == "AFFIRMATIVE" else "n") + str(seat),
                        side=side,
                        seat=seat,
                    )
                    people.append(person)
                    await value.request(
                        person,
                        "POST",
                        f"/api/rooms/{value.room_id}/join",
                        {
                            "member_role": "DEBATER",
                            "human_participation_terms_version": terms["version"],
                        },
                    )
                    await value.request(
                        person,
                        "POST",
                        f"/api/rooms/{value.room_id}/seat",
                        {
                            "side": side,
                            "seat_no": seat,
                            "human_participation_terms_version": terms["version"],
                        },
                    )
                    check = await value.request(
                        person,
                        "POST",
                        f"/api/rooms/{value.room_id}/device-check",
                        {"status": "PASS", "details": {"synthetic_48k_pcm": True}},
                    )
                    await value.request(
                        person,
                        "POST",
                        f"/api/rooms/{value.room_id}/ready",
                        {"check_version": check["latest_device_check"]["check_version"]},
                    )
            value.people = tuple(people)
            value.audience = await value._register(tag, "audience")
            await value.request(
                value.audience,
                "POST",
                f"/api/rooms/{value.room_id}/join",
                {"member_role": "SPECTATOR"},
            )
            value.room_snapshot = await value.request(
                value.admin, "GET", f"/api/rooms/{value.room_id}/snapshot"
            )
            return value
        except BaseException:
            await value.close()
            raise

    async def prepare_and_connect(self) -> dict[str, Any]:
        if self.admin is None or self.room_id is None:
            raise ClientFailure("qa_room_not_created")
        await self.request(self.admin, "POST", f"/api/rooms/{self.room_id}/start", {})
        snapshot = await self.request(
            self.admin, "POST", f"/api/rooms/{self.room_id}/prepare-match", {}
        )
        self.match_id = UUID(snapshot["match_id"])
        for person in self.people:
            await self.connect_person(person)
        return snapshot

    async def connect_person(self, person: Person) -> None:
        if self.match_id is None:
            raise ClientFailure("qa_match_not_prepared")
        if person.socket is not None:
            raise ClientFailure("qa_person_socket_already_open")
        parsed = urlsplit(self.base_url)
        url = urlunsplit(("ws", parsed.netloc, f"/api/matches/{self.match_id}/events", "", ""))
        cookie = "; ".join(f"{item.name}={item.value}" for item in person.http.cookies.jar)
        person.socket = await connect(
            url,
            origin=self.origin,
            additional_headers={"Cookie": cookie},
            proxy=None,
            max_size=2**20,
        )
        first = json.loads(await asyncio.wait_for(person.socket.recv(), 10))
        if first.get("type") != "match.snapshot" or type(first.get("connection_epoch")) is not int:
            raise ClientFailure("qa_corews_handshake_invalid")
        person.epoch = first["connection_epoch"]
        person.receiver = asyncio.create_task(
            self._receive(person), name=f"qa-corews-{person.label}"
        )
        token = await self.request(
            person,
            "POST",
            f"/api/matches/{self.match_id}/livekit-token",
            {"connection_epoch": person.epoch},
        )
        person.publisher = HumanPublisher()
        await person.publisher.open(token, self.match_id)

    async def _receive(self, person: Person) -> None:
        assert person.socket is not None
        try:
            async for raw in person.socket:
                value = json.loads(raw)
                person.events_seen += 1
                if len(person.events) == person.events.maxlen:
                    person.events_evicted += 1
                person.events.append(
                    {k: value[k] for k in ("type", "sequence", "message_id", "code") if k in value}
                )
                if value.get("type") in {"command.ack", "command.error"}:
                    pending = person.pending.get(str(value.get("message_id")))
                    if pending is not None and not pending.done():
                        pending.set_result(value)
        finally:
            for future in person.pending.values():
                if not future.done():
                    future.set_exception(ClientFailure("qa_corews_closed"))

    async def snapshot(self, person: Person | None = None) -> dict[str, Any]:
        viewer = person or self.admin
        if viewer is None or self.match_id is None:
            raise ClientFailure("qa_match_not_prepared")
        return await self.request(viewer, "GET", f"/api/matches/{self.match_id}/snapshot")

    async def command(
        self,
        person: Person,
        command_type: str,
        payload: dict[str, Any] | None = None,
        *,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        async with person.command_lock:
            if person.socket is None or person.epoch is None:
                raise ClientFailure("qa_corews_not_connected")
            if payload is not None and set(payload) - {"reasons"}:
                raise ClientFailure("qa_command_payload_forbidden")
            stable_id = message_id or f"qa307-{uuid4().hex}"
            for attempt in range(2):
                snapshot = await self.snapshot(person)
                future = asyncio.get_running_loop().create_future()
                person.pending[stable_id] = future
                try:
                    await person.socket.send(
                        json.dumps(
                            {
                                "type": command_type,
                                "message_id": stable_id,
                                "expected_sequence": snapshot["sequence"],
                                "connection_epoch": person.epoch,
                                **(payload or {}),
                            }
                        )
                    )
                    result = await asyncio.wait_for(future, 30)
                finally:
                    person.pending.pop(stable_id, None)
                if result["type"] == "command.ack":
                    return result["snapshot"]
                code = safe_code(result.get("code"))
                if code != "match_state_conflict" or attempt:
                    raise ClientFailure(code)
            raise ClientFailure("qa_command_conflict")

    async def start_match(self) -> dict[str, Any]:
        if self.admin is None or self.room_id is None:
            raise ClientFailure("qa_room_not_created")
        return await self.request(
            self.admin, "POST", f"/api/rooms/{self.room_id}/runtime-start", {}
        )

    async def control(
        self, action: str, target: dict[str, Any] | None = None, *, operation_id: UUID | None = None
    ) -> dict[str, Any]:
        if self.admin is None or self.match_id is None:
            raise ClientFailure("qa_match_not_prepared")
        if target is None:
            target = await self.request(
                self.admin, "GET", f"/api/admin/matches/{self.match_id}/control-target"
            )
        if str(target.get("match_id")) != str(self.match_id):
            raise ClientFailure("qa_control_target_mismatch")
        payload: dict[str, Any] = {"action": action, "operation_id": str(operation_id or uuid4())}
        for field_name in (
            "sequence",
            "action_key",
            "stage_generation",
            "speech_id",
            "context_version",
        ):
            payload["expected_" + field_name] = target.get(field_name)
        return await self.request(
            self.admin, "POST", f"/api/admin/matches/{self.match_id}/control", payload
        )

    async def human_speak(
        self, person: Person, pcm: bytes, *, manual_finish: bool = False
    ) -> dict[str, Any]:
        if person.publisher is None or len(pcm) % 2:
            raise ClientFailure("qa_pcm48k_publisher_required")
        start = await self.command(person, "speech.start")
        speech_id = start.get("current_speech_id")
        if not speech_id:
            raise ClientFailure("qa_human_start_missing_speech")
        clock = monotonic()
        sent = 0
        snapshot = start
        next_snapshot = clock
        for offset in range(0, len(pcm), 1920):
            if monotonic() >= next_snapshot:
                snapshot = await self.snapshot(person)
                next_snapshot = monotonic() + 0.25
            if (
                snapshot.get("current_speech_id") != speech_id
                or snapshot.get("action_state") != "HUMAN_SPEAKING"
            ):
                break
            if monotonic() - clock > float(start.get("speech_remaining_ms") or 0) / 1000 + 5:
                raise ClientFailure("qa_human_authoritative_deadline_stuck")
            await asyncio.sleep(max(0, clock + sent / 48000 - monotonic()))
            frame = pcm[offset : offset + 1920]
            await person.publisher.source.capture_frame(
                rtc.AudioFrame(
                    data=frame,
                    sample_rate=48000,
                    num_channels=1,
                    samples_per_channel=len(frame) // 2,
                )
            )
            sent += len(frame) // 2
        await person.publisher.source.wait_for_playout()
        if manual_finish:
            current = await self.snapshot(person)
            if (
                current.get("current_speech_id") == speech_id
                and current.get("action_state") == "HUMAN_SPEAKING"
            ):
                await self.command(person, "speech.finish")
        elif sent * 2 >= len(pcm):
            raise ClientFailure("qa_long_material_exhausted_before_authoritative_end")
        return {
            "speech_id": speech_id,
            "source_samples": len(pcm) // 2,
            "sent_samples": sent,
            "source_exhausted": sent * 2 >= len(pcm),
            "wall_ms": round((monotonic() - clock) * 1000, 3),
        }

    def browser_credentials(self, role: str) -> dict[str, str]:
        person = self.admin if role == "admin" else self.audience
        if person is None:
            raise ClientFailure("qa_browser_account_missing")
        return {"username": person.username, "password": person.password}

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        errors: list[str] = []
        for person in reversed(self.accounts):
            if person.publisher is not None:
                try:
                    await person.publisher.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if person.socket is not None:
                try:
                    await person.socket.close()
                except Exception as exc:
                    errors.append(type(exc).__name__)
            if person.receiver is not None:
                person.receiver.cancel()
                await asyncio.gather(person.receiver, return_exceptions=True)
            try:
                await self.request(person, "POST", "/api/auth/logout", {})
            except Exception as exc:
                errors.append(type(exc).__name__)
            person.http.cookies.clear()
            await person.http.aclose()
        if errors:
            raise ClientFailure("qa_client_cleanup_failed")
