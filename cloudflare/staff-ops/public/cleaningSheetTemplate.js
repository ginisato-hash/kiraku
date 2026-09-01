// cleaningSheetTemplate.js — 清掃・客室指示表（印刷用A4シート）のHTML生成、および
// 印刷ページ/モバイルページ/スタッフ清掃リストの3画面が共有する純粋ヘルパー関数群。
//
// 2026-09改訂: 現場フィードバックに基づき、表示項目・列構成・Cleaning DTOを更新。
// 変更点: OUT列削除、ステータス1列化(IN/連泊のみ、印刷/モバイル専用)、人数の
// 大人/子供内訳表示、お客様からのお知らせ(guest_notice)、現地決済列を追加。
// Staff cleaning list(cleaningStaffView.js)側のフル情報表示(statusLabel/inMark/
// outMark、IN/OUT/連泊/入替/空室/未割当)は意図的に変更しない — printStatusLabel()は
// 印刷/モバイル専用の別関数として新設した。
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

// ステータス(英語enum) -> 表示用の日本語ラベル(フル表示)。Staff cleaning list
// (cleaningStaffView.js)専用 — CANCELLEDはVACANTと同一表示。
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

// 印刷/モバイル専用の簡略ステータス表示。紙面の視認性優先でIN/連泊の2種類のみに
// 単純化する。TURNOVERは「当日次の客が入る」ことが重要なのでINとして扱う。
// OUT/入替/空室/未割当は紙面には出さない(空欄)。
export const PRINT_STATUS_LABELS = {
  CHECKIN: "IN",
  TURNOVER: "IN",
  STAYOVER: "連泊",
  CHECKOUT: "",
  VACANT: "",
  CANCELLED: "",
  UNASSIGNED: "",
};

