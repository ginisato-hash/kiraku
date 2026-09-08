// roomAccessState.js — pure logic for `room_access_status` (real-time
// occupancy/cleaning-access confirmation). This is a DELIBERATELY SEPARATE
// axis from the existing Cleaning DTO `status` enum (CHECKIN/CHECKOUT/
// TURNOVER/STAYOVER/VACANT/UNASSIGNED/CANCELLED, see cleaningSheetTemplate.js
// STATUS_LABELS_JP) and from the existing 清掃 override system
// (cleaningOverrides.js, KV-backed, instruction text only). Never mix the
// two enums, never store roomAccessStatus in CLEANING_OVERRIDES KV.
//
// WAITING_CHECKOUT: front desk has NOT yet confirmed the physical departure
//   of the guest checking out of this room today. This is the safe/default
//   state — cleaning staff must not enter.
// CLEANING_ALLOWED: front desk has explicitly confirmed the room is empty.
//
// A room only ever carries a non-null room_access_status when a guest is
// actually departing it today (Cleaning DTO status CHECKOUT or TURNOVER, as
// already computed by the Python classifier) — STAYOVER/CHECKIN/VACANT/
// UNASSIGNED/CANCELLED rooms are out of scope for this feature entirely and
// always resolve to null, even if a stale Durable Object record exists for
// that room number (e.g. after the R2 snapshot refreshes and a booking
// changes) — see applyRoomAccessStatus below.
//
// This module is imported by BOTH src/worker.js (Worker) and
// src/cleaningLiveState.js (Durable Object) — keep it free of any
// Worker-only or DO-only API usage (no env, no DurableObjectState, no KV).

export const WAITING_CHECKOUT = "WAITING_CHECKOUT";
export const CLEANING_ALLOWED = "CLEANING_ALLOWED";
export const ROOM_ACCESS_STATUSES = [WAITING_CHECKOUT, CLEANING_ALLOWED];
export const DEFAULT_ROOM_ACCESS_STATUS = WAITING_CHECKOUT;

// Cleaning DTO room.status values for which room_access_status is meaningful
// at all today (i.e. a guest is physically departing this room).
const DEPARTING_CLEANING_STATUSES = new Set(["CHECKOUT", "TURNOVER"]);

export function isDepartingRoomStatus(cleaningStatus) {
  return DEPARTING_CLEANING_STATUSES.has(cleaningStatus);
}

export function isValidRoomAccessStatus(value) {
  return ROOM_ACCESS_STATUSES.includes(value);
}

// The transition a front-desk button click always proposes: WAITING_CHECKOUT
// <-> CLEANING_ALLOWED (never any other target).
export function nextRoomAccessStatus(currentStatus) {
  return currentStatus === CLEANING_ALLOWED ? WAITING_CHECKOUT : CLEANING_ALLOWED;
}

export function roomAccessStatusLabelJa(status) {
  if (status === WAITING_CHECKOUT) return "未退室";
  if (status === CLEANING_ALLOWED) return "清掃可";
  return "";
}

// Body validation for POST /api/cleaning/access-status. kirakuRoomOrder is
// injected (rather than imported from roomMaster.js here) purely so this
// function has one job — the caller (worker.js) already owns
// KIRAKU_ROOM_ORDER and passes it through, same spirit as the existing
// validateDateAndRoomBody helper in worker.js but for a different allowed-key
// set/enum (status, not instruction).
export function validateAccessStatusBody(body, kirakuRoomOrder) {
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return { error: "invalid_payload" };
  }
  const allowedKeys = new Set(["date", "roomNumber", "status"]);
  for (const key of Object.keys(body)) {
    if (!allowedKeys.has(key)) return { error: "invalid_payload" };
  }
  const { date, roomNumber, status } = body;
  if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return { error: "invalid_date" };
  if (typeof roomNumber !== "string" || !kirakuRoomOrder.includes(roomNumber)) {
    return { error: "invalid_room" };
  }
  if (!isValidRoomAccessStatus(status)) return { error: "invalid_status" };
  return { ok: true, date, roomNumber, status };
}

// Pure merge: attaches roomAccessStatus/roomAccessUpdatedAt to each cleaning
// room row (rooms here is already the mergeCleaningOverrides() output, or
// any array of Cleaning DTO room rows — this function does not care).
//
// doRoomsState: { [roomNumber]: { status, updatedAt } }, as read from
// CleaningLiveState's /internal/state — or {}/null/undefined if the Durable
// Object binding/state is unavailable, which degrades to the same "no live
// record yet" default (DEFAULT_ROOM_ACCESS_STATUS) rather than throwing.
// This is the fail-safe path required by spec: never surface
// CLEANING_ALLOWED just because we could not read the real state.
export function applyRoomAccessStatus(rooms, doRoomsState) {
  const list = Array.isArray(rooms) ? rooms : [];
  const doRooms = (doRoomsState && typeof doRoomsState === "object") ? doRoomsState : {};
  return list.map((room) => {
    if (!room || !isDepartingRoomStatus(room.status)) {
      return { ...room, roomAccessStatus: null, roomAccessUpdatedAt: null };
    }
    const record = room.room_number ? doRooms[room.room_number] : null;
    const status = (record && isValidRoomAccessStatus(record.status)) ? record.status : DEFAULT_ROOM_ACCESS_STATUS;
    const updatedAt = (record && record.updatedAt) || null;
    return { ...room, roomAccessStatus: status, roomAccessUpdatedAt: updatedAt };
  });
}
