// roomAccessState.js のテスト: room_access_status(在室確認)の純粋ロジック。
// このモジュールはworker.js(Worker)とcleaningLiveState.js(Durable Object)の
// 両方から使われるので、DOやenvに一切依存しないことも間接的に確認する
// (importするだけでDOM/Workers専用APIを要求されないこと自体がテスト)。
import assert from "node:assert";
import {
  WAITING_CHECKOUT, CLEANING_ALLOWED, ROOM_ACCESS_STATUSES, DEFAULT_ROOM_ACCESS_STATUS,
  isDepartingRoomStatus, isValidRoomAccessStatus, nextRoomAccessStatus,
  roomAccessStatusLabelJa, validateAccessStatusBody, applyRoomAccessStatus,
} from "../src/roomAccessState.js";
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("WAITING_CHECKOUT is the default/safe status", async () => {
  assert.equal(DEFAULT_ROOM_ACCESS_STATUS, WAITING_CHECKOUT);
});

await check("ROOM_ACCESS_STATUSES is exactly the two-value enum", async () => {
  assert.deepEqual(ROOM_ACCESS_STATUSES, [WAITING_CHECKOUT, CLEANING_ALLOWED]);
});

await check("isDepartingRoomStatus is true only for CHECKOUT/TURNOVER", async () => {
  assert.equal(isDepartingRoomStatus("CHECKOUT"), true);
  assert.equal(isDepartingRoomStatus("TURNOVER"), true);
  assert.equal(isDepartingRoomStatus("CHECKIN"), false);
  assert.equal(isDepartingRoomStatus("STAYOVER"), false);
  assert.equal(isDepartingRoomStatus("VACANT"), false);
  assert.equal(isDepartingRoomStatus("UNASSIGNED"), false);
  assert.equal(isDepartingRoomStatus("CANCELLED"), false);
  assert.equal(isDepartingRoomStatus(undefined), false);
});

await check("isValidRoomAccessStatus rejects anything outside the two-value enum, including the old CHECKOUT name", async () => {
  assert.equal(isValidRoomAccessStatus("WAITING_CHECKOUT"), true);
  assert.equal(isValidRoomAccessStatus("CLEANING_ALLOWED"), true);
  assert.equal(isValidRoomAccessStatus("CHECKOUT"), false, "must never accept the Cleaning DTO's own CHECKOUT enum value");
  assert.equal(isValidRoomAccessStatus(""), false);
  assert.equal(isValidRoomAccessStatus(null), false);
  assert.equal(isValidRoomAccessStatus(undefined), false);
});

await check("nextRoomAccessStatus toggles WAITING_CHECKOUT <-> CLEANING_ALLOWED only", async () => {
  assert.equal(nextRoomAccessStatus(WAITING_CHECKOUT), CLEANING_ALLOWED);
  assert.equal(nextRoomAccessStatus(CLEANING_ALLOWED), WAITING_CHECKOUT);
});

await check("roomAccessStatusLabelJa returns the exact Japanese labels", async () => {
  assert.equal(roomAccessStatusLabelJa(WAITING_CHECKOUT), "未退室");
  assert.equal(roomAccessStatusLabelJa(CLEANING_ALLOWED), "清掃可");
  assert.equal(roomAccessStatusLabelJa(null), "");
  assert.equal(roomAccessStatusLabelJa("garbage"), "");
});

// ---------------- validateAccessStatusBody ----------------

