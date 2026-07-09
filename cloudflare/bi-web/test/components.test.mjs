// components.js の軽量テスト（DOM生成の純粋関数）。
import assert from "node:assert";
import {
  renderMetricCard, renderCommandCenter, renderInsightBanner, renderStatusChips,
  renderDetails, renderErrorState, renderMonthSelector, renderHeader, renderDailyNewBookings,
  renderDailyNewBookingDetails,
} from "../public/components.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

await check("renderMetricCard includes tone and size classes", async () => {
  const html = renderMetricCard({ id: "x", label: "予約ペース", value: "グリーン", tone: "green", size: "hero" });
  assert.ok(html.includes("size-hero"));
  assert.ok(html.includes("tone-green"));
  assert.ok(html.includes("グリーン"));
});

await check("renderCommandCenter renders all cards", async () => {
  const html = renderCommandCenter([
    { id: "a", label: "A", value: "1", tone: "gray", size: "normal" },
    { id: "b", label: "B", value: "2", tone: "gray", size: "normal" },
  ]);
  assert.ok(html.includes("A") && html.includes("B"));
});

await check("renderInsightBanner uses tone class", async () => {
  const html = renderInsightBanner({ text: "テスト", tone: "amber" });
  assert.ok(html.includes("tone-amber"));
  assert.ok(html.includes("テスト"));
});

await check("renderStatusChips renders japanese labels", async () => {
  const html = renderStatusChips([{ label: "売上", value: "速報", tone: "green" }]);
  assert.ok(html.includes("売上"));
  assert.ok(html.includes("速報"));
});

await check("renderDetails renders accordion cards for each section", async () => {
  const html = renderDetails([
    { id: "breakeven", title: "損益分岐", summary: "会計BEP ¥100", rows: [["a", "b"]] },
  ], null, null);
  assert.ok(html.includes("損益分岐"));
  assert.ok(html.includes("会計BEP"));
  assert.ok(html.includes("<details"));
});

await check("renderErrorState does not leak internal paths or tokens", async () => {
  const html = renderErrorState();
  assert.ok(!html.toLowerCase().includes("token"));
  assert.ok(!html.includes("/Users/"));
  assert.ok(html.includes("refresh-beds24-bi"));
});

await check("renderMonthSelector renders japanese labels, not raw YYYY-MM", async () => {
  const html = renderMonthSelector({
    monthOptions: [{ value: "2026-07", label: "2026年7月" }, { value: "2026-08", label: "2026年8月" }],
    selectedMonth: "2026-07",
  });
  assert.ok(html.includes("2026年7月"));
  assert.ok(html.includes("2026年8月"));
  assert.ok(html.includes('value="2026-07"'));
  assert.ok(html.includes("対象月"));
});

await check("renderMonthSelector marks the selected option", async () => {
  const html = renderMonthSelector({
    monthOptions: [{ value: "2026-07", label: "2026年7月" }, { value: "2026-08", label: "2026年8月" }],
    selectedMonth: "2026-08",
  });
  assert.ok(html.includes('value="2026-08" selected'));
  assert.ok(!html.includes('value="2026-07" selected'));
});

await check("renderMonthSelector returns empty string when no months available", async () => {
  const html = renderMonthSelector({ monthOptions: [], selectedMonth: null });
  assert.equal(html, "");
});

await check("renderHeader includes monthSelectorHtml alongside pillHtml", async () => {
  const header = renderHeader({
    title: "喜らく 速報BI", targetMonth: "2026-07", generatedAtJst: "x",
    selectedMonth: "2026-07",
    monthOptions: [{ value: "2026-07", label: "2026年7月" }],
    statusPill: { label: "速報", tone: "blue" },
  });
  assert.ok(header.monthSelectorHtml.includes("2026年7月"));
  assert.ok(header.pillHtml.includes("速報"));
});

