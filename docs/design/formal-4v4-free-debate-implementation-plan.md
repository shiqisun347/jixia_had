# Formal 4v4 free-debate implementation plan

This plan implements Specs 215 and 216 without introducing a second match
state machine or a new service.

## Slice A: decision contract and opportunity authority

**Core modules**

- `matches/domain.py`: make formal 4v4 opportunity transitions independent of
  `experiment_mode`; remove formal fallback and willingness ordering; add
  seed-derived equal-probability selection; clear competition state on speech
  start; preserve selection-window deadlines across pause.
- `agent/runtime.py`: select the boolean parser for formal 4v4; retain the
  legacy parser only for historical read-only snapshots; persist attempt and
  opportunity identity on every decision request.
- `matches/service.py`: classify late/stale decisions without Actor mutation;
  persist request/response timing and opportunity IDs. Migration 0040 makes
  `free_debate_opportunities.experiment_attempt_id` nullable so formal matches
  use the same opportunity/audit tables without a fabricated experiment row.

**Evidence**

- Domain tests prove human priority, all-true deterministic selection, all
  false/failed human-only wait, deadline rejection, and state clearing.
- Runtime tests feed `{"should_speak": false}` and assert no invalid result.

## Slice B: privacy projection and controls

**Core modules**

- `matches/routes.py` and schemas: derive public, candidate-side, and admin
  projections before JSON serialization. Admin receives both sides; opponent
  and audience receive no candidate IDs, ranks, statuses, or private countdown.
- contracts generation: update OpenAPI and generated TypeScript types.

**Web modules**

- `use-match-runtime.ts`: consume server events, clear live subtitle when the
  authoritative speech ID changes, and smooth only the server deadline.
- `debate-page-layout.tsx` and styles: render per-seat status, bottom human
  controls, 0.1-second competition countdown, integer human-only countdown,
  admin two-side view, and generic public waiting state.

**Evidence**

- HTTP and WebSocket projection tests assert private fields are absent for the
  opponent/audience.
- React tests assert labels, rank changes, control transitions, and old
  subtitle removal on a new speech ID.
- Playwright checks desktop and mobile layout and no overlapping controls.

## Slice C: media interruption fence and ASR recovery

**Core modules**

- `agent/runtime.py`: assign a generation fence before cancellation; stop and
  clear the old source, disconnect its room, and reject late audio callbacks.
  Cleanup timeout becomes a hard invalidation/pause diagnostic, never permission
  for the old source to continue.
- `asr/runtime.py` and `asr/session.py`: bind task to speech/connection epoch,
  require a subscribed track and first PCM frame before provider start, and
  report `EmptyAudio` with captured request/response IDs.
- `matches/service.py`: validate every callback tuple and ignore stale media
  work after pause/reset/recovery.

**Evidence**

- Injected cleanup timeout proves no old `capture_frame` reaches LiveKit after
  pause.
- Track-without-PCM test proves no provider task starts before the first PCM
  frame, no successful finalization occurs, and `asr_empty_audio` is surfaced.
- Reconnect test proves same human `speech_id`, contiguous ASR prefix and no
  duplicate `speech.started`.

## Slice D: integrated gates

Run in this order:

```bash
uv run pytest apps/core/tests/test_match_domain.py \
  apps/core/tests/test_match_service_reset.py apps/core/tests/test_asr.py
uv run ruff check apps/core/src apps/core/tests
uv run pyright
pnpm contracts:check
pnpm lint
pnpm typecheck
pnpm test
pnpm test:storybook
pnpm test:browser
pnpm build
```

PostgreSQL tests use the dedicated `TEST_DATABASE_URL`; absent PostgreSQL or
Docker is reported as an unrun gate, never as a pass.

## Slice E: production rollout

1. Confirm no active matches and record service versions, database revision,
   source hashes and rollback directory.
2. Run append-only migrations, if Slice A/B proves one is required; never edit
   a released migration.
3. Deploy Core, Jobs and Web using repository release scripts, then verify
   import paths and service health.
4. Run one controlled formal 4v4: Agent pause/resume, human ASR reconnect,
   boolean decision false, human raise priority, and public-view leakage.
5. Observe Core/Jobs warning-and-error logs for five minutes and verify restart
   counts remain unchanged.
6. Roll back the source only if a gate fails; preserve all captured calls and
   match events for diagnosis.

## Requirement traceability

| Requirement | Spec | Code evidence | Runtime evidence |
|---|---|---|---|
| Human priority and re-raise order | 216 §2 | Actor/domain tests | controlled raise sequence |
| Boolean decision only | 216 §3 | parser/runtime tests | third-party request/response log |
| Private state projection | 216 §4/6.1 | route/WS/UI tests | public and admin browser sessions |
| Agent reset after pause | 215 §Scope, 216 §5 | media fence tests | audible interruption test |
| Human ASR continuity | 215 §Scope | ASR reconnect tests | real microphone reconnect |
| EmptyAudio handling | 215 §Acceptance | provider capture test | request with nonzero PCM or explicit pause |
 | Rollback and stability | both §Rollback | release scripts | five-minute service observation |

## Production evidence (2026-08-23)

- Production match `74f7c36c-3b17-4d3d-bb45-341869554810` was terminated with explicit
  authorization before release; non-terminal match count reached zero.
- Root-only source/Web/database backup was created; migration `0040_formal_4v4_opportunities`
  is the production head and `alembic check` is clean. Core, Jobs, Web, and LiveKit are active;
  live/ready and Web HTTP 200 checks pass; source and Web directory modes are 0755; restart
  counters remain zero.
- Production administrator browser smoke verified the match data page and API request log page;
  public browser access redirects to authentication and reveals no private competition state.
- A WebSocket projection audit fixed non-candidate exposure of `opportunity_id`, `decision_round_id`,
  and `generation_id` in selection/start/playback events. A follow-up defensive projection sends
  all non-admin queued events through the public projection, hides internal media paths and
  unplayed Agent deltas, and leaves candidate detail to viewer-scoped snapshots. Core route tests
  and all local gates pass.
- Ten 30-second service samples remained healthy with unchanged restart counters; the controlled
  Web stop produced the expected SIGTERM 143 only and is excluded from warning counts.
- Real microphone reconnect and real Agent pause/resume remain explicitly unclaimed until a human
  participant session is available; simulated tests cover the state and media-fence behavior.
