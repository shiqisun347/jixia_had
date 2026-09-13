# 230 Human ASR retry deadline and participant survey audit

- Version: v2.0
- Status: Implemented and deployed 2026-08-25
- Approved by: user on 2026-08-25
- Date: 2026-08-25
- Depends on: Specs 216, 217, 219, 220, 222, 223, 226, 229

## Production evidence and root cause

Production match `74840b50-a941-48dc-bab9-7eb1dfb96565` reached natural
`FINISHED`, but two human free-debate turns required manual early finish after
an ASR error recovery. This is not a frontend countdown defect:

- Speech `7346fb23-b701-448e-96bb-755cfc960e73` reached
  `speech.finalizing / TIME_LIMIT` at sequence 398 and then
  `match.error / asr_task_failed` at sequence 400. Recovery restarted the same
  Speech with a full 30,000 ms at sequence 403, but no second time-limit event
  arrived. Manual early finish finally advanced it at sequence 406.
- Speech `585b734a-dfea-4a15-9abb-cd4fd6b13d43` repeated the same sequence at
  445/447/450 and required manual early finish at 453.
- `speech.start` schedules its internal deadline with an idempotency key based
  only on `speech_id`. Error recovery intentionally reused that Speech ID, so
  the second deadline command matched the already cached first deadline and
  was silently returned as a duplicate. The authoritative display time reached
  zero while the state remained `HUMAN_SPEAKING`.
- ASR failure counting currently groups by `action_key`. Every free-debate turn
  shares action key `3:0`; therefore an earlier turn's failed Speech consumes
  the retry allowance of later independent opportunities. Production Speech
  attempts 18, 21 and 23 demonstrate this cross-turn accumulation.
- A failed Speech restarted after `match.error` currently reuses the same row.
  It can later become `FINALIZED` while retaining `asr_error_code`, and the
  speculative next-side opportunity opened by the failed attempt is not
  authoritatively invalidated before another is opened.

The same production flow submitted both participant questionnaires, but its
audit table contains only administrator survey view/export entries. Participant
personal and post-match draft/submission routes do not write audit records,
contradicting the approved questionnaire acceptance requirement.

## Required behavior

### Authoritative human deadline

- Every committed `speech.started` instance receives a deadline command whose
  idempotency identity is unique to that authoritative start, even when the
  logical turn resumes with the same Speech ID.
- Replaying the same callback for one start remains idempotent; a later valid
  restart is not deduplicated against an earlier start.
- A start with zero remaining milliseconds immediately reaches
  `SPEECH_FINALIZING`; it cannot remain in `HUMAN_SPEAKING / 0 ms`.
- Stale deadline callbacks from an earlier start, attempt or connection cannot
  finalize the current attempt. MatchActor remains the only timer authority.

### ASR attempt and recovery scope

- The automatic retry budget belongs to the current logical speaking turn:
  the allocated `opportunity_id` in free debate and the concrete action outside
  free debate. A failure in one free-debate opportunity cannot consume another
  opportunity's retry.
- The first ASR failure records the failed attempt immutably, clears its
  unfinished media/text, invalidates only the speculative next-side
  opportunity derived from it, refunds the full allowed turn duration, and
  returns the same allocated speaker to `HUMAN_READY_TO_START` with a new
  Speech ID on the next click.
- The second failure for that logical turn pauses under the existing
  `asr_task_failed` semantics. Recovery keeps the allocated human speaker but
  starts a new Speech attempt with full allowed duration; it never overwrites a
  failed Speech row or leaves its `asr_error_code` on a finalized row.
- Each retry has a new callback envelope attempt identity. Late results from a
  failed Speech, ASR segment, connection epoch or opportunity generation are
  persisted only where already required for diagnostics and never advance the
  match.
- This change does not switch ASR provider/model, invent a third automatic
  retry, transfer speaking rights, or weaken the existing second-failure pause.

### Participant survey audit

- Successful personal-survey and post-match-survey draft saves and final
  submissions write append-only audit rows in the same transaction as the
  corresponding answer mutation.
- Actions distinguish personal/post-match and draft/submission. Targets use the
  response/task stable ID; details may include questionnaire version and
  resulting status, but never answer content, Prompt text, credentials or raw
  provider payloads.
- Failed validation, forbidden access, stale/locked submissions and rolled-back
  writes produce no successful audit row.
- Existing administrator view, individual-view and export audits remain
  unchanged. Ordinary users still cannot access another user's answers or
  audit data.

## Required state matrix

