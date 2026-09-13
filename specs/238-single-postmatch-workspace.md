# Spec 238: Single Post-Match Survey Workspace

Status: released; Next.js 16 dynamic-route regression fixed 2026-08-30

## Scope

- `/me/postmatch-surveys` is a task list; each task opens at `/me/postmatch-surveys/[taskId]`.
- The single-task view uses a transcript timeline and a right-side question panel on desktop, with a single-column mobile layout.
- Completed speech annotations can be reopened and edited. The Core preserves unlocked transcript visibility and records the previous submitted snapshot in the audit event when a submitted task is reopened.
- Personal AI experience surveys remain independent.

## Verification

- Core non-integration tests: 371 passed.
- Web typecheck, lint, OpenAPI contract check, and production build passed.
- Dedicated post-match list test passed.

## Rollback

Revert the Web standalone build and the Core service revision together; no database migration was added.
