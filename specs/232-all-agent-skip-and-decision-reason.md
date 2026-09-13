# 232 All-Agent skip fallback and decision reason

- Version: v2.1
- Status: Released to production
- Date: 2026-08-29
- Depends on: Specs 216, 217, 225, 228, 231

## Required behavior

- In formal free debate, if a side has no human participants and every Agent
  returns a valid `should_speak=false`, deterministically select one Agent so
  the opportunity cannot stall in human-only wait.
- Persist and expose this selection to administrators as
  `ALL_AGENT_SKIP_RANDOM`; it remains team-private in participant/observer
  projections except for the selected public speaker.
- Free-debate decision JSON contains exactly `should_speak` and a required
  `decision_reason` string of at most 20 characters.
- Invalid, missing or overlong reasons are rejected using the existing failed
  decision path; late results retain the reason for diagnostics without
  changing the authoritative allocation.

## Verification

- Domain regression covers all-Agent all-skip allocation and deterministic
  selection.
- Runtime parser regression covers required reason and length validation.
- Existing human-priority, failed-decision and stale-callback tests remain
  green.

## Release evidence

- Migration `0042_agent_decision_reason` applied on 2026-08-29.
- Core reached live/ready 200 after the final parser fix; `NRestarts=0`, no
  non-terminal matches remained, and all four services stayed active.
- Web was rebuilt with the production Core origin and atomically switched;
  home and both questionnaire pages returned HTTP 200. The previous Web build
  remains in its dated rollback directory.

## Rollback

Apply the source rollback and leave migration `0042` in place; the nullable
column is additive and historical decision rows remain readable.
