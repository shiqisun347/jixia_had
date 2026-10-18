import concurrent.futures
import io
import json
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import zxingcpp
from app import GREETING_IDS, IDS, ROOT, create_app, load_greetings, password_hash
from fastapi.testclient import TestClient
from fixtures import prepare_static
from PIL import Image


class ShowcaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.static_root = prepare_static(ROOT, self.root / "site")
        self.root_patch = patch("app.ROOT", self.static_root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.pw = self.root / "password-hash"
        self.pw.write_text(password_hash("synthetic-test-password"))
        self.app = create_app(self.root / "data", "https://showcase.test", self.pw)
        self.admin = TestClient(self.app, base_url="https://showcase.test")
        self.viewer = TestClient(self.app, base_url="https://showcase.test")
        self.headers = {"Origin": "https://showcase.test"}
        self.assertEqual(
            self.admin.post(
                "/api/login",
                json={"password": "synthetic-test-password"},
                headers=self.headers,
            ).status_code,
            200,
        )

    def tearDown(self):
        self.admin.close()
        self.viewer.close()
        self.temp.cleanup()

    def admin_state(self):
        return self.admin.get("/api/admin").json()

    def save(self, state):
        response = self.admin.put(
            "/api/admin/draft",
            json={"revision": state["revision"], "content": state["content"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def action(self, action, state):
        response = self.admin.post(
            "/api/admin/" + action,
            json={"revision": state["revision"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def lease(self, active=True, sequence=1, display_id="a" * 32, state=None):
        state = state or self.viewer.get("/api/state").json()
        return self.viewer.post(
            "/api/display",
            json={
                "round_id": state["round_id"],
                "display_epoch": state["display_epoch"],
                "display_id": display_id,
                "sequence": sequence,
                "active": active,
            },
            headers=self.headers,
        )

    def open_vote(self):
        state = self.admin_state()
        state["content"]["phase"] = "vote"
        state["content"]["voting_open"] = False
        state = self.save(state)
        state = self.action("publish", state)
        self.assertEqual(self.lease().status_code, 200)
        return state

    def open_intro(self):
        state = self.admin_state()
        state["content"]["phase"] = "intro"
        self.action("publish", self.save(state))
        return self.viewer.get("/api/state").json()

    def audio_lease(
        self,
        sequence=1,
        active=True,
        ready=True,
        status="idle",
        command_id=0,
        display_id="c" * 32,
        display_key="a" * 64,
        state=None,
    ):
        state = state or self.viewer.get("/api/state").json()
        return self.viewer.post(
            "/api/audio-display",
            json={
                "round_id": state["round_id"],
                "display_epoch": state["display_epoch"],
                "display_id": display_id,
                "display_key": display_key,
                "sequence": sequence,
                "active": active,
                "ready": ready,
                "status": status,
                "command_id": command_id,
            },
            headers=self.headers,
        )

    def play(self, action="play", participant_id="aff-2", request_id="d" * 32, state=None):
        state = state or self.viewer.get("/api/state").json()
        return self.admin.post(
            "/api/admin/playback",
            json={
                "round_id": state["round_id"],
                "display_epoch": state["display_epoch"],
                "request_id": request_id,
                "action": action,
                "participant_id": participant_id if action == "play" else "",
            },
            headers=self.headers,
        )

    def ballot(self, client=None, best=None, round_id=None):
        client = client or self.viewer
        rid = client.get("/api/state").json()["round_id"]
        return client.post(
            "/api/vote",
            json={
                "round_id": round_id or rid,
                "winner": "AFFIRMATIVE",
                "best": best or IDS[0],
            },
            headers=self.headers,
        )

    def test_public_pages_and_no_draft_leak(self):
        for page in ["/", "/?mode=vote", "/control"]:
            self.assertEqual(self.viewer.get(page).status_code, 200)
        state = self.admin_state()
        state["content"]["topic"] = "PRIVATE DRAFT TOPIC"
        state["content"]["comments"] = "SECRET COMMENT"
        self.save(state)
        public = self.viewer.get("/api/state")
        self.assertFalse(public.json()["published"])
        self.assertNotIn("PRIVATE", public.text)
        self.assertNotIn("SECRET", public.text)
        self.assertEqual(self.viewer.get("/api/admin").status_code, 401)

    def test_anonymous_vote_complete_and_duplicate_rejected(self):
        self.open_vote()
        self.assertEqual(self.ballot().status_code, 200)
        self.assertEqual(self.ballot().status_code, 409)
        state = self.viewer.get("/api/state").json()
        self.assertTrue(state["has_voted"])
        self.assertEqual(state["total_votes"], 1)
        self.assertNotIn("audience", state)
        self.assertNotIn("winner", state["content"])

    def test_vote_requires_two_way_winner_and_exactly_one_best(self):
        state = self.open_vote()
        valid = {"round_id": state["round_id"], "winner": "AFFIRMATIVE", "best": IDS[0]}
        invalid = [
            {**valid, "winner": "DRAW"},
            {**valid, "winner": ""},
            {**valid, "best": ""},
            {**valid, "best": "aff-9"},
            {**valid, "best": [IDS[0], IDS[1]]},
            {**valid, "best": [IDS[0]]},
            {**valid, "ranking": IDS},
            {k: v for k, v in valid.items() if k != "best"},
            {k: v for k, v in valid.items() if k != "winner"},
            {"round_id": state["round_id"], "winner": "AFFIRMATIVE", "ranking": IDS},
        ]
        for payload in invalid:
            with self.subTest(payload=payload):
                response = self.viewer.post("/api/vote", json=payload, headers=self.headers)
                self.assertEqual(response.status_code, 422)
                self.assertIn("一位最佳辩手", response.json()["detail"])
        self.assertEqual(self.viewer.get("/api/state").json()["total_votes"], 0)

    def test_vote_closed_and_stale_round_rejected(self):
        self.assertEqual(self.ballot().status_code, 409)
        state = self.open_vote()
        old_id = state["round_id"]
        state = self.action("new-round", state)
        self.assertEqual(self.ballot(round_id=old_id).status_code, 409)
        self.assertFalse(self.viewer.get("/api/state").json()["published"])

    def test_phases_redact_awards_until_results_and_draft_save_not_public(self):
        state = self.open_vote()
        state["content"].update(
            phase="review", comments="评委点评公开", winner="NEGATIVE", best=IDS[3]
        )
        state = self.save(state)
        self.assertEqual(self.viewer.get("/api/state").json()["content"]["phase"], "vote")
        state = self.action("publish", state)
        public = self.viewer.get("/api/state").json()["content"]
        self.assertNotIn("comments", public)
        self.assertNotIn("winner", public)
        self.assertNotIn("best", public)
        self.assertEqual(self.ballot().status_code, 409)
        state["content"].update(phase="results", result_view="judge")
        state = self.save(state)
        self.action("publish", state)
        public = self.viewer.get("/api/state").json()["content"]
        self.assertEqual(public["winner"], "NEGATIVE")
        self.assertEqual(public["best"], IDS[3])

    def test_best_vote_count_and_empty_results(self):
        self.open_vote()
        self.assertEqual(self.ballot(best=IDS[3]).status_code, 200)
        with TestClient(self.app, base_url="https://showcase.test") as another:
            self.assertEqual(self.ballot(another, IDS[7]).status_code, 200)
        state = self.admin_state()
        state["content"].update(phase="results", result_view="audience")
        self.action("publish", self.save(state))
        public = self.viewer.get("/api/state").json()
        audience = public["audience"]
        self.assertEqual(audience["total"], 2)
        self.assertEqual(audience["winners"], {"AFFIRMATIVE": 2, "NEGATIVE": 0})
        self.assertEqual([r["id"] for r in audience["ranking"][:2]], [IDS[3], IDS[7]])
        self.assertTrue(all(r["points"] == 1 and r["tied"] for r in audience["ranking"][:2]))
        self.assertEqual(sum(r["points"] for r in audience["ranking"]), 2)
        self.assertNotIn("comments", public["content"])
        state = self.action("new-round", self.admin_state())
        state["content"].update(phase="results", result_view="audience")
        self.action("publish", self.save(state))
        self.assertEqual(self.viewer.get("/api/state").json()["audience"]["ranking"], [])

    def test_each_of_eight_debaters_can_receive_one_best_vote(self):
        self.open_vote()
        for pid in IDS:
            with TestClient(self.app, base_url="https://showcase.test") as another:
                self.assertEqual(self.ballot(another, pid).status_code, 200)
        audience = self.admin_state()["audience"]
        self.assertEqual(audience["total"], 8)
        self.assertTrue(all(r["points"] == 1 for r in audience["ranking"]))
        with self.app.state.store.db() as db:
            self.assertTrue(
                all(len(json.loads(row[0])) == 1 for row in db.execute("SELECT ranking FROM votes"))
            )

    def test_legacy_ballots_first_choice_counted_without_rewriting_history(self):
        state = self.open_vote()
        with self.app.state.store.db() as db:
            db.execute(
                "INSERT INTO votes VALUES(?,?,?,?,?)",
                (state["round_id"], "legacy-a", "DRAW", json.dumps(IDS), 123),
            )
            db.execute(
                "INSERT INTO votes VALUES(?,?,?,?,?)",
                (
                    state["round_id"],
                    "legacy-b",
                    "NEGATIVE",
                    json.dumps(list(reversed(IDS))),
                    124,
                ),
            )
            before = [tuple(row) for row in db.execute("SELECT * FROM votes ORDER BY 2")]
        self.assertEqual(self.ballot(best=IDS[0]).status_code, 200)
        audience = self.admin_state()["audience"]
        self.assertEqual(audience["total"], 3)
        self.assertEqual(audience["winners"], {"AFFIRMATIVE": 1, "NEGATIVE": 1})
        counts = {r["id"]: r["points"] for r in audience["ranking"]}
        self.assertEqual(counts[IDS[0]], 2)
        self.assertEqual(counts[IDS[7]], 1)
        self.assertEqual(sum(counts.values()), 3)
        self.action("new-round", self.admin_state())
        with self.app.state.store.db() as db:
            after = [
                tuple(row)
                for row in db.execute(
                    "SELECT * FROM votes WHERE voter IN ('legacy-a','legacy-b') ORDER BY 2"
                )
            ]
        self.assertEqual(before, after)

    def test_conflicting_admin_does_not_override(self):
        state = self.admin_state()
        self.save(state)
        r = self.admin.put(
            "/api/admin/draft",
            json={"revision": state["revision"], "content": state["content"]},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 409)
        r = self.admin.post(
            "/api/admin/publish",
            json={"revision": state["revision"]},
            headers=self.headers,
        )
        self.assertEqual(r.status_code, 409)

    def test_no_unauthenticated_write_or_cross_origin_write(self):
        state = self.admin_state()
        for action in ["publish", "withdraw", "new-round"]:
            self.assertEqual(
                self.viewer.post(
                    "/api/admin/" + action,
                    json={"revision": state["revision"]},
                    headers=self.headers,
                ).status_code,
                401,
            )
        self.assertEqual(
            self.viewer.post(
                "/api/login",
                json={"password": "synthetic-test-password"},
                headers={"Origin": "https://evil.test"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.admin.post("/api/admin/publish", json={"revision": state["revision"]}).status_code,
            403,
        )

    def test_bounded_payload_and_validation_does_not_echo_secrets(self):
        response = self.viewer.post(
            "/api/login",
            content="x" * 66000,
            headers={**self.headers, "Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        response = self.viewer.post(
            "/api/login",
            json={"password": "DO-NOT-ECHO", "extra": "secret"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret", response.text)
        self.assertNotIn("DO-NOT-ECHO", response.text)

    def test_cookie_flags_signature_and_logout(self):
        response = self.viewer.get("/api/state")
        value = response.headers["set-cookie"]
        for item in [
            "Secure",
            "HttpOnly",
            "SameSite=strict",
            "Path=/",
            "__Host-showcase_voter",
        ]:
            self.assertIn(item, value)
        self.assertNotIn("Domain=", value)
        self.open_vote()
        self.viewer.cookies.set(
            "__Host-showcase_voter",
            "x" * 32 + "." + "0" * 64,
            domain="showcase.test",
            path="/",
        )
        response = self.viewer.post(
            "/api/vote",
            json={
                "round_id": self.admin_state()["round_id"],
                "winner": "NEGATIVE",
                "best": IDS[0],
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            self.admin.post("/api/logout", json={}, headers=self.headers).status_code,
            200,
        )
        self.assertEqual(self.admin.get("/api/admin").status_code, 401)

    def test_expired_sessions_rejected_and_wrong_password(self):
        with self.app.state.store.db() as db:
            db.execute("UPDATE sessions SET expires=?", (int(time.time()) - 1,))
        self.assertEqual(self.admin.get("/api/admin").status_code, 401)
        self.assertEqual(
            self.viewer.post(
                "/api/login", json={"password": "wrong"}, headers=self.headers
            ).status_code,
            401,
        )

    def test_vote_locks_identity_and_new_round_preserves_history(self):
        state = self.open_vote()
        self.assertEqual(self.ballot().status_code, 200)
        state = self.admin_state()
        state["content"]["participants"][0]["name"] = "another"
        response = self.admin.put(
            "/api/admin/draft",
            json={"revision": state["revision"], "content": state["content"]},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 409)
        new = self.action("new-round", self.admin_state())
        self.assertNotEqual(new["round_id"], state["round_id"])
        self.assertEqual(new["audience"]["total"], 0)
        with self.app.state.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM votes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM rounds").fetchone()[0], 2)

    def test_withdraw_and_restart_persistence(self):
        self.open_vote()
        self.ballot()
        restarted = create_app(self.root / "data", "https://showcase.test", self.pw)
        with TestClient(restarted, base_url="https://showcase.test") as client:
            self.assertEqual(client.get("/api/state").json()["total_votes"], 1)
        self.action("withdraw", self.admin_state())
        self.assertNotIn("content", self.viewer.get("/api/state").json())

    def test_concurrent_duplicates_one_vote(self):
        state = self.open_vote()
        self.viewer.get("/api/state")
        token = self.viewer.cookies.get("__Host-showcase_voter")

        def submit(_):
            with TestClient(self.app, base_url="https://showcase.test") as client:
                return client.post(
                    "/api/vote",
                    json={
                        "round_id": state["round_id"],
                        "winner": "NEGATIVE",
                        "best": IDS[0],
                    },
                    headers={
                        **self.headers,
                        "Cookie": "__Host-showcase_voter=" + token,
                    },
                ).status_code

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            codes = list(pool.map(submit, range(8)))
        self.assertEqual(codes.count(200), 1)
        self.assertEqual(codes.count(409), 7)
        self.assertEqual(self.admin_state()["audience"]["total"], 1)

    def test_synced_versioned_images_cached_without_caching_errors_or_private_state(
        self,
    ):
        urls = [
            "/assets/avatars/aff-1-000000000001.webp",
            "/assets/judges-commentary-v2.webp",
        ]
        for url in urls:
            with self.subTest(url=url):
                response = self.viewer.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content[:4], b"RIFF")
                self.assertIn("immutable", response.headers["cache-control"])
        response = self.viewer.get("/assets/avatars/aff-1-000000000000.webp")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.viewer.get("/api/state").headers["cache-control"], "no-store")

    def test_qr_png_decodes_to_anonymous_vote_url(self):
        response = self.viewer.get("/api/vote-qr.png")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        decoded = zxingcpp.read_barcode(Image.open(io.BytesIO(response.content)))
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.text, "https://showcase.test/?mode=vote")
        self.assertIn("public", response.headers["cache-control"])
        self.assertEqual(self.viewer.get("/api/state").headers["cache-control"], "no-store")
        self.assertEqual(self.admin.get("/api/admin").headers["cache-control"], "no-store")

    def test_phone_and_control_read_requests_do_not_open_voting(self):
        state = self.admin_state()
        state["content"]["voting_open"] = True
        state = self.action("publish", self.save(state))
        for _ in range(4):
            self.viewer.get("/api/state")
            self.admin.get("/api/admin")
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])
        self.assertEqual(self.ballot().status_code, 409)

    def test_close_display_rejects_votes_reopen_keeps_tally_and_dedup(self):
        self.open_vote()
        self.assertEqual(self.ballot().status_code, 200)
        self.assertTrue(self.lease(active=False, sequence=2).json()["accepted"])
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])
        with TestClient(self.app, base_url="https://showcase.test") as other:
            self.assertEqual(self.ballot(other).status_code, 409)
            self.assertTrue(self.lease(sequence=3).json()["collecting"])
            self.assertEqual(self.ballot(other).status_code, 200)
        self.assertEqual(self.ballot().status_code, 409)
        self.assertEqual(self.admin_state()["audience"]["total"], 2)

    def test_late_heartbeat_cannot_revive_closed_display(self):
        self.open_vote()
        self.lease(active=False, sequence=3)
        late = self.lease(sequence=2)
        self.assertFalse(late.json()["accepted"])
        self.assertFalse(late.json()["collecting"])
        self.assertEqual(self.ballot().status_code, 409)

    def test_expired_display_lease_blocks_vote_without_browser_cleanup(self):
        self.open_vote()
        now = time.time()
        with patch("app.time.time", return_value=now + 7):
            self.assertFalse(self.viewer.get("/api/state").json()["collecting"])
            self.assertEqual(self.ballot().status_code, 409)

    def test_multiple_displays_collect_until_last_display_closes(self):
        self.open_vote()
        self.assertEqual(self.lease(display_id="b" * 32).json()["active_displays"], 2)
        self.lease(active=False, sequence=2)
        self.assertTrue(self.viewer.get("/api/state").json()["collecting"])
        self.lease(active=False, sequence=2, display_id="b" * 32)
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])

    def test_phase_epoch_fences_late_display_across_leave_and_return(self):
        state = self.open_vote()
        old = self.viewer.get("/api/state").json()
        self.assertEqual(self.ballot().status_code, 200)
        state = self.admin_state()
        state["content"]["phase"] = "review"
        state = self.action("publish", self.save(state))
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])
        state["content"]["phase"] = "vote"
        state = self.action("publish", self.save(state))
        self.assertEqual(self.lease(sequence=5, state=old).status_code, 409)
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])
        self.assertTrue(self.lease(sequence=6).json()["collecting"])
        self.assertEqual(self.admin_state()["audience"]["total"], 1)
        self.assertEqual(self.ballot().status_code, 409)

    def test_old_round_display_cannot_open_new_round(self):
        self.open_vote()
        old = self.viewer.get("/api/state").json()
        self.action("new-round", self.admin_state())
        self.assertEqual(self.lease(sequence=2, state=old).status_code, 409)
        self.assertFalse(self.viewer.get("/api/state").json()["collecting"])

    def test_draft_change_does_not_interrupt_current_display(self):
        self.open_vote()
        state = self.admin_state()
        state["content"]["phase"] = "review"
        self.save(state)
        self.assertTrue(self.viewer.get("/api/state").json()["collecting"])
        self.assertEqual(self.ballot().status_code, 200)

    def test_intro_is_publishable_without_changing_legacy_default(self):
        self.assertEqual(self.admin_state()["content"]["phase"], "vote")
        state = self.open_intro()
        self.assertEqual(state["content"]["phase"], "intro")
        self.assertFalse(state["collecting"])
        self.assertIsNone(state["playback"])
        self.assertEqual([item["id"] for item in state["greetings"]], list(GREETING_IDS))
        self.assertTrue(all(item["available"] for item in state["greetings"]))
        self.assertEqual(self.admin_state()["audio_displays"], [])
        self.assertEqual(self.admin_state()["display_epoch"], state["display_epoch"])

    def test_playback_requires_auth_origin_intro_ready_and_valid_agent(self):
        state = self.open_intro()
        payload = {
            "round_id": state["round_id"],
            "display_epoch": state["display_epoch"],
            "request_id": "d" * 32,
            "action": "play",
            "participant_id": "aff-2",
        }
        self.assertEqual(
            self.viewer.post("/api/admin/playback", json=payload, headers=self.headers).status_code,
            401,
        )
        self.assertEqual(self.admin.post("/api/admin/playback", json=payload).status_code, 403)
        self.assertEqual(self.play().status_code, 409)
        self.audio_lease(ready=False, status="blocked")
        self.assertEqual(self.admin_state()["audio_displays"][0]["status"], "blocked")
        self.assertEqual(self.play().status_code, 409)
        self.audio_lease(sequence=2)
        self.assertEqual(self.play(participant_id="aff-1").status_code, 422)
        self.assertEqual(self.play().status_code, 200)
        state = self.admin_state()
        state["content"]["phase"] = "review"
        self.action("publish", self.save(state))
        self.assertEqual(self.play(request_id="e" * 32).status_code, 409)

    def test_stop_is_safe_without_ready_displays(self):
        self.open_intro()
        stopped = self.play(action="stop").json()
        self.assertEqual(stopped["playback"]["action"], "stop")
        self.assertEqual(stopped["playback"]["display_ids"], [])
        state = self.admin_state()
        state["content"]["phase"] = "review"
        self.action("publish", self.save(state))
        response = self.play(action="stop", request_id="e" * 32)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["playback"])

    def test_audio_status_play_end_stop_and_error_are_real_acknowledgements(self):
        self.open_intro()
        self.audio_lease()
        play = self.play().json()["playback"]
        self.assertEqual(play["display_ids"], ["c" * 32])
        self.assertEqual(play["participant_id"], "aff-2")
        self.assertTrue(play["url"].startswith("/assets/greetings/"))
        self.assertEqual(play["expires_at"] - play["issued_at"], 20)
        for sequence, status in ((2, "playing"), (3, "ended"), (4, "error")):
            response = self.audio_lease(sequence=sequence, status=status, command_id=play["id"])
            self.assertEqual(response.status_code, 200)
            display = self.admin_state()["audio_displays"][0]
            self.assertEqual(display["status"], status)
            self.assertEqual(display["command_id"], play["id"])
        stopped = self.play(action="stop", request_id="e" * 32).json()["playback"]
        self.assertGreater(stopped["id"], play["id"])
        self.assertEqual(self.admin_state()["audio_displays"][0]["status"], "idle")

    def test_newer_audio_command_fences_late_previous_ack(self):
        self.open_intro()
        self.audio_lease()
        first = self.play().json()["playback"]
        second = self.play(participant_id="neg-4", request_id="e" * 32).json()["playback"]
        self.audio_lease(sequence=2, status="playing", command_id=second["id"])
        self.audio_lease(sequence=3, status="ended", command_id=first["id"])
        display = self.admin_state()["audio_displays"][0]
        self.assertEqual(display["status"], "playing")
        self.assertEqual(display["command_id"], second["id"])
        self.assertEqual(self.viewer.get("/api/state").json()["playback"]["id"], second["id"])

    def test_audio_close_and_sequence_fencing(self):
        self.open_intro()
        self.audio_lease()
        self.audio_lease(sequence=3, active=False)
        late = self.audio_lease(sequence=2)
        self.assertFalse(late.json()["accepted"])
        self.assertEqual(self.admin_state()["audio_displays"], [])
        self.assertEqual(self.play().status_code, 409)
        self.assertTrue(self.audio_lease(sequence=4).json()["accepted"])
        self.assertEqual(self.play().status_code, 200)

    def test_public_command_target_id_cannot_forge_display_status(self):
        self.open_intro()
        self.audio_lease()
        command = self.play().json()["playback"]
        forged = self.audio_lease(
            sequence=9007199254740991,
            status="playing",
            command_id=command["id"],
            display_key="f" * 64,
        )
        self.assertEqual(forged.status_code, 403)
        honest = self.audio_lease(sequence=2, status="ended", command_id=command["id"])
        self.assertTrue(honest.json()["accepted"])
        self.assertEqual(self.admin_state()["audio_displays"][0]["status"], "ended")
        for response in (
            self.viewer.get("/api/state"),
            self.admin.get("/api/admin"),
            honest,
        ):
            self.assertNotIn("display_key", response.text)
            self.assertNotIn("capability_hash", response.text)

    def test_expired_audio_displays_and_commands_disappear(self):
        self.open_intro()
        self.audio_lease()
        self.play()
        now = time.time()
        with patch("app.time.time", return_value=now + 7):
            self.assertEqual(self.admin_state()["audio_displays"], [])
            self.assertEqual(self.play(request_id="e" * 32).status_code, 409)
        with patch("app.time.time", return_value=now + 21):
            self.assertIsNone(self.viewer.get("/api/state").json()["playback"])

    def test_hidden_screen_is_connected_but_never_ready_for_playback(self):
        self.open_intro()
        self.assertEqual(self.audio_lease(status="hidden", ready=True).status_code, 200)
        display = self.admin_state()["audio_displays"][0]
        self.assertEqual(display["status"], "hidden")
        self.assertFalse(display["ready"])
        self.assertEqual(self.play().status_code, 409)
        now = time.time()
        with patch("app.time.time", return_value=now + 70):
            self.assertEqual(len(self.admin_state()["audio_displays"]), 1)
            # Another screen's heartbeat must not prune the hidden connection.
            self.audio_lease(display_id="f" * 32)
            self.assertEqual(len(self.admin_state()["audio_displays"]), 2)
        with patch("app.time.time", return_value=now + 91):
            self.assertEqual(self.admin_state()["audio_displays"], [])

    def test_hidden_screen_stop_and_close_keep_truthful_connection_state(self):
        self.open_intro()
        self.audio_lease(status="hidden", ready=False, command_id=12)
        self.assertEqual(self.admin_state()["audio_displays"][0]["status"], "hidden")
        stopped = self.play(action="stop").json()
        self.assertEqual(stopped["audio_displays"][0]["status"], "hidden")
        self.assertFalse(stopped["audio_displays"][0]["ready"])
        stale = self.audio_lease(sequence=2, ready=True, status="idle").json()
        self.assertEqual(stale["audio_displays"][0]["status"], "hidden")
        self.assertFalse(stale["audio_displays"][0]["ready"])
        visible = self.audio_lease(
            sequence=3, ready=True, status="idle", command_id=stopped["command_id"]
        ).json()
        self.assertTrue(visible["audio_displays"][0]["ready"])
        self.audio_lease(
            sequence=4,
            active=False,
            ready=False,
            status="hidden",
            command_id=stopped["command_id"],
        )
        self.assertEqual(self.admin_state()["audio_displays"], [])

    def test_new_or_refreshed_screen_is_not_targeted_by_an_old_command(self):
        self.open_intro()
        self.audio_lease()
        old = self.play().json()["playback"]
        registered = self.audio_lease(display_id="f" * 32).json()
        self.assertNotIn("f" * 32, registered["playback"]["display_ids"])
        self.assertEqual(registered["playback"]["id"], old["id"])
        new = self.play(request_id="e" * 32).json()["playback"]
        self.assertEqual(set(new["display_ids"]), {"c" * 32, "f" * 32})

    def test_playback_idempotency_and_payload_collision(self):
        self.open_intro()
        self.audio_lease()
        first = self.play().json()
        repeat = self.play().json()
        self.assertFalse(first["duplicate"])
        self.assertTrue(repeat["duplicate"])
        self.assertEqual(first["command_id"], repeat["command_id"])
        self.assertEqual(self.play(participant_id="neg-2").status_code, 409)
        with self.app.state.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audio_commands").fetchone()[0], 1)

    def test_old_audio_round_and_epoch_cannot_affect_current_stage(self):
        old = self.open_intro()
        self.audio_lease()
        self.play()
        state = self.admin_state()
        state["content"]["phase"] = "review"
        state = self.action("publish", self.save(state))
        self.assertIsNone(self.viewer.get("/api/state").json()["playback"])
        self.assertEqual(self.admin_state()["audio_displays"], [])
        state["content"]["phase"] = "intro"
        self.action("publish", self.save(state))
        self.assertEqual(self.audio_lease(sequence=2, state=old).status_code, 409)
        self.assertEqual(self.play(state=old, request_id="e" * 32).status_code, 409)
        new_intro = self.viewer.get("/api/state").json()
        self.assertEqual(self.play(request_id="e" * 32).status_code, 409)
        self.action("new-round", self.admin_state())
        self.assertEqual(self.audio_lease(sequence=3, state=new_intro).status_code, 409)
        self.assertEqual(self.play(state=new_intro, request_id="f" * 32).status_code, 409)

    def test_audio_draft_does_not_interrupt_published_intro_and_withdraw_does(self):
        intro = self.open_intro()
        self.audio_lease()
        first = self.play().json()["playback"]
        state = self.admin_state()
        state["content"]["phase"] = "vote"
        state = self.save(state)
        self.assertEqual(self.viewer.get("/api/state").json()["playback"]["id"], first["id"])
        self.assertEqual(self.play(request_id="e" * 32).status_code, 200)
        self.action("withdraw", state)
        self.assertIsNone(self.viewer.get("/api/state").json()["playback"])
        self.assertEqual(self.admin_state()["audio_displays"], [])
        self.assertEqual(self.play(state=intro, request_id="f" * 32).status_code, 409)

    def test_audio_tables_are_bounded_and_command_ids_do_not_recycle(self):
        state = self.open_intro()
        with self.app.state.store.db() as db:
            db.executemany(
                "INSERT INTO audio_commands(request_id,round_id,phase_token,action,"
                "participant_id,url,display_ids,issued_at,expires) VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f"{i:032x}",
                        state["round_id"],
                        state["display_epoch"],
                        "stop",
                        "",
                        "",
                        "[]",
                        1,
                        2,
                    )
                    for i in range(130)
                ],
            )
        new = self.play(action="stop").json()["command_id"]
        self.assertEqual(new, 131)
        with self.app.state.store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audio_commands").fetchone()[0], 128)
            db.execute("DELETE FROM audio_commands")
        self.assertEqual(self.play(action="stop", request_id="e" * 32).json()["command_id"], 132)

    def test_missing_or_invalid_packaged_audio_cannot_play(self):
        with patch("app.load_greetings", return_value=[]):
            app = create_app(self.root / "data", "https://showcase.test", self.pw)
        with TestClient(app, base_url="https://showcase.test") as client:
            client.cookies.update(self.admin.cookies)
            self.open_intro()
            self.audio_lease()
            state = self.viewer.get("/api/state").json()
            response = client.post(
                "/api/admin/playback",
                json={
                    "round_id": state["round_id"],
                    "display_epoch": state["display_epoch"],
                    "request_id": "d" * 32,
                    "action": "play",
                    "participant_id": "aff-2",
                },
                headers=self.headers,
            )
            self.assertEqual(response.status_code, 409)

    def test_packaged_manifest_is_bounded_local_and_valid_wav(self):
        manifest = load_greetings(self.static_root / "static/assets")
        self.assertEqual(len(manifest), 4)
        self.assertTrue(all(item["available"] and 0 < item["duration"] <= 20 for item in manifest))
        assets = self.root / "assets"
        folder = assets / "greetings"
        folder.mkdir(parents=True)
        self.assertTrue(all(not item["available"] for item in load_greetings(assets)))
        metadata = dict(manifest[0], duration=1, url="/assets/greetings/test.wav")
        with wave.open(str(folder / "test.wav"), "wb") as wav:
            wav.setparams((1, 2, 24000, 24000, "NONE", "not compressed"))
            wav.writeframes(bytes(48000))
        path = folder / "manifest.json"
        path.write_text(json.dumps({"greetings": [metadata]}))
        self.assertTrue(load_greetings(assets)[0]["available"])
        audio = folder / "test.wav"
        audio.write_bytes(audio.read_bytes()[:100])
        self.assertFalse(load_greetings(assets)[0]["available"])
        metadata["url"] = "/assets/greetings/../../outside.wav"
        path.write_text(json.dumps({"greetings": [metadata]}))
        self.assertFalse(load_greetings(assets)[0]["available"])
        path.write_text("{" * 65537)
        self.assertTrue(all(not item["available"] for item in load_greetings(assets)))

    def test_restart_preserves_command_identity_and_target_fencing(self):
        self.open_intro()
        self.audio_lease()
        first = self.play().json()["playback"]
        app = create_app(self.root / "data", "https://showcase.test", self.pw)
        with TestClient(app, base_url="https://showcase.test") as client:
            state = client.get("/api/state").json()
            self.assertEqual(state["playback"]["id"], first["id"])
            self.assertEqual(state["playback"]["display_ids"], ["c" * 32])
            self.assertEqual([item["id"] for item in state["greetings"]], list(GREETING_IDS))


if __name__ == "__main__":
    unittest.main()
