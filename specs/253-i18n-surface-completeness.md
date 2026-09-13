# 253 i18n surface completeness

> Version: v2.1
> Status: implementing
> Date: 2026-09-05

## Scope

- Complete Chinese/English localization for all Web-owned visible UI, beginning with the live
  debate surface.
- Preserve server-originated topics, rules, prompts, transcripts, Agent output, audit records,
  diagnostics and error payloads exactly as received. Standard formal-4v4 stage names are the
  explicit display-localization exception.

## Invariants

- Locale changes remain client-side presentation only; no Core API, WebSocket, LiveKit, timer,
  authorization, room or match state change.
- Stable API codes are mapped to local UI copy. Raw server messages are not machine translated.
- Static UI text must use typed message keys; fixtures and tests may retain source-language data.

## Acceptance

- In `en`, the live debate page's state labels, controls, transcript shell, device/audio controls,
  runtime recovery, network dialog and local errors are English.
- In `zh-CN`, those same UI strings are Chinese.
- English mode preserves raw topic, participant names, rule names and speech text, while
  standard formal-4v4 stage names use approved English debate terminology.
- Inventory checks distinguish allowed business-data paths from UI literals; targeted unit tests,
  typecheck, lint and browser snapshots pass.

## 2026-09-05 implementation evidence

- Completed the live-debate priority slice: match status, team panels, speaking controls,
  transcript and drawer, audio/device controls, network dialog, local recovery messages,
  confirmations, entry states and post-match survey prompts use typed `Debate`/`Match` messages.
- Locale changes do not recreate the WebSocket or LiveKit audio session. The connection effects
  use stable translation references; language changes remain presentation-only.
- Preserved topic, rule/stage names, participant names, transcript text, and server-provided
  recovery reasons exactly as received.
- Verified with TypeScript, targeted ESLint, 39 related Vitest tests, `git diff --check`, and
  the repository production Web build script. Browser snapshot verification remains required
  before declaring the entire cross-site specification complete.

## 2026-09-05 release evidence

- Five-human in-memory MatchActor simulation completed 10 rounds: 50 speeches completed, 40
  stale callbacks rejected, malformed UUID callbacks rejected, and internal timer failure
  converged to recovery. This does not claim five physical devices, LiveKit media or provider
  ASR capacity verification.
- Released only the complete Web standalone. Core, Jobs, LiveKit and the database were not
  changed. The previous Web directory remains at `/opt/jixia-web-before-253-20260905`.
- Production verification: `jx-web` active with zero restarts; public `/` and `/debate`, and
  Core live/ready, all returned HTTP 200. No new jx-web error-level log was observed after the
  release start time.

## 2026-09-05 standard stage terminology

- English live-debate display now uses approved terminology for the standard formal-4v4 stages,
  including `正方一辩立论` → `First Affirmative Constructive` and `自由辩论` → `Free Debate`.
- The same mapping is used by the current-stage label, transcript entries, and copied transcript
  text. It is presentation-only: stored stage names, host copy, prompts and match state remain
  unchanged. Unrecognized custom stage names remain in their original language.
- Verification: TypeScript, targeted ESLint, 41 debate tests, `git diff --check`, and the
  production Web build passed. The complete Web standalone was released; the prior directory is
  retained at `/opt/jixia-web-before-253e-20260905`. Public `/debate` returned 200, `jx-web`
  remained active with zero restarts, and no post-release error log was observed.

## Rollback

- Revert Web message/component changes only. No server data or migration is involved.