| Scenario | Required result |
| --- | --- |
| First ASR failure in fixed speech | Failed attempt retained; full-time manual retry with new Speech ID |
| First ASR failure in free opportunity B after a failure in opportunity A | B still receives its own first automatic retry |
| Second ASR failure in one logical turn | Match pauses once; no speaker transfer or silent provider fallback |
| Resume after second failure | Same allocated human, new Speech attempt, full allowed time |
| Restarted Speech reaches its deadline | Exactly one new `speech.finalizing / TIME_LIMIT` event |
| Restart starts with 0 ms | Immediate authoritative finalization; never stable at `HUMAN_SPEAKING / 0 ms` |
| Old timer or ASR callback arrives after retry | No mutation of current state or current Speech |
| Successful survey draft save | Answer and matching redacted audit row commit together |
| Successful survey submit | Submission and matching redacted audit row commit together |
| Locked/invalid/foreign survey request | Request rejected and no success audit is added |

## Acceptance

1. A deterministic Actor regression reproduces the production sequence: one
   Speech reaches its deadline, enters ASR error recovery, restarts, and reaches
   a second deadline without manual early finish or duplicate suppression.
2. A zero-duration restart regression reaches `SPEECH_FINALIZING` without
   remaining indefinitely in `HUMAN_SPEAKING`.
3. PostgreSQL integration proves two free-debate opportunities have independent
   ASR retry budgets; every attempt uses a distinct Speech row, failed rows stay
   failed, and speculative opportunities from failed attempts are invalidated.
4. Callback regressions cover stale `speech_id`, `attempt_no`,
   `connection_epoch`, `context_version`, `opportunity_id` and
   `opportunity_generation` after recovery.
5. Participant survey route/integration tests prove draft and submit audit
   atomicity, redaction, target identity, authorization and no success audit on
   `survey_locked` or validation failure.
6. Core focused tests, the full Core/Jobs/Web suites, Ruff, Pyright/TypeScript,
   contracts, Storybook, browser tests, production build and diff checks pass.
7. Release preflight proves migration head, zero non-terminal matches, retained
   rollback, runtime mode 0755 and healthy dependencies before switch.
8. After deployment, brand-new authenticated accounts complete room creation,
   human seat occupation, device readiness, both theories, affirmative-first
   free debate, at least one Agent turn, at least one human ASR turn, natural
   `FINISHED`, strict post-match survey and personal survey. No manual
   zero-time rescue, operator-induced timeout, `match.error`, unexpected pause
   or missing participant audit is allowed in the accepted run.
9. Production evidence includes authoritative event sequences, Speech attempt
   rows, opportunity status, audit actions, health 200/200 and stable service
   restart counters. Synthetic media is labelled and does not count as a
   physical-microphone acceptance test.

## Rollback boundary

No migration is expected. Before release, retain the current Spec 229 source
and database rollback. Restore only the touched Core source and restart Core;
never rewrite existing Speech, opportunity, match, survey or audit history.
Any active QA match is recovered or terminated through MatchActor under the
standing authorization, never by direct database state mutation.

## Implementation evidence

- Human and Agent deadline commands now carry the authoritative `speech_id`
  and committed start sequence. The Actor ignores a callback whose identity no
  longer matches the current start; historical snapshot/command recovery stays
  compatible and requires no migration.
- ASR callback envelopes use the allocated free-debate opportunity, scope
  `attempt_no` and failure counts to that logical opportunity, and validate
  Speech, attempt, connection epoch, context version, opportunity and
  opportunity generation before mutation. Failed Speech rows are immutable to
  late ASR finalization.
- First ASR failure resets to the same allocated human with full turn time and
  a new Speech row. Second failure preserves the existing pause/recovery rule,
  invalidates the speculative opportunity and restarts with another distinct
  Speech row.
- Personal and post-match draft/submission mutations create redacted audit
  records in their answer transaction. Locked or invalid writes roll back
  without a success audit.
- Verification on 2026-08-25: Core `368 passed` (database-dependent tests
  excluded by the normal command), isolated PostgreSQL integration `5 passed`,
  Jobs `25 passed`, Web unit `191 passed`, Storybook `90 passed`, browser
  `236 passed`; Ruff, Pyright/TypeScript, contracts, production build and diff
  checks passed. The browser suite was rerun serially after an initial parallel
  build-lock collision and then passed completely.
- Deployment evidence: only the three Core implementation files were synced;
  rollback backup is `/opt/jixia-backup-before-spec230-20260825T1405Z`.
  Production preflight after restart passed at migration `0041`, zero
  non-terminal matches, health `200/200`, runtime mode `0755`, and service
  restart counters `0`.
- Online smoke evidence used a brand-new account. The flow reached both
  theories, entered affirmative-first free debate, completed an Agent turn,
  queued a human hand during Agent playback, and a human free Speech reached
  its authoritative 30-second deadline and retried after the intentionally
  synthetic ASR stream failed. This run was terminated under the standing QA
  authorization and is not counted as the physical-microphone/natural-
  `FINISHED` acceptance. The account's personal survey draft and submission
  completed with both participant audit rows. Physical microphone and a
  natural finished match remain an operational follow-up requiring a real
  device/session; no such evidence is claimed here.

