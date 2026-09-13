# 168 Human speaking reconnect and ASR continuity

- Version: v1.0
- Status: Released and production-verified
- Approved by: production interruption plan and user request to complete testing and release
- Date: 2026-08-21

## Problem

Production dogfooding confirmed that closing the last debate tab while the
current human is speaking commits `match.offline`, but does not pause the ASR
runtime. The detached audio track later causes `asr_task_failed`; the ordinary
ASR retry path starts a replacement attempt while the user is still offline,
and the second failure moves the match to `ERROR` about eleven seconds after
the disconnect. This violates the authoritative 60-second offline grace
period.

The observed sequence for match
`97405745-02a0-4e68-889f-c48aff6eb73d` was `speech.started` at 12:04:44.772,
`match.offline` at 12:04:57.352, and `match.error/asr_task_failed` at
12:05:08.811. The returning browser obtained a newer lease, but the match was
already in error recovery.

## State Matrix

| Race or failure | Required authoritative behavior |
| --- | --- |
| Current human speaker becomes offline | Freeze the speech deadline and pause ASR for the same `speech_id` after the presence event commits |
| Same epoch/user becomes online within 60 seconds | Resume ASR for the same `speech_id` and remaining speech time; do not create a new business speech attempt |
| Offline then online arrives before ASR pause completes | Serialize pause then resume; never fail with `asr_speech_busy` and never run two sessions |
| Old ASR task fails after the speaker is authoritatively offline | Treat it as interruption cleanup, not a provider retry or `system.error` |
| Old pause/resume work completes after speech/action/epoch changed | Drop it without changing the current match |
| Non-current member disconnects | Do not pause or restart current ASR |
| Current speaker stays offline for 60 seconds | Enter `PAUSED/PLAYER_OFFLINE_TIMEOUT`; keep existing recovery semantics |
| ASR fails while the current speaker is online | Preserve the existing one-retry-then-error behavior |
| Manual pause, termination, reset, or Core recovery races reconnect | Existing authoritative transition wins; stale media work is cancelled or ignored |

## Scope

- Couple committed `match.offline` and `match.online` events for the current
  human speaker to bounded, serialized ASR pause/resume operations.
- Validate `match_id`, `speech_id`, current speaker, action state and online
  marker again after every await before resuming media work.
- Suppress a late ASR failure only while that exact current speech's speaker is
  authoritatively offline; do not weaken real provider-failure handling.
- Add manager/runtime tests for normal reconnect, rapid reconnect, stale
  completion, non-speaker disconnect, late offline failure and genuine online
  failure.
- Re-run the Core, Web and browser regression gates, then repeat the production
  last-tab test under the zero-active-match release gate.

## Non-goals

- No Web-owned timer or Web command is added.
- No database migration, API shape, LiveKit protocol or 60-second duration
  change.
- No claim that headless synthetic input replaces final real-device ASR
  acceptance.

## Acceptance

1. Leaving during `HUMAN_SPEAKING` for 10 seconds and returning keeps the match
   running, resumes the same business speech and preserves remaining time.
2. No `asr.retry_required`, `match.error` or duplicate `speech.started` is
   emitted solely because the browser audio track disconnected.
3. A 59.9-second return still recovers; a continuous 60-second absence pauses
   once with `PLAYER_OFFLINE_TIMEOUT`.
4. A real ASR failure while online retains the existing retry/error semantics.
5. The production release occurs only with zero active matches and includes
   source hash, health, event sequence and five-minute log checks.

## Rollback

Restore only the previous Core source and restart `jx-core`. Web, Jobs,
LiveKit, database schema and historical events remain unchanged. Any speech
already interrupted before rollback follows the persisted authoritative match
state; rollback does not rewrite production data.

## Evidence

- Local gates passed: tooling 47, QA 5, Web 166, Core 188, Jobs 19,
  Storybook 82, Playwright 200/200, Ruff, Pyright, ESLint, TypeScript,
  contracts, diff checks and the 27-page production build. The 26 PostgreSQL
  integration tests remain unrun because this machine has no PostgreSQL or
  Docker daemon.
- Core-only release used rollback point
  `/opt/jixia-backup-before-168-20260821122033`. The deployed ASR session,
  runtime and match service hashes match the validated local files, and Core
  imports from `/opt/jixia-debate/apps/core/src`.
- Production match `c4474be5-ea07-4f0c-beb9-d6f20aaa807c` started human
  speech at 12:22:12.749, committed the final-tab offline event at
  12:22:22.740, committed online at 12:22:35.249, and reached its original
  30-second speech deadline at 12:22:55.264. The approximately 12.5-second
  absence was excluded from the speech timer, the same `speech_id` continued,
  the Web automatically republished the microphone, and no `match.error` or
  disconnect-induced retry occurred.
- The headless synthetic microphone produced no usable speech, so normal ASR
  finalization later exercised the existing first-attempt retry and returned
  to `speech.ready`. This is not evidence for real-device recognition quality.
  The dedicated QA match was terminated through the normal UI afterward.
- More than five minutes after the Core restart, all four services remained
  active, Core `NRestarts` was zero, Core live/ready and local/public Web were
  200, active matches were zero, and Core/Jobs had no warning-or-higher log
  entries in the release window.
