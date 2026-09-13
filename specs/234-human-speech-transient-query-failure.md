# Spec 234: Human Speech Completion Query Resilience

## Scope

Keep an already-entered live match mounted when a post-speech snapshot, room,
or current-user refresh has a transient failure. Make human raw-audio
persistence idempotent across normal and late ASR finalization paths.

## Invariants

- Initial authorization or initial match snapshot failure still blocks entry.
- Once a valid match snapshot, room, and authenticated user are present, a
  later refresh failure does not replace the live match UI.
- WebSocket snapshots remain authoritative for live match state.
- At most one `(match_id, file_key)` row exists for a human raw recording;
  repeated finalization updates the existing row.
- No match state, timer, media protocol, or permission semantics change.

## Acceptance

- A transient snapshot/room/current-user refresh error produces a recoverable
  notice and the current match remains visible.
- A fresh WebSocket snapshot clears a stale snapshot query error.
- Repeated human ASR finalization does not raise a unique-constraint error.
- Existing initial-entry error and terminal-match behavior remain unchanged.

## Rollback

Revert the frontend error-boundary and idempotent file persistence changes; no
schema or data migration is required.
