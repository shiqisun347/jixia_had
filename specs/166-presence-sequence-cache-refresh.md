# 166 Presence sequence cache refresh

- Version: v1.0
- Status: Released
- Approved by: user request to complete testing and production release
- Date: 2026-08-21

## Problem

Core is authoritative for match sequence. A committed `member.online` or
`member.offline` transition increments that sequence and broadcasts
`match.online` or `match.offline`. The Web client currently refreshes only the
room snapshot for those events. Its cached match snapshot can therefore retain
an older sequence, causing the next command to fail with
`match_state_conflict` even though the user has just reconnected successfully.

This was reproduced in a dedicated production match after leaving and
returning to the debate page. Server events reached sequence 9, while the next
`speech.start` command used the older cached sequence and was rejected.

## Scope

- Invalidate the authoritative match snapshot after `match.online` and
  `match.offline` events.
- Continue invalidating the room snapshot so presence UI remains current.
- Preserve monotonic snapshot application through `newestMatchSnapshot`.
- Add a WebSocket-to-React-Query regression test for both presence event types.
- Confirm that a command sent after the refreshed snapshot uses its sequence.

## Non-goals

- No Core state-machine, timer, epoch, lease, or LiveKit protocol changes.
- No client-side sequence increment or optimistic presence authority.
- No polling and no changes to command conflict semantics.

## Acceptance

1. `match.online` and `match.offline` invalidate both
   `['matches', match_id, 'snapshot']` and the matching room snapshot.
2. After the snapshot refetch resolves, the next command carries the new
   `expected_sequence` and current `connection_epoch`.
3. An older refetch response cannot overwrite a newer WebSocket snapshot.
4. Web unit tests, typecheck, lint, contracts check, repository tests, and the
   production Web build pass.
5. Production deployment occurs only with no active match in `RUNNING`,
   `PAUSED`, `START_COUNTDOWN`, or `START_PENDING_RUNTIME`, and is followed by a
   dedicated reconnect reproduction plus log observation.

## Rollback

Restore only `apps/web/src/features/debate/use-match-runtime.ts`, its regression
test, and the previous Web standalone deployment. Core, Jobs, LiveKit, database
schema, and already persisted match events are unchanged.

## Evidence

- Regression test covers both presence event types, verifies match and room
  invalidation, and proves the next command uses sequence 9 with the current
  epoch instead of the stale sequence 7.
- Automated gates passed: tooling 47, QA 5, Web 160, Core 175, Jobs 19,
  Storybook 82, and Playwright 200. TypeScript, Pyright, ESLint, Ruff, OpenAPI
  contracts, formatting, and the 27-page production build also passed.
- Production was gated at zero active matches. The Web-only release used
  `/opt/jixia-backup-before-166-20260821013500` as its rollback point; Core,
  Jobs, LiveKit, and the database were not deployed or restarted.
- In a fresh production match, one browser tab left the debate page for about
  five seconds and returned. After the reconnect and media unlock, `speech.start`
  entered live human speech without `match_state_conflict`; the same UI then
  terminated the test match successfully.
- Five minutes after release, all four services were active, Core live/ready and
  local/public Web checks returned 200, active matches were zero, and Web/Core/Jobs
  error-level journals plus the browser error list were empty.
