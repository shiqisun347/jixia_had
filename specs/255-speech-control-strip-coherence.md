# 255 speech control strip coherence

> Version: v2.1
> Status: released
> Date: 2026-09-06

## Scope

- Keep the live-debate Audio, Speech, and Match control groups on one aligned row at the
  supported 1280×720 desktop baseline in both Chinese and English.
- Replace the awkward abnormal-reset wording with a concise restart action while preserving
  the existing authoritative `speech.reset` command and confirmation flow.
- Add an English Storybook state that exercises the longest normal Speech control combination.

## Invariants

- This is a Web presentation change only. It does not change command permissions, MatchActor
  state, speech-reset semantics, timing, ASR/LLM/TTS cleanup, or persisted data.
- The reset action remains confirmation-gated and continues to discard the unfinished speech
  before restarting from the authoritative safe boundary.
- Redundant group labels may be removed, but button names and accessible labels remain visible
  and localized.

## Acceptance

- At 1280×720 in English, `Finish early` and `Restart speech` remain on one row and the Audio,
  Speech, and Match groups have equal height.
- English UI no longer contains `Reset abnormal speech`; Chinese UI no longer uses `异常重置`.
- Audio, Speech, and Match actions remain visually grouped without redundant labels competing
  for horizontal space.
- Targeted Web tests, TypeScript, ESLint, production build, and browser screenshots pass.

## Rollback

- Revert the Web message, control-strip layout, and Storybook changes only. No Core,
  database, migration, or contract rollback is required.

## 2026-09-06 implementation evidence

- Replaced `Reset abnormal speech` / `异常重置` with `Restart speech` / `重新开始发言` and
  aligned the confirmation copy without changing the `speech.reset` command.
- Removed the redundant Audio/Speech/Match micro-labels and compacted only the two simultaneous
  Speech buttons. Browser measurements at 1280×720, 1440×900, and 1920×1080 show all three
  control groups at 46px and the Speech buttons on the same row.
- TypeScript, targeted ESLint, 40 focused Vitest tests, the new English Storybook interaction,
  and the repository production Web build passed. The full Web unit suite has two pre-existing
  `use-match-command` copy-expectation failures; 205 tests passed.
- Production preflight found one RUNNING and one PAUSED match. No production files or services
  were changed; release must wait until the non-terminal match count is zero.
- A follow-up release attempt after explicit user confirmation found the RUNNING match finished,
  but one PAUSED match still remained. The release gate stopped again without changing production.

## 2026-09-06 release evidence

- Final production preflight passed with `active_matches=0`; no Core, Jobs, LiveKit, database, or
  migration change was required.
- Atomically released the complete Web standalone from the verified local build. Local and remote
  SHA-256 hashes match for `server.js` and `BUILD_ID`; rollback is retained at
  `/opt/jixia-web-before-255-20260906182055`.
- Public `/debate` rendered the expected unauthenticated login boundary. All observed HTML, JS,
  CSS, image, and RSC requests succeeded; the only 401 was the expected `/api/auth/me` boundary.
  Production chunks contain `Restart speech` and contain no `Reset abnormal speech`.
- The five-minute observation passed 11/11 samples: Core live/ready and Web `/debate` returned 200,
  `jx-web` remained active with zero restarts, and no warning-or-higher Web logs appeared after the
  new process started.
