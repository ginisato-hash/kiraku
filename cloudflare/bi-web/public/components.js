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

// トップカード群より上のsummary strip。「本日の新規予約」件数・金額。
export function renderDailyNewBookings(summary) {
  const valueText = summary.revenue ? `${summary.count} / ${summary.revenue}` : summary.count;
  const helperLine = summary.helper ? `${summary.targetMonthLabel} / ${summary.helper}` : summary.targetMonthLabel;
  return `<section class="daily-summary-strip tone-${summary.tone}">
    <div>
      <p class="eyebrow">${summary.label}</p>
      <p class="daily-summary-value">${valueText}</p>
      <p class="daily-summary-helper">${helperLine}</p>
    </div>
  </section>`;
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
