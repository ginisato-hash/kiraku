// cleaningSheetTemplate.js — 清掃・客室指示表（印刷用A4シート）のHTML生成、および
// 印刷ページ/モバイルページ/スタッフ清掃リストの3画面が共有する純粋ヘルパー関数群。
//
// これはFINAL・確定仕様の実装であり、以前のPLACEHOLDER(原本写真待ち)版は廃止された。
// 列構成・寸法・文言はすべて仕様書通りに固定。ここにあるガード/選択ロジック
// (cleanValue/priorityGuestFor/nightProgressFor等)は3画面すべてが必ずこのファイル
// から import して使うこと — 個別に再実装して食い違いを生まないため。
//
// KIRAKU_ROOM_ORDER は src/roomMaster.js が唯一の実体。この相対import
// ("../src/roomMaster.js")は、Node実行時(このファイルからの相対ファイルパス解決)と
// ブラウザ実行時(このファイルが配信されるURL "/cleaningSheetTemplate.js" からの
// 相対URL解決 -> "/src/roomMaster.js")の両方で同じ文字列のまま正しく解決される。
// ブラウザ向けには、public/配下の静的資産からsrc/配下のファイルへ直接アクセスできない
// (ASSETS bindingはpublic/配下のみを配信する)ため、worker.jsが認証済みリクエストに
// 対して `/src/roomMaster.js` をこのファイル(src/roomMaster.js)の内容から動的に
// 生成して配信する — 実体は1箇所のみで二重管理にはならない。
import { KIRAKU_ROOM_ORDER } from "../src/roomMaster.js";
import { escapeHtml } from "./printUtils.js";
import { formatJapaneseDateWithWeekdayParen } from "./jst.js";

// ステータス(英語enum) -> 表示用の日本語ラベル。CANCELLEDはVACANTと同一表示
// (仕様上「特別なUIなし、VACANT同様に扱う」)。
export const STATUS_LABELS_JP = {
  CHECKIN: "IN",
  CHECKOUT: "OUT",
  STAYOVER: "連泊",
  TURNOVER: "入替",
  VACANT: "空室",
  CANCELLED: "空室",
  UNASSIGNED: "未割当",
};

export function statusLabel(status) {
  return STATUS_LABELS_JP[status] || "";
}

// None/null/プレースホルダー文字列("None"/"null"/"undefined"/"N/A")を空文字へ
// 正規化する。印刷/モバイル/スタッフ画面のすべてがこれを経由すること。
export function cleanValue(value) {
  if (value == null) return "";
  const v = String(value).trim();
  if (!v) return "";
  if (/^(none|null|undefined|n\/a)$/i.test(v)) return "";
  return v;
}

// 予約元(OTA)の印刷用最終短縮レイヤー。sourceは既にPython側の
// normalize_booking_source()で正規化済みの表示名なので、ここでOTA正規化ロジック
// そのものを再実装しない — あくまで紙面用の短縮表記のみ。
export const OTA_PRINT_SHORT_NAMES = {
  "Booking.com": "Booking",
  "楽天トラベル": "楽天",
  "じゃらんnet": "じゃらん",
  "じゃらん": "じゃらん",
  "Direct": "直",
};

export function otaPrintShortName(source) {
  const v = cleanValue(source);
  if (!v) return "";
  return OTA_PRINT_SHORT_NAMES[v] || v;
}

// 行のstatusに応じて「優先ゲスト」を返す: TURNOVER/CHECKIN -> arriving_guest、
// CHECKOUT -> departing_guest、STAYOVER -> staying_guest、それ以外(VACANT/
// UNASSIGNED/CANCELLED)は null。TURNOVERで2名分の氏名を連結すること
// ("A → B"のような表示)は絶対にしない — 常に到着ゲストの氏名のみ。
export function priorityGuestFor(room) {
  if (!room) return null;
  switch (room.status) {
    case "TURNOVER":
    case "CHECKIN":
      return room.arriving_guest || null;
    case "CHECKOUT":
      return room.departing_guest || null;
    case "STAYOVER":
      return room.staying_guest || null;
    default:
      return null;
  }
}

export function guestNameFor(room) {
  const g = priorityGuestFor(room);
  return g ? cleanValue(g.guest_name) : "";
}

export function guestCountFor(room) {
  const g = priorityGuestFor(room);
  if (!g || g.total_guests == null) return "";
  return String(g.total_guests);
}

