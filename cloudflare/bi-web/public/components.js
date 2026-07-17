// 喜らく 速報BI — DOM生成コンポーネント（純粋関数。副作用なし）。
// app.js はこれらを呼んでinnerHTMLへ差し込むだけにする。

function metaListHtml(meta) {
  if (!meta || !meta.length) return "";
  return `<div class="metric-meta-list">${meta.map(
    (m) => `<span class="meta-item">${m.label} <b>${m.value}</b></span>`).join("")}</div>`;
}

export function renderMetricCard(card) {
  const badge = card.badge
    ? `<span class="state-badge tone-${card.tone}"><span class="dot"></span>${card.badge}</span>` : "";
  const helper = card.helper ? `<div class="metric-helper">${card.helper}</div>` : "";
  const note = card.note ? `<div class="metric-note">${card.note}</div>` : "";
  return `<div class="metric-card size-${card.size || "normal"} tone-${card.tone || "gray"}">
    <div class="metric-label">${card.label}</div>
    <div class="metric-value">${card.value}</div>
    ${badge}
    ${helper}
    ${metaListHtml(card.meta)}
    ${note}
  </div>`;
}

export function renderCommandCenter(primaryCards) {
  return primaryCards.map(renderMetricCard).join("");
}

// 部屋変更履歴ブロック。取得不可(roomChangeSummary=null)の場合は何も表示しない。
// 履歴を確認できて0件なら「部屋変更なし」、1件以上あれば<details>に折り畳んで表示する。
function renderRoomChangeBlock(d) {
  if (!d.roomChangeSummary) return "";
  if (d.hasRoomChange) {
    const items = d.roomChangeHistory.map((c) => {
      const when = c.changedAt ? `${c.changedAt} ` : "";
      const from = c.fromRoomType || "?";
      const to = c.toRoomType || "?";
      const note = c.rawNote ? `（${c.rawNote}）` : "";
      return `<li>${when}${from} → ${to}${note}</li>`;
    }).join("");
    return `<details class="room-change-details">
      <summary>${d.roomChangeSummary}</summary>
      <ul class="room-change-list">${items}</ul>
    </details>`;
  }
  return `<div class="daily-booking-detail-room-change">${d.roomChangeSummary}</div>`;
}

// 日次サマリーカード1枚分の予約明細一覧。PII(email/phone/address等)は含めない。
// PCでは1件=カード内2カラム(左:宿泊者/OTA/CI-CO、右:部屋タイプ/金額/部屋変更)で右側スペースを
// 活用し、モバイルでは縦積みにする(table形式は情報量が増えて窮屈になったため廃止)。
export function renderDailySummaryDetails(card) {
  const cards = (card.details || []).map((d) => `<article class="daily-booking-detail-card">
      <div class="daily-booking-detail-main">
        <div class="daily-booking-detail-guest">${d.guestName}<span class="daily-booking-detail-ota"> / ${d.otaName}</span></div>
        <div class="daily-booking-detail-dates">CI ${d.checkin} → CO ${d.checkout}</div>
      </div>
      <div class="daily-booking-detail-sub">
        <div class="daily-booking-detail-room">部屋: ${d.roomType}</div>
        <div class="daily-booking-detail-amount">${d.revenue}</div>
        ${renderRoomChangeBlock(d)}
      </div>
    </article>`).join("");
  return `<div class="daily-booking-list">
    <h3>${card.detailsTitle || "予約一覧"}</h3>
    <div class="daily-booking-detail-list">${cards}</div>
  </div>`;
}

