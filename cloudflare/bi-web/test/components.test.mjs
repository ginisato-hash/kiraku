// components.js の軽量テスト（DOM生成の純粋関数）。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import {
  renderMetricCard, renderCommandCenter, renderInsightBanner, renderStatusChips,
  renderDetails, renderErrorState, renderMonthSelector, renderHeader, renderDailyNewBookings,
  renderDailyNewBookingDetails, renderRoomTypeOccupancyChart, renderRoomTypeRevenueMix,
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

// ---------------- 部屋タイプ別 日別稼働率グラフ ----------------
const sampleChart = {
  title: "部屋タイプ別 日別稼働率",
  helper: "選択月の日別推移。キャンセル除外、月跨ぎ按分。",
  hasData: true,
  dates: ["2026-08-01", "2026-08-02"],
  lines: [
    { label: "シングル", color: "#2f6fed", points: [50.0, 0.0] },
    { label: "ツイン", color: "#e2725b", points: [30.0, 60.0] },
  ],
  warnings: [],
};

await check("renderRoomTypeOccupancyChart renders an SVG with a polyline per room type series", async () => {
  const html = renderRoomTypeOccupancyChart(sampleChart);
  assert.ok(html.includes("<svg"));
  assert.ok(html.includes("部屋タイプ別 日別稼働率"));
  const polylineCount = (html.match(/<polyline/g) || []).length;
  assert.equal(polylineCount, 2);
  assert.ok(html.includes("#2f6fed"));
  assert.ok(html.includes("#e2725b"));
});

await check("renderRoomTypeOccupancyChart legend shows room type labels", async () => {
  const html = renderRoomTypeOccupancyChart(sampleChart);
  assert.ok(html.includes("chart-legend"));
  assert.ok(html.includes("シングル"));
  assert.ok(html.includes("ツイン"));
});

await check("renderRoomTypeOccupancyChart shows データなし when hasData is false", async () => {
  const html = renderRoomTypeOccupancyChart({ title: "部屋タイプ別 日別稼働率", hasData: false, dates: [], lines: [] });
  assert.ok(html.includes("データなし"));
  assert.ok(!html.includes("<svg"));
});

await check("renderRoomTypeOccupancyChart surfaces warnings when present", async () => {
  const html = renderRoomTypeOccupancyChart({
    ...sampleChart, warnings: ["2026-08-12 ツインの稼働率が100%を超えています: sold=4 available=3"],
  });
  assert.ok(html.includes("稼働率が100%を超えています"));
});

// ---------------- 部屋タイプ別 売上構成 ----------------
const sampleMix = {
  title: "部屋タイプ別 売上構成",
  hasData: true,
  rows: [
    { roomTypeLabel: "ツイン（トイレ）", revenue: "¥600,000", share: "62.5%", sharePercent: 62.5,
      soldRoomNights: "35泊", adr: "¥17,143" },
    { roomTypeLabel: "シングル", revenue: "¥120,000", share: "12.5%", sharePercent: 12.5,
      soldRoomNights: "10泊", adr: "¥12,000" },
  ],
};

await check("renderRoomTypeRevenueMix shows revenue/share/sold nights/ADR per room type", async () => {
  const html = renderRoomTypeRevenueMix(sampleMix);
  assert.ok(html.includes("部屋タイプ別 売上構成"));
  assert.ok(html.includes("ツイン（トイレ）"));
  assert.ok(html.includes("¥600,000"));
  assert.ok(html.includes("62.5%"));
  assert.ok(html.includes("35泊"));
  assert.ok(html.includes("¥17,143"));
});

await check("renderRoomTypeRevenueMix bar width reflects sharePercent", async () => {
  const html = renderRoomTypeRevenueMix(sampleMix);
  assert.ok(html.includes("width:62.5%"));
  assert.ok(html.includes("width:12.5%"));
});

await check("renderRoomTypeRevenueMix shows データなし when hasData is false", async () => {
  const html = renderRoomTypeRevenueMix({ title: "部屋タイプ別 売上構成", hasData: false, rows: [] });
  assert.ok(html.includes("データなし"));
  assert.ok(!html.includes("revenue-mix-row"));
});

