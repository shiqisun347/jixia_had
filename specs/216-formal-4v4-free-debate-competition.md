# 216 Formal 4v4 free-debate competition and private UI

- Version: v2.0
- Status: Implemented and published; live media acceptance still requires a real participant session
- Approved by: user approval of Specs 215 and 216
- Date: 2026-08-23
- Implementation plan: `docs/design/formal-4v4-free-debate-implementation-plan.md`
- Supersedes for formal 4v4 only: the free-debate selection portions of
  TechDesign 8.3 and Spec 201 section 4.2/8.3/10.2

## 1. Goal and compatibility

Every newly created formal 4v4 match uses one authoritative free-debate
opportunity model, regardless of `experiment_mode`. Historical non-4v4 matches
remain read-only compatible. `experiment_mode` may continue to label research
data, but it must not decide whether the competition state or boolean Prompt is
enabled.

The model is owned by Core's existing MatchActor. Web is a projection and never
owns deadlines, queue order, selection, audio recovery, or match time.

## 2. Opportunity lifecycle

### 2.1 Opening and deadline

- While the opponent is speaking, every human on the candidate side may raise
  or cancel repeatedly. Core assigns acceptance order; a re-raise gets a new
  order.
- The ordinary absolute selection deadline is exactly three seconds after the
  opponent's actual speech end. ASR/model latency never extends it.
- The first free-debate opportunity starts when the host prompt audio begins
  for the negative side, and retains a three-second window after host audio
  ends.
- For a human opponent, ASR final immediately starts Agent decision requests.
  A final arriving after the deadline still creates a late data record only.
- For an Agent opponent, completed LLM text immediately starts the candidate
  decision requests; the full generated text is valid decision context even if
  not fully played. Public transcript remains actual played/spoken content.
- Each opportunity has one stable `opportunity_id` and generation. Pause and
  resume rules never create a second opportunity unless the current speech is
  explicitly reset or regenerated.

### 2.2 Allocation

At the deadline, one serialized Actor command chooses:

1. The first valid human queue entry, always preferred.
2. If no human exists, an equal-probability seed-deterministic choice among all
   Agent decisions that completed with `should_speak=true` before the deadline.
3. Otherwise, `HUMAN_ONLY_WAIT` with every Agent effective status `SKIP`.

There is no willingness sorting, Agent fallback, or forced Agent assignment in
formal 4v4. A human or Agent allocation cannot be transferred. After an Agent
or human is allocated and speech starts, all competition seat statuses clear
immediately and are not carried to the next opportunity.

### 2.3 Human-only wait

The first valid human raise during the 60-second human-only wait is allocated
immediately and does not consume free-debate team time. The Agent remains
`SKIP`. If the timer expires with no human, Core pauses the match. Resuming
restarts a complete 60-second wait with the same opportunity and Agent skip
state. Repeated expiry may pause repeatedly.

## 3. Agent states and decision contract

The four mutually exclusive Agent seat states are `WAITING`, `DECIDING`,
`RAISE`, and `SKIP`; UI labels are `等待决策`, `决策中`, `举手`, and `跳过`.
If a short opponent speech already satisfies the request condition in its first
three seconds, state changes directly to `DECIDING`; `WAITING` is only shown
after three seconds when no request condition is available.

Formal 4v4 decision Prompt accepts exactly:

```json
{"should_speak": true}
```

or the same object with `false`. `willingness` is not required or used. A
valid false is `SKIP`; malformed output, provider failure, and two-attempt
failure are also `SKIP` and are saved as technical diagnostics. They never
become a fallback Agent candidate.

## 4. Visibility and UI

- Candidate-side debaters and administrators see exact private statuses,
  candidate ranks, queue state, and authoritative countdowns.
- The opposing side and audience receive only the generic public state
  “等待下一位发言者”. No private fields may appear in HTTP snapshots,
  WebSocket events, or rendered HTML.
- Seat cards show human status as `未举手`, `第 N 位`, or `将发言` and Agent
  status as the four labels above. No central full candidate queue is added.
- Human controls are fixed in the bottom action area: `申请发言`, `取消举手`,
  and, after allocation, `开始发言`. A human allocation expires after 60
  seconds without that same human starting, then pauses; resume retains the
  original human and restarts the complete start window.
