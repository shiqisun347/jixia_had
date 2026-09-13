# 243 Cross-room device check reuse

- Version: v2.0
- Status: Implemented locally; production release pending
- Date: 2026-09-01

## Scope

Successful device checks issue a revocable, server-validated browser-scoped
credential valid for 24 hours. The credential is bound to the authenticated
user and the browser cookie, and lets Core create a normal room-level
`DeviceCheck` when that user enters another room. Existing room readiness
checks remain authoritative.

The credential is HttpOnly and stored only as a hash in Core. It is revoked
when the current browser explicitly invalidates its device check, and expires
automatically. Device changes and permission loss continue to trigger the
existing explicit recheck flow.

## Acceptance

1. A user can complete a device check in room A and prepare in room B from the
   same browser without another hardware probe while the credential is valid.
2. A different browser, user, expired credential, or modified credential must
   perform a fresh probe.
3. Existing `DeviceCheck` rows, `ready()` validation and historical rooms stay
   compatible.
4. The browser credential cannot be read by JavaScript or used by another
   authenticated user.

## Rollback

Revert the Web/Core release and leave the additive table in place. Existing
room-level checks remain usable; the reuse endpoint can be disabled without
rewriting historical checks.
