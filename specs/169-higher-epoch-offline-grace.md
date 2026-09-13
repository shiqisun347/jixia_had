# 169 Higher-epoch offline grace continuity

- Version: v1.0
- Status: Released and production-module verified; live-device boundary pending
- Approved by: production interruption plan and user request to complete testing and release
- Date: 2026-08-21

## Problem

Production dogfooding confirmed that a continuously absent participant can
receive a newer connection epoch before the new WebSocket and LiveKit channels
are both online. The newer epoch's `member.offline` currently emits a second
`match.offline`, overwrites `offline_since_ms`, and re-arms the 60-second grace
timer. A participant who was continuously absent for more than 60 seconds can
therefore avoid the required `PLAYER_OFFLINE_TIMEOUT` pause.

In match `bcfc23b4-f4da-4948-bce1-e67253821596`, epoch 27 became offline at
12:37:07.330. Epoch 28 committed another offline event at 12:38:08.220 and
online at 12:38:08.809. The continuous absence was 61.479 seconds, but the
second offline event reset the grace period and no timeout pause occurred.

## State Matrix

| Race or failure | Required authoritative behavior |
| --- | --- |
| Same epoch sends duplicate offline | Ignore it; preserve the first timestamp, timer and event sequence |
| Higher epoch becomes offline while the user is continuously offline | Persist only the newer epoch high-water; do not emit another event, change the timestamp, or replace the timer |
| Lower epoch sends offline after a newer epoch | Drop it without changing state |
| Higher epoch becomes online after repeated offline signals | Clear the continuous offline interval and cancel its original timer |
| Higher epoch offline arrives at 59 seconds | Preserve the original interval; pause at the original 60-second boundary |
| Expiry is queued before higher-epoch offline is processed | Serialize both commands; the higher epoch cannot invalidate or postpone the original expiry |
| Persisting a higher epoch fails | Roll back both the state snapshot and in-memory epoch high-water |
| Current human speaker receives repeated offline signals | Freeze the speech timer and pause ASR exactly once |

## Scope

- Separate epoch high-water advancement from the first online-to-offline state
  transition in `MatchActor._member_offline`.
- Persist a valid newer epoch even when no business event is emitted.
- Preserve the original `offline_since_ms`, offline expiry task, speech
  remaining time and ASR presence transition for a continuously offline user.
- Add deterministic Actor coverage for the 59/60-second race, online recovery,
  stale events, queued expiry and transaction rollback; add manager coverage
  that repeated offline signals do not pause ASR twice.
- Re-run the complete non-database gates and production release checks, then
  reproduce the boundary in a dedicated production match.

## Non-goals

- No API, Web, LiveKit protocol, database migration or grace-duration change.
- No change to the requirement that a genuine newer `member.online` ends the
  continuous offline interval.
- No Web-owned timer and no production data rewrite.

## Acceptance

1. A newer offline epoch during continuous absence persists as the high-water
   epoch but emits no event and leaves the first offline timestamp unchanged.
2. The same expiry task remains active and pauses once at the first offline
   timestamp plus 60 seconds, including when the newer epoch arrives at 59
   seconds or after expiry was queued.
3. A genuine newer online event before the original boundary cancels expiry;
   after the boundary it does not automatically resume a paused match.
4. Current-speaker deadline freeze and ASR pause occur only on the first
   offline transition.
5. Failed persistence restores the previous state and epoch high-water.
6. Release occurs only with zero active matches and includes source hash,
   import path, health, production event sequence and five-minute log checks.

## Rollback

Restore only the previous Core source and restart `jx-core`. Web, Jobs,
LiveKit, database schema and historical events remain unchanged. Rollback does
not modify already persisted connection epochs or match events.

## Evidence

- The deterministic regression first failed because epoch 5 emitted a second
  `match.offline` and replaced epoch 4's timestamp. It passes after the scoped
  Actor change, together with same-epoch duplicate, queued-expiry, transaction
  rollback and single-ASR-pause coverage.
- Local gates passed: tooling 47, QA 5, Web 166, Core 191, Jobs 19,
  Storybook 82, Playwright 200/200, Ruff, Pyright, ESLint, TypeScript,
  contracts, diff checks and the 27-page production build. The 26 Core and 2
  Jobs PostgreSQL integration tests remain unrun because this machine has no
  PostgreSQL or Docker daemon.
- Core-only production release used rollback point
  `/opt/jixia-backup-before-169-20260821125055`. The deployed file hash is
  `3e9043782e08306d0843f4ef9543fe459f8462cd76f9df0547001a99dee34a27`,
  matching the validated local source, and Core imports the repository module
  from `/opt/jixia-debate/apps/core/src`.
- The exact 59/60-second regression ran on the production host against the
  deployed module and passed. Release and final checks both found zero
  non-terminal matches. After 335 seconds, all four services were active,
  Core `NRestarts` was zero, live/ready and public Web were 200, and Core/Jobs
  had zero warning-or-higher records in the release window.
- A dedicated UI room was created and closed through the normal confirmation
  flow, but the isolated browser's synthetic microphone produced no valid
  amplitude, so it could not pass the mandatory device check or start a real
  human match. A live-device cold-start reproduction remains required and is
  not represented as passed.