// 日次サマリーの1カード（本日の新規予約／前日の新規予約／本日のチェックイン、いずれも
// 月選択に依らないグローバル集計）。hasDetails=trueの場合はクリックで対象予約一覧を
// 展開できる(ネイティブ<details>/<summary>でキーボード操作・スクリーンリーダー対応を
// 確保しつつ、明示的にaria-expandedも付与する。app.js側でtoggleイベントに合わせて更新)。
export function renderDailySummaryCard(card) {
  const valueText = card.revenue ? `${card.count} / ${card.revenue}` : card.count;
  const subLabelHtml = card.subLabel ? `<p class="daily-summary-sublabel">${card.subLabel}</p>` : "";
  const dateHtml = (card.dateLabel && card.dateLabel !== "—")
    ? `<p class="daily-summary-date">対象日: ${card.dateLabel}</p>` : "";
  const helperLine = card.helper || "";
  const clickableClass = card.hasDetails ? " is-clickable" : "";
  const cta = card.hasDetails
    ? `<span class="daily-summary-cta">${card.detailsCta || "詳細を見る"}</span>` : "";

  const stripInner = `<div>
      <p class="eyebrow">${card.label}</p>
      ${subLabelHtml}
      ${dateHtml}
      <p class="daily-summary-value">${valueText}</p>
      <p class="daily-summary-helper">${helperLine}</p>
    </div>
    ${cta}`;

  if (!card.hasDetails) {
    const unavailable = card.detailsUnavailableNote
      ? `<p class="daily-summary-unavailable">${card.detailsUnavailableNote}</p>` : "";
    return `<section class="daily-summary-strip tone-${card.tone}">${stripInner}</section>${unavailable}`;
  }

  return `<details class="daily-summary-details">
    <summary class="daily-summary-strip tone-${card.tone}${clickableClass}" aria-expanded="false">${stripInner}</summary>
    ${renderDailySummaryDetails(card)}
  </details>`;
}

// 「本日の新規予約」「前日の新規予約」「本日のチェックイン」の3枚を並べて表示する
// (いずれも月選択に依らないグローバル集計)。desktop 3列/tablet 2列/mobile 1列
// (.daily-summary-grid、styles.cssで定義)。
export function renderDailySummarySection(cards) {
  const heading = `<div class="daily-summary-heading">
    <h2>日次サマリー</h2>
    <p class="daily-summary-heading-note">対象月に関係なく、当日・前日の予約状況を表示</p>
  </div>`;
  const grid = `<div class="daily-summary-grid">${(cards || []).map(renderDailySummaryCard).join("")}</div>`;
  return heading + grid;
}

// 部屋タイプ別 日別稼働率（軽量SVG折れ線グラフ。chart libraryは使わない）。
// x軸=日付、y軸=稼働率%。selectedMonthのsnapshotのみ参照するため月切替で自動更新される。
const OCCUPANCY_CHART_WIDTH = 720;
const OCCUPANCY_CHART_HEIGHT = 220;
const OCCUPANCY_CHART_PAD = { top: 10, right: 12, bottom: 26, left: 40 };

