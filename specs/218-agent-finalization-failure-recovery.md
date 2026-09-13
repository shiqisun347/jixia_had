# 218 Agent finalization failure recovery

- Version: v2.0
- Status: Released to production; provider-chain follow-up remains open
- Approved by: user request to fix the production Agent finalization deadlock
- Date: 2026-08-24
- Depends on: Specs 162, 163, 217

## Problem

When an `agent.finalizing` event starts the asynchronous finalization task,
an exception or indefinite wait can leave the match in `AGENT_FINALIZING`
forever. The background task logger records the failure but the authoritative
Actor receives no transition, so the next action never starts.

## Required behavior

- Agent finalization has a bounded timeout.
- Any finalization exception or timeout submits one authoritative `system.error`
  command with a stable error code and the current match/speech/generation
  identity.
- Duplicate failure notifications are idempotent and stale callbacks cannot
  alter a newer action.
- The failure is visible in redacted diagnostics with task name and exception
  type; raw provider payloads and secrets are excluded.
- Existing successful finalization and interruption reset behavior is unchanged.

## Acceptance

- A finalization exception cannot leave an Actor in `AGENT_FINALIZING` without
  either `agent.finalized` or `ERROR/RECOVERY_REQUIRED`.
- A hung finalization reaches bounded recovery.
- A late finalization after recovery is ignored as stale.
- Existing Agent, match reset, Core and repository gates remain green.

## Rollback

Source-only rollback: restore the previous Core source and restart `jx-core`.
No migration is required.

## Local verification (2026-08-24)

- Agent and match recovery regression tests: 32 passed.
- Core suite: 322 passed, 32 skipped.
- Ruff, Pyright, Web lint/typecheck, OpenAPI contract check and production Web
  build passed.

## Production release evidence (2026-08-24)

- The user authorized termination of the only non-terminal match. The command
  was submitted through the production `MatchActor` as privileged
  `match.terminate`; the match reached `TERMINATED` at sequence 29 before the
  release gate was opened.
- Preflight then passed with four services active, Core live/ready 200,
  migration `0040_formal_4v4_opportunities`, and zero non-terminal matches.
- Rollback backup: `/opt/jixia-backup-before-0218-20260823T163420Z`.
- `agent/runtime.py` and `matches/service.py` were synchronized with matching
  SHA-256 hashes. Core compiled and restarted with `NRestarts=0`.
- Post-release preflight passed. Ten 30-second samples over five minutes kept
  Core, Jobs, Web and LiveKit active, live/ready 200, with restart counters
  `0/0/0/0`.
- The earlier ASR provider `wait_ready()` timeout remains an external provider
  acceptance gap; it was not retried as part of this source-only fix.

## Follow-up hotfix: callback commit retry (2026-08-24)

- Production inspection of the first-theory-to-free-debate stall found that
  generation and audio persistence had completed, but the authoritative
  `finalize_agent_speech` callback transaction failed. The speech remained in
  `FINALIZING`, so the next action could not be compiled.
- `finalize_agent` now retries that callback once after a bounded 50 ms delay.
  Cancellation still propagates, and a second failure uses the existing
  `agent_finalization_failed` recovery path; no timing or state-machine rule
  was changed.
- Regression coverage verifies transient callback failure followed by success.
  Agent voice and match-domain tests: 104 passed; Ruff, Pyright, and
  `git diff --check` passed.
- Production backup: `/opt/jixia-backup-before-agent-finalization-retry-20260824T024248Z`.
  Core was restarted after syncing the runtime file; live/ready checks were
  200, all four services were active, migration `0041_ai_debate_questionnaires`
  was present, and there were zero active matches. Runtime SHA-256:
  `d6c043154cb2392354fe304c66616ad5ab1b3df7da9c5fe9eb405dc790558623`.

## Recurrence diagnosis (2026-08-24)

- Match `ceea7ef0-96fa-4276-879d-c0dfc9ad0266` reproduced the incident after
  the callback retry hotfix. Its durable sequence was `agent.finalizing` at
  sequence 14 followed by `match.error` with `agent_finalization_failed` at
  sequence 15; no `agent.finalized` event was committed.
- The Agent generation and audio asset were already `FINALIZED`, and the
  speech contained the full 411-character transcript while remaining
  `FINALIZING`. This proves LLM/TTS generation completed and the failure was in
  the MatchActor finalization transaction, before the next action could enter.
