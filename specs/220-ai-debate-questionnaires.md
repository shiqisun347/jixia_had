# 220 AI debate questionnaires

- Version: v2.0
- Status: Released to production
- Date: 2026-08-24
- Depends on: Specs 201, 209, 213

## Scope

Add a rule-controlled post-match questionnaire for `HUMAN_SELF` and `TEAM_AI`
speech evaluation, plus a versioned long-term AI debate experience survey on
the participant profile. Historical experiment Q1-Q5 data and APIs remain
unchanged.

## Invariants

- The rule setting is frozen into the room snapshot and cannot change an
  already-created match.
- Only `FINISHED` matches with the frozen setting enabled create post-match
  tasks; terminated matches never create them.
- Answers are account-scoped, versioned, idempotent; each submitted personal
  response is copied to an immutable revision before a later edit.
- Admin-only individual views and CSV/JSON exports include identity and are
  audited; participants can access only their own responses.

## Rollback

Source and migration are additive. Disable the new routes/UI and roll back the
new migration only before any production questionnaire data is created; after
data exists, retain tables and disable creation through the rule setting.

## Production release evidence (2026-08-24)

- The only non-terminal production match was an `ERROR` match at sequence 22;
  it was terminated through the privileged MatchActor endpoint and reached
  `TERMINATED` at sequence 23. The temporary admin session used for this action
  was revoked immediately.
- Backup: `/opt/jixia-backup-before-0220-20260824T022347Z`.
- Migration `0041_ai_debate_questionnaires` upgraded successfully. Core, Jobs,
  Web and LiveKit remained active; Core live/ready returned 200 and the public
  home and personal survey page returned 200. Unauthenticated admin survey API
  access returned 401.
- Key Core, migration and Web hashes matched the local checkout. Ten 30-second
  post-release observations completed with live/ready 200 and restart counters
  `0/0/0/0`.
