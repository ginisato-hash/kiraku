// dailyOpsViewModel.js の純粋関数テスト（fixture JSONに対するshape/計算の正しさ）。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { buildDailyOpsViewModel, buildBookingRow, summarizeBookings, formatFreshness, formatDateJp }
  from "../public/dailyOpsViewModel.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const day = fixture.dates["2026-08-30"];
const emptyDay = fixture.dates["2026-08-31"];

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("formatDateJp renders Japanese date", async () => {
  assert.equal(formatDateJp("2026-08-30"), "2026年8月30日");
});

await check("formatDateJp handles missing/invalid input gracefully", async () => {
  assert.equal(formatDateJp(null), "—");
  assert.equal(formatDateJp("garbage"), "—");
});

await check("summarizeBookings computes room count / adults / children / total", async () => {
  const s = summarizeBookings(day.arrivals);
  assert.equal(s.roomCount, 2);
  assert.equal(s.adults, 3); // 2 + 1
  assert.equal(s.children, 1); // 1 + 0
  assert.equal(s.totalGuests, 4);
});

await check("summarizeBookings on empty list returns zeros, not crash", async () => {
  const s = summarizeBookings([]);
  assert.deepEqual(s, { roomCount: 0, adults: 0, children: 0, totalGuests: 0 });
});

await check("buildBookingRow handles null room_number by falling back to type label, never a blank dash", async () => {
  const row = buildBookingRow(day.arrivals[0]);
  assert.equal(row.roomNumber, null);
  assert.equal(row.hasRoomNumber, false);
  assert.equal(row.roomTypeLabel, "ツイン｜客室トイレ付");
});

await check("buildBookingRow handles missing arrival_time/phone/address/ota gracefully", async () => {
  const row = buildBookingRow(day.arrivals[1]); // 佐藤花子 has null ota_name/phone/address
  assert.equal(row.otaName, "Direct");
  assert.equal(row.arrivalTime, "15:00");
  assert.equal(row.phone, null);
  assert.equal(row.address, null);
});

await check("buildBookingRow falls back guest_name when absent", async () => {
  const row = buildBookingRow({});
  assert.equal(row.guestName, "氏名未取得");
  assert.equal(row.bookingId, "—");
});

await check("buildDailyOpsViewModel: full shape correctness against fixture", async () => {
  const vm = buildDailyOpsViewModel(day, "2026-08-30");
  assert.equal(vm.date, "2026-08-30");
  assert.equal(vm.dateLabelJp, "2026年8月30日");
  assert.equal(vm.arrivals.length, 2);
  assert.equal(vm.departures.length, 1);
  assert.equal(vm.stayovers.length, 1);
  assert.equal(vm.hasArrivals, true);
  assert.equal(vm.hasDepartures, true);
  assert.equal(vm.hasStayovers, true);

  const [arrivalsCard, departuresCard, stayoversCard] = vm.summaryCards;
  assert.equal(arrivalsCard.label, "本日の到着");
  assert.equal(arrivalsCard.roomCount, 2);
  assert.equal(arrivalsCard.totalGuests, 4);
  assert.equal(departuresCard.label, "本日の出発");
  assert.equal(departuresCard.roomCount, 1);
  assert.equal(stayoversCard.label, "連泊");
  assert.equal(stayoversCard.roomCount, 1);
});

await check("buildDailyOpsViewModel: empty day (0 arrivals/departures) does not crash and reports false flags", async () => {
  const vm = buildDailyOpsViewModel(emptyDay, "2026-08-31");
  assert.equal(vm.hasArrivals, false);
  assert.equal(vm.hasDepartures, false);
  assert.equal(vm.hasStayovers, false);
  assert.equal(vm.summaryCards[0].roomCount, 0);
});

await check("buildDailyOpsViewModel: missing dayData entirely does not crash", async () => {
  const vm = buildDailyOpsViewModel(null, "2026-09-01");
  assert.equal(vm.date, "2026-09-01");
  assert.equal(vm.arrivals.length, 0);
});

await check("formatFreshness: fresh vs stale (nowMs passed explicitly, no wall-clock dependency)", async () => {
  const generated = "2026-08-30T06:15:00+09:00";
  const nowFresh = new Date("2026-08-30T06:20:00+09:00").getTime(); // 5分後
  const fresh = formatFreshness(generated, nowFresh);
  assert.equal(fresh.stale, false);

  const nowStale = new Date("2026-08-30T07:00:00+09:00").getTime(); // 45分後
  const stale = formatFreshness(generated, nowStale);
  assert.equal(stale.stale, true);
});

await check("formatFreshness: missing generated_at_jst -> empty text, not a crash", async () => {
  const r = formatFreshness(null, Date.now());
  assert.equal(r.text, "");
  assert.equal(r.stale, false);
});

console.log(`\n${passed} dailyOpsViewModel checks passed`);
