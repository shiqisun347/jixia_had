# 219 ASR LiveKit 真人身份解析

- Version: v2.0
- Status: Released to production
- Date: 2026-08-24
- Depends on: Specs 168, 215, 216

## Problem

The match LiveKit token uses the identity format
`jx-human-<match_id>-<user_id>-<connection_epoch>-<nonce>`, while the ASR
receiver only accepted the obsolete `user-<user_id>` format. Audio frames were
therefore discarded, and a fixed-stage human speech ended with
`asr_empty_audio`; after the retry budget was exhausted the match entered the
user-visible error pause.

## Required behavior

- Parse the current `jx-human-...` identity and route audio to the matching user.
- Preserve compatibility with the legacy `user-<uuid>` identity used by older
  rooms and tests.
- Reject malformed or unrelated identities without subscribing their audio to
  a speech session.
- Do not change ASR retry, pause, or callback identity semantics.

## Acceptance

- Current LiveKit human identities resolve to the embedded user UUID.
- Legacy identities continue to resolve.
- Malformed and agent/service identities resolve to no user.
- A regression test proves a received frame can reach the active ASR session.

## Rollback

Source-only rollback: restore `apps/core/src/jx_core/asr/runtime.py` and restart
Core. No migration is required.

## Verification

- Local: ASR tests 13 passed; Match domain/recovery tests 91 passed; Ruff and
  `git diff --check` passed.
- The only active production match was terminated through the privileged
  `MatchActor` command, moving from sequence 15 to `TERMINATED` at sequence 16.
- Rollback backup: `/opt/jixia-backup-before-0219-20260824T000000Z`.
- The deployed `asr/runtime.py` SHA-256 matched the local file:
  `a48e0246ca427acd1f09dbe9d530893159970365cdac90909c24eac4b1108750`.
- Core compiled and restarted successfully. Post-release preflight passed with
  migration `0040_formal_4v4_opportunities` and zero non-terminal matches.
- Ten 30-second post-release samples passed over five minutes; Core, Jobs, Web
  and LiveKit stayed active, Core live/ready returned 200, and `NRestarts` stayed
  at 0. No new Core errors appeared during the observation window.