## Extended online flow evidence

- A second production run used two brand-new authenticated accounts in formal
  4v4 match `461dc960-b3c6-47ee-8860-8b5f88615614`. It completed both theory
  speeches, affirmative-first free debate, alternating Agent turns, human-only
  waits on both sides, human priority, ASR retry/recovery and a natural
  `FINISHED / MATCH_FINISHED` at sequence 547. Both free-debate clocks reached
  zero; the match was not terminated or edited through SQL.
- Authoritative production rows retained failed human Speech attempts as
  `FAILED`, used a new Speech ID and incremented attempt number on retry, and
  allowed a recovered controlled-audio Speech to finalize before subsequent
  Agent selection. Repeated Agent deadlines advanced to new opportunities;
  no stale timer or ASR callback ended a later Speech.
- `HUMAN_WAIT_TIMEOUT`, `HUMAN_START_TIMEOUT` and the second ASR failure pauses
  observed during the run matched the approved recovery rules. They were
  resumed through the owner UI, preserved the allocated human, and did not
  transfer speaking rights. These expected safety pauses are not treated as
  state-machine defects.
- Normal completion created exactly one strict post-match task per human. Both
  tasks were completed through staged pre-speech judgment disclosure and
  submitted as `SUBMITTED` on version `postmatch-v2-strict-2026-08-24`. Both
  personal AI survey responses were also saved and submitted on the current
  published version.
- Production audit evidence contains both post-match and personal draft/submit
  actions for the two accounts. Audit details contain only status and version;
  no answers, Prompt text or provider payloads were copied into audit rows.
- Browser media used a controlled Chinese audio file. This proves the deployed
  browser/LiveKit/Core/ASR integration flow, but it is not a physical-
  microphone acceptance test; physical-device behavior remains explicitly
  unclaimed.
- Because this run intentionally exercised ASR failure recovery and included
  operator-missed human start windows, it does not satisfy acceptance item 8's
  stricter zero-`match.error` and zero-operator-timeout condition. It closes the
  natural-finish and questionnaire evidence gap, while a clean physical-device
  run remains outstanding.

## Clean production acceptance

- Two additional brand-new accounts registered through the public UI, created
  and joined formal 4v4 room `948778`, occupied opposite fourth-debater seats,
  completed live device checks, became ready and started production match
  `dd4ab26e-ed75-42df-b4d7-c66009889d83`.
- The match completed both theory speeches and entered affirmative-first free
  debate without pausing. Thirty-one Agent speeches alternated normally. When
  the last affirmative opportunity had 3.57 seconds remaining and all Agents
  skipped, the affirmative human raised a hand during the human-only window,
  started through the UI and completed a 3.3-second Speech with a 17-character
  ASR final. The match reached natural `FINISHED / MATCH_FINISHED` at sequence
  539 with both side clocks at zero.
- Authoritative database evidence contains one `match.finished`, zero
  `match.error`, zero `match.paused`, and 32 `FINALIZED` Speech rows with no
  failed Speech. No recovery, direct database mutation or operator timeout was
  used in this accepted run.
- Normal completion generated one strict post-match task for each human. Both
  users completed staged AI pre-speech judgments, saved drafts and submitted
  version `postmatch-v2-strict-2026-08-24`; both also saved and submitted the
  current personal AI survey version. Participant audit rows exist for all
  four draft/submit action types and expose only status/version detail keys.
- Both browsers used Chromium's deterministic fake microphone for the entry
  probe. The final human Speech used a page-injected controlled Chinese audio
  MediaStream and passed the real browser/LiveKit/Core/ASR path. This is a
  clean synthetic integration acceptance and still does not claim a physical
  microphone test.

## Production resource cleanup

- A second reference audit inspected systemd paths, mounts, symlinks, open
  files, PostgreSQL media references, Docker ownership and age/status of all
  remaining `.part` files. Six unreferenced failed Agent audio fragments older
  than 24 hours and belonging only to terminated matches were removed; two
  younger fragments remain inside the retention window.
- Unreferenced `/opt/apps` and `/opt/node_modules` from an obsolete Next build,
  plus 63 broken symlinks to a deleted temporary PostgreSQL source tree, were
  removed. Production Web uses `/opt/jixia-web`, Core/Jobs use
  `/opt/jixia-debate`, and no service or OpenResty configuration referenced the
  deleted paths. Unrelated Docker images, containers and volumes were retained.
- After cleanup all four services remained active, Core live/ready returned
  200/200, Core/Web/Jobs restart counters stayed zero, the runtime directory
  stayed mode 0755, and no non-terminal match remained after acceptance.
