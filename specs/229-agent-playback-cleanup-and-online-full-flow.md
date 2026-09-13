# 229 Agent playback cleanup and online full-flow verification

- Version: v2.0
- Status: Released
- Requested by: QA finding during approved Spec 228 release verification
- Date: 2026-08-24
- Depends on: Specs 216, 218, 223, 228

## Production reproduction and root cause

The first production account-to-room run reached a real Agent theory speech,
then its browser test connection went offline while the Agent was speaking.
Core correctly paused under the existing participant-offline safety rule. That
run was not valid evidence of an Agent state-machine defect.

A second isolated account and named browser session kept both Core WebSocket and
LiveKit online. It completed affirmative theory, negative theory and the host
transition into affirmative-first free debate. The affirmative decision round
selected an Agent, but both `agent.playback_started` callback attempts failed.
Core entered `ERROR / RECOVERY_REQUIRED` with `agent_pipeline_failed` at
sequence 32 while `offline_user_id` remained empty.

The persisted redacted diagnostic identifies the exact database failure:

- exception: PostgreSQL `IntegrityError`, SQLSTATE `23503`;
- constraint: `free_debate_opportunities_source_speech_id_fkey`;
- failing boundary: the first free-debate Agent playback start;
- generation: the selected Agent's otherwise successful LLM/TTS generation.

During one commit, `agent.playback_started` creates the Agent `Speech`, while
the newly opened next-side `FreeDebateOpportunity.source_speech_id` points to
that same Speech. SQLAlchemy has no relationship ordering for this circular FK
pair and flushes the opportunity reference before the Speech insert. Fixed
theory speeches do not pre-open a next-side opportunity and therefore cannot
exercise the failing transaction. The later `agent_cleanup_timeout` is cleanup
after the authoritative pipeline error, not the primary cause.

The first post-fix online run then reproduced the same constraint failure for
an allocated human speaker. Both WebSocket `speech.start` attempts raised a
persisted `IntegrityError`: `speech.started` also creates a `Speech` and opens
the next-side opportunity in one commit. This is the same circular write, not
a separate state-machine or media failure, and is included in the approved
full-flow acceptance boundary.

## Scope after approval

- Change the free-debate Agent and human speech-start persistence path so the
  new Speech row is flushed before any new opportunity stores it as
  `source_speech_id`.
- Keep the existing allocated opportunity on `Speech.opportunity_id`; do not
  remove either relationship or weaken either FK.
- Add a real PostgreSQL integration regression for both forms of the exact
  circular write: selected Agent playback and allocated human speech start,
  their Speech and following opportunity are committed, and both FK directions
  point to existing rows.
- Extend the online WebSocket/LiveKit QA harness using an authenticated account
  without exposing credentials or provider payloads.
- Re-run account creation, room creation, automatic Agent fill, both fixed
  speeches, affirmative-first free debate, normal `FINISHED`, post-match survey
  task generation/submission, and personal survey draft/submission.
- Capture the authoritative event sequence, connection epochs, pause/error
  reason, survey task status, and audit records.
- Keep cleanup changes out of this fix unless a post-FK-fix run independently
  reproduces cleanup timeout without a preceding pipeline/interruption error.
  Do not relax safety pause behavior or bypass MatchActor.

## Acceptance

1. A continuously connected authenticated participant remains online through
   both theory speeches and free debate.
2. The first free-debate Agent playback transaction commits its Speech before
   the next opportunity references that Speech; no FK is removed or nullable
   behavior expanded.
3. The match reaches `FINISHED / MATCH_FINISHED` without `match.paused` or
   `system.error`; the first free-debate holder is affirmative under Spec 228.
4. The participant receives exactly one post-match task, can save and submit
   the approved HUMAN_SELF/TEAM_AI questionnaire, and cannot access it for a
   terminated match.
5. The same account can save and submit the personal AI-experience survey;
   submission is idempotent and creates the documented revision behavior.
6. Any implementation change has focused Core/Web tests, full release gates,
   a rollback backup, and post-release service stability evidence.

## Rollback boundary

No migration or direct rewrite of matches/surveys is permitted. Before any
business-code change, retain a source/runtime rollback backup and stop if the
online harness cannot prove the failure class. Existing paused or terminated
QA records remain audit evidence and are not deleted casually.

## Release evidence

- Agent and human speech starts now flush the new `Speech` before the following
  opportunity references it. The real PostgreSQL regression covers both FK
  directions and passes (`3 passed`).
- Full gates passed: Core `366 passed`, Jobs `25 passed`, Web `191 passed`,
  tooling `47 passed`, QA `6 passed`, Storybook `90 passed`, browser `236
  passed`, contracts, lint, typecheck and the 37-route production build.
- Production matches `0919e342-34ba-4f3c-93ba-0024da7cf322` and
  `74840b50-a941-48dc-bab9-7eb1dfb96565` reached natural `FINISHED`. They covered
  both fixed speeches, affirmative-first free debate, Agent turns and finalized
  human Speech rows.
- The enabled-snapshot match generated exactly one task for the QA user with
  seven `HUMAN_SELF` and seven `TEAM_AI` items. Reveal gating, the five overall
  questions, submission locking, personal survey reopen/resubmit and revision
  retention passed. Terminated QA matches generated no task.
- Production QA found and released a PostgreSQL-only response fix: refresh the
  post-match task after flush before reading server-generated `updated_at`.
- Rollback remains at `/opt/jixia-backup-before-0229-20260824T163719Z`. Core is
  healthy 200/200, runtime permissions are 0755, restart counts are zero, and no
  active match remains.

## Residual QA limits

- Human audio used a clearly labelled synthetic Chinese track and is not a
  physical microphone acceptance test.
- Repeated synthetic tracks intermittently produced provider
  `asr_task_failed`; one resumed speech remained at `HUMAN_SPEAKING` with zero
  speech time until the participant used the normal early-finish control.
