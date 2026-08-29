// cleaningOverrides.js — pure functions for the清掃 override key scheme and merge.
//
// KEY SCHEME (both GET /api/cleaning and POST /api/cleaning/override must agree
// on this exact function — do not change one side without the other):
//
//   room_number is null in essentially all real cases today (Beds24 only
//   exposes room TYPE for this property, not physical unit numbers), so it
//   cannot be used as a stable per-room identifier. Instead we key an
//   override by the composite string:
//
//     `${room_type_key}:${checkout_booking_id || ""}:${checkin_booking_id || ""}`
//
//   which uniquely identifies one cleaning "slot" for a given date (the
//   overrides object itself is already scoped per-date via the KV key
//   `overrides:${date}`, so date is not part of this composite key).

export function buildOverrideKey(room) {
  const r = room || {};
  const roomTypeKey = r.room_type_key || "";
  const checkoutId = r.checkout_booking_id || "";
  const checkinId = r.checkin_booking_id || "";
  return `${roomTypeKey}:${checkoutId}:${checkinId}`;
}

// Pure merge: base cleaning room rows (from the R2 snapshot) + an overrides
// object (keyed by buildOverrideKey, value = partial fields to shallow-merge
// on top, e.g. { room_number: "12", notes: "水漏れ確認" }) -> merged room rows.
// Does not mutate either input. An absent override for a room leaves that
// room's object reference-equal to the input (cheap, and easy to assert in
// tests).
export function mergeCleaningOverrides(baseRooms, overridesObj) {
  const rooms = Array.isArray(baseRooms) ? baseRooms : [];
  const overrides = (overridesObj && typeof overridesObj === "object") ? overridesObj : {};
  return rooms.map((room) => {
    const key = buildOverrideKey(room);
    const override = overrides[key];
    if (!override || typeof override !== "object") return room;
    // updated_by / updated_at are bookkeeping fields written by the POST
    // handler — never surface them as if they were cleaning-sheet data.
    const { updated_by, updated_at, ...fields } = override;
    return { ...room, ...fields };
  });
}
