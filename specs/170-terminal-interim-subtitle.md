# 170 Terminal interim subtitle cleanup

- Version: v1.0
- Status: Released and verified in production
- Approved by: user request to systematically test, fix and release the 4v4 product
- Date: 2026-08-21

## Problem

A production 4v4 all-Agent match finished normally with authoritative sequence
449, but its final snapshot still contained 89 characters of `interim_text`.
The domain clears transient subtitles while completing an action, so the
remaining text proves that a late subtitle callback can write into the Actor
after the match has reached `FINISHED/MATCH_FINISHED`.

Transient text belongs only to the current speech. It must never survive into
a terminal state, a different action, or a recovery state.

## Scope

- Restrict Actor interim-text updates to active human or Agent speech states.
- Ignore an update that waited behind an Actor commit when the committed state
  no longer accepts transient speech text.
- Preserve current in-flight transaction serialization and normal live subtitle
  updates.
- Cover the behavior in MatchActor tests and the 4v4 state matrix.
- Do not change transcript records, final speech text, database schema, media
  protocol, authentication, or timing semantics.

## Acceptance

- A subtitle waiting behind the commit that finishes a match cannot repopulate
  `interim_text` after `FINISHED/MATCH_FINISHED`.
- `TERMINATED`, `PAUSED`, `ERROR`, `SYSTEM_RECOVERY`, host, preparation and
  selection states reject transient subtitle writes.
- Active `HUMAN_SPEAKING`, `SPEECH_FINALIZING`, `AGENT_SPEAKING` and
  `AGENT_FINALIZING` states continue to accept valid interim text.
- Core focused and full non-integration tests, Ruff and Pyright pass. Full
  workspace gates run before production release.
- Production release occurs only with zero non-terminal matches, verifies the
  actual imported Core module and hash, and observes services and logs for at
  least five minutes.

## Rollback

The change is Core-only and has no migration. Rollback restores the previous
`matches/domain.py` from the release backup and restarts only Core after the
same zero-active-match gate. Existing finished match snapshots are not
rewritten; new snapshots stop accepting terminal interim updates after release.

## Verification

- Production 4v4 reproduction: match finished at sequence 449 while the final
  snapshot retained 89 characters of transient text.
- Focused Actor coverage: 6 passed. Focused manager coverage: 2 passed.
- Core non-integration suite: 197 passed, with 26 PostgreSQL integration tests
  deselected because PostgreSQL and Docker are unavailable locally.
- Workspace gates: tooling 47, QA 5, Web 166, Jobs 19, Storybook 82,
  Playwright 200/200, Ruff, Pyright, lint, typecheck, contracts and the 27-page
  production build passed.
- Released Core-only with zero non-terminal matches. Rollback point:
  `/opt/jixia-backup-before-170-20260821142353`.
- Production imports resolved to `/opt/jixia-debate/apps/core/src`; deployed
  `domain.py` SHA-256 was
  `011de4bab34efb53013f0ce79e2178974bcb853022a31ff821c4056d93255634`
  and `service.py` was
  `3bf7313812db7cbb5f9ceeece38ecaf91a14f2744bd270c027c541e069aae394`.
- Post-release observation exceeded five minutes: four services active, Core
  and Web `NRestarts=0`, Core live/ready and Web returned 200, Core/Jobs had no
  warning-or-higher entries in the observation window, and active matches
  remained zero.