// ---------------- 2列コンパクトレイアウト ----------------
await check("renderCommandCenter renders exactly one metric-card per primary card (8 cards)", async () => {
  const cards = Array.from({ length: 8 }, (_, i) => (
    { id: `c${i}`, label: `L${i}`, value: `V${i}`, tone: "gray", size: "normal" }
  ));
  const html = renderCommandCenter(cards);
  const count = (html.match(/class="metric-card/g) || []).length;
  assert.equal(count, 8);
  assert.match(html, /ADR|L\d/); // sanity: content renders at all
});

await check("styles.css defines a 2-column command-center grid on both PC/tablet and mobile (2列×4段, no 1-column fallback)", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  assert.match(css, /\.command-center\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  // 運用上ほぼ携帯で見るため、モバイルも1列に落とさず2列を維持する
  assert.match(css, /@media \(max-width: 640px\)\s*\{\s*\.command-center\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  // 過去のroom-type-section overflowの再発防止: grid itemに対するmin-width:0が必須
  assert.match(css, /\.command-center\s*>\s*\*\s*\{\s*min-width:\s*0/);
});

// ---------------- 予約ペース評価カード(2列×4段グリッド統一) ----------------
await check("予約ペース評価 renders wrapped in the same .metric-card frame as other KPI cards", async () => {
  const html = renderCommandCenter([
    { id: "booking-pace", label: "予約ペース評価", value: "グリーン", tone: "green", size: "large",
      helper: "予約ペースはグリーンです。" },
    { id: "beds24-revenue", label: "速報売上", value: "¥1,200,000", tone: "blue", size: "large" },
  ]);
  assert.match(html, /<div class="metric-card[^"]*">\s*<div class="metric-label">予約ペース評価<\/div>/);
  assert.ok(!html.includes("size-hero"), "no card should use the removed full-width hero size");
});

await check("all 8 primary cards (including 予約ペース評価) render as span-1 half-width cards", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  assert.ok(!css.includes("size-hero"), "hero (full-width) card size should be fully removed, not just unused");
  assert.match(css, /\.metric-card\.size-large\s*\{\s*grid-column:\s*span 1/);
  assert.match(css, /\.metric-card\.size-normal\s*\{\s*grid-column:\s*span 1/);
});

// ---------------- グラフ系パネルは全幅維持 ----------------
await check("room-type occupancy chart and revenue mix stay full-width (stacked, not squeezed to half)", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  assert.match(css, /\.room-type-section\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)[^}]*\}/);
});

await check("room-type-section has no mobile-only override that would change it away from full-width", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  // room-type-sectionは常時1列(全幅)。過去にあった900px境界の2列/1列切替は撤廃済み。
  assert.ok(!/@media[^{]*\{\s*\.room-type-section\s*\{[^}]*grid-template-columns:\s*1fr 1fr/.test(css));
});

// ---------------- モバイルの高密度化・横overflow防止 ----------------
await check("styles.css keeps html/body overflow-x hidden with max-width 100% as a defensive guard", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  assert.match(css, /html,\s*body\s*\{[^}]*max-width:\s*100%[^}]*overflow-x:\s*hidden/);
});

await check("mobile media query compacts metric-card padding/font-size without dropping the 2-column grid", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  const mobileBlock = css.slice(css.indexOf("@media (max-width: 640px) {\n  .metric-card {"));
  assert.match(mobileBlock, /min-height:\s*118px/);
  assert.match(mobileBlock, /padding:\s*10px 10px/);
});

await check("daily-booking-table switches to a stacked block layout (no horizontal table) under 640px", async () => {
  const css = readFileSync(new URL("../public/styles.css", import.meta.url), "utf-8");
  assert.match(css, /@media \(max-width: 640px\)\s*\{\s*\.daily-booking-table thead\s*\{\s*display:\s*none/);
  // 旧: 720pxでdisplay:block+overflow-x:autoにして横スクロールさせていた実装は撤廃済み
  assert.ok(!/\.daily-booking-table\s*\{\s*display:\s*block;\s*overflow-x:\s*auto/.test(css));
});

console.log(`\n${passed} components checks passed`);
