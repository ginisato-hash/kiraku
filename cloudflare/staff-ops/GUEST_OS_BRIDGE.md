# Guest OS private event receiver

The named `GuestOsEvents` Worker entrypoint accepts only service-binding RPC. The
normal Staff Ops HTTP entrypoint keeps its existing authentication; there is no
public event-ingest route. This receiver reads neither Beds24 nor the guest/finance
snapshot. The integration is confined to `cloudflare/staff-ops/`.

The wire object has `id`, `propertyId=kiraku`, one of STAY_CHECKED_IN,
STAY_CHECKED_OUT, STAY_ROOM_MOVED, ROOM_ACCESS_READY or ROOM_CREDENTIAL_ALERT,
numeric internal `subjectRef`, exact mapped `canonicalRoomKey`/`roomNumber`, and a
UTC ISO millisecond `occurredAt`. Moves add exact from-room fields and
`roomRevision>=2`; checkout/move require boolean `vacatedRoomEmpty` committed by
the source transition. An alert can have an uppercase reason code and may be
roomless. Unknown keys, booking references, free text and malformed values refuse.
The eighteen-room mapping is a protocol identity check, not a second BI source.

A property-wide receipt object in the existing CleaningLiveState namespace fixes
the event ID/digest before forwarding to the event's JST date object. The latter
atomically persists receipt, both affected rooms, room high-water and capped
history. A source response is acknowledged only after those durable effects.
Unknown responses retry the same ID/digest; changes, including business date,
conflict. Per-room ordering prevents an older event undoing newer occupancy.
Later manual observations win; a timestamp tie cannot create cleaning permission.
Neither access-key readiness nor a credential alert proves vacancy or cleaning.

Physical events are visible even if the current R2 snapshot no longer has a
planned departure, such as after a room reassignment. Those non-departure rows
are read-only; existing legacy filtering and manual API admission are retained.
A recorded status is not evidence that cleaning is complete or a key is safe.

Tests: the existing `npm test` chain includes the receiver contracts from the
CleaningLiveState suite. `node test/guestOsEvents.mutation.mjs` checks the changed
safety guards. Native workerd tests separately prove named RPC, SQLite persistence
through restart, exact receipts, conflicts and two-room effects. Production
acceptance still needs a real synthetic outbox acknowledgement and replay probe.

The deployment workflow automatically deploys Staff Ops after a main merge.
Before merge, review the exact receiver candidate, unchanged auth/secrets/bindings,
assets and source lineage. Keep Guest OS unbound/off until receiver read-back and
canary acceptance. Do not alter BI, snapshot publication, R2/KV data, hardware,
shared staff credentials or any separate provider automation.
