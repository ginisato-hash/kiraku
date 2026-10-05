// htmlEscaping.test.mjs — スナップショット/manifest由来の文字列がHTMLとして解釈されないこと。
// この画面は管理ページと同一originで配信されるため、データ中の "<" や引用符が
// そのままタグ・属性になる（stored XSS）経路を塞ぐ。
//
// 方法: 全render関数の「文字列フィールド全部」に攻撃文字列を入れて描画し、
//   1) 生成HTMLのタグ/属性の骨格が、無害な文字列を入れた場合と完全に同一であること
//      （タグ・属性が増えていない = 注入も属性破壊も起きていない）
//   2) 整形式タグを除いた残りに "<" ">" が一つも残らないこと（<img ...> 等が生で出ていない）
//   3) 攻撃文字列がエスケープされた形でテキストとして表示されていること
// を検証する。さらに buildBiViewModel を通したデータ起点の経路でも確認する。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  escapeHtml, renderMetricCard, renderCommandCenter, renderInsightBanner, renderStatusChips,
  renderNotes, renderDetails, renderDetailCard, renderMonthSelector, renderHeader,
  renderDailySummaryCard, renderDailySummaryDetails, renderDailySummarySection,
  renderRoomTypeOccupancyChart, renderRoomTypeRevenueMix,
} from "../public/components.js";
import { buildBiViewModel } from "../public/biViewModel.js";

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

const PAYLOAD_IMG = "<img src=x onerror=alert(1)>";
const PAYLOAD_META = "\"><meta http-equiv=refresh content=\"0;url=//x.example\">";
const PAYLOAD_QUOTE = "O'Brien' onmouseover='alert(1)' \" autofocus=\"";
const PAYLOADS = [PAYLOAD_IMG, PAYLOAD_META, PAYLOAD_QUOTE, "a&b &lt;already&gt;"];
const BENIGN = "x";

