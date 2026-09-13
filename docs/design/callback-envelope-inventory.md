# Callback envelope implementation inventory

This inventory supports Spec 217 and is intentionally descriptive. It does not
change runtime behavior.

## Current callback surface

| Runtime | Callback | Current producer identity | Current consumer/persistence | Main gap |
|---|---|---|---|---|
| Agent | `report_free_decision` | match, action, agent, decision round, attempt | MatchActor validates round/phase; `AgentFreeDebateDecision` and `ExternalCall` persist opportunity/context | no typed envelope; source speech and opportunity generation are implicit |
| Agent | `agent_text_generated` | match, generation | Manager checks generation/action/context/current agent and latest generation | speech and opportunity are recovered after callback |
| Agent | `publish_agent_text_delta` | match, generation | WebSocket publication only | no context or speech fence at producer boundary |
| Agent | `publish_agent_retry` | match, generation | WebSocket publication only | retry attempt and context are implicit |
| Agent | `start_agent_playback` | match, speech, generation, agent | Actor command checks generation/speech/agent | attempt/context/opportunity are not carried |
| Agent | `publish_agent_subtitle` | match, speech, played time | Actor current speech check | generation and context are not carried |
| Agent | `finish_agent_playback` | match, speech | Actor current speech check | generation is not carried |
| Agent | `finalize_agent_speech` | match, speech, generation, text/audio | Actor and late-record path validate current state | attempt/context/opportunity are not carried |
| Agent | `handle_agent_failure` | match, generation, error | Manager checks latest generation | speech/context/opportunity are implicit |
| ASR | `start_asr_segment` | speech, segment, task | `AsrSegment` and `ExternalCall` rows | match, epoch and context are recovered |
| ASR | `publish_asr_interim` | match, speech, segment | Actor current speech check | task/epoch/context are not carried |
| ASR | `persist_asr_segment` | speech, segment, task | `AsrSegment` row keyed by speech/segment/task | connection epoch and context are implicit |
| ASR | `fail_asr_segment` | speech, segment, task | Updates matching started segment/call | no full identity envelope |
| ASR | `persist_asr_capture` | task, capture | Joins `AsrSegment` to `ExternalCall` | match/speech/epoch are indirect |
| ASR | `finalize_asr_speech` | match, speech | Actor current speech check and speech row | connection epoch/context/opportunity are implicit |
| ASR | `handle_asr_failure` | match, speech, error | Actor current speech check | connection epoch/context are implicit |
| Presence | offline/online transition | match, user, connection epoch | Actor connection epoch and speech state | speech/opportunity/context are captured only at consumer time |

## Identity acquisition plan

The producer owns a frozen `CallbackEnvelope` in its task/session object:

- Agent `AgentRun` receives context and opportunity identifiers when the Actor
  opens an action; generation creation binds `attempt_no` and generation ID.
- ASR `MatchAudioReceiver` receives the current connection epoch from the room
  lease and passes it into `AsrSpeechSession`; each segment inherits the same
  speech/context/opportunity identity.
- Presence callbacks already have a connection epoch; the manager resolves the
  authoritative speech only for validation, never to construct a late event.
- Decision tasks receive the opportunity generation together with the decision
  round; late results retain their original envelope for diagnostics.

## Persistence conclusion

`ExternalCall` already has nullable columns for every envelope field except a
duplicated `opportunity_generation`. That value is immutable on the
`FreeDebateOpportunity` row addressed by `opportunity_id`, and invalidation does
not delete the row. `SystemLogEvent.details` can persist the full redacted stale
envelope. The implementation therefore does not require a migration.

No historical row is rewritten. Public HTTP/WebSocket projections continue to
exclude the envelope.

## Implementation order

1. Typed value object, validation result and redacted stale diagnostic helper.
2. Agent decision and generation/TTS callbacks.
3. ASR segment and speech callbacks, including connection epoch.
4. Presence callbacks and adapter removal.
5. Static callback audit, unit/integration tests, staging, then production.

Each protocol-conversion slice updates its existing test doubles at the same
time. Because the callback protocols are not public APIs, no legacy overload or
optional envelope path is retained.

## File-level slices

### Slice 1: value object and validation vocabulary

- Add `apps/core/src/jx_core/runtime_identity.py` with the frozen envelope,
  callback-kind literal, applicability validation and redacted serialization.
- Add unit tests for invalid negative versions, resource applicability and
  stable serialization.
- No Actor, database or Web behavior changes in this slice.

### Slice 2: Agent decision and generation

- `agent/runtime.py`: freeze decision context/opportunity identity in each
  decision task; freeze generation attempt/context in `AgentRun`.
- `matches/service.py`: validate state-changing Agent callbacks and register an
  accepted in-memory generation identity for high-frequency deltas/subtitles.
- Update `test_agent_voice.py`, `test_experiment_free_debate_domain.py` and
  `test_match_service_reset.py` with stale-field and duplicate tests.

### Slice 3: ASR and presence

- Extend the internal `SpeechRuntime.start_speech` call with the authoritative
  connection epoch, context and opportunity identity captured by MatchActor.
- Store the envelope in `MatchAudioReceiver`/`AsrSpeechSession`; segment task IDs
  remain idempotency keys and do not replace the envelope.
- Update `test_asr.py`, presence tests and reset tests for higher epoch, old
  context, same-speech reconnect and EmptyAudio behavior.

### Slice 4: audit and release

- Static `rg`/AST test ensures every callback protocol method and every
  `_callbacks.*` invocation takes the envelope.
- Run the complete Core, Jobs, Web, contracts, Storybook, browser and build
  gates; then perform isolated PostgreSQL staging before any production sync.

## Test matrix

| Mutation | Expected result | Durable evidence | Public effect |
|---|---|---|---|
| wrong match | stale reject | structured diagnostic | none |
| old speech | stale reject | diagnostic + unchanged speech | no subtitle/final |
| old Agent generation | stale reject | generation remains cancelled | no audio/event |
| wrong attempt | stale reject | external call remains historical | no retry/final |
| old connection epoch | stale reject | ASR segment/capture retained stale | no interim/final |
| old context version | stale reject | diagnostic | no Actor mutation |
| old opportunity/generation | late/stale experiment record | decision remains ineffective | no allocation |
| exact duplicate | idempotent no-op | one business result | no duplicate event |

The performance test asserts that repeated interim/subtitle callbacks do not
open database sessions after their envelope has been accepted at the boundary.