- The three-second window displays tenths of a second; the 60-second wait
  displays integer seconds. Server deadline is authoritative; client smoothing
  cannot advance or close the state.
- Agent status and human queue are cleared as soon as speech begins. No public
  competition-history panel is added. CSS animation uses only transform and
  opacity and honors reduced-motion.

## 5. Pause, resume, reset

- Pause during opponent speech before ASR final clears old human raises and
  resumes the original speech competition after recovery.
- Pause during the three-second selection window preserves remaining deadline
  and candidates; resume continues without re-requesting models.
- Pause during human-only wait preserves Agent `SKIP` but restarts a full 60
  seconds after resume.
- Reset, regenerate, or an interrupted Agent speech invalidates the old
  opportunity, complete generated text, speech identity, decision callbacks,
  and seat statuses. Old callbacks are rejected by match/speech/generation/
  opportunity/context validation.
- An allocated Agent must wait at least 1500 ms after opponent speech or host
  audio ends. Generation time counts toward this gap and does not consume
  formal speech time.
- Agent LLM/TTS failure retries once. A second failure pauses the match and
  never transfers the allocation to a human.

### 5.1 Authoritative transition matrix

| Pause origin | Persist on pause | Resume behavior | New model request |
|---|---|---|---|
| Opponent speaking, no ASR final | speech identity and remaining speech time | same speech, clear raises, reopen competition | Agent decision only when a new final arrives |
| Three-second selection window | remaining deadline and candidates | continue saved deadline and candidates | no |
| Human-only wait | opportunity and Agent `SKIP` | full 60-second wait | no |
| Agent speaking/preparing | old generation invalidated, media fence set | new speech/generation from start | yes |
| Human ready to start | allocated human and full start window | same human, full 60-second start window | no |

Every asynchronous callback must carry and revalidate `match_id`,
`speech_id`, `generation_id`, `attempt_no`, `connection_epoch`,
`context_version`, `opportunity_id`, and `opportunity_generation`. A mismatch
is persisted as stale diagnostic data and has no Actor side effect.

## 6. Persistence and diagnostics

Core persists opportunity ID/generation, raise/cancel acceptance times,
decision request/response times, deadline, playback progress, generated-full
text time, actual playback progress, allocation and late/stale outcomes. The
existing administrator request log is filtered by opportunity ID and records
the redacted third-party input/output; no new log system is introduced.

Research exports include these records. The live UI does not expose raw model
requests or provider responses.

### 6.1 Viewer projection contract

The public projection may contain action, current speaker, public transcript
and generic waiting state. The private projection additionally contains only
the viewer's candidate-side human queue, Agent statuses/ranks and countdown.
The administrator projection contains both sides, explicitly marked as an
administrator view. The server constructs these projections before HTTP JSON
serialization and before WebSocket event serialization; filtering only in
React is insufficient.

For WebSocket delivery, candidate-side detail is obtained from the viewer-
scoped snapshot or command acknowledgement. Non-administrator event payloads
use the public projection even when the viewer is a candidate, so a queued
event cannot be authorized against a later side's runtime state. Public events
strip `opportunity_id`, `opportunity_generation`, `decision_round_id`,
`generation_id`, internal audio paths, and unplayed Agent text. Public
completion events contain only already-played/spoken result metadata.

## 7. Required changes

- Remove formal-4v4 dependence on `experiment_mode` in Actor transitions,
  decision parsing, persistence and snapshot projection.
- Keep legacy willingness/fallback branches only for historical read-only
  compatibility, with explicit tests proving they are unreachable for new
  formal 4v4 matches.
- Add privacy projection tests for HTTP, WebSocket and administrator snapshots.
- Add defensive event projection tests covering queued cross-side events,
  completion payloads, internal media paths, unplayed Agent text, and all
  opportunity/decision/generation identifiers.
- Add deterministic equal-probability Agent selection tests, deadline/late
  callback tests, pause matrix tests, and UI seat/control tests.

## 8. Acceptance and rollback

Acceptance requires all tests above, OpenAPI/TypeScript contract generation,
browser verification at desktop and mobile widths, and a production match with
zero private-state leakage. Release requires zero active matches, source and
database backup, health checks, one real Agent interruption/resume test, one
human ASR reconnect test, and five-minute warning/error log observation.

Rollback restores Core/Web source and restarts services. New migrations, if
needed, are append-only; persisted historical events are not rewritten.
