"""An isolated conference showcase. No imports or data from the debate platform."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import wave
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Literal

import segno
from audio_events import AudioReceipts, SignalHub
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

ROOT = Path(__file__).resolve().parent
IDS = [f"{side}-{seat}" for side in ("aff", "neg") for seat in range(1, 5)]
SIDES = {"aff": "AFFIRMATIVE", "neg": "NEGATIVE"}
GREETING_IDS = ("aff-2", "aff-4", "neg-2", "neg-4")
AUDIO_LEASE_SECONDS = 6
AUDIO_HIDDEN_CONNECTION_SECONDS = 90
AUDIO_COMMAND_SECONDS = 20
AUDIO_EXECUTION_SECONDS = 4


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Participant(Model):
    id: str
    name: str = Field(min_length=1, max_length=30)
    kind: Literal["HUMAN", "AGENT"] = "HUMAN"
    avatar: Literal[
        "human-01",
        "human-02",
        "human-03",
        "human-04",
        "agent-01",
        "agent-02",
        "agent-03",
        "agent-04",
    ] = "human-01"


class Content(Model):
    title: str = Field(default="稷下 · 现场争鸣", min_length=1, max_length=80)
    topic: str = Field(default="一场思想与智能的对话", min_length=1, max_length=180)
    affirmative: str = Field(default="正方主张", min_length=1, max_length=140)
    negative: str = Field(default="反方主张", min_length=1, max_length=140)
    participants: list[Participant] = Field(min_length=8, max_length=8)
    phase: Literal["intro", "vote", "review", "results"] = "vote"
    result_view: Literal["audience", "judge"] = "audience"
    voting_open: bool = False
    comments: str = Field(default="", max_length=12000)
    winner: Literal["", "AFFIRMATIVE", "NEGATIVE", "DRAW"] = ""
    best: str = ""

    @model_validator(mode="after")
    def valid_ids(self):
        if [p.id for p in self.participants] != IDS:
            raise ValueError("八席必须按正方一至四辩、反方一至四辩排列")
        if self.best and self.best not in IDS:
            raise ValueError("最佳辩手必须属于本场八席")
        return self


class Draft(Model):
    revision: int = Field(ge=0, strict=True)
    content: Content


class Version(Model):
    revision: int = Field(ge=0, strict=True)


class DisplayLease(Model):
    display_epoch: str = Field(min_length=1, max_length=64)
    round_id: str = Field(min_length=1, max_length=80)
    display_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sequence: int = Field(ge=1, le=9007199254740991, strict=True)
    active: bool


class AudioDisplay(DisplayLease):
    probe_no: int = Field(default=0, ge=0, le=9007199254740991, strict=True)
    display_key: str = Field(pattern=r"^[a-f0-9]{64}$")
    ready: bool
    status: Literal["idle", "playing", "ended", "blocked", "error", "hidden"]
    command_id: int = Field(ge=0, le=9007199254740991, strict=True)


class AudioProbe(Model):
    round_id: str = Field(min_length=1, max_length=80)
    display_epoch: str = Field(min_length=1, max_length=64)


class AudioConfirmation(AudioProbe):
    command_id: int = Field(ge=1, le=9007199254740991, strict=True)


class Playback(Model):
    probe_no: int = Field(default=0, ge=0, le=9007199254740991, strict=True)
    round_id: str = Field(min_length=1, max_length=80)
    display_epoch: str = Field(min_length=1, max_length=64)
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    action: Literal["play", "stop"]
    participant_id: str = Field(default="", max_length=20)

    @model_validator(mode="after")
    def valid_participant(self):
        if self.action == "play" and self.participant_id not in GREETING_IDS:
            raise ValueError("请选择四位Agent之一")
        if self.action == "stop" and self.participant_id:
            raise ValueError("停止播放不需要辩手")
        return self


class Login(Model):
    password: str = Field(min_length=1, max_length=200)


class Vote(Model):
    round_id: str = Field(max_length=80)
    winner: Literal["AFFIRMATIVE", "NEGATIVE"]
    best: str = Field(max_length=20)

    @model_validator(mode="after")
    def valid_best(self):
        if self.best not in IDS:
            raise ValueError("请选择一位本场最佳辩手")
        return self


def initial_content():
    return Content(
        participants=[
            Participant(
                id=pid,
                name=f"{'正' if pid.startswith('aff') else '反'}方{pid[-1]}辩",
                avatar=f"human-0{pid[-1]}",
            )
            for pid in IDS
        ]
    ).model_dump()


def password_hash(password: str, salt: str | None = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt${salt}${digest}"


def password_matches(password: str, stored: str):
    try:
        _, salt, expected = stored.strip().split("$")
        return hmac.compare_digest(password_hash(password, salt).split("$")[-1], expected)
    except (ValueError, TypeError):
        return False


def load_greetings(assets: Path):
    """Read only the packaged, bounded public metadata; missing audio stays unavailable."""
    names = ("执锋 Agent", "博闻 Agent", "睿断 Agent", "明辨 Agent")
    greetings = {
        pid: {
            "id": pid,
            "name": name,
            "voice": "",
            "text": "",
            "url": "",
            "duration": 0,
            "available": False,
        }
        for pid, name in zip(GREETING_IDS, names, strict=True)
    }
    try:
        manifest = assets / "greetings/manifest.json"
        if manifest.stat().st_size > 65536:
            return list(greetings.values())
        data = json.loads(manifest.read_text())
        entries = data.get("greetings", []) if isinstance(data, dict) else data
        if not isinstance(entries, list) or len(entries) > 4:
            return list(greetings.values())
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pid = entry.get("id")
            if pid not in greetings or pid in seen:
                continue
            seen.add(pid)
            url = entry.get("url", "")
            duration = entry.get("duration", 0)
            if (
                not isinstance(url, str)
                or not url.startswith("/assets/greetings/")
                or not url.endswith(".wav")
                or len(url) > 180
                or not isinstance(duration, (float, int))
                or isinstance(duration, bool)
                or not 0 < duration <= 60
                or any(
                    not isinstance(entry.get(key), str) or not 0 < len(entry[key]) <= limit
                    for key, limit in (("name", 60), ("voice", 100), ("text", 800))
                )
            ):
                continue
            audio = (assets / url.removeprefix("/assets/")).resolve()
            if not audio.is_relative_to((assets / "greetings").resolve()):
                continue
            available = False
            if audio.is_file() and 44 <= audio.stat().st_size <= 12000000:
                try:
                    with wave.open(str(audio), "rb") as wav:
                        available = (
                            wav.getnchannels() in (1, 2)
                            and wav.getsampwidth() == 2
                            and 8000 <= wav.getframerate() <= 48000
                            and wav.getnframes() > 0
                            and abs(wav.getnframes() / wav.getframerate() - duration) < 0.1
                            and len(wav.readframes(wav.getnframes()))
                            == wav.getnframes() * wav.getnchannels() * wav.getsampwidth()
                        )
                except (wave.Error, EOFError, OSError):
                    pass
            greetings[pid].update(
                **{key: entry[key] for key in ("name", "voice", "text", "url")},
                duration=duration,
                available=available,
            )
    except (OSError, ValueError, TypeError):
        pass
    return list(greetings.values())


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "showcase.sqlite3"
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS rounds(id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                draft TEXT NOT NULL, published TEXT, created_at INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS current_round(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS votes(round_id TEXT NOT NULL, voter TEXT NOT NULL,
                winner TEXT NOT NULL, ranking TEXT NOT NULL, created_at INTEGER NOT NULL,
                PRIMARY KEY(round_id,voter));
              CREATE TABLE IF NOT EXISTS sessions(
                token_hash TEXT PRIMARY KEY, expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS display_leases(display_id TEXT PRIMARY KEY,
                round_id TEXT NOT NULL, phase_token TEXT NOT NULL, sequence INTEGER NOT NULL,
                expires REAL NOT NULL, updated_at REAL NOT NULL);
              CREATE INDEX IF NOT EXISTS display_round_expiry ON display_leases(round_id, expires);
              CREATE TABLE IF NOT EXISTS audio_displays(display_id TEXT PRIMARY KEY,
                round_id TEXT NOT NULL, phase_token TEXT NOT NULL, sequence INTEGER NOT NULL,
                expires REAL NOT NULL, updated_at REAL NOT NULL, ready INTEGER NOT NULL,
                status TEXT NOT NULL, command_id INTEGER NOT NULL, capability_hash TEXT NOT NULL);
              CREATE INDEX IF NOT EXISTS audio_display_expiry ON audio_displays(round_id, expires);
              CREATE TABLE IF NOT EXISTS audio_commands(id INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id TEXT NOT NULL UNIQUE, round_id TEXT NOT NULL, phase_token TEXT NOT NULL,
                action TEXT NOT NULL, participant_id TEXT NOT NULL, url TEXT NOT NULL,
                display_ids TEXT NOT NULL, issued_at REAL NOT NULL, expires REAL NOT NULL);
            """)
            if not db.execute("SELECT id FROM current_round").fetchone():
                rid = secrets.token_hex(16)
                db.execute(
                    "INSERT INTO rounds VALUES(?,0,?,NULL,?)",
                    (
                        rid,
                        json.dumps(initial_content(), ensure_ascii=False),
                        int(time.time()),
                    ),
                )
                db.execute("INSERT INTO current_round VALUES(1,?)", (rid,))

    @contextmanager
    def db(self):
        con = sqlite3.connect(self.path, timeout=5)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA foreign_keys=ON")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def current(self, db):
        return db.execute("SELECT r.* FROM rounds r JOIN current_round c ON r.id=c.id").fetchone()

    def collection(self, db, row):
        published = json.loads(row["published"]) if row["published"] else None
        count = db.execute(
            "SELECT count(*) FROM display_leases WHERE round_id=? AND phase_token=? AND expires>?",
            (
                row["id"],
                published.get("_display_epoch", "legacy") if published else "",
                time.time(),
            ),
        ).fetchone()[0]
        return {
            "collecting": bool(published and published["phase"] == "vote" and count),
            "active_displays": count,
        }

    def audio_displays(self, db, row):
        published = json.loads(row["published"]) if row["published"] else None
        if not published or published["phase"] != "intro":
            return []
        return [
            {
                "display_id": display["display_id"],
                "ready": bool(display["ready"]),
                "status": display["status"],
                "command_id": display["command_id"],
                "expires_at": display["expires"],
            }
            for display in db.execute(
                "SELECT * FROM audio_displays WHERE round_id=? AND phase_token=? "
                "AND expires>? ORDER BY display_id",
                (row["id"], published.get("_display_epoch", "legacy"), time.time()),
            )
        ]

    def playback(self, db, row, *, with_validity=False):
        published = json.loads(row["published"]) if row["published"] else None
        if not published or published["phase"] != "intro":
            return None
        command = db.execute(
            "SELECT * FROM audio_commands WHERE round_id=? AND phase_token=? "
            "ORDER BY id DESC LIMIT 1",
            (row["id"], published.get("_display_epoch", "legacy")),
        ).fetchone()
        if not command or command["expires"] <= time.time():
            return None
        result = {
            "id": command["id"],
            "round_id": command["round_id"],
            "display_epoch": command["phase_token"],
            "action": command["action"],
            "participant_id": command["participant_id"],
            "url": command["url"],
            "display_ids": json.loads(command["display_ids"]),
            "issued_at": command["issued_at"],
            "expires_at": command["expires"],
        }
        if with_validity:
            # A response delayed in transit must expire before confirmation can
            # cancel this command. The public 20s command retention is unchanged.
            execute_before = min(command["expires"], command["issued_at"] + AUDIO_EXECUTION_SECONDS)
            result["valid_for_seconds"] = max(0, execute_before - time.time())
        return result

    def totals(self, db, rid):
        scores = dict.fromkeys(IDS, 0)
        winners = {s: 0 for s in ("AFFIRMATIVE", "NEGATIVE")}
        total = 0
        for row in db.execute("SELECT winner,ranking FROM votes WHERE round_id=?", (rid,)):
            total += 1
            if row["winner"] in winners:
                winners[row["winner"]] += 1
            # Keep historical ballots intact; their first choice is one best vote.
            choices = json.loads(row["ranking"])
            if choices and choices[0] in scores:
                scores[choices[0]] += 1
        ordered = sorted(IDS, key=lambda pid: (-scores[pid], IDS.index(pid)))
        ranking = (
            [
                {
                    "id": pid,
                    "points": scores[pid],
                    "rank": i + 1,
                    "tied": list(scores.values()).count(scores[pid]) > 1,
                }
                for i, pid in enumerate(ordered)
            ]
            if total
            else []
        )
        return {"total": total, "winners": winners, "ranking": ranking}


class Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.windows = {}

    def check(self, key, limit, seconds):
        now = time.monotonic()
        with self.lock:
            self.windows = {k: v for k, v in self.windows.items() if v[0] > now}
            expires, count = self.windows.get(key, (now + seconds, 0))
            if count >= limit or (key not in self.windows and len(self.windows) >= 4096):
                raise HTTPException(429, "操作较频繁，请稍后再试。")
            self.windows[key] = (expires, count + 1)


def create_app(
    data_dir: Path,
    origin: str,
    password_file: Path,
    secure=True,
    *,
    cookie_key_file: Path | None = None,
):
    store = Store(data_dir)
    limiter = Limiter()
    screen_signals = SignalHub(32)
    report_signals = SignalHub(8)
    receipts = AudioReceipts()
    recovery = {}
    greetings = load_greetings(ROOT / "static/assets")
    key_file = cookie_key_file if cookie_key_file is not None else data_dir / "cookie-key"
    if cookie_key_file is None and not key_file.exists():
        fd = os.open(key_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
    signing_key = key_file.read_bytes()
    if cookie_key_file is not None and len(signing_key) < 32:
        raise ValueError("cookie_key_file_must_contain_at_least_32_bytes")
    qr_buffer = io.BytesIO()
    segno.make(origin + "/?mode=vote", error="h", micro=False).save(
        qr_buffer, kind="png", scale=12, border=4, dark="#14263e", light="#ffffff"
    )
    qr_png = qr_buffer.getvalue()
    session_cookie = "__Host-showcase_admin" if secure else "showcase_admin"
    voter_cookie = "__Host-showcase_voter" if secure else "showcase_voter"

    @asynccontextmanager
    async def lifespan(app):
        import anyio.to_thread

        anyio.to_thread.current_default_thread_limiter().total_tokens = 8
        yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    password_gate = threading.Semaphore(2)
    app.state.store = store
    app.state.screen_signals = screen_signals
    app.state.audio_receipts = receipts
    app.state.report_signals = report_signals

    def cookie(response, name, value, age):
        response.set_cookie(
            name,
            value,
            max_age=age,
            secure=secure,
            httponly=True,
            samesite="strict",
            path="/",
        )

    def voter(request):
        value = request.cookies.get(voter_cookie, "")
        if len(value) > 200 or "." not in value:
            return None
        token, signature = value.rsplit(".", 1)
        expected = hmac.new(signing_key, token.encode(), hashlib.sha256).hexdigest()
        return token if len(token) == 32 and hmac.compare_digest(signature, expected) else None

    def require_admin(request):
        token = request.cookies.get(session_cookie, "")
        with store.db() as db:
            row = db.execute(
                "SELECT expires FROM sessions WHERE token_hash=?",
                (hashlib.sha256(token.encode()).hexdigest(),),
            ).fetchone()
        if not row or row[0] <= time.time():
            raise HTTPException(401, "请先输入控制台管理口令。")

    def check_version(row, version):
        if row["revision"] != version:
            raise HTTPException(
                409,
                "另一控制台已更新内容。请重新载入后再操作，当前修改尚未覆盖服务器。",
            )

    @app.middleware("http")
    async def boundaries(request: Request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            if request.headers.get("origin") != origin:
                return JSONResponse({"detail": "请求来源不符，请从本站页面操作。"}, status_code=403)
            length = request.headers.get("content-length", "")
            if not length.isdigit():
                return JSONResponse({"detail": "无法确认提交内容长度。"}, status_code=411)
            if int(length) > 65536:
                return JSONResponse({"detail": "内容太长，请缩短后再提交。"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable"
            if request.url.path
            in {
                "/assets/award-trophy-v1.png",
                "/assets/award-medal-v1.png",
                "/assets/judges-commentary-v2.webp",
            }
            or (
                response.status_code == 200
                and re.fullmatch(
                    r"/assets/avatars/(aff|neg)-[1-4]-[0-9a-f]{12}\.webp",
                    request.url.path,
                )
            )
            or (
                request.url.path.startswith("/assets/greetings/")
                and request.url.path.endswith(".wav")
            )
            else "public, max-age=86400"
            if request.url.path == "/api/vote-qr.png"
            else "no-store"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; "
            "base-uri 'none'; form-action 'self'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        return JSONResponse(
            {
                "detail": (
                    "请选择正方或反方，并选择一位最佳辩手；旧投票页面请刷新后重试。"
                    if request.url.path == "/api/vote"
                    else "提交内容不完整或格式不正确，请检查后重试。"
                )
            },
            status_code=422,
        )

    @app.get("/health")
    def health():
        return {"status": "ok", "app": "jixia-showcase"}

    @app.get("/")
    def screen():
        return FileResponse(ROOT / "static/index.html")

    @app.get("/control")
    def control():
        return FileResponse(ROOT / "static/control.html")

    @app.get("/api/state")
    def state(request: Request, response: Response):
        token = voter(request)
        if not token:
            token = secrets.token_hex(16)
            signature = hmac.new(signing_key, token.encode(), hashlib.sha256).hexdigest()
            cookie(response, voter_cookie, token + "." + signature, 60 * 60 * 24 * 90)
        with store.db() as db:
            row = store.current(db)
            published = json.loads(row["published"]) if row["published"] else None
            totals = store.totals(db, row["id"])
            collection = store.collection(db, row)
            playback = store.playback(db, row)
            has_voted = bool(
                db.execute(
                    "SELECT 1 FROM votes WHERE round_id=? AND voter=?",
                    (row["id"], token),
                ).fetchone()
            )
        result = {
            "revision": row["revision"],
            "round_id": row["id"],
            "published": bool(published),
            "has_voted": has_voted,
            "total_votes": totals["total"],
            **collection,
            "vote_url": origin + "/?mode=vote",
            "qr_url": "/api/vote-qr.png",
            "greetings": greetings,
            "playback": playback,
            "display_epoch": published.get("_display_epoch", "legacy") if published else "",
        }
        if published:
            if (
                published["phase"] == "intro"
                and recovery.get("round_id") == row["id"]
                and recovery.get("display_epoch") == result["display_epoch"]
            ):
                result["audio_recovery"] = dict(recovery)
            visible = {
                k: v
                for k, v in published.items()
                if k not in {"comments", "winner", "best", "_display_epoch"}
            }
            visible["voting_open"] = collection["collecting"]
            if published["phase"] == "results" and published["result_view"] == "judge":
                visible.update(winner=published["winner"], best=published["best"])
            result["content"] = visible
            if published["phase"] == "results" and published["result_view"] == "audience":
                result["audience"] = totals
        return result

    @app.get("/api/screen-events")
    async def screen_events(request: Request):
        if request.headers.get("origin") not in (None, origin):
            raise HTTPException(403, "请从本站打开大屏。")
        limiter.check(("screen-events", request.client.host), 60, 60)
        try:
            queue = screen_signals.subscribe()
        except OverflowError:
            raise HTTPException(503, "大屏连接较多，请稍后重试。") from None

        async def events():
            try:
                while not await request.is_disconnected():
                    yield (
                        "retry: 1000\nevent: refresh\ndata: "
                        + json.dumps({"probe_no": receipts.probe_no})
                        + "\n\n"
                    )
                    try:
                        await asyncio.wait_for(queue.get(), timeout=2)
                    except TimeoutError:
                        pass
            finally:
                screen_signals.unsubscribe(queue)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    @app.get("/api/vote-qr.png")
    def vote_qr():
        return Response(qr_png, media_type="image/png")

    @app.post("/api/display")
    def display_lease(payload: DisplayLease, request: Request):
        limiter.check(("display", request.client.host), 600, 60)
        now = time.time()
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            if row["id"] != payload.round_id:
                raise HTTPException(409, "展示轮次已更新，请重新载入。")
            published = json.loads(row["published"]) if row["published"] else None
            epoch = published.get("_display_epoch", "legacy") if published else ""
            if payload.display_epoch != epoch:
                raise HTTPException(409, "展示阶段已更新。")
            db.execute("DELETE FROM display_leases WHERE updated_at<?", (now - 60,))
            previous = db.execute(
                "SELECT sequence FROM display_leases WHERE display_id=?",
                (payload.display_id,),
            ).fetchone()
            if previous and payload.sequence <= previous["sequence"]:
                return {"accepted": False, **store.collection(db, row)}
            if (
                previous is None
                and db.execute("SELECT count(*) FROM display_leases").fetchone()[0] >= 512
            ):
                raise HTTPException(429, "展示连接较多，请稍后重试。")
            published = json.loads(row["published"]) if row["published"] else None
            active = bool(payload.active and published and published["phase"] == "vote")
            db.execute(
                """INSERT INTO display_leases VALUES(?,?,?,?,?,?)
                ON CONFLICT(display_id) DO UPDATE SET round_id=excluded.round_id,
                phase_token=excluded.phase_token, sequence=excluded.sequence,
                expires=excluded.expires, updated_at=excluded.updated_at""",
                (
                    payload.display_id,
                    row["id"],
                    payload.display_epoch,
                    payload.sequence,
                    now + 6 if active else 0,
                    now,
                ),
            )
            return {"accepted": True, **store.collection(db, row)}

    @app.post("/api/audio-display")
    def audio_display(payload: AudioDisplay, request: Request):
        limiter.check(("audio-display", request.client.host), 600, 60)
        now = time.time()
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            published = json.loads(row["published"]) if row["published"] else None
            epoch = published.get("_display_epoch", "legacy") if published else ""
            if row["id"] != payload.round_id or payload.display_epoch != epoch:
                raise HTTPException(409, "展示阶段已更新，请重新载入。")
            db.execute(
                "DELETE FROM audio_displays WHERE updated_at<?",
                (now - AUDIO_HIDDEN_CONNECTION_SECONDS,),
            )
            previous = db.execute(
                "SELECT * FROM audio_displays WHERE display_id=?",
                (payload.display_id,),
            ).fetchone()
            capability_hash = hashlib.sha256(payload.display_key.encode()).hexdigest()
            if previous and not hmac.compare_digest(previous["capability_hash"], capability_hash):
                raise HTTPException(403, "展示连接校验失败，请刷新大屏。")
            if previous and payload.sequence <= previous["sequence"]:
                return {
                    "accepted": False,
                    "playback": store.playback(db, row, with_validity=True),
                    "audio_displays": store.audio_displays(db, row),
                }
            if (
                not previous
                and db.execute("SELECT count(*) FROM audio_displays").fetchone()[0] >= 512
            ):
                raise HTTPException(429, "展示连接较多，请稍后重试。")
            active = bool(payload.active and published and published["phase"] == "intro")
            assigned = (
                previous["command_id"]
                if previous
                and previous["round_id"] == row["id"]
                and previous["phase_token"] == epoch
                else 0
            )
            status = (
                payload.status
                if payload.command_id == assigned
                else previous["status"]
                if previous and assigned
                else "idle"
            )
            hidden = payload.status == "hidden"
            if hidden:
                status = "hidden"
            lease_seconds = AUDIO_HIDDEN_CONNECTION_SECONDS if hidden else AUDIO_LEASE_SECONDS
            db.execute(
                """INSERT INTO audio_displays VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(display_id) DO UPDATE SET round_id=excluded.round_id,
                phase_token=excluded.phase_token, sequence=excluded.sequence,
                expires=excluded.expires, updated_at=excluded.updated_at,
                ready=excluded.ready, status=excluded.status, command_id=excluded.command_id""",
                (
                    payload.display_id,
                    row["id"],
                    epoch,
                    payload.sequence,
                    now + lease_seconds if active else 0,
                    now,
                    int(active and payload.ready and status != "hidden"),
                    status if active else "idle",
                    assigned,
                    capability_hash,
                ),
            )
            result = {
                "accepted": True,
                "playback": store.playback(db, row, with_validity=True),
                "audio_displays": store.audio_displays(db, row),
            }
        receipts.record(payload, active)
        report_signals.notify()
        return result

    @app.post("/api/vote")
    def vote(payload: Vote, request: Request):
        token = voter(request)
        if not token:
            raise HTTPException(400, "请先打开投票页面，再提交投票。")
        limiter.check(("vote", request.client.host), 600, 60)
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            if row["id"] != payload.round_id:
                raise HTTPException(409, "投票轮次已更新，请重新载入页面。")
            if not store.collection(db, row)["collecting"]:
                raise HTTPException(409, "本轮投票尚未开放或已经结束。")
            try:
                db.execute(
                    "INSERT INTO votes VALUES(?,?,?,?,?)",
                    (
                        row["id"],
                        token,
                        payload.winner,
                        json.dumps([payload.best]),
                        int(time.time()),
                    ),
                )
            except sqlite3.IntegrityError:
                raise HTTPException(409, "你已完成本轮投票，每个浏览器本轮限投一次。") from None
        return {"ok": True}

    @app.post("/api/login")
    def login(payload: Login, request: Request, response: Response):
        limiter.check(("login", request.client.host), 8, 600)
        with password_gate:
            valid = password_file.exists() and password_matches(
                payload.password, password_file.read_text()
            )
        if not valid:
            raise HTTPException(401, "管理口令不正确，请检查后重试。")
        token = secrets.token_urlsafe(32)
        with store.db() as db:
            db.execute("DELETE FROM sessions WHERE expires <= ?", (int(time.time()),))
            if db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 30:
                raise HTTPException(429, "控制台会话较多，请稍后重试。")
            db.execute(
                "INSERT INTO sessions VALUES(?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time()) + 43200),
            )
        cookie(response, session_cookie, token, 43200)
        return {"ok": True}

    @app.post("/api/logout")
    def logout(request: Request, response: Response):
        require_admin(request)
        with store.db() as db:
            db.execute(
                "DELETE FROM sessions WHERE token_hash=?",
                (hashlib.sha256(request.cookies.get(session_cookie, "").encode()).hexdigest(),),
            )
        response.delete_cookie(
            session_cookie, path="/", secure=secure, httponly=True, samesite="strict"
        )
        return {"ok": True}

    def admin_view(db):
        row = store.current(db)
        published = json.loads(row["published"]) if row["published"] else None
        return {
            "revision": row["revision"],
            "round_id": row["id"],
            "content": json.loads(row["draft"]),
            "published_content": published,
            "display_epoch": published.get("_display_epoch", "legacy") if published else "",
            "greetings": greetings,
            "playback": store.playback(db, row),
            "audio_displays": store.audio_displays(db, row),
            "audience": store.totals(db, row["id"]),
            **store.collection(db, row),
        }

    @app.get("/api/admin")
    def admin(request: Request):
        require_admin(request)
        with store.db() as db:
            return admin_view(db)

    @app.put("/api/admin/draft")
    def draft(payload: Draft, request: Request):
        require_admin(request)
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            check_version(row, payload.revision)
            updated = payload.content.model_dump()
            old = json.loads(row["draft"])
            if db.execute(
                "SELECT 1 FROM votes WHERE round_id=? LIMIT 1", (row["id"],)
            ).fetchone() and any(
                updated[k] != old[k] for k in ("topic", "affirmative", "negative", "participants")
            ):
                raise HTTPException(409, "本轮已有投票，辩题与八席名单已锁定。更换比赛请新建轮次。")
            db.execute(
                "UPDATE rounds SET draft=?,revision=revision+1 WHERE id=?",
                (json.dumps(updated, ensure_ascii=False), row["id"]),
            )
            return admin_view(db)

    @app.post("/api/admin/playback")
    def playback_command(payload: Playback, request: Request):
        require_admin(request)
        limiter.check(("playback", request.client.host), 120, 60)
        now = time.time()
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            published = json.loads(row["published"]) if row["published"] else None
            epoch = published.get("_display_epoch", "legacy") if published else ""
            if row["id"] != payload.round_id or not published or payload.display_epoch != epoch:
                raise HTTPException(409, "展示阶段已更新，请重新载入后操作。")
            previous = db.execute(
                "SELECT * FROM audio_commands WHERE request_id=?", (payload.request_id,)
            ).fetchone()
            if previous:
                if (
                    previous["round_id"] != row["id"]
                    or previous["phase_token"] != epoch
                    or previous["action"] != payload.action
                    or previous["participant_id"] != payload.participant_id
                ):
                    raise HTTPException(409, "这次操作标识已使用，请重新操作。")
                return {
                    "ok": True,
                    "duplicate": True,
                    "command_id": previous["id"],
                    "playback": store.playback(db, row),
                    "audio_displays": store.audio_displays(db, row),
                }
            displays = store.audio_displays(db, row)
            audio = next(
                (item for item in greetings if item["id"] == payload.participant_id),
                None,
            )
            if payload.action == "play":
                if published["phase"] != "intro":
                    raise HTTPException(409, "请先在大屏发布主持开场页面。")
                if not audio or not audio["available"]:
                    raise HTTPException(409, "这位辩手的招呼音频尚未准备好。")
                displays = [
                    display
                    for display in displays
                    if display["ready"]
                    and (
                        not payload.probe_no
                        or receipts.fresh(display["display_id"], row["id"], epoch, payload.probe_no)
                    )
                ]
                if not displays:
                    raise HTTPException(409, "请在大屏点击“启用现场声音”，再播放招呼。")
            targets = [display["display_id"] for display in displays]
            command = db.execute(
                "INSERT INTO audio_commands(request_id,round_id,phase_token,action,"
                "participant_id,url,display_ids,issued_at,expires) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    payload.request_id,
                    row["id"],
                    epoch,
                    payload.action,
                    payload.participant_id,
                    audio["url"] if payload.action == "play" else "",
                    json.dumps(targets),
                    now,
                    now + AUDIO_COMMAND_SECONDS,
                ),
            )
            command_id = command.lastrowid
            for display_id in targets:
                db.execute(
                    "UPDATE audio_displays SET command_id=?,"
                    "status=CASE WHEN status='hidden' THEN 'hidden' ELSE 'idle' END "
                    "WHERE display_id=?",
                    (command_id, display_id),
                )
            db.execute(
                "DELETE FROM audio_commands WHERE id NOT IN "
                "(SELECT id FROM audio_commands ORDER BY id DESC LIMIT 128)"
            )
            result = {
                "ok": True,
                "duplicate": False,
                "command_id": command_id,
                "playback": store.playback(db, row),
                "audio_displays": store.audio_displays(db, row),
            }
        screen_signals.notify()
        return result

    def audio_snapshot(scope, command_id=0):
        with store.db() as db:
            row = store.current(db)
            published = json.loads(row["published"]) if row["published"] else None
            if (
                row["id"] != scope.round_id
                or not published
                or published["phase"] != "intro"
                or published.get("_display_epoch", "legacy") != scope.display_epoch
            ):
                raise HTTPException(409, "现场轮次或画面已变化，请重新载入。")
            return {
                "round_id": row["id"],
                "display_epoch": scope.display_epoch,
                "audio_displays": store.audio_displays(db, row),
                "playback": store.playback(db, row),
                "command": dict(
                    db.execute("SELECT * FROM audio_commands WHERE id=?", (command_id,)).fetchone()
                    or {}
                ),
            }

    def cancel_unconfirmed(scope, confirmed):
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            current = store.playback(db, row)
            if not current or current["id"] != scope.command_id:
                return
            if (
                current["round_id"] != scope.round_id
                or current["display_epoch"] != scope.display_epoch
            ):
                return
            if confirmed:
                db.execute(
                    "UPDATE audio_commands SET display_ids=? WHERE id=?",
                    (json.dumps(confirmed), scope.command_id),
                )
            else:
                db.execute(
                    "UPDATE audio_commands SET expires=? WHERE id=?",
                    (time.time() - 1, scope.command_id),
                )
            for display_id in set(current["display_ids"]) - set(confirmed):
                db.execute(
                    "UPDATE audio_displays SET ready=0,status='error' "
                    "WHERE display_id=? AND command_id=?",
                    (display_id, scope.command_id),
                )
        screen_signals.notify()

    @app.post("/api/admin/audio-probe")
    async def audio_probe(payload: AudioProbe, request: Request):
        await run_in_threadpool(require_admin, request)
        limiter.check(("audio-probe", request.client.host), 120, 60)
        try:
            queue = report_signals.subscribe()
        except OverflowError:
            raise HTTPException(429, "声音确认正在进行，请稍后重试。") from None
        try:
            snapshot = await run_in_threadpool(audio_snapshot, payload)
            probe_no = receipts.probe()
            screen_signals.notify()
            deadline = asyncio.get_running_loop().time() + 4
            while True:
                snapshot = await run_in_threadpool(audio_snapshot, payload)
                ready = any(
                    d["ready"]
                    and receipts.fresh(
                        d["display_id"],
                        payload.round_id,
                        payload.display_epoch,
                        probe_no,
                    )
                    for d in snapshot["audio_displays"]
                )
                remaining = deadline - asyncio.get_running_loop().time()
                if ready or remaining <= 0:
                    snapshot.pop("command")
                    return {**snapshot, "probe_ready": ready, "probe_no": probe_no}
                try:
                    await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    pass
        finally:
            report_signals.unsubscribe(queue)

    @app.post("/api/admin/audio-recover")
    def audio_recover(payload: AudioProbe, request: Request):
        require_admin(request)
        limiter.check(("audio-recover", request.client.host), 12, 60)
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            published = json.loads(row["published"]) if row["published"] else None
            if (
                row["id"] != payload.round_id
                or not published
                or published["phase"] != "intro"
                or published.get("_display_epoch", "legacy") != payload.display_epoch
            ):
                raise HTTPException(409, "仅当前候场开场画面可以重连，请重新载入控制台。")
            db.execute(
                "UPDATE audio_commands SET expires=? WHERE round_id=? AND phase_token=?",
                (time.time() - 1, payload.round_id, payload.display_epoch),
            )
            db.execute(
                "UPDATE audio_displays SET ready=0 WHERE round_id=? AND phase_token=?",
                (payload.round_id, payload.display_epoch),
            )
            # One scoped signal only; no backlog to replay on later phases.
            requested = {
                "round_id": payload.round_id,
                "display_epoch": payload.display_epoch,
                "id": secrets.token_hex(16),
            }
        recovery.update(requested)
        screen_signals.notify()
        return {"requested": True}

    @app.post("/api/admin/audio-confirm")
    async def audio_confirm(payload: AudioConfirmation, request: Request):
        await run_in_threadpool(require_admin, request)
        limiter.check(("audio-confirm", request.client.host), 120, 60)
        try:
            queue = report_signals.subscribe()
        except OverflowError:
            raise HTTPException(429, "声音确认正在进行，请稍后重试。") from None
        try:
            deadline = asyncio.get_running_loop().time() + 4
            while True:
                snapshot = await run_in_threadpool(audio_snapshot, payload, payload.command_id)
                command = snapshot.pop("command")
                if (
                    not command
                    or command["round_id"] != payload.round_id
                    or command["phase_token"] != payload.display_epoch
                ):
                    raise HTTPException(409, "播放操作已失效，请重试。")
                current = snapshot["playback"]
                if not current or current["id"] != payload.command_id:
                    return {**snapshot, "acknowledged": False, "superseded": True}
                targets = json.loads(command["display_ids"])
                confirmed = [
                    d
                    for d in targets
                    if receipts.confirms(
                        d,
                        payload.round_id,
                        payload.display_epoch,
                        payload.command_id,
                        command["action"],
                    )
                ]
                remaining = deadline - asyncio.get_running_loop().time()
                if len(confirmed) == len(targets) and confirmed:
                    return {
                        **snapshot,
                        "acknowledged": True,
                        "confirmed": len(confirmed),
                        "targets": len(targets),
                    }
                if remaining <= 0:
                    await run_in_threadpool(cancel_unconfirmed, payload, confirmed)
                    fresh = await run_in_threadpool(audio_snapshot, payload)
                    fresh.pop("command")
                    return {
                        **fresh,
                        "acknowledged": bool(confirmed),
                        "confirmed": len(confirmed),
                        "targets": len(targets),
                        "cancelled": not confirmed,
                    }
                try:
                    await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    pass
        finally:
            report_signals.unsubscribe(queue)

    @app.post("/api/admin/{action}")
    def publish(
        action: Literal["publish", "withdraw", "new-round"],
        payload: Version,
        request: Request,
    ):
        require_admin(request)
        with store.lock, store.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = store.current(db)
            check_version(row, payload.revision)
            if action == "publish":
                content = json.loads(row["draft"])
                content["voting_open"] = content["phase"] == "vote"
                content["_display_epoch"] = secrets.token_hex(12)
                db.execute(
                    "UPDATE rounds SET published=?,revision=revision+1 WHERE id=?",
                    (json.dumps(content, ensure_ascii=False), row["id"]),
                )
            elif action == "withdraw":
                db.execute("DELETE FROM display_leases WHERE round_id=?", (row["id"],))
                db.execute(
                    "UPDATE rounds SET published=NULL,revision=revision+1 WHERE id=?",
                    (row["id"],),
                )
            else:
                rid = secrets.token_hex(16)
                content = json.loads(row["draft"])
                content.update(
                    phase="vote",
                    result_view="audience",
                    voting_open=False,
                    comments="",
                    winner="",
                    best="",
                )
                db.execute(
                    "INSERT INTO rounds VALUES(?,?,?,NULL,?)",
                    (
                        rid,
                        row["revision"] + 1,
                        json.dumps(content, ensure_ascii=False),
                        int(time.time()),
                    ),
                )
                db.execute("UPDATE current_round SET id=? WHERE singleton=1", (rid,))
            result = admin_view(db)
        screen_signals.notify()
        return result

    app.mount("/assets", StaticFiles(directory=ROOT / "static/assets"), name="assets")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=os.environ.get("SHOWCASE_DATA_DIR"),
        required=not os.environ.get("SHOWCASE_DATA_DIR"),
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        default=os.environ.get("SHOWCASE_PASSWORD_FILE"),
        required=not os.environ.get("SHOWCASE_PASSWORD_FILE"),
    )
    parser.add_argument(
        "--cookie-key-file",
        type=Path,
        default=os.environ.get("SHOWCASE_COOKIE_KEY_FILE"),
        required=not os.environ.get("SHOWCASE_COOKIE_KEY_FILE"),
    )
    parser.add_argument(
        "--origin",
        default=os.environ.get("SHOWCASE_ORIGIN"),
        required=not os.environ.get("SHOWCASE_ORIGIN"),
    )
    parser.add_argument("--port", type=int, default=os.environ.get("SHOWCASE_PORT", "8120"))
    parser.add_argument("--local-http", action="store_true")
    args = parser.parse_args()
    if not args.password_file.is_file() or not args.cookie_key_file.is_file():
        parser.error("provide existing private password-hash and cookie-key files")
    import uvicorn

    uvicorn.run(
        create_app(
            args.data_dir,
            args.origin.rstrip("/"),
            args.password_file,
            not args.local_http,
            cookie_key_file=args.cookie_key_file,
        ),
        host="127.0.0.1",
        port=args.port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        access_log=False,
        limit_concurrency=100,
        timeout_keep_alive=5,
        timeout_graceful_shutdown=3,
    )


if __name__ == "__main__":
    main()
