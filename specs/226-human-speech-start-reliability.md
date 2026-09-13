# 226 Human speech start reliability

- Version: v2.0
- Status: Released to production; physical microphone/ASR acceptance remains open
- Approved by: user request for a systematic fix
- Date: 2026-08-24
- Depends on: Specs 166, 167, 168, 217, 219, 223

## Problem

A human can reach authoritative `HUMAN_READY_TO_START` and still fail to enter
`HUMAN_SPEAKING`. The previous fixes covered stale presence snapshots,
authoritative start timeout, ASR reconnect identity, and concurrent WebSocket
writes, but three current gaps remain:

1. Web learns a higher event `sequence` and starts an asynchronous snapshot
   refresh, while controls remain enabled against the older cached sequence.
   A click in that interval sends a predictably stale `expected_sequence`.
2. `MatchRuntimeManager.submit` starts ASR before submitting `speech.start` to
   MatchActor. This bypasses the Actor's serialized idempotency check, so an ACK
   retry or concurrent duplicate can touch the media runtime twice before Core
   identifies the duplicate.
3. The match WebSocket command loop catches validation/domain failures only.
   A timeout or unexpected ASR/LiveKit/runtime exception escapes the command
   boundary, tears down the socket, and leaves the user in the ready state
   without a durable `speech.started` event.

## Required behavior

- Every human start command enters MatchActor before its external ASR side
  effect. The Actor pre-commit hook starts ASR once, only after state and
  identity validation and idempotency lookup, and before the database commit.
- ASR startup is bounded to ten seconds. Failure cleans the attempted runtime,
  rolls Actor state back to `HUMAN_READY_TO_START`, preserves the full speech
  allowance, and returns a stable actionable command error. It does not close
  the business WebSocket and does not automatically open the microphone.
- If database commit fails after ASR startup, the attempted ASR session is
  cleaned before the command returns.
- A duplicate successful `message_id` returns the cached ACK without starting
  a second ASR session. A rejected command must never reset an already valid
  human speech.
- Web records the highest authoritative event sequence synchronously. Before
  sending a command it refreshes a lagging match snapshot and rechecks socket,
  epoch and sequence. Controls are not command-ready while the cached snapshot
  is behind an observed event.
- A synchronous socket send failure settles and removes the pending command;
  it cannot leave the button pending until the ten-second client timeout.
- Unexpected per-command exceptions are logged with sanitized exception type
  and returned as `internal_server_error`; the socket remains available for a
  later retry.

## Non-goals

- No change to human allocation, 60-second start window, speech timer origin,
  pause/resume, free-debate selection, LiveKit identity, ASR provider, database
  schema, permissions, or public/private snapshot fields.
- No automatic microphone activation, automatic speech start, or weakening of
  Core's expected-sequence and connection-epoch checks.
- No production release without the user's separate release request and the
  existing zero-active-match gate.

## Acceptance

1. Core test: ASR startup occurs once inside serialized pre-commit; duplicate
   `speech.start` returns duplicate success without a second startup.
2. Core test: timeout/start exception leaves the Actor ready with unchanged
   duration, cleans the attempted runtime, and becomes a command error without
   ending the route.
3. Core route test: an unexpected first command failure returns a sanitized
   error and a later valid command on the same socket receives an ACK.
4. Web hook test: a higher event sequence blocks/synchronizes a command until
   the refreshed snapshot is current; the command carries the latest sequence
   and epoch.
5. Web hook test: synchronous `socket.send` failure settles immediately and
   leaves no stale pending command.
6. Browser test: after allocation and a concurrent presence sequence update,
   the human can click once and reach `HUMAN_SPEAKING`; microphone activation
   happens only after the authoritative ACK.
7. Focused Core/Web tests, Core non-integration tests, Web tests, Ruff, Pyright,
   ESLint, TypeScript, contracts, browser test, build and `git diff --check`
   pass. PostgreSQL and physical-microphone evidence are reported accurately.

## Rollback

Restore the touched MatchRuntimeManager/route and Web runtime files plus their
tests. No migration or persisted-data rewrite is required. A match already in
an active state must be recovered or terminated through MatchActor, never by
direct database mutation.

## Implementation

- `speech.start` now reaches MatchActor validation and idempotency before the
  human ASR side effect. ASR startup runs in the Actor pre-commit hook with a
  ten-second bound; failed startup or a later database commit failure cleans
  only that attempted runtime and leaves the authoritative state ready to
  retry with the full speech allowance.
- The match WebSocket isolates unexpected failures at one command boundary,
  returns a sanitized `internal_server_error`, and keeps the connection open
  for a later command.
- Web records the highest event sequence synchronously. A lagging snapshot
  disables commands and is refreshed at most twice before send; socket, epoch
  and sequence are then re-read. A synchronous `socket.send` failure removes
  and settles its pending command immediately.

## Verification

- Core focused regressions: 20 passed.
- Core non-integration suite: 355 passed, 33 integration tests deselected.
- Repository test command: tooling 47 passed, QA 6 passed, Web 190 passed,
  Core 355 passed, Jobs 25 passed.
- Web sequence/send regressions: included in the 190 passing Web tests.
- Live-match browser suite: 12 passed across four configured viewports; the
  complete browser suite passed 232 tests.
- Storybook: 90 passed. Ruff, ESLint, TypeScript/Pyright, OpenAPI contracts and
  the production Web build passed.
- PostgreSQL integration was not run because this workspace has neither an
  independent `TEST_DATABASE_URL` nor Docker.
- Production release used rollback backup
  `/opt/jixia-backup-before-0226-0227-20260824T101605Z`; the four Core source
  hashes and Web runtime source hash matched the local checkout. Migration
  remained `0041_ai_debate_questionnaires`, the running directory remained
  `0755`, and the final zero-active-match preflight passed.
- The deployed `MatchActor` completed the deterministic two-theory and two-turn
  free-debate scenario at sequence 35 with `FINISHED / MATCH_FINISHED`. LLM,
  TTS, judge and LiveKit production probes passed. The ASR readiness probe timed
  out, so physical microphone and real ASR start remain explicitly unaccepted.
- Final browser checks found no horizontal overflow, preserved the protected
  admin login boundary, and confirmed the public navigation did not regress.
  Ten 30-second observations kept all four services active, live/ready at 200,
  restart counters at zero, and produced no new warning-or-higher logs.
