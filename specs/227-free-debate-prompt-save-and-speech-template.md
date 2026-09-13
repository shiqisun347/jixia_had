# 227 Free-debate Prompt save and speech template

- Version: v2.0
- Status: Released and production rule normalized
- Approved by: user request on 2026-08-24
- Date: 2026-08-24
- Depends on: Specs 213, 216, 224, 225

## Problem

The rule workspace rejects the newly confirmed free-debate speech Prompt with
`prompt_missing_variables`. The template intentionally omits
`SIDE_REMAINING_MS` and `OPPONENT_REMAINING_MS`, while Core currently derives
the required speech variables from the decision-variable set and therefore
requires both. The runtime already renders only variables actually present in
the template, so this is a save-time constraint mismatch rather than a missing
runtime value.

## Scope

- Keep the confirmed free-debate decision Prompt unchanged.
- Replace the paper-experiment free-debate speech Prompt with the exact text
  supplied in the approving request, excluding the Markdown fence.
- Use the same speech wording as the default for newly created formal 4v4 free-
  debate stages.
- Require role variables, `MAX_SPEECH_SECONDS`, `TARGET_CHAR_COUNT` and
  `DEBATE_HISTORY` for free-debate speech templates. Continue to recognize
  `SIDE_REMAINING_MS` and `OPPONENT_REMAINING_MS` as optional variables.
- Keep rule-stage saves transactional, audited and revisioned. Existing room
  and match snapshots remain immutable; a saved rule change affects only rooms
  created afterward.

## Non-goals

- No change to decision semantics, JSON output contract, Agent selection,
  timing, human priority, model parameters, personalization storage, database
  schema, room snapshots already created, or legacy experiment fallback text.
- No production update or rule-resource ensure operation without a separate
  release request.

## Acceptance

1. The exact approved speech template passes Core validation without either
   remaining-time variable and still rejects a missing role, history, duration
   or target-character variable.
2. Remaining-time variables remain valid when an administrator chooses to use
   them in a speech template.
3. The paper rule and ordinary formal-4v4 default contain the exact approved
   speech wording; the decision Prompt remains byte-for-byte unchanged.
4. The rule workspace can switch between both free-debate Prompt slots and
   sends a save to the selected `SPEECH` or `DECISION` endpoint.
5. Prompt/rule and rule-workspace tests, Ruff, ESLint, TypeScript/Pyright,
   contracts, focused browser verification, build and `git diff --check` pass.

## Rollback

Restore the previous free-speech required-variable set and speech constants.
No migration or persisted snapshot rewrite is required. A rule already saved
with the relaxed variable set remains valid because the runtime renders its
declared variables from the frozen snapshot.

## Implementation

- `FREE_SPEECH_VARIABLES` no longer inherits the decision-only remaining-time
  requirements. It requires role variables, maximum seconds, target character
  count and complete debate history; both remaining-time variables stay on the
  global allowlist and can still be used optionally.
- The ordinary formal-4v4 default and paper-experiment speech slot now share
  one exact constant containing the user-confirmed text. This prevents the two
  rule creation paths from drifting.
- The free-debate decision Prompt was not edited. Its SHA-256 before and after
  implementation is
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`.
- Rule-workspace unit and browser regressions select and save the `SPEECH` and
  `DECISION` slots independently.

## Verification

- Prompt and paper-rule regressions: 21 passed.
- Repository tests: tooling 47 passed, QA 6 passed, Web 191 passed, Core 366
  passed with 33 integration tests deselected, and Jobs 25 passed.
- Rule-workspace Prompt save browser regression: 4 passed across all configured
  viewports; complete browser suite: 236 passed.
- Storybook: 90 passed. Ruff, ESLint, TypeScript/Pyright, OpenAPI contracts,
  production build, Prettier and `git diff --check` passed.
- This environment has no independent `TEST_DATABASE_URL` or Docker, so the
  PostgreSQL save transaction was not exercised locally. The validator,
  endpoint selection and request payload are covered separately.
- Production release created and enabled paper-experiment rule v4 after all
  three host-audio assets reached `READY`; versions 1-3 are archived and no
  existing room or match snapshot was rewritten. The persisted decision Prompt
  SHA-256 remains
  `4ac43ed2e3d6a9daf8cf8e1a288e778f231cb7971c10441c5a6fafb424ae0a3e`.
- Release rollback backup is
  `/opt/jixia-backup-before-0226-0227-20260824T101605Z`. The final preflight,
  browser checks and ten-sample stability observation passed with zero active
  matches, zero service restarts and no new warning-or-higher logs.
