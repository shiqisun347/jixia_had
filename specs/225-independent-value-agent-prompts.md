# 225 Independent-value Agent Prompts

- Version: v2.0
- Status: Withdrawn by user, not released
- Approved by: user request on 2026-08-24
- Date: 2026-08-24
- Depends on: Specs 213, 216, 224
- Supersedes: Spec 213 free-debate decision and speech Prompt wording only

## Problem

The current shared decision Prompt asks every Agent to consider the whole team
while emphasizing unknown human hand state. Agents can therefore defer to an
imagined teammate instead of judging their own contribution, making correlated
all-skip outcomes likely. Giving each Agent a different personality or strategy
Prompt would reduce experimental comparability and confound Agent identity with
the intervention.

The shared speech Prompt also lacks an explicit non-repetition criterion, so
different selected Agents can produce similar arguments from the same history.

## Scope

- Keep one identical decision Prompt and one identical speech Prompt for every
  Agent. Side, stance, seat, time and history remain factual runtime variables,
  not Agent-specific instructions.
- Ask each Agent to independently decide whether it can provide effective value
  not already present in the record. It must not infer or wait for human or
  other-Agent actions; Core remains responsible for human priority and final
  selection.
- Define positive decision criteria and make `false` appropriate only for clear
  repetition, stance deviation or absence of new value.
- Require a selected speaker to address the current priority and add at least
  one new argument, counterexample, qualification or question without pursuing
  novelty away from the live issue.
- Apply the same semantics to the paper rule templates, ordinary formal-4v4
  defaults and legacy experiment compatibility renderer.
- Bump the legacy experiment Prompt version. Do not mutate existing room or
  match snapshots; only newly created snapshots use the revised text.

## Non-goals

- No Agent personality, seat strategy, private coordination, willingness score,
  forced Agent fallback or change to the boolean JSON contract.
- No change to human priority, deadline, random tie-break, human-only wait,
  model, temperature, timing, media, database schema, Web UI or questionnaire.

## Acceptance

1. Every decision Prompt says to judge independently, ignore unknown teammate
   actions and use the same explicit positive/negative criteria.
2. Every speech Prompt requires a current-issue response and at least one
   non-repeated contribution while preserving the assigned stance.
3. Paper-rule, ordinary default and legacy experiment templates pass the same
   text-contract tests and retain all required variables/output contracts.
4. Existing frozen snapshots remain unchanged; stale paper-rule Prompt text is
   detected as a new rule version by the existing ensure workflow.
5. Prompt/rule tests, Core non-integration tests, Ruff, Pyright and
   `git diff --check` pass.

## Rollback

Restore the previous Prompt constants and experiment Prompt version. No
migration or persisted-answer rewrite is required. Rules/rooms already created
with either Prompt version remain immutable historical snapshots.

## Verification

- The proposed Prompt and test changes were reverted on 2026-08-24 at the
  user's request.
- The Spec 213 approved Prompt text and legacy experiment Prompt version were
  restored.
- No production release was performed. Existing frozen rule, room and match
  snapshots were never changed by this proposal.
