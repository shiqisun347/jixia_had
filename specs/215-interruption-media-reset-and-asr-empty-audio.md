# 215 Interruption media reset and ASR empty-audio recovery

- Version: v2.0
- Status: Implemented and published; live media acceptance still requires a real participant session
- Approved by: user approval of Specs 215 and 216
- Date: 2026-08-23
- Implementation plan: `docs/design/formal-4v4-free-debate-implementation-plan.md`

## Evidence

Production match `74f7c36c-3b17-4d3d-bb45-341869554810` exposed two recovery
failures:

- Agent speech `40d5a6b7-0081-4465-9ee7-00ac9eb47346` was paused at 17:11:17.
  Core logged `agent media cleanup timed out` and
  `agent attempt cleanup timed out`. The old LiveKit source was not proven
  silent before the resumed generation `0e319b24-ede3-45d5-a1e9-816f0360083c`
  started.
- Human speech `71e5d8c5-25d7-48a4-b278-6022e569923f` resumed at 17:13:30,
  created one ASR segment, then failed with `EmptyAudio`. The captured request
  contained zero PCM bytes and the provider response was `task-failed` with
  `error_code=EmptyAudio`.

## Scope

- A manual pause, error, or system recovery invalidates the interrupted Agent
  media generation and guarantees that no old source can publish audio after
  the authoritative pause event.
- A resumed Agent turn gets a new generation and speech identity. Its full LLM
  draft is persisted as the transcript; subtitle state starts empty and is
  scoped by the new speech identity.
- ASR reconnect/resume must verify that a subscribed LiveKit audio track is
  present and that PCM frames have arrived before starting the provider task.
  Empty-audio provider failures are recorded with the exact segment and call
  identifiers and follow the approved retry/pause policy, without silently
  treating them as a successful final.
- Add Core tests for pause/reset ordering, stale Agent callbacks, source
  cleanup timeout, ASR track-without-frames, and successful same-speech ASR
  resume. Add UI regression coverage that does not retain old live subtitles
  after a new speech identity begins.

## Non-goals

- No change to debate durations, pause/resume countdowns, ASR provider choice,
  LiveKit protocol, public transcript schema, or historical speech records.
- No frontend-owned recovery timer or media state authority.
- Formal-4v4 boolean decision parsing, fallback removal and private competition
  UI are specified separately by Spec 216.

## Acceptance

1. After pausing an Agent speech, no audio from the old generation is audible
   after the pause event is committed; resume starts only the new generation.
2. The resumed Agent transcript contains the complete new LLM draft, never a
   suffix assembled from playback subtitles.
3. A resumed human speech with a live audio track produces PCM before ASR task
   start and finalizes the same `speech_id` without a duplicate business turn.
4. A track with no PCM is surfaced as `EmptyAudio` with request/response
   capture, and cannot advance the match as a valid speech.
5. Stale callbacks from the interrupted generation or old ASR task are ignored
   after the new speech/generation identity is authoritative.

## Rollback

Restore the previous Core/Web source and restart services. No migration is
required; persisted events and call captures remain append-only.
