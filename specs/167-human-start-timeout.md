# 167 Human speech start timeout

- Version: v1.0
- Status: Released and production-verified
- Approved by: user request to complete testing and release after terminating the active match
- Date: 2026-08-21

## Problem

The PRD gives a human speaker 60 seconds to press "Start speaking" after
Core grants the turn. Production inspection confirmed that a match can remain
in `RUNNING/HUMAN_READY_TO_START` for more than two minutes because MatchActor
does not schedule or handle this timeout.

This is a Core-authoritative timer. The Web may display the state but must not
advance or pause the match.

## State Matrix

| Entry or race | Required authoritative behavior |
| --- | --- |
| Fixed human action becomes ready | Schedule one 60-second start window without consuming speech time |
| Human wins a free-debate selection | Schedule the same 60-second start window |
| Human speech is reset or ASR retry returns to ready | Restart a full 60-second start window |
| Global resume returns to human ready | Restart a full 60-second start window, preserving speech allowance |
| 59.9 seconds have elapsed | Remain `RUNNING/HUMAN_READY_TO_START`; re-arm the remaining boundary if an expiry arrives early |
| 60.0 seconds have elapsed | Enter `PAUSED/RECOVERY_REQUIRED` with reason `HUMAN_START_TIMEOUT` |
| `speech.start` commits before expiry | Cancel/replace the start timer with the speech deadline |
| Expiry was queued before `speech.start` but handled afterward | Ignore it because the authoritative ready deadline is no longer current |
| Member disconnects/reconnects within the grace period | Do not restart the start window; presence and start timers remain independent |
| Manual pause, system recovery, error, termination, or action advance | Cancel the start timer and clear its runtime deadline |

## Scope

- Add a dedicated MatchActor internal `human.start_timeout` command and an
  in-process monotonic deadline for the current ready window.
- Schedule it from every human-ready entry path.
- Freeze the full speech allowance when timeout pauses the match.
- Persist `HUMAN_START_TIMEOUT` in the existing snapshot `error_code` field and
  show a specific actionable Chinese explanation in the Web UI.
- Add deterministic MatchActor regression tests for fixed, free-debate,
  reset/retry, resume, boundary, cancellation, reconnect, and queued-expiry
  races.
- Update the state-matrix documentation and project memory after validation.

## Non-goals

- No Web-owned timeout, client command, API or OpenAPI shape change.
- No database migration. A Core restart already enters system recovery; after
  operator resume, the PRD requires a fresh full 60-second window.
- No change to the independent 60-second offline grace period or speech
  duration accounting.
- No change to pause/resume authorization.

## Acceptance

1. Every transition into `HUMAN_READY_TO_START` has exactly one authoritative
   60-second start timer.
2. At 59.9 seconds the match is still running; at 60.0 seconds it pauses with
   `paused_from_action_state=HUMAN_READY_TO_START` and
   `reason=HUMAN_START_TIMEOUT`.
3. The timeout never decreases `speech_remaining_ms`.
4. Starting speech, pausing, terminating, advancing, or replacing the ready
   window makes a stale timeout harmless.
5. Resume grants a new full 60 seconds, while an ordinary reconnect does not
   extend the existing window.
6. Focused Core tests, full non-database tests, type checks, lint, contracts,
   Web tests, browser tests, and production build pass. PostgreSQL integration
   tests run where a database is available or are reported accurately.
7. Production deployment occurs only with zero active matches and includes
   module-path/hash checks, live/ready checks, a dedicated real 60-second
   reproduction, and at least five minutes of log observation.

## Rollback

Restore only the previous Core source deployment and its systemd process.
Jobs, Web, LiveKit, database schema, persisted historical events, and spec 166
remain unchanged. The timeout adds no irreversible data migration.

## Evidence

- Deterministic MatchActor coverage includes fixed and free-debate entry,
  59.9/60.0-second boundaries, speech-start/queued-expiry ordering, reset,
  resume, reconnect, pause, termination, system recovery, and error cleanup.
- Local gates passed: tooling 47, QA 5, Web 166, Core 184, Jobs 19,
  Storybook 82, Playwright 200/200, Ruff, Pyright, ESLint, TypeScript,
  contracts, diff checks, and the 27-page production build. The 26 PostgreSQL
  integration tests remain unrun because this machine has no PostgreSQL or
  Docker daemon.
- Core and Web were released only after the active-match count reached zero.
  Core source and the final Web source hashes match the local validated files;
  the deployed Core imports from `/opt/jixia-debate/apps/core/src`.
- Core/Web rollback point:
  `/opt/jixia-backup-before-167-20260821023303`. The follow-up Web-only message
  rollback point is
  `/opt/jixia-backup-before-167-web-message-20260821024824`.
- Two dedicated production matches paused at 60,009ms and 60,008ms after the
  latest `speech.ready` event. Both persisted `HUMAN_START_TIMEOUT`; the second
  displayed the actionable Chinese explanation in the real page. Resume
  returned to `HUMAN_READY_TO_START` after the global three-second countdown,
  proving a fresh window was scheduled. Both QA matches were terminated via
  the normal UI and active matches returned to zero.
- More than five minutes after the final Web switch, all four services were
  active, Core ready and local/public Web returned 200, and Core/Jobs had no
  warning-or-higher records. Web recorded only the controlled restart's
  SIGTERM exit 143 before starting normally; `NRestarts=0` afterward.