// "idx/total" 形式。current_night_index/total_nightsはPython側で既に計算済み
// (compute_night_progress) — ここで再計算しない。
export function nightProgressFor(room) {
  const g = priorityGuestFor(room);
  if (!g) return "";
  const idx = room.current_night_index;
  const total = room.total_nights;
  if (idx == null || total == null) return "";
  return `${idx}/${total}`;
}

// 到着時刻はCHECKIN/TURNOVERの到着ゲストにのみ意味がある。
export function arrivalTimeFor(room) {
  if (!room || (room.status !== "CHECKIN" && room.status !== "TURNOVER")) return "";
  const g = room.arriving_guest;
  return g ? cleanValue(g.arrival_time) : "";
}

export function otaFor(room) {
  const g = priorityGuestFor(room);
  return g ? cleanValue(g.source) : "";
}

export function inMark(room) {
  return (room && (room.status === "CHECKIN" || room.status === "TURNOVER")) ? "✓" : "";
}

export function outMark(room) {
  return (room && (room.status === "CHECKOUT" || room.status === "TURNOVER")) ? "✓" : "";
}

// UNASSIGNED件数（room_numberがnullの行）。18室グリッドには絶対に混ぜない。
export function countUnassigned(rooms) {
  return (Array.isArray(rooms) ? rooms : []).filter((r) => r && r.room_number == null).length;
}

// 18室固定のグリッド用に、KIRAKU_ROOM_ORDER順へ並べ替える(UNASSIGNED行は除外)。
// 対象日にデータが欠けている物理室があっても防御的にVACANT扱いの行を補う。
export function roomsByCanonicalOrder(rooms) {
  const list = Array.isArray(rooms) ? rooms : [];
  const byRoom = new Map();
  for (const r of list) {
    if (r && r.room_number) byRoom.set(r.room_number, r);
  }
  return KIRAKU_ROOM_ORDER.map((roomNumber) => byRoom.get(roomNumber) || {
    room_number: roomNumber,
    status: "VACANT",
    departing_guest: null,
    arriving_guest: null,
    staying_guest: null,
    current_night_index: null,
    total_nights: null,
    effectiveInstruction: "",
    hasOverride: false,
    updatedAt: null,
  });
}

// ---------------- 印刷ページ（/ops/print/cleaning）本体 ----------------

// mm単位。相対比率(客室名が広い/IN・OUTが狭い/備考・通信が最も広い)を必ず維持
// すること。合計196mm(.cleaning-sheetの幅と一致)。
// RoomNo列は見出し「RoomNo」が2.8mmフォントで確実に収まる最小幅として15mm
// (13mmでは実測約1.1mm不足し見出しが枠内で見切れていた)。その2mm分は
// 備考・通信列(56mm)から差し引いており、備考欄の実用上の広さには影響しない。
export const COLUMN_WIDTHS_MM = [15, 38, 9, 11, 18, 8, 8, 15, 20, 54];
export const COLUMN_LABELS = [
  "RoomNo", "お客様名", "人数", "泊数", "清掃区分", "IN", "OUT", "到着", "予約元", "備考・通信",
];

function buildColgroup() {
  return `<colgroup>${COLUMN_WIDTHS_MM.map((w) => `<col style="width:${w}mm;">`).join("")}</colgroup>`;
}

function buildHeaderRow() {
  return `<tr>${COLUMN_LABELS.map((label) => `<th>${escapeHtml(label)}</th>`).join("")}</tr>`;
}

function buildRoomRow(room) {
  const instruction = cleanValue(room.effectiveInstruction);
  return `<tr data-room-number="${escapeHtml(room.room_number)}">
    <td class="cs-c-room">${escapeHtml(room.room_number)}</td>
    <td class="cs-c-guest">${escapeHtml(guestNameFor(room))}</td>
    <td class="cs-c-count">${escapeHtml(guestCountFor(room))}</td>
    <td class="cs-c-nights">${escapeHtml(nightProgressFor(room))}</td>
    <td class="cs-c-status">${escapeHtml(statusLabel(room.status))}</td>
    <td class="cs-c-in">${inMark(room)}</td>
    <td class="cs-c-out">${outMark(room)}</td>
    <td class="cs-c-arrival">${escapeHtml(arrivalTimeFor(room))}</td>
    <td class="cs-c-ota">${escapeHtml(otaPrintShortName(otaFor(room)))}</td>
    <td class="cs-c-notes" title="${escapeHtml(instruction)}">${escapeHtml(instruction)}</td>
  </tr>`;
}

