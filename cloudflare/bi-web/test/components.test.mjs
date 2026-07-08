// components.js の軽量テスト（DOM生成の純粋関数）。
import assert from "node:assert";
import {
  renderMetricCard, renderCommandCenter, renderInsightBanner, renderStatusChips,
  renderDetails, renderErrorState, renderMonthSelector, renderHeader,
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

console.log(`\n${passed} components checks passed`);