await check("renderDailyNewBookings returns HTML with count/revenue and tone class", async () => {
  const html = renderDailyNewBookings({
    label: "本日の新規予約", count: "3件", revenue: "¥84,000",
    helper: "JST今日作成された予約のみ", tone: "green", targetMonthLabel: "2026年8月宿泊分",
  });
  assert.ok(html.includes("daily-summary-strip"));
  assert.ok(html.includes("tone-green"));
  assert.ok(html.includes("本日の新規予約"));
  assert.ok(html.includes("3件 / ¥84,000"));
  assert.ok(html.includes("2026年8月宿泊分"));
});

await check("renderDailyNewBookings shows 判定不可 without a revenue slash", async () => {
  const html = renderDailyNewBookings({
    label: "本日の新規予約", count: "判定不可", revenue: "",
    helper: "Beds24の予約作成日時fieldを確認できません", tone: "amber", targetMonthLabel: "2026年8月宿泊分",
  });
  assert.ok(html.includes("tone-amber"));
  assert.ok(html.includes("判定不可"));
  assert.ok(!html.includes("判定不可 /"));
});

// ---------------- 本日新規予約 詳細drilldown ----------------
const sampleDetails = [
  { bookingId: "1", checkin: "2026-08-10", checkout: "2026-08-12", guestName: "Yamada Taro",
    revenue: "¥24,000", roomName: "201", createdAtJst: "2026-08-10T09:00:00+09:00" },
];

await check("renderDailyNewBookings renders as <details> with 詳細を見る when hasDetails", async () => {
  const html = renderDailyNewBookings({
    label: "本日の新規予約", count: "1件", revenue: "¥24,000",
    helper: "JST今日作成された予約のみ", tone: "green", targetMonthLabel: "2026年8月宿泊分",
    details: sampleDetails, hasDetails: true, detailsTitle: "本日新規予約一覧", detailsCta: "詳細を見る",
  });
  assert.ok(html.startsWith("<details"));
  assert.ok(html.includes("<summary"));
  assert.ok(html.includes("is-clickable"));
  assert.ok(html.includes("詳細を見る"));
  assert.ok(html.includes("本日新規予約一覧"));
});

await check("daily booking detail table shows checkin/checkout/guest name/amount", async () => {
  const html = renderDailyNewBookingDetails({ details: sampleDetails, detailsTitle: "本日新規予約一覧" });
  assert.ok(html.includes("2026-08-10"));
  assert.ok(html.includes("2026-08-12"));
  assert.ok(html.includes("Yamada Taro"));
  assert.ok(html.includes("¥24,000"));
  assert.ok(html.includes("チェックイン"));
  assert.ok(html.includes("チェックアウト"));
  assert.ok(html.includes("宿泊者名"));
});

await check("renderDailyNewBookings does not render a booking list when hasDetails is false (0件)", async () => {
  const html = renderDailyNewBookings({
    label: "本日の新規予約", count: "0件", revenue: "¥0",
    helper: "本日、選択月の新規予約はまだありません", tone: "neutral", targetMonthLabel: "2026年8月宿泊分",
    details: [], hasDetails: false,
  });
  assert.ok(!html.includes("<details"));
  assert.ok(!html.includes("daily-booking-list"));
  assert.ok(html.includes("まだありません"));
});

await check("renderDailyNewBookings shows unavailable note when details cannot be determined", async () => {
  const html = renderDailyNewBookings({
    label: "本日の新規予約", count: "判定不可", revenue: "",
    helper: "Beds24の予約作成日時fieldを確認できません", tone: "amber", targetMonthLabel: "2026年8月宿泊分",
    details: [], hasDetails: false,
    detailsUnavailableNote: "予約作成日時を確認できないため、詳細を表示できません",
  });
  assert.ok(!html.includes("<details"));
  assert.ok(html.includes("予約作成日時を確認できないため、詳細を表示できません"));
});

console.log(`\n${passed} components checks passed`);