export function printStatusLabel(status) {
  return PRINT_STATUS_LABELS[status] || "";
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

// 実宿泊人数(total_guests)。Staff cleaning list/mobileの「人数」表示用
// (2026-09: 印刷帳票の大きい数字はbeddingCountForに置き換わった — 意味が
// 異なる別関数として分離している。こちらは変更しない)。
export function guestCountFor(room) {
  const g = priorityGuestFor(room);
  if (!g || g.total_guests == null) return "";
  return String(g.total_guests);
}

// 印刷帳票の人数セル上段(最大サイズの数字)専用: 「布団を敷く必要がある人数」
// (bedding_guest_count、extract.compute_bedding_guest_count()参照)。
// 実宿泊人数(total_guests)とは意味が異なる — 混同しないこと。
export function beddingCountFor(room) {
  const g = priorityGuestFor(room);
  if (!g || g.bedding_guest_count == null) return "";
  return String(g.bedding_guest_count);
}

// 人数セル下段の「大人/子供」実人数内訳。常にBeds24のnumAdult/numChildをそのまま
// 表示する — 上段(bedding count)がBooking.comの年齢判定で子供を除外していても、
// 下段の実人数からは絶対に消さない(要件11)。子供0名なら「子供0」は表示しない。
export function guestBreakdownFor(room) {
  const g = priorityGuestFor(room);
  if (!g) return "";
  const parts = [];
  if (g.adults != null) parts.push(`大人${g.adults}`);
  if (g.children > 0) parts.push(`子供${g.children}`);
  return parts.join(" ");
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

// 到着セルのfont-size段階。Beds24の明示arrival_timeは "15:00"(5文字)だが、
// Booking.com commentsからのfallbackは "17:00-18:00"(11文字)になり、14mm幅の
// 到着列には基本font-size(3.4mm)では収まらない。要件14どおり1行表示を優先し、
// この列だけ段階的に縮小する(3行に折り返す表示にはしない)。
// 判定は表示文字数のみ(到着時刻は半角数字/コロン/ハイフンのみのため全角幅換算は不要)。
export function arrivalSizeClass(text) {
  const v = typeof text === "string" ? text.trim() : "";
  if (v.length <= 5) return "";            // "15:00" 等: 既定サイズのまま
  if (v.length <= 8) return "cs-arrival-9";
  return "cs-arrival-range";               // "17:00-18:00" 等
}

export function otaFor(room) {
  const g = priorityGuestFor(room);
  return g ? cleanValue(g.source) : "";
}

// お客様からのお知らせ(guest_notice)。内部メモ(override/effectiveInstruction)とは
// 完全に別データ。
export function guestNoticeFor(room) {
  const g = priorityGuestFor(room);
  return g ? cleanValue(g.guest_notice) : "";
}

// 現地決済表示。payment_due_at_property && amount_due_at_propertyが正の有限数の
// 場合のみ表示対象とする(不確実な金額を「?」等で誤表示するくらいなら完全空欄)。
// 2026-09: 金額のsource of truthはBeds24公式のbooking単位Invoice Balance
// (extract.extract_amount_due_at_property()、channel collect除外済み)。旧
// 「現地支払いmarkerが明示された予約だけ対象」という実装は、markerの無い実予約
// (未払いのBooking.com等)を誤って除外していたため撤回した(要件A参照)。
export function onsiteInfoFor(room) {
  const g = priorityGuestFor(room);
  if (!g || !g.payment_due_at_property) return { show: false, amountText: "" };
  const amount = g.amount_due_at_property;
  if (typeof amount !== "number" || !Number.isFinite(amount) || amount <= 0) {
    return { show: false, amountText: "" };
  }
  return { show: true, amountText: `¥${Math.round(amount).toLocaleString("ja-JP")}` };
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

// 印刷帳票のrow内容表示判定。2026-09改訂(撤回→再確定): 18室のroom rowは常に
// 全室出力する(前回試みたrow自体の間引きは撤回)。CHECKIN/TURNOVER/STAYOVER
// だけが「操作対象データ(氏名/人数/泊数/ステータス/到着/予約元/現地決済/備考)」
// を表示し、それ以外(CHECKOUT/VACANT/CANCELLED)はRoomNo以外を完全空欄にする —
// 「CHECKOUT客の情報がステータスなしで残る」問題を、rowを消さずに解消する。
const PRINTABLE_STATUSES = new Set(["CHECKIN", "TURNOVER", "STAYOVER"]);
export function isPrintableRoomStatus(status) {
  return PRINTABLE_STATUSES.has(status);
}

// 4F(401-406)/5F(501-507)/6F(601-607)の階境界。18室固定rowへ戻したため、
// 501/601の物理room番号で固定判定する(表示された最初のrowを探すdynamic版は
// 不要になった)。
const FLOOR_START_ROOMS = new Set(["501", "601"]);
export function isFloorStartRoom(roomNumber) {
  return FLOOR_START_ROOMS.has(String(roomNumber));
}

// ---------------- 印刷ページ（/ops/print/cleaning）本体 ----------------

// mm単位。RoomNo/人数/泊数は縮小方向への調整禁止(現場フィードバックで拡大した列)。
// 合計196mm(.cleaning-sheetの幅と一致)。
export const COLUMN_WIDTHS_MM = [16, 36, 21, 14, 16, 14, 18, 24, 37];
export const COLUMN_LABELS = [
  "RoomNo", "お客様名", "人数", "泊数", "ステータス", "到着", "予約元", "現地決済", "備考・通信",
];
// ヘッダ<th>とボディ<td>で同じ列に同じキーのclassを付けるための対応表
// (cs-h-room/cs-c-room のように接頭辞だけ変える)。CSS側がRoomNoヘッダだけ
// 個別にfont-sizeを調整できるようにするための最小限のフック。
const COLUMN_KEYS = ["room", "guest", "count", "nights", "status", "arrival", "ota", "onsite", "notes"];

function buildColgroup() {
  return `<colgroup>${COLUMN_WIDTHS_MM.map((w) => `<col style="width:${w}mm;">`).join("")}</colgroup>`;
}

function buildHeaderRow() {
  return `<tr>${COLUMN_LABELS.map((label, i) => `<th class="cs-h-${COLUMN_KEYS[i]}">${escapeHtml(label)}</th>`).join("")}</tr>`;
}

// 氏名の「見た目の幅」を全角=2/半角=1で概算し、raw文字数だけでは区別できない
// 日本語(全角)と英語(半角)の実際の表示幅の違いを反映する。通常の日本人氏名
// (例:「山田 太郎」)は全角中心のため幅が大きく見えても実文字数は少なく、
// 段階縮小の対象にならない。長い欧文氏名(例:"Martin Doherty")は半角文字が
// 続くぶん幅が伸び、必要な場合だけ縮小される。
const FULL_WIDTH_RANGES = [
  [0x1100, 0x115f], [0x2e80, 0xa4cf], [0xac00, 0xd7a3],
  [0xf900, 0xfaff], [0xff00, 0xff60], [0xffe0, 0xffe6],
];
function isFullWidthChar(codePoint) {
  return FULL_WIDTH_RANGES.some(([lo, hi]) => codePoint >= lo && codePoint <= hi);
}
export function effectiveTextWidth(str) {
  let width = 0;
  for (const ch of String(str)) {
    width += isFullWidthChar(ch.codePointAt(0)) ? 2 : 1;
  }
  return width;
}

// お客様名セルのfont-sizeクラスを返すpure helper。枠(36mm列)に収まる範囲で
// 既定は13px相当(cs-guest-13)、実効幅が長い氏名だけ段階的に12px→11px相当へ
// 縮小する。しきい値(10/16)は「半角のみの氏名ならlength<=10は13px」という
// 直感的な基準をeffectiveTextWidthに適用したもの。
export function guestNameSizeClass(name) {
  const width = effectiveTextWidth(cleanValue(name));
  if (width <= 10) return "cs-guest-13";
  if (width <= 16) return "cs-guest-12";
  return "cs-guest-11";
}

function buildNotesCell(room) {
  const notice = guestNoticeFor(room);
  const instruction = cleanValue(room.effectiveInstruction);
  const lines = [];
  if (notice) lines.push(`<div class="cs-notice-line" title="${escapeHtml(notice)}">客: ${escapeHtml(notice)}</div>`);
  if (instruction) lines.push(`<div class="cs-instruction-line" title="${escapeHtml(instruction)}">指: ${escapeHtml(instruction)}</div>`);
  return `<td class="cs-c-notes">${lines.join("")}</td>`;
}

function buildOnsiteCell(room) {
  const onsite = onsiteInfoFor(room);
  if (!onsite.show) return `<td class="cs-c-onsite"></td>`;
  return `<td class="cs-c-onsite">
    <div class="cs-onsite-label">現地</div>
    <div class="cs-onsite-amount">${escapeHtml(onsite.amountText)}</div>
  </td>`;
}

// 操作対象データ(氏名/人数/泊数/ステータス/到着/予約元/現地決済/備考)を出さない
// 空欄row(RoomNoだけ)。CHECKOUT/VACANT/CANCELLEDが対象(要件16 — CHECKOUT客の
// guest name/instruction等が一切残らないよう、9セルすべてを構造的に空にする)。
function buildBlankOperationalCells() {
  return `<td class="cs-c-guest"></td>
    <td class="cs-c-count"></td>
    <td class="cs-c-nights"></td>
    <td class="cs-c-status"></td>
    <td class="cs-c-arrival"></td>
    <td class="cs-c-ota"></td>
    <td class="cs-c-onsite"></td>
    <td class="cs-c-notes"></td>`;
}

function buildOperationalCells(room) {
  const guestName = guestNameFor(room);
  return `<td class="cs-c-guest ${guestNameSizeClass(guestName)}">${escapeHtml(guestName)}</td>
    <td class="cs-c-count">
      <div class="cs-guest-count-wrap">
        <div class="cs-total-guests">${escapeHtml(beddingCountFor(room))}</div>
        <div class="cs-guest-breakdown">${escapeHtml(guestBreakdownFor(room))}</div>
      </div>
    </td>
    <td class="cs-c-nights">${escapeHtml(nightProgressFor(room))}</td>
    <td class="cs-c-status">${escapeHtml(printStatusLabel(room.status))}</td>
    ${buildArrivalCell(room)}
    <td class="cs-c-ota">${escapeHtml(otaPrintShortName(otaFor(room)))}</td>
    ${buildOnsiteCell(room)}
    ${buildNotesCell(room)}`;
}

function buildArrivalCell(room) {
  const arrival = arrivalTimeFor(room);
  const sizeClass = arrivalSizeClass(arrival);
  const cls = sizeClass ? `cs-c-arrival ${sizeClass}` : "cs-c-arrival";
  return `<td class="${cls}">${escapeHtml(arrival)}</td>`;
}

function buildRoomRow(room) {
  const trClass = isFloorStartRoom(room.room_number) ? ` class="cs-floor-start"` : "";
  const cells = isPrintableRoomStatus(room.status)
    ? buildOperationalCells(room)
    : buildBlankOperationalCells();
  return `<tr data-room-number="${escapeHtml(room.room_number)}"${trClass}>
    <td class="cs-c-room">${escapeHtml(room.room_number)}</td>
    ${cells}
  </tr>`;
}

// ---------------- モバイル今日ビュー（/cleaning/today）本体 ----------------
//
// 印刷帳票を縮小したものではなく、1室=1ブロックの独自レイアウト(genuinely
// different presentation)。ただしゲスト優先選択/ラベル/夜数進捗等のロジックは
// 印刷ページと完全に同じ上記ヘルパーを再利用し、食い違いを生まない。
// today.js内にDOM非依存で置くとmain()の即時実行(window参照)によりNode環境の
// テストからimportできなくなるため、副作用の無いこのファイルに置く。
// 2026-09: 印刷側と合わせ、OUT表示を削除しstatusはIN/連泊のみにした
// (printStatusLabel()を共有)。現地決済金額は今回モバイルへは追加しない
// (清掃担当者に金額情報を見せる必要は今回指定されていないため)。
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

  const label = printStatusLabel(room.status);
  const guestName = guestNameFor(room);
  const count = guestCountFor(room);
  const breakdown = guestBreakdownFor(room);
  const nights = nightProgressFor(room);
  const arrival = arrivalTimeFor(room);
  const ota = cleanValue(otaFor(room));
  const notice = guestNoticeFor(room);
  const instruction = cleanValue(room.effectiveInstruction);

  const countNightsParts = [];
  if (count) countNightsParts.push(`人数 ${count}名${breakdown ? `（${breakdown}）` : ""}`);
  if (nights) countNightsParts.push(`泊数 ${nights}`);

  return `<div class="mc-room-block">
    <div class="mc-room-number">${escapeHtml(room.room_number)}号室</div>
    ${label ? `<div class="mc-status-badge mc-status-${room.status}">${escapeHtml(label)}</div>` : ""}
    ${guestName ? `<div class="mc-guest-name">${escapeHtml(guestName)}</div>` : ""}
    ${countNightsParts.length ? `<div class="mc-detail-line">${escapeHtml(countNightsParts.join("　"))}</div>` : ""}
    ${arrival ? `<div class="mc-detail-line">到着 ${escapeHtml(arrival)}</div>` : ""}
    ${ota ? `<div class="mc-detail-line">予約元 ${escapeHtml(ota)}</div>` : ""}
    ${notice ? `<div class="mc-instruction">客: ${escapeHtml(notice)}</div>` : ""}
    ${instruction ? `<div class="mc-instruction">指: ${escapeHtml(instruction)}</div>` : ""}
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
  const canonicalRows = roomsByCanonicalOrder(list); // 18室固定(要件14 — 前回のrow間引きは撤回)
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
