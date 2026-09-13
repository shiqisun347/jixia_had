# 221 Public home navigation scope

- Version: v2.0
- Status: Released to production
- Date: 2026-08-24
- Depends on: Specs 201, 206

## Problem

The public home page and global user header exposed `实验安排` to anonymous
visitors and ordinary users. The route is a protected participant workspace,
not a general public entry point, so its schedule data appeared unexpectedly
in the product's primary navigation.

## Required behavior

- Public navigation contains only 首页、比赛大厅、排行榜、使用指南.
- `/experiments` remains available as a protected route for a participant who
  has a direct link or enters through an account-specific page.
- No experiment appointment or schedule data is fetched by the public home.
- Admin navigation and experiment workspace routes are unchanged.

## Acceptance

- Anonymous home browser snapshot has no `实验安排` navigation link.
- Home, lobby, leaderboard and guide navigation still render and remain
  responsive at desktop and narrow viewports.
- Protected `/experiments` continues to render its login boundary.

## Rollback

Source-only rollback of the public navigation list; no database or migration
change.

## Verification and release evidence

- Web unit coverage: 29 focused home/debate tests passed; the home-only slice
  passed 13 tests.
- Browser coverage: the home and narrow-header scenarios passed at all four
  configured viewports (8/8), and the production build generated 37 routes.
- Production preflight passed before and after the Web-only release at
  migration `0041_ai_debate_questionnaires` with zero non-terminal matches.
- Rollback directory: `/opt/jixia-web-before-0221-20260824T094340`.
- An isolated production browser confirmed the anonymous home has four public
  navigation links and no 实验安排 item. Direct `/experiments` still reaches the
  protected login boundary.
- Ten 30-second stability samples kept Core, Jobs, Web and LiveKit active with
  live/ready checks passing and restart counters `0/0/0/0`.
