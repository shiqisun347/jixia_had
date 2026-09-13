# 217 Unified asynchronous callback identity envelope

- Version: v2.0
- Status: Implemented locally; production release pending protected deployment session
- Approved by: user approval in current task
- Date: 2026-08-23
- Depends on: Specs 215 and 216
- Implementation inventory: `docs/design/callback-envelope-inventory.md`

## 1. Problem

Agent, ASR and media callbacks currently validate different subsets of identity.
Some callbacks carry only `match_id` and `speech_id`; others carry a generation or
decision round and rely on Core to recover the remaining context from mutable
state. That makes late callbacks difficult to classify consistently and leaves a
protocol gap in Spec 216 §5.1.

## 2. Goal and non-goals

Every asynchronous callback that can mutate match state or append a provider
result carries one immutable identity envelope. Core validates the envelope
before any Actor command, event publication or business-row mutation. A mismatch
is persisted as a stale diagnostic and has no match side effect.

This does not change stage timing, provider selection, LiveKit transport, public
transcript schemas, or the formal 4v4 selection rules. It does not expose private
identifiers to public HTTP/WebSocket projections.

## 3. Envelope

```json
{
  "match_id": "uuid",
  "speech_id": "uuid|null",
  "attempt_no": 1,
  "generation_id": "uuid|null",
  "connection_epoch": null,
  "context_version": 12,
  "opportunity_id": "uuid|null",
  "opportunity_generation": null
}
```

`speech_id`, `generation_id`, `connection_epoch`, `opportunity_id`, and
`opportunity_generation` are nullable only when that resource does not apply to
the callback class. The envelope itself is always present. `attempt_no` is a
positive integer, `context_version` is a non-negative integer, and present epoch
or generation values are positive integers. The producer captures the values at
task creation; it must never rebuild them from current mutable state when the
callback completes.

| Callback class | speech | generation | connection epoch | opportunity |
|---|---:|---:|---:|---:|
| Agent free-debate decision | source speech when present | null | null | required |
| Agent LLM/TTS/playback | required after speech allocation | required | null | required in free debate |
| Human ASR segment/final | required | null | required | required in free debate |
| Fixed-stage Agent/Human callback | required where speech exists | per media kind | per media kind | null |

Before an Agent speech row exists, generation callbacks may carry a null
`speech_id`; once playback allocation creates the speech, all subsequent
callbacks for that task use the bound speech identity.

## 4. Callback classes

The envelope is required for:

- Agent free-debate decisions, LLM text generation, TTS completion/retry,
  playback start/progress/finish, finalization and failure.
- ASR segment start, interim, final, provider capture, failure and speech
  finalization.
- Human media connection/presence transitions that can pause or resume ASR.

Non-mutating logs may reference the envelope but are not themselves callbacks.

## 5. Validation and stale handling

Core validates in this order:

1. `match_id` exists and is the target Actor.
2. `context_version` and `opportunity_generation` match the authoritative
   opportunity/snapshot when those resources are present.
3. `speech_id`, `generation_id`, and `connection_epoch` match the currently
   authoritative identity for the callback class.
4. `attempt_no` is the current or expected retry attempt.

Any mismatch is recorded with `stale=true`, a stable reason code, callback
class, and the complete redacted envelope. It must not publish subtitles,
advance timers, finalize speech, allocate a speaker, or change an Actor state.
Duplicate callbacks with the same envelope and idempotency key are harmless.

## 6. Compatibility rollout

Migration is additive and does not rewrite historical events.

1. Add a typed `CallbackEnvelope` and constructor helpers. These callback
   protocols are internal, so all producers, consumers and test doubles change
   together; do not keep a second legacy signature that can bypass validation.
2. Convert Agent decision and Agent generation/TTS callbacks first, with tests
   proving late generation callbacks are rejected.
3. Convert ASR segment and connection callbacks, including the LiveKit epoch.
4. Run a static audit proving no internal producer still calls a callback
   without the envelope. Historical rows remain readable with nullable identity
   columns where the data did not exist.

High-frequency interim, subtitle and text-delta callbacks must not introduce a
database query per chunk. State-changing boundaries validate durable identities;
subsequent progress callbacks validate against the accepted in-memory run/session
identity and the current Actor speech fence.

No public API or WebSocket payload includes the raw envelope. Administrator
diagnostics may show it subject to existing redaction and permissions.

## 7. Persistence

Provider request logs already have the envelope identity columns except a
duplicated `opportunity_generation`. That generation is immutable on the
`FreeDebateOpportunity` row identified by `opportunity_id`, including after the
opportunity is invalidated. Stale diagnostics can retain the complete redacted
envelope in the existing structured `details` field. Therefore this design does
not require a migration. Stale callback diagnostics use the existing
diagnostic/request-log path; no new service or queue is introduced.

## 8. Acceptance

- Static audit: every callback protocol and producer includes `CallbackEnvelope`.
- Unit tests cover mismatches for each identity field and prove no Actor side
  effect, including duplicate/idempotent callbacks.
- Integration tests cover a paused Agent generation, an ASR reconnect with a
  higher connection epoch, a late decision after the three-second deadline, and
  a reset with a new context version.
- Existing Core, Jobs, Web, contract, browser and build gates remain green.
- Staging logs show stale callbacks retained as diagnostics without public
  leakage.

## 9. Rollback

Rollback is source-only because this design adds no migration. Restore the
previous Core source and restart Core; never delete historical diagnostics.

## 10. Local verification (2026-08-23)

- Core tests: 320 passed, 32 skipped.
- Jobs tests: 23 passed, 2 deselected.
- Web tests: 184 passed.
- Tooling/QA tests: 53 passed.
- Ruff, ESLint, Pyright, TypeScript and OpenAPI contract checks passed.
- `pnpm build` passed and generated 34 production routes.
- Browser suite: 228 passed across reference, desktop, compact and wide viewports.
- `git diff --check` passed.

## 11. Production release evidence (2026-08-23)

- Read-only preflight passed before and after sync: four systemd services active,
  Core live/ready 200, migration `0040_formal_4v4_opportunities`, and zero
  non-terminal matches.
- A root-only rollback backup was created at
  `/opt/jixia-backup-before-0217-20260823T151553Z` before source synchronization.
- Only the four Core source files in the implementation slice were synchronized;
  each remote SHA-256 matched the local file. No environment, database, Web
  standalone, or runtime directory permission was overwritten.
- Core compiled and restarted successfully; post-restart `NRestarts=0`.
- Ten 30-second samples over five minutes kept Core, Jobs, Web and LiveKit
  active with live/ready 200 and all restart counters at zero.
- The protected provider-chain probe was attempted twice but both attempts timed
  out in ASR `wait_ready()` before a provider session became ready. TTS/LLM/
  judge/LiveKit probe success is therefore not claimed for this release window;
  this is an external provider-chain acceptance gap requiring follow-up.
