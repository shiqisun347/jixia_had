# 223 Human speaking and full-flow reliability

- Version: v2.0
- Status: Completed and released; remaining real-device risks are explicitly open
- Date: 2026-08-24
- Depends on: Specs 164, 166, 167, 168, 169, 215, 216, 217, 218, 219

## Problem and evidence

Production match `abc2151f-af65-4d32-a6d1-0ae3de601ee0` reached
`HUMAN_READY_TO_START` after a human was selected, but never committed
`speech.started`. The browser repeatedly lost its command WebSocket and
obtained a higher connection epoch; after the 60-second start window the
authoritative timeout paused the match. Core and LiveKit remained healthy and
there was no `speech.start` or ASR failure event in that interval.

The current route has two independent writers to one WebSocket: the queue
sender task sends match events while the receive loop sends command ACK/error
messages. A disconnect or close in either path can make the other path call
`send_json` after Starlette has transitioned the socket to disconnected. The
exception escapes the route as `Exception in ASGI application`, and cleanup
then races connection replacement and Presence callbacks. This is a proven
exception-handling and supervision defect; current evidence does not yet prove
that it initiated the first transport close. The first persisted
`match.offline` occurred about 3.18 seconds after `speech.ready`, and the ASGI
exception followed that offline commit by about 7 ms. LiveKit webhook Presence
transitions also currently allow unexpected database/runtime errors to escape
the webhook request, making a media callback failure look like a socket failure
to the browser.

Evidence collected 2026-08-24:

- Production Core logged repeated `Exception in ASGI application`; each was
  followed by a new accepted match WebSocket about 0.55 seconds later.
- Core and LiveKit health remained up and Core `NRestarts=0`; the match paused
  from `HUMAN_READY_TO_START` after no `speech.started` event arrived.
- The isolated browser diagnostic observed no command payloads or credentials.
  It captured LiveKit traffic but did not capture the already-created Core
  command socket, so it cannot establish the close code or the initial trigger.
  Its temporary session was revoked after capture and the browser was closed.

## Scope

- Add one authoritative, serialized WebSocket send boundary for the initial
  snapshot, event sender, command ACK and command error.
- Treat expected client disconnect/OSError/closed-state errors as connection
  termination; stop the sender and run lease/presence cleanup exactly once.
- Ensure an event sender failure wakes the receive path and cannot leave an
  orphaned lease or background task.
- Keep all commands, expected sequence checks, connection epochs, and
  MatchActor ordering unchanged.
- Make LiveKit Presence webhook failures bounded and observable through the
  existing sanitized diagnostic path; a bad/late media callback must never
  create an unhandled ASGI exception or alter authoritative match state.
- Add integration-facing route tests for simultaneous event/command sends,
  disconnect during ACK/error, stale lease replacement, and webhook callback
  failure. Add full Actor tests for human and Agent theory, free debate,
  pause/resume and normal finish.

## Required state matrix

| Scenario | Required result |
| --- | --- |
| Human selected, command socket healthy | Start control enabled; `speech.start` ACK commits `speech.started`; microphone then enables |
| Event and ACK ready concurrently | Messages are serialized; neither is lost or sent after close |
| Browser disconnects during event/ACK | Socket exits normally; current lease/presence cleanup runs once; no ASGI traceback |
| Reconnect replaces an older epoch | Old release cannot mark the new connection offline; new snapshot carries current sequence/epoch |
| Media webhook is duplicate, stale or malformed | Return safe 2xx/no-op; no state mutation and no unhandled ASGI exception |
| Media webhook persistence/runtime failure | Return bounded failure, write redacted diagnostic, and leave MatchActor authoritative state unchanged |
| Manual pause during human ready/speaking | Pause is authoritative; media is disabled/paused; resume restores the correct state without restarting the wrong speech |
| Manual pause during Agent preparing/speaking/finalizing | Agent work is fenced/cancelled by the existing recovery path; resume does not reuse stale generation/audio |
| Human/Agent normal full flow | Both theory speeches complete, free debate opens, turns can be allocated and started, and match reaches `FINISHED` |
| Post-match completion | Strict annotation/questionnaire, restricted result gate, and administrator CSV/JSON export remain usable |