// ---------------- モバイル今日ビュー（/cleaning/today）本体 ----------------
//
// 印刷帳票を縮小したものではなく、1室=1ブロックの独自レイアウト(genuinely
// different presentation)。ただしゲスト優先選択/ラベル/夜数進捗等のロジックは
// 印刷ページと完全に同じ上記ヘルパーを再利用し、食い違いを生まない。
// today.js内にDOM非依存で置くとmain()の即時実行(window参照)によりNode環境の
// テストからimportできなくなるため、副作用の無いこのファイルに置く。
export function renderMobileRoomBlock(room) {
  if (room.status === "VACANT" || room.status === "CANCELLED") {
    // 空室でも、指示(override)が付いていれば必ず表示する — 印刷ページの
    // 備考・通信列と同じデータ(effectiveInstruction)を隠さない(部屋が空室でも
    // 「電球交換」等のスタッフ向け指示が付くことは実際にあり得るため)。
    const vacantInstruction = cleanValue(room.effectiveInstruction);
    return `<div class="mc-room-block mc-room-vacant">
      <div class="mc-room-number">${escapeHtml(room.room_number)}</div>
      <div class="mc-vacant-label">空室</div>
      ${vacantInstruction ? `<div class="mc-instruction">${escapeHtml(vacantInstruction)}</div>` : ""}
    </div>`;
  }

  const label = statusLabel(room.status);
  const guestName = guestNameFor(room);
  const count = guestCountFor(room);
  const nights = nightProgressFor(room);
  const arrival = arrivalTimeFor(room);
  const ota = cleanValue(otaFor(room));
  const instruction = cleanValue(room.effectiveInstruction);

  const inOutParts = [];
  if (inMark(room)) inOutParts.push("IN");
  if (outMark(room)) inOutParts.push("OUT");
  const inOutText = inOutParts.join(" / ");

  const countNightsParts = [];
  if (count) countNightsParts.push(`人数 ${count}名`);
  if (nights) countNightsParts.push(`泊数 ${nights}`);

  return `<div class="mc-room-block">
    <div class="mc-room-number">${escapeHtml(room.room_number)}号室</div>
    <div class="mc-status-badge mc-status-${room.status}">${escapeHtml(label)}</div>
    ${guestName ? `<div class="mc-guest-name">${escapeHtml(guestName)}</div>` : ""}
    ${countNightsParts.length ? `<div class="mc-detail-line">${escapeHtml(countNightsParts.join("　"))}</div>` : ""}
    ${inOutText ? `<div class="mc-detail-line">${escapeHtml(inOutText)}</div>` : ""}
    ${arrival ? `<div class="mc-detail-line">到着 ${escapeHtml(arrival)}</div>` : ""}
    ${ota ? `<div class="mc-detail-line">予約元 ${escapeHtml(ota)}</div>` : ""}
    ${instruction ? `<div class="mc-instruction">${escapeHtml(instruction)}</div>` : ""}
  </div>`;
}

// cleaningRooms: GET /api/cleaning?date=... のマージ済みroom配列(18室 + 任意の
// UNASSIGNED行、順不同でよい)。常にKIRAKU_ROOM_ORDER順の18ブロックを返す
// (UNASSIGNED行はこの画面には出さない — 印刷ページと同じ扱い)。
export function renderMobileCleaningBody(cleaningRooms) {
  return roomsByCanonicalOrder(cleaningRooms).map(renderMobileRoomBlock).join("");
}

// cleaningRooms: GET /api/cleaning?date=... のマージ済みroom配列(18室 + 任意の
// UNASSIGNED行)。date: "YYYY-MM-DD"。
export function renderCleaningSheetTemplate(cleaningRooms, date) {
  const list = Array.isArray(cleaningRooms) ? cleaningRooms : [];
  const canonicalRows = roomsByCanonicalOrder(list);
  const unassignedCount = countUnassigned(list);
  const dateLabel = formatJapaneseDateWithWeekdayParen(date);

  const warningHtml = unassignedCount > 0
    ? `<div class="cs-unassigned-warning">未割当予約あり：${unassignedCount}件</div>`
    : "";

  return `<div class="cleaning-sheet">
    <div class="cs-header">
      <div class="cs-header-date">${escapeHtml(dateLabel)}</div>
      <div class="cs-header-title">清掃・客室指示表</div>
      <div class="cs-header-property">喜らく</div>
    </div>
    ${warningHtml}
    <table class="cs-main-table">
      ${buildColgroup()}
      <thead>${buildHeaderRow()}</thead>
      <tbody>${canonicalRows.map(buildRoomRow).join("")}</tbody>
    </table>
    <div class="cs-footer-box">
      <div class="cs-footer-title">全体通信・引継ぎ</div>
    </div>
  </div>`;
}
