// cleaningOverrides.js の純粋関数テスト（buildOverrideKey / readOverridesForDate /
// mergeCleaningOverrides）— NEWキー体系 `${date}:${roomNumber}`。
import assert from "node:assert";
import { buildOverrideKey, readOverridesForDate, mergeCleaningOverrides } from "../src/cleaningOverrides.js";
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const baseRoomCheckin = {
  date: "2026-08-30", room_number: "402", status: "CHECKIN",
  departing_guest: null,
  arriving_guest: {
    booking_id: "89381999", guest_name: "佐藤 花子", adults: 1, children: 0,
    total_guests: 1, check_in: "2026-08-30", check_out: "2026-08-31",
    arrival_time: "15:00", source: "Direct",
  },
  staying_guest: null, current_night_index: 1, total_nights: 1, source_instruction: "",
};

const unassignedRoom = {
  date: "2026-08-30", room_number: null, status: "UNASSIGNED",
  departing_guest: null, arriving_guest: null, staying_guest: null,
  current_night_index: null, total_nights: null, source_instruction: "",
};

await check("buildOverrideKey format is `${date}:${roomNumber}`", async () => {
  assert.equal(buildOverrideKey("2026-08-31", "607"), "2026-08-31:607");
});

await check("readOverridesForDate returns {} when kv binding is absent", async () => {
  const result = await readOverridesForDate(null, "2026-08-30");
  assert.deepEqual(result, {});
});

await check("readOverridesForDate reads all 18 canonical rooms via individual gets (no list())", async () => {
  const seenKeys = [];
  const kv = {
    get: async (key) => {
      seenKeys.push(key);
      return null;
    },
  };
  await readOverridesForDate(kv, "2026-08-30");
  assert.equal(seenKeys.length, 18);
  for (const room of KIRAKU_ROOM_ORDER) {
    assert.ok(seenKeys.includes(`2026-08-30:${room}`));
  }
});

await check("readOverridesForDate returns only rooms that have a stored override", async () => {
  const store = {
    "2026-08-30:402": JSON.stringify({ instruction: "タオル追加", updatedAt: "2026-08-30T00:00:00.000Z" }),
    "2026-08-30:607": JSON.stringify({ instruction: "水漏れ確認", updatedAt: "2026-08-30T01:00:00.000Z" }),
  };
  const kv = { get: async (key) => (key in store ? store[key] : null) };
  const result = await readOverridesForDate(kv, "2026-08-30");
  assert.deepEqual(Object.keys(result).sort(), ["402", "607"]);
  assert.equal(result["402"].instruction, "タオル追加");
  assert.equal(result["607"].updatedAt, "2026-08-30T01:00:00.000Z");
});

await check("readOverridesForDate tolerates a KV get() that throws (treats as no override)", async () => {
  const kv = { get: async () => { throw new Error("kv down"); } };
  const result = await readOverridesForDate(kv, "2026-08-30");
  assert.deepEqual(result, {});
});

await check("readOverridesForDate tolerates malformed JSON / missing instruction field", async () => {
  const store = {
    "2026-08-30:401": "not-json{{{",
    "2026-08-30:402": JSON.stringify({ updatedAt: "2026-08-30T00:00:00.000Z" }), // no instruction
  };
  const kv = { get: async (key) => (key in store ? store[key] : null) };
  const result = await readOverridesForDate(kv, "2026-08-30");
  assert.deepEqual(result, {});
});

await check("mergeCleaningOverrides: no override -> effectiveInstruction falls back to source_instruction, hasOverride false", async () => {
  const result = mergeCleaningOverrides([baseRoomCheckin], {});
  assert.equal(result[0].effectiveInstruction, "");
  assert.equal(result[0].hasOverride, false);
  assert.equal(result[0].updatedAt, null);
});

await check("mergeCleaningOverrides: override present -> effectiveInstruction is the override value, hasOverride true, updatedAt populated", async () => {
  const overrides = { "402": { instruction: "追加タオル希望", updatedAt: "2026-08-30T02:00:00.000Z" } };
  const result = mergeCleaningOverrides([baseRoomCheckin], overrides);
  assert.equal(result[0].effectiveInstruction, "追加タオル希望");
  assert.equal(result[0].hasOverride, true);
  assert.equal(result[0].updatedAt, "2026-08-30T02:00:00.000Z");
  // original fields survive untouched
  assert.equal(result[0].room_number, "402");
  assert.equal(result[0].status, "CHECKIN");
});

await check("mergeCleaningOverrides: a room with room_number null (UNASSIGNED) is never looked up and never crashes", async () => {
  const overrides = { null: { instruction: "should never match", updatedAt: "x" } };
  const result = mergeCleaningOverrides([unassignedRoom], overrides);
  assert.equal(result[0].effectiveInstruction, "");
  assert.equal(result[0].hasOverride, false);
  assert.equal(result[0].room_number, null);
});

await check("mergeCleaningOverrides does not mutate its inputs", async () => {
  const rooms = [{ ...baseRoomCheckin }];
  mergeCleaningOverrides(rooms, { "402": { instruction: "x", updatedAt: "y" } });
  assert.equal(rooms[0].effectiveInstruction, undefined);
});

await check("mergeCleaningOverrides only applies an override to its matching room (multi-room)", async () => {
  const roomB = { ...baseRoomCheckin, room_number: "403" };
  const overrides = { "403": { instruction: "確認済み", updatedAt: "z" } };
  const result = mergeCleaningOverrides([baseRoomCheckin, roomB], overrides);
  assert.equal(result[0].hasOverride, false);
  assert.equal(result[1].hasOverride, true);
  assert.equal(result[1].effectiveInstruction, "確認済み");
});

await check("mergeCleaningOverrides on a non-array base gracefully returns []", async () => {
  assert.deepEqual(mergeCleaningOverrides(null, {}), []);
  assert.deepEqual(mergeCleaningOverrides(undefined, {}), []);
});

console.log(`\n${passed} cleaningOverrides checks passed`);