- Four production incidents show the same signature. The finalization path now
  updates an existing `agent-{speech_id}` `MatchFile` row when present instead
  of unconditionally inserting it, and records only the sanitized exception
  type in diagnostics.

## Recurrence fix release evidence (2026-08-24)

- The current `ERROR` match was terminated through an isolated, privileged
  `MatchActor` after Core was stopped, avoiding concurrent actors. The source
  fix was then released and the preflight passed at migration
  `0041_ai_debate_questionnaires` with zero non-terminal matches.
- Match `ceea7ef0-96fa-4276-879d-c0dfc9ad0266` reached `TERMINATED` at sequence
  16 through `match.terminate`; no direct database state mutation was used.
- Backup: `/opt/jixia-backup-before-finalization-idempotency-20260824T20260824T030911+0800`.
- Deployed runtime hashes match the local source:
  `runtime.py` `d30c451c8f75c407f40a04d94938afc0159e6eaefd2430165c9bcf78e4546429`;
  `matches/service.py`
  `44dfec9cb4e4eb00680653691a9dbb5cc7df538beb4306de339e00cd38e8f528`.
- Release preflight passed: all four services active, Core live/ready 200,
  experiment capability valid, migration `0041_ai_debate_questionnaires`, and
  zero non-terminal matches. Post-release checks remained healthy with Core
  `NRestarts=0` and no new finalization/background-task errors.

## Definitive free-debate transition fix (2026-08-24)

- A fresh production run reproduced the earlier symptom after both Agent theory
  speeches had generated successfully. The failing transaction was the first
  free-debate decision persistence transaction: `AgentFreeDebateDecision` was
  inserted before its newly-added `FreeDebateOpportunity`, violating
  `fk_agent_free_debate_decisions_opportunity` (`23503`).
- `matches/service.py` now explicitly flushes a newly-created opportunity before
  adding its decision children. This is the definitive cause of the
  “negative theory ends and the match pauses” incident; it is not an LLM, TTS,
  audio playback, or `MatchFile` failure.
- A second bounded retry was added around the first `agent.playback_started`
  persistence callback. A second failure still follows the existing
  `agent_pipeline_failed` recovery path, and the runtime records only safe
  exception type/SQL constraint diagnostics.
- Regression evidence: 116 Agent/match recovery tests passed; Ruff, Pyright,
  and `git diff --check` passed. The PostgreSQL integration regression covers
  the opportunity/decision commit ordering when enabled with
  `RUN_DATABASE_INTEGRATION=1`.
- Production evidence after deployment: final test match
  `ddaf46a8-5517-4b45-989c-5051af64aa6c` recorded both theory
  `agent.finalized` events, then `free_debate.started`, `agent.decision_started`,
  and `hand.window_opened`; the no-candidate branch entered the specified
  `free.human_wait_started` and `match.paused` without `ERROR`. The test match
  was then terminated through a privileged `MatchActor` command.
- Final preflight passed with migration `0041_ai_debate_questionnaires`, zero
  non-terminal matches, all four services active, Core live/ready 200, and
  `NRestarts=0` for every service. Backups were created before each source-only
  runtime sync under `/opt/jixia-backup-before-fk-flush-*`,
  `/opt/jixia-backup-before-agent-pipeline-diagnostics-*`, and
  `/opt/jixia-backup-before-agent-playback-retry-*`.

## Full-flow regression follow-up (2026-08-24)

- The transition regression must cover a normally completed match, not stop at
  `free_debate.started` or a deliberate human-wait pause.
- The deterministic Actor test runs affirmative Agent theory, negative Agent
  theory, the first negative free-debate decision/speech, the following
  affirmative decision/speech, and asserts `FINISHED / MATCH_FINISHED` with a
  terminal `match.finished` event.
- Production browser navigation is a separate product issue. The root PRD fixes
  the public navigation to 首页 / 比赛大厅 / 使用指南 / 我的页面, while Spec 206
  explicitly requires 实验安排 to remain visible. The later product decision
  resolved this conflict in favor of the root PRD; the Web-only change and its
  release evidence are recorded in Spec 221.
- Verification completed with 330 Core non-integration tests, including the
  full-flow regression, and two formal 4v4 PostgreSQL integration tests against
  a migrated isolated database. The temporary database was removed afterward.
- Production key Core runtime hashes already matched local source. The latest
  completed production match crossed both theory finalizations and entered
  free debate without an error; no newer non-terminal match or Core error was
  present during the release observation.
