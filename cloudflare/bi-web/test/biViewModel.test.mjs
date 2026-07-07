// biViewModel.js の軽量テスト（node実行・wrangler不要）。
import assert from "node:assert";
import { buildBiViewModel, _internal } from "../public/biViewModel.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const baseSnapshot = {
  month: "2026-07",
  beds24_stay_month_revenue_excluding_cancelled: 1057943,
  cash_operating_breakeven_revenue: 2009657,
  cash_operating_breakeven_achievement_rate: 0.2536, // 大幅未達水準
  cash_revenue_gap_to_breakeven: 951714,
  required_remaining_revenue_per_day: 41379,
  gop_after_mc: -695941,
  booking_pace_status: "green",
  booking_pace_label: "グリーン",
  booking_pace_reason: "現時点の予約済み売上がキャッシュBEP以上です。",
  revenue_data_status: "速報",
  breakeven_model_status: "大幅未達",
  pace_model_status: "推計",
  labor_model_status: "推計",
  debt_service_status: "予定表未投入",
  opening_balance_status: "会計士確定",
  // deprecated（primary表示には使わない）
  breakeven_revenue_current_structure: 999999,
};

await check("buildBiViewModel returns 7 primaryCards", async () => {
  const vm = buildBiViewModel(baseSnapshot, { generated_at_jst: "2026-07-08T00:00:00+09:00" });
  assert.equal(vm.primaryCards.length, 7);
});

await check("primaryCards do not use deprecated fields", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const json = JSON.stringify(vm.primaryCards);
  assert.ok(!json.includes("999999"), "primaryCards must not surface breakeven_revenue_current_structure value");
});

await check("debt_service_status 予定表未投入 produces a note", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const hasNote = vm.notes.some((n) => n.text.includes("返済予定表未投入"));
  assert.ok(hasNote);
});

await check("booking_pace_status green renders グリーン label", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const paceCard = vm.primaryCards.find((c) => c.label === "予約ペース");
  assert.equal(paceCard.value, "グリーン");
});

await check("大幅未達 achievement + green pace both surface (not contradictory)", async () => {
  const vm = buildBiViewModel(baseSnapshot, {});
  const achievementCard = vm.primaryCards.find((c) => c.label === "達成率");
  const paceCard = vm.primaryCards.find((c) => c.label === "予約ペース");
  assert.equal(achievementCard.badge, "大幅未達");
  assert.equal(paceCard.value, "グリーン");
  // 両方が同時にビューモデルに存在する（矛盾として扱われていない）
  assert.ok(achievementCard && paceCard);
});

await check("missing values render as —", async () => {
  const sparse = { month: "2026-08" };
  const vm = buildBiViewModel(sparse, {});
  const revCard = vm.primaryCards.find((c) => c.label === "速報売上");
  assert.equal(revCard.value, "—");
  const paceCard = vm.primaryCards.find((c) => c.label === "予約ペース");
  assert.equal(paceCard.value, "判定不可");
});

await check("_internal helpers format yen/pct correctly", async () => {
  assert.equal(_internal.yen(1000), "¥1,000");
  assert.equal(_internal.yen(null), "—");
  assert.equal(_internal.pct(0.5), "50.0%");
});

console.log(`\n${passed} biViewModel checks passed`);