export function renderRoomTypeOccupancyChart(chart) {
  const title = chart ? chart.title : "部屋タイプ別 日別稼働率";
  if (!chart || !chart.hasData) {
    return `<div class="chart-card">
      <h3>${title}</h3>
      <p class="chart-empty">データなし</p>
    </div>`;
  }
  const w = OCCUPANCY_CHART_WIDTH, h = OCCUPANCY_CHART_HEIGHT, pad = OCCUPANCY_CHART_PAD;
  const plotW = w - pad.left - pad.right;
  const plotH = h - pad.top - pad.bottom;
  const n = chart.dates.length;
  const allValues = chart.lines.flatMap((l) => l.points);
  const yMax = Math.ceil(Math.max(100, ...allValues, 0) / 10) * 10;

  const px = (i) => pad.left + (n <= 1 ? plotW / 2 : (i / (n - 1)) * plotW);
  const py = (v) => pad.top + plotH - (v / yMax) * plotH;

  const gridSteps = [0, 25, 50, 75, 100].filter((v) => v <= yMax);
  const gridLines = gridSteps.map((v) => {
    const yy = py(v);
    return `<line x1="${pad.left}" y1="${yy}" x2="${w - pad.right}" y2="${yy}" class="chart-grid-line" />` +
      `<text x="${pad.left - 6}" y="${yy + 4}" class="chart-axis-label" text-anchor="end">${v}%</text>`;
  }).join("");

  const labelStep = n > 10 ? Math.ceil(n / 10) : 1;
  const dateLabels = chart.dates.map((d, i) => {
    if (i % labelStep !== 0) return "";
    return `<text x="${px(i)}" y="${h - 6}" class="chart-axis-label" text-anchor="middle">${d.slice(8, 10)}</text>`;
  }).join("");

  const lines = chart.lines.map((line) => {
    const points = line.points.map((v, i) => `${px(i)},${py(v)}`).join(" ");
    return `<polyline points="${points}" fill="none" stroke="${line.color}" stroke-width="2" class="chart-line">
      <title>${line.label}</title>
    </polyline>`;
  }).join("");

  const legend = chart.lines.map((line) =>
    `<span class="chart-legend-item"><span class="chart-legend-dot" style="background:${line.color}"></span>${line.label}</span>`
  ).join("");

  const warningItems = (chart.warnings || []).slice(0, 3).map((w2) => `<li>${w2}</li>`).join("");
  const warningsHtml = warningItems ? `<ul class="chart-warnings">${warningItems}</ul>` : "";

  return `<div class="chart-card">
    <h3>${chart.title}</h3>
    <p class="chart-helper">${chart.helper}</p>
    <div class="chart-scroll">
      <svg viewBox="0 0 ${w} ${h}" class="occupancy-chart" role="img" aria-label="${chart.title}">
        ${gridLines}
        ${lines}
        ${dateLabels}
      </svg>
    </div>
    <div class="chart-legend">${legend}</div>
    ${warningsHtml}
  </div>`;
}

// 部屋タイプ別 売上構成（横棒/progress bar + 数値行）。
export function renderRoomTypeRevenueMix(mix) {
  const title = mix ? mix.title : "部屋タイプ別 売上構成";
  if (!mix || !mix.hasData) {
    return `<div class="revenue-mix-card">
      <h3>${title}</h3>
      <p class="chart-empty">データなし</p>
    </div>`;
  }
  const rows = mix.rows.map((r) => `<div class="revenue-mix-row">
      <div class="revenue-mix-row-head">
        <span class="revenue-mix-label">${r.roomTypeLabel}</span>
        <span class="revenue-mix-value">${r.revenue} / ${r.share}</span>
      </div>
      <div class="revenue-mix-bar-track">
        <div class="revenue-mix-bar-fill" style="width:${Math.max(0, Math.min(r.sharePercent, 100))}%"></div>
      </div>
      <div class="revenue-mix-row-foot">${r.soldRoomNights} ｜ ADR ${r.adr}</div>
    </div>`).join("");
  return `<div class="revenue-mix-card">
    <h3>${mix.title}</h3>
    <div class="revenue-mix-list">${rows}</div>
  </div>`;
}

export function renderInsightBanner(paceComment) {
  return `<div class="insight-banner tone-${paceComment.tone}">
    <span class="status-dot"></span>
    <span class="insight-text">${paceComment.text}</span>
  </div>`;
}

export function renderStatusChips(chips) {
  return chips.map((c) => `<span class="chip tone-${c.tone}">
    <span class="chip-label">${c.label}</span><span class="chip-value">${c.value}</span>
  </span>`).join("");
}

export function renderNotes(notes) {
  return `<div class="notes-stack">${notes.map(
    (n) => `<div class="note-line tone-${n.tone}">${n.text}</div>`).join("")}</div>`;
}

function detailTableHtml(rows) {
  return `<table class="detail-table">${rows.map(([k, v]) =>
    `<tr><td class="k">${k}</td><td class="v">${v}</td></tr>`).join("")}</table>`;
}

