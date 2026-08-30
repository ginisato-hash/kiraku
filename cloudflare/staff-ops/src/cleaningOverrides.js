// cleaningOverrides.js — pure functions for the清掃 override key scheme and merge.
//
// KEY SCHEME (both GET /api/cleaning and POST/DELETE /api/cleaning/override
// must agree on this exact function — do not change one side without the
// other):
//
//   `${date}:${roomNumber}` e.g. "2026-08-31:607"
//
// This replaces the OLD `room_type_key:checkout_booking_id:checkin_booking_id`
// scheme entirely — room_number is now always resolved to one of the 18
// canonical physical rooms (KIRAKU_ROOM_ORDER) by the Python classifier, so a
// stable per-room-per-date key is available and preferred.
//
// VALUE SHAPE stored in KV (JSON string): { instruction, updatedAt } — never
// copy booking data (guest name, counts, etc.) into the override value.

import { KIRAKU_ROOM_ORDER } from "./roomMaster.js";

export function buildOverrideKey(date, roomNumber) {
  return `${date}:${roomNumber}`;
}

// Reads all override entries for a date (bounded to the 18 canonical rooms —
// UNASSIGNED rows have no room_number and can never have an override, so
// there is no reason to ever look one up for them). 18 individual KV reads
// via Promise.all rather than a list() call — this avoids needing KV's
// list() API and keeps behavior simple/predictable.
// Returns a plain object: { [roomNumber]: { instruction, updatedAt } }.
export async function readOverridesForDate(kv, date) {
  if (!kv) return {};
  const entries = await Promise.all(KIRAKU_ROOM_ORDER.map(async (room) => {
    let raw = null;
    try {
      raw = await kv.get(buildOverrideKey(date, room));
    } catch (e) {
      raw = null;
    }
    if (!raw) return null;
    try {
      const parsed = JSON.parse(raw);
      return (parsed && typeof parsed.instruction === "string") ? [room, parsed] : null;
    } catch (e) {
      return null;
    }
  }));
  const result = {};
  for (const entry of entries) {
    if (entry) result[entry[0]] = entry[1];
  }
  return result;
}

// Pure merge: base cleaning room rows (from the R2 snapshot) + an
// overridesByRoom object (from readOverridesForDate) -> rows with
// effectiveInstruction/hasOverride/updatedAt added. Does not mutate either
// input.
//
// A room whose room_number is null (UNASSIGNED) is never looked up in the
// overrides map — it always falls back to its own source_instruction ("").
export function mergeCleaningOverrides(baseRooms, overridesByRoom) {
  const rooms = Array.isArray(baseRooms) ? baseRooms : [];
  const overrides = (overridesByRoom && typeof overridesByRoom === "object") ? overridesByRoom : {};
  return rooms.map((room) => {
    const override = room && room.room_number ? overrides[room.room_number] : null;
    if (override) {
      return {
        ...room,
        effectiveInstruction: override.instruction,
        hasOverride: true,
        updatedAt: override.updatedAt || null,
      };
    }
    return {
      ...room,
      effectiveInstruction: (room && room.source_instruction) || "",
      hasOverride: false,
      updatedAt: null,
    };
  });
}
