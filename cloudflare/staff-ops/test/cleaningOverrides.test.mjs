// cleaningOverrides.js の純粋関数テスト（buildOverrideKey / mergeCleaningOverrides）。
import assert from "node:assert";
import { buildOverrideKey, mergeCleaningOverrides } from "../src/cleaningOverrides.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const baseRoom = {
  room_type_key: "twin_toilet",
  room_type_label: "ツイン｜客室トイレ付",
  room_number: null,
  state: "TURNOVER",
  checkout_booking_id: "89381500",
  checkin_booking_id: "89381508",
  adults: 2, children: 1, total_guests: 3, notes: null,
};

await check("buildOverrideKey is deterministic and stable", async () => {
  const k1 = buildOverrideKey(baseRoom);
  const k2 = buildOverrideKey({ ...baseRoom });
  assert.equal(k1, k2);
  assert.equal(k1, "twin_toilet:89381500:89381508");
});

await check("buildOverrideKey handles missing booking ids gracefully", async () => {
  const k = buildOverrideKey({ room_type_key: "single_no_toilet", checkout_booking_id: null, checkin_booking_id: undefined });
  assert.equal(k, "single_no_toilet::");
});

await check("mergeCleaningOverrides: override absent -> room unchanged", async () => {
  const result = mergeCleaningOverrides([baseRoom], {});
  assert.deepEqual(result[0], baseRoom);
});

await check("mergeCleaningOverrides: matching override shallow-merges fields on top", async () => {
  const key = buildOverrideKey(baseRoom);
  const overrides = { [key]: { room_number: "12", notes: "水漏れ確認済み" } };
  const result = mergeCleaningOverrides([baseRoom], overrides);
  assert.equal(result[0].room_number, "12");
  assert.equal(result[0].notes, "水漏れ確認済み");
  // untouched fields survive
  assert.equal(result[0].state, "TURNOVER");
  assert.equal(result[0].room_type_key, "twin_toilet");
});

await check("mergeCleaningOverrides: override clears a field via explicit deletion upstream", async () => {
  // The POST handler deletes a key from the stored record when value===null,
  // so from the merge function's perspective a cleared field is simply
  // absent from the override record — confirm merge doesn't reintroduce it.
  const key = buildOverrideKey(baseRoom);
  const overrides = { [key]: { notes: undefined } };
  const result = mergeCleaningOverrides([{ ...baseRoom, notes: "old note" }], overrides);
  assert.equal(result[0].notes, undefined);
});

await check("mergeCleaningOverrides strips bookkeeping fields (updated_by/updated_at) from visible output", async () => {
  const key = buildOverrideKey(baseRoom);
  const overrides = { [key]: { room_number: "5", updated_by: "s_sato@yuge-zao.com", updated_at: "2026-08-30T00:00:00Z" } };
  const result = mergeCleaningOverrides([baseRoom], overrides);
  assert.equal(result[0].room_number, "5");
  assert.equal(result[0].updated_by, undefined);
  assert.equal(result[0].updated_at, undefined);
});

await check("mergeCleaningOverrides does not mutate its inputs", async () => {
  const rooms = [{ ...baseRoom }];
  const key = buildOverrideKey(baseRoom);
  const overrides = { [key]: { room_number: "9" } };
  mergeCleaningOverrides(rooms, overrides);
  assert.equal(rooms[0].room_number, null);
});

await check("mergeCleaningOverrides only applies overrides to their matching room (multi-room)", async () => {
  const roomB = { ...baseRoom, room_type_key: "single_no_toilet", checkin_booking_id: "89381999", checkout_booking_id: null };
  const overrides = { [buildOverrideKey(roomB)]: { room_number: "3" } };
  const result = mergeCleaningOverrides([baseRoom, roomB], overrides);
  assert.equal(result[0].room_number, null);
  assert.equal(result[1].room_number, "3");
});

console.log(`\n${passed} cleaningOverrides checks passed`);
