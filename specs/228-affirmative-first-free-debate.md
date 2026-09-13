# 228 Affirmative-first free debate

- Version: v2.0
- Status: Released
- Approved by: user request on 2026-08-24
- Requested by: user request on 2026-08-24
- Date: 2026-08-24
- Depends on: Specs 201, 213, 216, 223, 227

## Conflict to resolve

Spec 201 and the current paper-experiment rule explicitly require the negative
side to receive the first free-debate opportunity. The requested behavior
replaces that requirement: newly published formal 4v4 rules, including the
paper-experiment rule, must start free debate with the affirmative side.

Approval of this specification supersedes only the `starting_side=NEGATIVE`
requirement in Spec 201 sections 5 and 22. It does not change the order or
duration of fixed speeches, human priority, Agent decision timing, alternating
turns, or any existing room or match snapshot.

## Scope

- Change the default free-debate `starting_side` for newly created formal 4v4
  rule versions from `NEGATIVE` to `AFFIRMATIVE`.
- Publish a new paper-experiment rule version whose free-debate stage starts
  with `AFFIRMATIVE`; archive the prior enabled version only after the new
  version and all owned resources are ready.
- Update paper-rule creation, normalization and restoration checks so they
  require the affirmative side for the first free-debate opportunity.
- Update rule-workspace defaults and tests to display and save the affirmative
  starting side.
- Preserve rule snapshots already attached to rooms and matches. Those matches
  continue to use their frozen starting side.
- Keep the free-debate decision and speech Prompts byte-for-byte unchanged.

## Non-goals

- No migration or rewrite of historical rule versions, rooms, matches,
  opportunities, decisions, speeches or survey data.
- No change to fixed-speech order, timers, the three-second competition window,
  human priority, Agent selection, human-only waiting, pause/recovery semantics,
  WebSocket privacy projection or provider configuration.
- No production release until local gates and a deterministic complete-match
  simulation pass.

## Acceptance

1. A newly created formal 4v4 rule uses `starting_side=AFFIRMATIVE`.
2. The paper-experiment rule builder, ensure script and restore script all
   create or validate `starting_side=AFFIRMATIVE`.
3. Entering free debate opens the first competition opportunity for the
   affirmative side; after that speech finishes, the next opportunity belongs
   to the negative side and alternation continues normally.
4. Existing snapshots with `starting_side=NEGATIVE` retain their original
   behavior and remain readable and recoverable.
5. Decision and speech Prompt hashes are unchanged.
6. A deterministic full MatchActor flow completes both theory speeches,
   affirmative first free-debate selection and speech, negative reply, and
   normal `FINISHED / MATCH_FINISHED` termination.
7. Focused Core/Web tests, repository tests, lint, type checks, contracts,
   browser checks, production build and `git diff --check` pass.
8. Production release uses a zero-active-match preflight, protected rollback
   backup, resource-ready paper-rule version switch, browser smoke test and a
   five-minute no-restart stability observation.

## Rollback

Re-enable the previous paper-experiment rule version and restore the previous
application artifacts. Rooms and matches created under the affirmative-first
version retain their frozen snapshots and are not rewritten. No database
migration rollback is required.

## Implementation

- The paper-experiment rule description, host announcement and frozen
  `starting_side` now consistently say and use `AFFIRMATIVE`.
- The admin rule directory creates new formal 4v4 drafts with an affirmative
  free-debate starting side. The existing rule workspace continues to display
  and preserve either valid side from the selected version.
- The paper-rule ensure and experiment-preparation checks consume the canonical
  draft, so a persisted negative-first paper rule no longer matches and causes
  creation of a new version. The restore utility intentionally preserves the
  side stored in each backup because it restores historical versions rather
  than normalizing them.
- MatchActor and its compiler were already snapshot-driven and defaulted to
  `AFFIRMATIVE`; no state-machine production code needed to change. An explicit
  negative starting side remains supported for frozen historical snapshots.
- The free-debate decision Prompt SHA-256 remains
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`;
  the speech Prompt SHA-256 remains
  `be38ff2874939758cf9397ca4f37bf96391c790e3cd65713bcb2ee80e6f7ba92`.

## Verification

- Focused Core regressions: 8 passed. They cover the paper-rule draft and host
  copy, affirmative initial experiment opportunity, affirmative first Agent
  turn, negative reply, normal `FINISHED / MATCH_FINISHED` completion, and
  explicit negative-first legacy snapshot compilation.
- Repository tests: tooling 47, QA 6, Web 191, Core 366 and Jobs 25 passed;
  33 Core and 2 Jobs integration tests were deselected by the standard
  non-integration command.
- Web rule tests: 5 passed. The create payload explicitly asserts
  `starting_side=AFFIRMATIVE`.
- Full browser suite: 236 passed. Storybook: 90 passed. Production Web build,
  ESLint, TypeScript, project Pyright, Ruff, OpenAPI contracts, Prettier and
  `git diff --check` passed.
- The first browser invocation overlapped the production build and was rejected
  by the repository's concurrent-build guard. It was rerun sequentially and
  completed 236/236; no product test failed.
- No independent `TEST_DATABASE_URL` was supplied, so PostgreSQL integration
  tests were not run.
- Production preflight passed at migration 0041 with zero active matches. The
  root-only rollback backup is
  `/opt/jixia-backup-before-0228-20260824T132000Z`; the immediate pre-switch Web
  rollback is `/opt/jixia-web-before-0228-switch-20260824T141443Z`.
- The production source tree was missing three already released Spec 220 survey
  routes and the corresponding `surveyApi` client export. The first rebuilt
  artifact therefore exposed only 34 routes and was rejected before switch;
  the next build failed its import check. The missing approved sources were
  synchronized, and the accepted Linux build passed TypeScript and exposed all
  37 routes, including admin surveys and both personal survey pages. Existing
  Web remained available except during explicit zero-match maintenance windows.
- Paper-experiment rule v5 is the sole enabled version with
  `starting_side=AFFIRMATIVE`, all three host assets `READY`, and its judge
  enabled. Versions 1-4 are archived; no existing room or match snapshot was
  rewritten.
- The deployed MatchActor simulation completed both theory speeches,
  affirmative first free-debate selection and speech, negative reply, and
  `FINISHED / MATCH_FINISHED` at sequence 35. One initial in-memory attempt hit
  a transient playback state conflict; a minimal command trace and the complete
  rerun both passed against the same deployed module hash, with no database
  match created and no service impact.
- Public browser smoke confirmed the approved navigation, no horizontal
  overflow at 1280 px, and correct login redirects for rule, survey and personal
  questionnaire routes. Final preflight passed with zero active matches. Ten
  30-second samples kept all four services active, live/ready at 200 and restart
  counters at zero; there were no warning-or-higher logs after the final start.