export function renderDetailCard(section, extraNoteHtml) {
  const summary = section.summary
    ? `<span class="summary-value">${section.summary}</span>` : "";
  return `<details class="detail-card">
    <summary>
      <span class="summary-title">${section.title}</span>
      ${summary}
      <span class="summary-chevron" aria-hidden="true">›</span>
    </summary>
    <div class="detail-body">
      ${detailTableHtml(section.rows)}
      ${extraNoteHtml || ""}
    </div>
  </details>`;
}

export function renderDetails(sections, validationSummary, exceptionCount) {
  return sections.map((section) => {
    let extra = "";
    let summary = section.summary;
    if (section.id === "validation" && validationSummary) {
      summary = `${validationSummary.ok ? "検証OK" : "要確認 " + validationSummary.criticalCount + "件"}`;
      extra = `<p class="detail-note">warning ${validationSummary.warningCount}件 ｜ exception件数: ${exceptionCount ?? "—"}</p>`;
    }
    return renderDetailCard({ ...section, summary }, extra);
  }).join("");
}

export function renderMonthSelector(header) {
  const options = header.monthOptions || [];
  if (!options.length) return "";
  const optHtml = options.map((o) =>
    `<option value="${o.value}"${o.value === header.selectedMonth ? " selected" : ""}>${o.label}</option>`
  ).join("");
  return `<div class="month-selector">
    <label for="month-select">対象月</label>
    <select id="month-select" aria-label="対象月">${optHtml}</select>
  </div>`;
}

const REFRESH_BUTTON_LABELS = {
  idle: "最新情報に更新",
  loading: "更新中...",
  success: "最新情報を取得しました",
  error: "更新に失敗しました",
};

// 「最新情報に更新」ボタン。WorkerはBeds24 APIを叩かないため、これはR2に公開済みの
// 最新snapshotをbrowser/中間キャッシュを回避して再取得するだけ(Beds24 fetch自体は行わない)。
// render()の他の部分(月選択・カード等)とは独立したDOM(#refresh-button-wrap)として
// 管理し、状態変化のためだけに全体を再描画して開いているdetailsを閉じないようにする。
export function renderRefreshButton(state) {
  const s = state || "idle";
  const label = REFRESH_BUTTON_LABELS[s] || REFRESH_BUTTON_LABELS.idle;
  const disabled = s === "loading" ? " disabled" : "";
  const messageClass = s === "success" ? "tone-green" : s === "error" ? "tone-red" : "";
  const message = (s === "success" || s === "error")
    ? `<span class="refresh-message ${messageClass}">${label}</span>` : "";
  return `<span class="refresh-button-wrap" id="refresh-button-wrap">
    <button type="button" class="refresh-button" id="refresh-button"${disabled}>
      ${s === "loading" ? label : REFRESH_BUTTON_LABELS.idle}
    </button>
    ${message}
  </span>`;
}

export function renderHeader(header) {
  return {
    title: header.title,
    metaLine: `対象月: ${header.targetMonth} ｜ 最終更新: ${header.generatedAtJst || "—"}`,
    pillHtml: `<span class="status-pill tone-${header.statusPill.tone}">
      <span class="dot"></span>${header.statusPill.label}
    </span>`,
    monthSelectorHtml: renderMonthSelector(header),
  };
}

export function renderErrorState() {
  return `<div class="state-card">
    <h2>BIデータが見つかりません</h2>
    <p>R2に <code>latest/bi_snapshot.json</code> が投入されていない可能性があります。</p>
    <ol>
      <li><code>refresh-beds24-bi --publish</code> を実行してBIデータを生成する</li>
      <li><code>publish-bi-r2</code> を実行してR2へアップロードする</li>
      <li><code>/api/snapshot</code> にアクセスしてデータが返るか確認する</li>
    </ol>
  </div>`;
}

export function renderSkeleton() {
  return {
    header: `<div class="skeleton skeleton-header"></div>`,
    cards: Array.from({ length: 3 }).map(() => `<div class="skeleton skeleton-card"></div>`).join(""),
    details: Array.from({ length: 3 })
      .map(() => `<div class="skeleton skeleton-line" style="margin-bottom:8px;"></div>`).join(""),
  };
}
