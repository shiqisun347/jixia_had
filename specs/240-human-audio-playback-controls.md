# Spec 240: Human Audio Playback Controls

- Version: v2.0
- Status: Released to production
- Date: 2026-08-30
- Depends on: Specs 219, 223

## Problem

LiveKit already delivers the current human speaker's microphone track to other
participants and viewers. The match page only offers a global output mute,
which also silences Agent speech and host audio. Participants sharing a physical
room need to mute one or more nearby human speakers locally without changing the
published microphone track, Core ASR, or anyone else's playback.

## Required behavior

- Keep the existing global output mute.
- Add local controls to mute all remote human speakers or selected remote human
  speakers by name, side, and seat.
- Classify Core-minted `jx-human-<match_id>-<user_id>-...` identities against
  the room's known human seats; treat `jx-agent-<match_id>-...` separately.
- Human mute never silences Agent or host audio. Global mute continues to mute
  every output source.
- New and reconnected tracks inherit the selected user's mute state.
- Store the muted user ids in `sessionStorage` under the current match only.
- Show the controls to debaters, viewers, and administrators who can subscribe
  to match audio.
- Do not change LiveKit publishing, ASR subscriptions, MatchActor state,
  database state, or API contracts.

## Acceptance

- A second browser can hear a human speech track by default.
- Muting one human affects only that user's remote tracks in the local browser.
- Agent and host audio remain audible while human mute is active.
- Global mute overrides all source-specific playback settings.
- Refreshing the same match restores human mute choices; another match starts
  unmuted.
- Desktop and mobile controls remain accessible without horizontal overflow.

## Rollback

Source-only Web rollback. Restore the previous match audio subscription and
footer controls, rebuild the repository Web target, and restart Web. No Core,
database, migration, or contract rollback is required.

## Verification

- Audio classification, mute policy, reconnect identity, and match-scoped
  persistence unit tests passed (22 targeted tests including existing live
  match regressions).
- The interactive debate Story passed all 20 scenarios, including individual,
  partial, all-human, and restore controls.
- Match Playwright passed 12/12 across 1280, 1440, 1672, and 1920 widths. A
  separate 390px browser inspection reported `scrollWidth === innerWidth` and
  kept the audio menu within the viewport.
- Web TypeScript, ESLint, and the repository production build passed.
- Full Web unit tests passed 196/198; the two failures are pre-existing stale
  homepage-copy assertions unrelated to match audio.
- A physical two-human LiveKit microphone test remains required before claiming
  real-room acoustic acceptance.
- Production release used Web build `IAAoYiEL2x-YLldsl3aE8`. The complete prior
  standalone is retained at
  `/opt/jixia-web-before-spec240-20260830185617`, with affected source files at
  `/opt/jixia-source-before-spec240-20260830185617`.
- Production preflight and postflight passed at migration
  `0042_agent_decision_reason` with zero non-terminal matches. Eleven 30-second
  samples over five minutes kept all four services active, Core live/ready and
  the public Web at 200, `jx-web` restarts at zero, and warning logs empty after
  the new process started.
