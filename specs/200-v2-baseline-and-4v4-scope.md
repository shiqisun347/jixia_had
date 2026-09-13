# 200 v2.0 baseline and 4v4 product scope

- Version: v2.0
- Status: Released and verified in production
- Approved by: user instruction to treat the current release as 2.0, keep only 4v4, and reorganize code and documentation
- Date: 2026-08-21

## Goals

- Establish `2.0.0` as the current application/package/API version.
- Make formal 4v4 the only product-supported rule size for newly created
  rooms, while preserving generic runtime code and read access for historical
  matches created under older rules.
- Replace stale v1.0 current-state documentation with one concise v2.0 source
  map and evidence record.
- Remove only proven redundancy: duplicated Python API version literals and
  repeated/stale current-state claims. Do not perform speculative domain or
  protocol refactors.

## Product Boundary

| Area | v2.0 behavior |
| --- | --- |
| Room creation UI | Lists enabled 4v4 rules only |
| Room creation API | Rejects a non-4v4 rule for a newly created room |
| Historical rooms/matches | Remain readable; no data rewrite or deletion |
| MatchActor/compiler | Remain generic because 4v4 stages, free debate and historical recovery share this engine |
| Admin rule management | Keeps versioned rule records; non-4v4 historical rules are not treated as current product options |

## Documentation

- Update `AGENTS.md`, `README.md`, PRD, TechDesign, `agent_docs/`, deployment,
  AI-start, current-system-reference, specs entry and `MEMORY.md` to v2.0.
- Keep specs 161-170 and `docs/v1.0-audit.md` as immutable historical evidence;
  label them superseded instead of rewriting their version metadata.
- Add a v2.0 structure audit that explains what was removed, retained and why.
- Do not edit or track `paper_docs/`, `api.md`, `.env`, production data or QA
  artifacts.

## Acceptance

- All package and runtime version surfaces report `2.0.0`.
- Web creation cannot submit a non-4v4 rule; Core independently rejects it.
- Existing generic MatchActor tests continue to pass, proving historical and
  internal rule execution was not deleted.
- Current docs contain no claim that v1.0 is the active version and no stale
  Core 191/Spec 169 release baseline.
- Full lint, typecheck, tests, contracts, Storybook, browser suite and production
  build pass. PostgreSQL integration remains explicitly reported if unavailable.

## Rollback

This slice has no migration and is not part of the already completed Spec 170
production release. Rollback restores the version files, room-creation filter
and validation, and current documentation. Historical data and v1.0 specs are
unchanged.

## Verification

- Runtime versions: Core package, Jobs package and FastAPI app all report
  `2.0.0`; `uv lock --check` passed.
- Focused 4v4 boundary: Core room tests 16 and Web tests 167 passed.
- Full workspace: tooling 47, QA 5, Web 167, Core 198, Jobs 19, Storybook 82
  and Playwright 200/200 passed.
- Ruff, Pyright, ESLint, TypeScript, contracts, Prettier, `git diff --check`
  and the 27-page production Web build passed.
- The first browser invocation overlapped the production build and stopped on
  Next's build lock before tests started; the isolated rerun passed 200/200.
- 26 Core and 2 Jobs PostgreSQL integration tests remain unexecuted because the
  local machine has no PostgreSQL/Docker daemon.
- Released with zero non-terminal matches. Runtime rollback point:
  `/opt/jixia-backup-before-v2-20260821144920`; source/document rollback point:
  `/opt/jixia-backup-before-v2-docs-20260821145415`.
- Production Core, Jobs and FastAPI report `2.0.0`; Core/Jobs import paths put
  `/opt/jixia-debate/apps/*/src` first. Deployed domain, service, room service
  and package-version hashes matched the locally verified files.
- More than five minutes after restart, Core, Jobs, Web and LiveKit were active;
  Core/Jobs/Web `NRestarts=0`; Core live/ready, local Web and public Web returned
  200; Core/Jobs warning-or-higher count was zero; active matches remained zero.
- Anonymous production browser checks covered the home and lobby/login route;
  browser errors were empty and the home Axe audit reported zero violations.
