# 224 Agent Prompt side rendering

- Version: v2.0
- Status: Completed and released
- Approved by: user request to fix and release on 2026-08-24
- Date: 2026-08-24
- Depends on: Specs 213, 216, 223

## Problem

Fixed-stage `agent.preparing` events do not include the action side. The Agent
runtime therefore falls back to the rule snapshot, but that fallback currently
returns the Chinese display labels `正方` or `反方`. The downstream Prompt
renderer compares the value with the canonical code `AFFIRMATIVE`, so both
display labels take the negative branch. As a result, a fixed affirmative Agent
speech receives `POSITION=反方` and `STANCE=NEGATIVE_STANCE`; the incorrect
speech then contaminates later free-debate history and decisions.

Free-debate allocation already carries canonical side codes and must remain
unchanged.

## Scope

- Include canonical `side` and `seat_no` in fixed-stage `agent.preparing`
  events.
- Make the rule-snapshot fallback return only `AFFIRMATIVE` or `NEGATIVE`.
- Reject a missing or invalid effective side instead of silently selecting the
  negative branch.
- Add focused domain/runtime regressions for both sides.
- Do not change Prompt wording, room assignment, timing, selection, media,
  database schema, Web behavior, or contracts.

## Acceptance

1. An affirmative fixed Agent action renders `POSITION=正方` and
   `STANCE=AFFIRMATIVE_STANCE`.
2. A negative fixed Agent action renders `POSITION=反方` and
   `STANCE=NEGATIVE_STANCE`.
3. Fixed-stage `agent.preparing` carries the same canonical side and seat as the
   authoritative action.
4. Free-debate Agent selection and decision tests remain green.
5. Core tests, Ruff, Pyright and `git diff --check` pass.
6. Production release requires zero non-terminal matches, a protected Core
   backup, Core health/readiness checks and stable restart counters.

## Rollback

Restore the previous Core source and restart `jx-core`. There is no migration,
persisted-state rewrite, Web artifact, or external contract change. Existing
matches are not modified directly.

## Release evidence

- Focused Agent/runtime and MatchActor tests passed 114/114; the complete Core
  non-integration suite passed 349 tests with 33 integration tests deselected.
  Ruff, Pyright and `git diff --check` passed.
- The one non-terminal production match
  `705eaedf-b55f-4fe2-81ac-cd6854ae5f2a` was terminated through an isolated
  privileged MatchActor after Core was stopped. It reached `TERMINATED` at
  sequence 15; no direct database state update was used.
- Core-only rollback backup:
  `/opt/jixia-backup-before-0224-20260824T162549`.
- Production hashes match the tested local files: `agent/runtime.py`
  `a92184c568864391a066c7f59de5072cf13509b8e1cc7f5fda16e0d067e371ab` and
  `matches/domain.py`
  `589d7fc5b96a925870e9170394a35038f152990228619fd2bc696a5f64a74bee`.
- Post-release preflight passed at migration `0041_ai_debate_questionnaires`
  with zero non-terminal matches. A production import-level regression proved
  both Prompt side mappings, and six ten-second samples kept all four services
  active, Core ready at 200 and Core `NRestarts=0`.
