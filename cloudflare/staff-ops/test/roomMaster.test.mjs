// roomMaster.js のテスト：18室固定・順序・存在しない部屋番号が含まれないこと。
import assert from "node:assert";
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const EXPECTED_ORDER = [
  "401", "402", "403", "404", "405", "406",
  "501", "502", "503", "504", "505", "507",
  "601", "602", "603", "604", "605", "607",
];

await check("KIRAKU_ROOM_ORDER has exactly 18 rooms", async () => {
  assert.equal(KIRAKU_ROOM_ORDER.length, 18);
});

await check("KIRAKU_ROOM_ORDER matches the exact canonical order", async () => {
  assert.deepEqual(KIRAKU_ROOM_ORDER, EXPECTED_ORDER);
});

await check("KIRAKU_ROOM_ORDER never contains a room number that doesn't exist (301-306/407/506/606)", async () => {
  const nonExistent = ["301", "302", "303", "304", "305", "306", "407", "506", "606"];
  for (const room of nonExistent) {
    assert.ok(!KIRAKU_ROOM_ORDER.includes(room), `${room} must not be in KIRAKU_ROOM_ORDER`);
  }
});

await check("KIRAKU_ROOM_ORDER has no duplicate entries", async () => {
  assert.equal(new Set(KIRAKU_ROOM_ORDER).size, KIRAKU_ROOM_ORDER.length);
});

console.log(`\n${passed} roomMaster checks passed`);