## Acceptance

1. A route regression reproduces a concurrent sender/command disconnect and
   finishes without an `Exception in ASGI application`; no lease remains for
   the closed socket.
2. A human allocation-to-start integration test proves the command reaches
   Core, returns an ACK, and transitions to `HUMAN_SPEAKING` with the same
   `speech_id` used by ASR.
3. Reconnect, stale sequence, stale epoch, duplicate command, and command
   timeout paths are covered and preserve existing error codes.
4. Web UI keeps the Start button disabled only for a genuinely unavailable
   audio or command connection, and recovers automatically after the current
   snapshot/epoch is ready.
5. Pause/resume tests cover human ready, human speaking, Agent preparing,
   Agent speaking, Agent finalizing, selection window, and human-only wait;
   no stale media callback advances the match.
6. A deterministic six-human/Agent simulation covers theory, free debate,
   normal finish, questionnaire submission, restricted result access, and
   match-data CSV/JSON export. Provider/API request logs are present in the
   administrator export and absent from public projections.
7. Core, Jobs, Web, contracts, browser and PostgreSQL integration gates pass
   where the required database is available. Production is deployed only
   after a protected backup and zero non-terminal matches, then observed for
   at least ten 30-second samples with zero new Core errors and zero restarts.

## Rollback

Restore the previous Core/Web source and restart only the affected service.
No migration or persisted state rewrite is required. Existing leases are
cleared by the normal Core startup recovery; existing matches must be handled
through the privileged MatchActor recovery/termination flow, never by direct
database mutation.

## Security and privacy

Do not add credentials, provider raw secrets, request bodies, or traceback
contents to logs. WebSocket private candidate fields remain viewer-scoped;
webhook failures expose only stable redacted diagnostics to operators.

## Release verification (2026-08-24)

- Local gates passed: tooling 47, QA 6, Core 347 passed/33 deselected,
  Jobs 25 passed/2 deselected, Web 188 passed, Storybook 90/90,
  browser 228/228, `pnpm lint`,
  `pnpm typecheck`, `pnpm contracts:check`, `pnpm build`, and `git diff --check`.
  PostgreSQL integration remains unavailable in this workspace.
- Production export `175bb060-f651-4584-a822-53ad5f4363b8` completed `SUCCEEDED`
  for one selected match. The ZIP contained one match directory and seven
  `MATCH` provider records; all seven records had non-null request and response
  fields. No payload bodies were copied into this document.
- Production match `b094115b-224b-4bb2-8375-63525f56dbd6` crossed
  `agent.finalized -> free_debate.started -> hand.window_opened`; its later
  pause was the specified `HUMAN_WAIT_TIMEOUT` after a 60-second human-only wait,
  then it was terminated through MatchActor authorization.
- Production match `9eab04f7-ebc5-4638-a576-4b84915ffd2e` used a browser fake
  microphone for deterministic media validation. It reached `HUMAN_READY_TO_START`,
  entered `HUMAN_SPEAKING`, paused and resumed at the same human speech boundary,
  finalized the human turn, crossed the reverse Agent turn, and reached
  `free_debate.started`; it was then terminated through the authorized control
  path. This does not claim validation with a physical microphone.
- Web now renders `HUMAN_WAIT_TIMEOUT` as a normal rule pause rather than a
  generic service fault. Production Web was rebuilt with `pnpm build` and
  deployed with backup `/opt/jixia-web-prev-human-wait-20260824T135531Z`.
- Post-release production checks: all four services active, Core live/ready
  returned 200, all four `NRestarts=0`, `/opt/jixia-web` and `/opt/jixia-debate`
  were `0755`, and non-terminal match count was zero.

### Remaining risks

- A pure Agent side whose candidates all skip still enters the specified
  human-only wait; a product decision is required before changing that rule.
- Physical microphone/ASR, Bluetooth/noise recovery, long concurrent matches,
  and real-device reconnection remain unclaimed.
