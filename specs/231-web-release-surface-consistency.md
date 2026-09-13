# 231 Web release surface consistency

- Version: v2.1
- Status: Released to production
- Date: 2026-08-29
- Depends on: Specs 220, 221, 222, 230

## Problem

The production Web standalone was built without the current `/me` profile
navigation while the questionnaire route bundles were present. This made the
features reachable by direct URL but invisible from the profile page.

## Required behavior

- Every production Web build contains `/me/ai-experience` and
  `/me/postmatch-surveys` routes.
- The `/me` bundle contains links to both routes.
- A build fails before packaging when either condition is false.
- Deployment switches one standalone build atomically and never mixes an old
  standalone with newer source files.

## Verification

- Build-surface regression test covers missing links and required routes.
- Web TypeScript, ESLint and production build pass.
- After deployment, authenticated browser verification must open `/me` and
  both direct questionnaire URLs; unauthenticated requests must remain at the
  login boundary.

## Release evidence

- The first attempted switch was rolled back after the build used the
  development Core rewrite (`127.0.0.1:8000`) and produced 502 responses.
- The corrected build was produced with `CORE_API_ORIGIN=http://127.0.0.1:8100`
  and switched atomically on 2026-08-29. The prior Web directory remains at
  `/opt/jixia-web.before-survey-surface-20260829T072838Z`.
- Post-switch checks: `jx-web` active, `NRestarts=0`, Web directory `0755`,
  home and both questionnaire pages HTTP 200, Core live/ready HTTP 200, and
  unauthenticated questionnaire APIs HTTP 401.

## Rollback

Restore the previous Web standalone directory only. Do not alter questionnaire
tables, answers, or historical experiment APIs.