// ---- 生成HTMLのタグ/属性の骨格（タグ名 + 属性名の列）を取り出す小さなトークナイザ ----
const TAG_RE = /<(\/?)([a-zA-Z][a-zA-Z0-9-]*)((?:\s+[^\s"'<>\/=]+(?:="[^"]*")?)*)\s*(\/?)>/g;
const ATTR_RE = /\s+([^\s"'<>\/=]+)(?:="[^"]*")?/g;

function skeleton(html) {
  const tags = [];
  const text = html.replace(TAG_RE, (_m, close, name, attrs) => {
    const attrNames = [...attrs.matchAll(ATTR_RE)].map((a) => a[1]);
    tags.push(`${close}${name.toLowerCase()}[${attrNames.join(",")}]`);
    return "\u0000"; // タグ位置の目印
  });
  return { tags, text };
}

function assertInert(render, label) {
  const benign = skeleton(render(BENIGN));
  for (const payload of PAYLOADS) {
    const html = render(payload);
    const sk = skeleton(html);
    assert.deepEqual(sk.tags, benign.tags, `${label}: tag/attribute structure changed with payload ${JSON.stringify(payload)}`);
    assert.ok(!sk.text.includes("<") && !sk.text.includes(">"),
      `${label}: raw angle bracket outside well-formed tags with payload ${JSON.stringify(payload)}`);
    assert.ok(!/<img|<meta|<script/i.test(html), `${label}: raw dangerous tag in output`);
  }
}

function assertPayloadShownAsText(render, label) {
  const html = render(PAYLOAD_IMG);
  assert.ok(html.includes("&lt;img src=x onerror=alert(1)&gt;"), `${label}: payload must be displayed escaped`);
  const html2 = render(PAYLOAD_META);
  assert.ok(html2.includes("&quot;&gt;&lt;meta http-equiv=refresh"), `${label}: quote/meta payload must be displayed escaped`);
}

// ---- 各render関数の入力（文字列フィールドはすべて s で埋める。数値・真偽値は現実的な値） ----
const metaCard = (s) => ({
  id: s, label: s, value: s, tone: s, size: s, badge: s, helper: s, note: s,
  meta: [{ label: s, value: s }],
});
const bookingDetail = (s, { withChange = true } = {}) => ({
  guestName: s, otaName: s, checkin: s, checkout: s, roomType: s, revenue: s,
  roomChangeSummary: s, hasRoomChange: withChange,
  roomChangeHistory: [{ changedAt: s, fromRoomType: s, toRoomType: s, rawNote: s }],
});
const dailyCard = (s, overrides = {}) => ({
  label: s, subLabel: s, dateLabel: s, count: s, revenue: s, helper: s, tone: s,
  hasDetails: true, detailsCta: s, detailsTitle: s, details: [bookingDetail(s)], ...overrides,
});
const occupancyChart = (s) => ({
  hasData: true, title: s, helper: s, dates: [s, s],
  lines: [{ label: s, color: s, points: [10, 40] }, { label: s, color: s, points: [20, 60] }],
  warnings: [s, s],
});
const revenueMix = (s) => ({
  hasData: true, title: s,
  rows: [{ roomTypeLabel: s, revenue: s, share: s, sharePercent: 50, soldRoomNights: s, adr: s }],
});
const detailSections = (s) => [
  { id: "validation", title: s, summary: s, rows: [[s, s], [s, s]] },
  { id: "other", title: s, summary: s, rows: [[s, s]] },
];
const header = (s) => ({
  title: s, targetMonth: s, statusPill: { tone: s, label: s },
  monthOptions: [{ value: s, label: s }, { value: "2026-07", label: s }], selectedMonth: s,
});

const RENDERERS = {
  renderMetricCard: (s) => renderMetricCard(metaCard(s)),
  renderCommandCenter: (s) => renderCommandCenter([metaCard(s), metaCard(s)]),
  renderInsightBanner: (s) => renderInsightBanner({ tone: s, text: s }),
  renderStatusChips: (s) => renderStatusChips([{ label: s, value: s, tone: s }]),
  renderNotes: (s) => renderNotes([{ tone: s, text: s }]),
  renderDetails: (s) => renderDetails(detailSections(s), { ok: false, criticalCount: s, warningCount: s }, s),
  renderDetailCard: (s) => renderDetailCard(detailSections(s)[0]),
  renderMonthSelector: (s) => renderMonthSelector(header(s)),
  "renderHeader.pillHtml": (s) => renderHeader(header(s)).pillHtml,
  "renderHeader.monthSelectorHtml": (s) => renderHeader(header(s)).monthSelectorHtml,
  "renderDailySummaryCard (details)": (s) => renderDailySummaryCard(dailyCard(s)),
  "renderDailySummaryCard (no details)": (s) => renderDailySummaryCard(
    dailyCard(s, { hasDetails: false, details: [], detailsUnavailableNote: s })),
  "renderDailySummaryDetails (room change)": (s) => renderDailySummaryDetails(dailyCard(s)),
  "renderDailySummaryDetails (no room change)": (s) => renderDailySummaryDetails(
    dailyCard(s, { details: [bookingDetail(s, { withChange: false })] })),
  renderDailySummarySection: (s) => renderDailySummarySection([dailyCard(s), dailyCard(s, { hasDetails: false, details: [] })]),
  renderRoomTypeOccupancyChart: (s) => renderRoomTypeOccupancyChart(occupancyChart(s)),
  "renderRoomTypeOccupancyChart (empty)": (s) => renderRoomTypeOccupancyChart({ hasData: false, title: s }),
  renderRoomTypeRevenueMix: (s) => renderRoomTypeRevenueMix(revenueMix(s)),
  "renderRoomTypeRevenueMix (empty)": (s) => renderRoomTypeRevenueMix({ hasData: false, title: s }),
};

// ---------------------------------------------------------------- escapeHtml
await check("escapeHtml escapes & < > \" ' and coerces non-strings", async () => {
  assert.equal(escapeHtml("<a href=\"x\">'&'</a>"), "&lt;a href=&quot;x&quot;&gt;&#39;&amp;&#39;&lt;/a&gt;");
  assert.equal(escapeHtml(PAYLOAD_IMG), "&lt;img src=x onerror=alert(1)&gt;");
  assert.equal(escapeHtml(12345), "12345");
  assert.equal(escapeHtml("¥1,200 ｜ ADR 9,000"), "¥1,200 ｜ ADR 9,000", "plain Japanese/number text is unchanged");
  assert.equal(escapeHtml("a&amp;b"), "a&amp;amp;b", "already-escaped input is escaped again (no double-decode trust)");
});

// ---------------------------------------------------------------- every render function
for (const [name, render] of Object.entries(RENDERERS)) {
  await check(`${name}: hostile strings in every field never become tags or attributes`, async () => {
    assertInert(render, name);
  });
}

for (const name of Object.keys(RENDERERS).filter((n) => !n.includes("(empty)"))) {
  await check(`${name}: hostile strings are displayed as escaped text`, async () => {
    // renderHeader.pillHtml 等も含め、文字列を表示する全関数で、エスケープされた形で出ていること。
    assertPayloadShownAsText(RENDERERS[name], name);
  });
}

await check("escaped output is still the same as before for plain text (no visible change)", async () => {
  const html = renderMetricCard({ label: "予約ペース", value: "¥1,234 / 56%", tone: "green", size: "hero" });
  assert.ok(html.includes("<div class=\"metric-label\">予約ペース</div>"));
  assert.ok(html.includes("<div class=\"metric-value\">¥1,234 / 56%</div>"));
  assert.ok(html.includes("size-hero") && html.includes("tone-green"));
});

await check("renderHeader metaLine/title stay plain text (app.js assigns them via textContent)", async () => {
  const h = renderHeader(header("a&b"));
  assert.equal(h.metaLine, "対象月: a&b");
  assert.equal(h.title, "a&b");
  const appJs = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../public/app.js"), "utf-8");
  assert.ok(/getElementById\("header-meta"\)\.textContent\s*=/.test(appJs),
    "header-meta must be written with textContent, never innerHTML, with the unescaped metaLine");
});

// ---------------------------------------------------------------- data -> view model -> HTML
await check("snapshot data with hostile guest/OTA/room/date/note strings renders inert HTML end-to-end", async () => {
  const build = (s) => {
    const detail = {
      booking_id: "1", checkin: s, checkout: s, guest_name: s, revenue: 12000,
      ota_name: s, room_type: s, room_type_key: "k", room_change_history_status: "ok",
      room_change_history: [{ changed_at: s, from_room_type: s, to_room_type: s, changed_by: s, raw_note: s }],
    };
    const snapshot = {
      month: "2026-07",
      daily_global_summary: {
        today_new_bookings: { date_jst: "2026-07-18", status: "ok", count: 1, revenue: 12000, details: [detail] },
        yesterday_new_bookings: { date_jst: "2026-07-17", status: "ok", count: 1, revenue: 12000, details: [detail] },
        today_checkins: { date_jst: "2026-07-18", status: "ok", count: 1, revenue: 12000, details: [detail] },
      },
    };
    const vm = buildBiViewModel(snapshot, {});
    assert.equal(vm.dailySummaryCards[0].details[0].guestName, s, "view model passes data through unchanged (escaping is the renderer's job)");
    return renderDailySummarySection(vm.dailySummaryCards);
  };
  assertInert(build, "snapshot->daily summary");
  assertPayloadShownAsText(build, "snapshot->daily summary");
});

await check("manifest month options with hostile values render inert HTML end-to-end", async () => {
  const build = (s) => {
    const vm = buildBiViewModel({ month: "2026-07" },
      { available_months: ["2026-07", s], default_month: "2026-07" }, null, null, { selectedMonth: "2026-07" });
    const h = renderHeader(vm.header);
    return h.monthSelectorHtml + h.pillHtml;
  };
  assertInert(build, "manifest->month selector");
});

// ---------------------------------------------------------------- app.js does not build HTML from data itself
await check("app.js never interpolates data into an innerHTML/outerHTML template literal", async () => {
  const appJs = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../public/app.js"), "utf-8");
  assert.ok(!/(?:innerHTML|outerHTML)\s*=\s*`[^`]*\$\{/.test(appJs), "template literal with ${...} assigned to innerHTML/outerHTML");
  assert.ok(!/insertAdjacentHTML|document\.write/.test(appJs));
});

await check("components.js: every ${...} that carries data goes through e()/escapeHtml (allowlist of the rest)", async () => {
  const src = readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "../public/components.js"), "utf-8");
  // コメント行を除いて、e()を通していない補間を列挙し、数値/定数/既にエスケープ済みの断片だけが残ることを固定する。
  const code = src.split("\n").filter((l) => !l.trim().startsWith("//")).join("\n");
  // 最内側の ${...}（波括弧を含まないもの）だけを見る。外側の入れ子（.map(... `...${e(x)}...`) 等）は
  // 内側の補間で個別に検査される。
  const unescaped = [...code.matchAll(/\$\{([^{}]*)\}/g)].map((m) => m[1].trim()).filter((x) => !x.startsWith("e("));
  const ALLOWED = new Set([
    // 既にエスケープ済みの断片・定数・内部で組み立てたHTML
    "badge", "helper", "note", "metaListHtml(card.meta)", "when", "from", "to", "items", "renderRoomChangeBlock(d)",
    "cards", "card.count", "card.revenue", "subLabelHtml", "dateHtml", "cta", "stripInner", "unavailable",
    "clickableClass", "renderDailySummaryDetails(card)", "(cards || []).map(renderDailySummaryCard).join(\"\")",
    // SVG座標など数値のみ
    "pad.left", "yy", "w - pad.right", "pad.left - 6", "yy + 4", "v", "px(i)", "h - 6", "py(v)", "points", "w", "h",
    "gridLines", "lines", "dateLabels", "legend", "warningItems", "warningsHtml",
    "Math.max(0, Math.min(r.sharePercent, 100))", "rows", "summary", "detailTableHtml(section.rows)",
    "extraNoteHtml || \"\"", "optHtml", "messageClass", "label", "disabled", "message",
    "s === \"loading\" ? label : REFRESH_BUTTON_LABELS.idle",
    "o.value === header.selectedMonth ? \" selected\" : \"\"",
    "validationSummary.ok ? \"検証OK\" : \"要確認 \" + validationSummary.criticalCount + \"件\"", // renderDetailCardでエスケープされる
    "header.targetMonth", // metaLine: textContent専用のプレーンテキスト
    "note",
  ]);
  const unexpected = unescaped.filter((x) => !ALLOWED.has(x));
  assert.deepEqual(unexpected, [], `new un-escaped interpolation(s) in components.js: ${JSON.stringify(unexpected)}`);
});

console.log(`\n${passed} htmlEscaping checks passed`);
