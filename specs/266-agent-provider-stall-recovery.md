# 266 Agent provider stall recovery

- Version: v2.0
- Status: Implemented and released to production
- Approved by: user request to test, fix, and release the Agent thinking stall
- Date: 2026-10-10
- Depends on: Specs 162, 215, 217, 218, 223

## Problem

After an Agent attempt is cancelled or interrupted, the per-match Qwen TTS
connection can remain in `AgentRuntime._tts_connections` and be reused by the
next attempt. If TTS then fails while the LLM continues streaming, the LLM
callback can block forever on the bounded text queue after the TTS consumer has
exited. The generation and provider-call records remain `STARTED`, leaving the
match in `AGENT_PREPARING` and the UI showing that the Agent is thinking.

## Required behavior

- Cancelling or resetting an Agent attempt fences media, cancels the attempt,
  and discards the match-scoped TTS connection within the bounded cleanup path.
- Every LLM text delta either enters the TTS queue or observes the TTS task's
  failure/early completion and fails the Agent attempt promptly. It must never
  wait forever for a queue consumer that has already exited.
- Provider failures continue through the existing retry and authoritative
  `handle_agent_failure` semantics; no silent provider or model fallback is
  introduced.
- Existing successful streaming, TTS socket reuse between successful attempts,
  playback fencing, stale-callback rejection, and finalization behavior remain
  unchanged.
- Diagnostics contain only stable error codes and safe exception types; no
  provider payloads, API keys, or internal paths are exposed.

## Acceptance

- A stale TTS connection is closed and removed when an Agent run is cancelled.
- A TTS failure while the LLM produces more than 32 deltas converges to the
  existing failure/retry path instead of blocking the LLM callback.
- A successful Agent attempt still completes playback and finalization.
- Targeted Agent voice, MatchActor recovery, and Web runtime tests pass.
- Production release is gated on zero non-terminal matches, active services,
  Core live/ready 200, and post-release health samples.

## Evidence

- Core test suite: 420 passed, 76 skipped; targeted Agent/MatchActor tests:
  49 passed; failure/recovery slice: 22 passed.
- Ruff and Pyright passed with zero errors. The two regression tests cover the
  full TTS queue and cancellation cleanup paths.
- Production preflight passed with migration `0044_rule_audio_generations`,
  zero non-terminal matches, four active services, and live/ready 200.
- Production Core runtime was replaced with the tested file, restarted once,
  and served six consecutive live/ready/public 200 samples. The rollback copy
  is retained at `/opt/jixia-spec266-20261010/backup/runtime.py`.

## Rollback

Source-only rollback: restore the previous Core runtime source and restart
`jx-core`. No migration or historical data change is required.
