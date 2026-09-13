# Spec 233: Free Debate Zero-Time Queue Safety

## Scope

Prevent a participant whose side has no usable free-debate time from entering
the hand queue, and discard queued human entries that do not belong to the
holder side when a speech turn closes.

## Invariants

- A hand raise is rejected only when the target side has zero remaining time.
- A queued human may be selected only for the current free_holder_side.
- Raising during the opponent's speech remains supported when the participant's
  side still has usable time.
- No new pause or automatic side switch is introduced by this fix.

## Acceptance

- A zero-time side receives hand_not_eligible and its user is absent from the
  queue; any positive remaining time remains eligible.
- A stale queue entry from the exhausted/non-holder side is removed before
  selection; a valid Agent or human candidate can proceed.
- Existing free-debate competition, human priority, and all-agent fallback
  behavior remain unchanged.

## Rollback

Revert the domain change and its regression tests; no migration or persisted
data change is required.