await check("validateAccessStatusBody accepts a well-formed body", async () => {
  const result = validateAccessStatusBody({ date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED" }, KIRAKU_ROOM_ORDER);
  assert.deepEqual(result, { ok: true, date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED" });
});

await check("validateAccessStatusBody rejects a non-object / array / null body", async () => {
  assert.equal(validateAccessStatusBody(null, KIRAKU_ROOM_ORDER).error, "invalid_payload");
  assert.equal(validateAccessStatusBody("x", KIRAKU_ROOM_ORDER).error, "invalid_payload");
  assert.equal(validateAccessStatusBody([], KIRAKU_ROOM_ORDER).error, "invalid_payload");
});

await check("validateAccessStatusBody rejects any extra/unexpected key (e.g. price, instruction)", async () => {
  const result = validateAccessStatusBody(
    { date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED", price: 9800 },
    KIRAKU_ROOM_ORDER,
  );
  assert.equal(result.error, "invalid_payload");
});

await check("validateAccessStatusBody rejects an invalid date format", async () => {
  const result = validateAccessStatusBody({ date: "20260908", roomNumber: "601", status: "CLEANING_ALLOWED" }, KIRAKU_ROOM_ORDER);
  assert.equal(result.error, "invalid_date");
});

await check("validateAccessStatusBody rejects a room number outside KIRAKU_ROOM_ORDER", async () => {
  const result = validateAccessStatusBody({ date: "2026-09-08", roomNumber: "999", status: "CLEANING_ALLOWED" }, KIRAKU_ROOM_ORDER);
  assert.equal(result.error, "invalid_room");
});

await check("validateAccessStatusBody rejects a status outside the two-value enum", async () => {
  const result = validateAccessStatusBody({ date: "2026-09-08", roomNumber: "601", status: "CHECKED_OUT" }, KIRAKU_ROOM_ORDER);
  assert.equal(result.error, "invalid_status");
});

// ---------------- applyRoomAccessStatus ----------------

const departingRoom = { room_number: "601", status: "CHECKOUT" };
const turnoverRoom = { room_number: "602", status: "TURNOVER" };
const stayoverRoom = { room_number: "603", status: "STAYOVER" };
const vacantRoom = { room_number: "604", status: "VACANT" };
const unassignedRoom = { room_number: null, status: "UNASSIGNED" };

await check("a departing room with no DO record defaults to WAITING_CHECKOUT (fail-safe)", async () => {
  const out = applyRoomAccessStatus([departingRoom], {});
  assert.equal(out[0].roomAccessStatus, WAITING_CHECKOUT);
  assert.equal(out[0].roomAccessUpdatedAt, null);
});

await check("a departing room reflects the DO's recorded status/updatedAt when present", async () => {
  const out = applyRoomAccessStatus([departingRoom], { "601": { status: "CLEANING_ALLOWED", updatedAt: "2026-09-08T00:00:00.000Z" } });
  assert.equal(out[0].roomAccessStatus, "CLEANING_ALLOWED");
  assert.equal(out[0].roomAccessUpdatedAt, "2026-09-08T00:00:00.000Z");
});

await check("a TURNOVER room is treated as departing too", async () => {
  const out = applyRoomAccessStatus([turnoverRoom], {});
  assert.equal(out[0].roomAccessStatus, WAITING_CHECKOUT);
});

await check("STAYOVER/VACANT/UNASSIGNED rooms always get roomAccessStatus=null, even with a stale DO record for that room number", async () => {
  const doState = { "603": { status: "CLEANING_ALLOWED", updatedAt: "x" }, "604": { status: "CLEANING_ALLOWED", updatedAt: "x" } };
  const out = applyRoomAccessStatus([stayoverRoom, vacantRoom, unassignedRoom], doState);
  assert.equal(out[0].roomAccessStatus, null);
  assert.equal(out[1].roomAccessStatus, null);
  assert.equal(out[2].roomAccessStatus, null);
  assert.equal(out[0].roomAccessUpdatedAt, null);
});

await check("applyRoomAccessStatus degrades to {} (never throws) when doRoomsState is null/undefined", async () => {
  assert.doesNotThrow(() => applyRoomAccessStatus([departingRoom], null));
  assert.doesNotThrow(() => applyRoomAccessStatus([departingRoom], undefined));
  const out = applyRoomAccessStatus([departingRoom], undefined);
  assert.equal(out[0].roomAccessStatus, WAITING_CHECKOUT);
});

await check("applyRoomAccessStatus ignores a DO record with an invalid/corrupt status value, falling back to the safe default", async () => {
  const out = applyRoomAccessStatus([departingRoom], { "601": { status: "garbage", updatedAt: "x" } });
  assert.equal(out[0].roomAccessStatus, WAITING_CHECKOUT);
});

await check("applyRoomAccessStatus never mutates the input array/objects", async () => {
  const input = [{ ...departingRoom }];
  const frozenCopy = JSON.stringify(input);
  applyRoomAccessStatus(input, { "601": { status: "CLEANING_ALLOWED", updatedAt: "x" } });
  assert.equal(JSON.stringify(input), frozenCopy);
});

await check("applyRoomAccessStatus handles a non-array rooms argument gracefully", async () => {
  assert.deepEqual(applyRoomAccessStatus(null, {}), []);
  assert.deepEqual(applyRoomAccessStatus(undefined, {}), []);
});

console.log(`\n${passed} roomAccessState checks passed`);
