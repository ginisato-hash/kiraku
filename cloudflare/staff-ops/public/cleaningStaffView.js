// cleaningStaffView.js — Daily Ops画面の「Staff cleaning list」: 18室の一覧表示 +
// 部屋ごとの指示編集UI(追加・変更指示の保存/リセット)。
//
// 設計方針: このファイルの「純粋関数(render/validate/body-builder/perform*)」と
// 「DOM配線(mountCleaningStaffView)」を明確に分離する — 他の画面
// (dailyOpsViewModel.js -> dailyOps.js と同じレイヤリング)と同じ考え方。
// performSave/performReset は fetchImpl を注入できるようにしてあり、DOMなしで
// テストできる。renderStaffCleaningTable/validateInstructionInput/
// buildOverrideSaveBody/buildOverrideDeleteBody は完全に純粋でDOM非依存。
import {
  statusLabel, guestNameFor, guestCountFor, guestBreakdownFor, nightProgressFor, arrivalTimeFor,
  otaFor, guestNoticeFor, onsiteInfoFor, cleanValue, inMark, outMark, roomsByCanonicalOrder,
} from "./cleaningSheetTemplate.js";
import { escapeHtml } from "./printUtils.js";
import { assertNoFinancialKeys, assertNoForbiddenCleaningKeys } from "./financialGuard.js";
import { CLEANING_ALLOWED, roomAccessStatusLabelJa, nextRoomAccessStatus } from "./roomAccessStatus.js";
import { todayJst } from "./jst.js";
import { cleaningLiveAccessAllowed } from "./featureFlags.js";

export const MAX_INSTRUCTION_LEN = 200;

// サーバ側の拒否条件(空文字禁止/200文字上限)をクライアント側でも先に検査する。
// 無効な値ではfetchImpl/performSaveのfetch呼び出しに絶対到達しない。
export function validateInstructionInput(rawValue) {
  const value = typeof rawValue === "string" ? rawValue.trim() : "";
  if (!value) {
    return { ok: false, error: "指示を入力してください（空欄のまま保存はできません）" };
  }
  if (value.length > MAX_INSTRUCTION_LEN) {
    return { ok: false, error: `${MAX_INSTRUCTION_LEN}文字以内で入力してください` };
  }
  return { ok: true, value };
}

export function buildOverrideSaveBody(date, roomNumber, instruction) {
  return { date, roomNumber, instruction };
}

export function buildOverrideDeleteBody(date, roomNumber) {
  return { date, roomNumber };
}

// override編集機能は既存(RoomNo/guest/status/人数/泊数/IN/OUT/arrival/source/指示)
// を一切変更せず、大人/子供内訳・お客様からのお知らせ・現地決済・在室確認のみを
// 追加した総15列(編集ボタン列含む)。colspanもこれに合わせる。
const STAFF_TABLE_COLSPAN = 15;

// 在室確認セル(未退室/清掃可)。room.roomAccessStatusが非nullのroom(=当日
// departing guestがいるroom)にのみボタン/バッジを出す。liveAccessEnabled
// (featureFlags.jsのCLEANING_LIVE_ACCESS_READY/preview=1)がfalseの間は
// 機能ごと非表示(空セル)にする。当日(JST)以外の日付を閲覧中は、クリック可能
// なボタンではなく静的バッジ+説明文にする(要件26 — 当日以外は読み取り専用)。
function buildAccessStatusCell(room, isToday, liveAccessEnabled) {
  const status = room.roomAccessStatus;
  if (!liveAccessEnabled || !status) return `<td class="csv-c-access"></td>`;
  const label = roomAccessStatusLabelJa(status);
  if (!isToday) {
    return `<td class="csv-c-access">
      <span class="csv-access-badge csv-access-badge-${escapeHtml(status)}">${escapeHtml(label)}</span>
      <div class="csv-access-note">当日のみ変更できます</div>
    </td>`;
  }
  return `<td class="csv-c-access">
    <button type="button" class="csv-access-btn csv-access-btn-${escapeHtml(status)}" data-action="access-request" data-room="${escapeHtml(room.room_number)}">${escapeHtml(label)}</button>
  </td>`;
}

// 在室確認の確認モーダル。要件11: room番号を非常に大きく再確認できるように
// する(押し間違い防止)。要件10: 「OK/Cancel」のような曖昧な文言は禁止 —
// 危険操作側(状態を変える側)には具体的な動詞を必ず入れる。
export function buildAccessStatusModalHtml(modal) {
  if (!modal) return "";
  const { roomNumber, targetStatus, pending, errorText } = modal;
  const isForward = targetStatus === CLEANING_ALLOWED;
  const title = isForward ? "この部屋を「清掃可」にしますか？" : "「未退室」に戻しますか？";
  const body = isForward ? "宿泊者が退室済みであることを確認してから変更してください。" : "";
  const confirmLabel = isForward ? "清掃可にする" : "未退室に戻す";
  const disabledAttr = pending ? " disabled" : "";
  return `<div class="csv-access-modal-backdrop">
    <div class="csv-access-modal" role="dialog" aria-modal="true" aria-label="在室確認">
      <div class="csv-access-modal-room">${escapeHtml(roomNumber)}号室</div>
      <div class="csv-access-modal-title">${escapeHtml(title)}</div>
      ${body ? `<div class="csv-access-modal-body">${escapeHtml(body)}</div>` : ""}
      ${errorText ? `<div class="csv-access-modal-error">${escapeHtml(errorText)}</div>` : ""}
      <div class="csv-access-modal-actions">
        <button type="button" class="csv-access-modal-btn-cancel" data-action="access-cancel"${disabledAttr}>キャンセル</button>
        <button type="button" class="csv-access-modal-btn-confirm" data-action="access-confirm"${disabledAttr}>${pending ? "更新中…" : escapeHtml(confirmLabel)}</button>
      </div>
    </div>
  </div>`;
}

export function buildAccessStatusBody(date, roomNumber, status) {
  return { date, roomNumber, status };
}

// POST /api/cleaning/access-status。performSave/performResetと同じ
// fetchImpl注入パターン。ネットワーク失敗/サーバ拒否は例外を投げず
// { ok:false, error } として返す(要件12 — 状態を先に変えない)。
export async function performAccessStatusChange({ date, roomNumber, status, fetchImpl }) {
  const doFetch = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  try {
    const res = await doFetch("/api/cleaning/access-status", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(buildAccessStatusBody(date, roomNumber, status)),
    });
    if (!res.ok) return { ok: false, error: "更新できませんでした。もう一度お試しください" };
    const data = typeof res.json === "function" ? await res.json() : {};
    return { ok: true, status: (data && data.status) || status, updatedAt: (data && data.updatedAt) || null };
  } catch (e) {
    return { ok: false, error: "更新できませんでした。もう一度お試しください" };
  }
}

function buildEditPanelRow(room, errorText) {
  const instruction = cleanValue(room.effectiveInstruction);
  return `<tr class="csv-edit-row" data-room-number="${escapeHtml(room.room_number)}">
    <td colspan="${STAFF_TABLE_COLSPAN}">
      <div class="csv-edit-panel">
        <div class="csv-edit-title">${escapeHtml(room.room_number)}号室</div>
        <label class="csv-edit-label" for="csv-edit-input-${escapeHtml(room.room_number)}">追加・変更指示</label>
        <textarea id="csv-edit-input-${escapeHtml(room.room_number)}" class="csv-edit-input" maxlength="${MAX_INSTRUCTION_LEN}">${escapeHtml(instruction)}</textarea>
        ${errorText ? `<div class="csv-edit-error">${escapeHtml(errorText)}</div>` : ""}
        <div class="csv-edit-actions">
          <button type="button" class="csv-btn-save" data-action="save" data-room="${escapeHtml(room.room_number)}">保存</button>
          <button type="button" class="csv-btn-reset" data-action="reset" data-room="${escapeHtml(room.room_number)}">元の指示に戻す</button>
          <button type="button" class="csv-btn-cancel" data-action="cancel" data-room="${escapeHtml(room.room_number)}">キャンセル</button>
        </div>
      </div>
    </td>
  </tr>`;
}

function buildRoomTableRow(room, editingRoom, errorText, isToday, liveAccessEnabled) {
  const instruction = cleanValue(room.effectiveInstruction);
  const notice = guestNoticeFor(room);
  const onsite = onsiteInfoFor(room);
  const baseRow = `<tr data-room-number="${escapeHtml(room.room_number)}">
    <td>${escapeHtml(room.room_number)}</td>
    <td>${escapeHtml(guestNameFor(room))}</td>
    <td>${escapeHtml(statusLabel(room.status))}</td>
    <td>${escapeHtml(guestCountFor(room))}</td>
    <td>${escapeHtml(guestBreakdownFor(room))}</td>
    <td>${escapeHtml(nightProgressFor(room))}</td>
    <td>${inMark(room)}</td>
    <td>${outMark(room)}</td>
    <td>${escapeHtml(arrivalTimeFor(room))}</td>
    <td>${escapeHtml(cleanValue(otaFor(room)))}</td>
    <td title="${escapeHtml(notice)}">${escapeHtml(notice)}</td>
    <td>${onsite.show ? `現地 ${escapeHtml(onsite.amountText)}` : ""}</td>
    <td>${escapeHtml(instruction)}</td>
    ${buildAccessStatusCell(room, isToday, liveAccessEnabled)}
    <td><button type="button" class="csv-edit-btn" data-action="edit" data-room="${escapeHtml(room.room_number)}">指示を編集</button></td>
  </tr>`;

  if (room.room_number !== editingRoom) return baseRow;
  return baseRow + buildEditPanelRow(room, errorText);
}

// rooms: GET /api/cleaning?date=... のマージ済みroom配列(18室 + 任意のUNASSIGNED
// 行 — UNASSIGNED行はroomsByCanonicalOrder内部で除外される)。
// editingRoom: 現在編集中の room_number（無ければnull）。
// errorText: editingRoom用のインライン検証エラー（無ければ空文字）。
// liveAccessEnabled/isToday: 在室確認機能のfeature flag状態と、表示中の日付が
// 当日(JST)かどうか — どちらもデフォルトtrue(呼び出し元を省略した既存の
// テスト/呼び出しは、この列が常に見える従来通りの挙動になる)。
export function renderStaffCleaningTable(rooms, editingRoom, errorText, liveAccessEnabled = true, isToday = true) {
  const canonicalRows = roomsByCanonicalOrder(rooms);
  const bodyRows = canonicalRows
    .map((room) => buildRoomTableRow(room, editingRoom, room.room_number === editingRoom ? errorText : "", isToday, liveAccessEnabled))
    .join("");
  return `<table class="csv-table">
    <thead>
      <tr>
        <th>RoomNo</th><th>guest</th><th>status</th><th>人数</th><th>大人/子供</th><th>泊数</th>
        <th>IN</th><th>OUT</th><th>arrival</th><th>source</th><th>お知らせ</th><th>現地決済</th>
        <th>指示</th><th>在室確認</th><th></th>
      </tr>
    </thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

// 保存: クライアント側検証 -> POST /api/cleaning/override。無効な値では
// fetchImplに絶対到達しない(空欄/200文字超は呼び出し元に error だけ返す)。
export async function performSave({ date, roomNumber, rawValue, fetchImpl }) {
  const doFetch = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  const validation = validateInstructionInput(rawValue);
  if (!validation.ok) return { ok: false, error: validation.error };
  try {
    const res = await doFetch("/api/cleaning/override", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(buildOverrideSaveBody(date, roomNumber, validation.value)),
    });
    if (!res.ok) return { ok: false, error: "保存に失敗しました" };
    return { ok: true, instruction: validation.value };
  } catch (e) {
    return { ok: false, error: "保存に失敗しました" };
  }
}

// リセット: DELETE /api/cleaning/override（元の指示=source_instructionへ戻す）。
export async function performReset({ date, roomNumber, fetchImpl }) {
  const doFetch = fetchImpl || (typeof fetch !== "undefined" ? fetch : null);
  try {
    const res = await doFetch("/api/cleaning/override", {
      method: "DELETE",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(buildOverrideDeleteBody(date, roomNumber)),
    });
    if (!res.ok) return { ok: false, error: "リセットに失敗しました" };
    return { ok: true };
  } catch (e) {
    return { ok: false, error: "リセットに失敗しました" };
  }
}

// 2〜3秒表示して自動的に消えるトースト通知(要件12)。containerの再render()で
// 消えてしまわないよう、documentへ直接appendする(container.innerHTMLの外)。
function showToast(text) {
  if (typeof document === "undefined") return;
  const el = document.createElement("div");
  el.className = "csv-toast";
  el.textContent = text;
  document.body.appendChild(el);
  setTimeout(() => {
    el.remove();
  }, 2500);
}

// --- DOM配線(mountCleaningStaffView) ---
// このセクションはthinなbootstrapで、他の画面のprint-cleaning.js/today.jsと同様、
// 個別のDOM単体テストは行わない(上記の純粋関数群が実際のロジックを担う)。
// キャンセルは状態を戻して再描画するだけで、ネットワーク呼び出しは一切行わない。
export function mountCleaningStaffView(container, date) {
  let rooms = [];
  let editingRoom = null;
  let errorText = "";
  let accessModal = null; // { roomNumber, currentStatus, targetStatus, pending, errorText } | null

  function render() {
    const isToday = date === todayJst();
    const liveAccessEnabled = cleaningLiveAccessAllowed();
    const tableHtml = renderStaffCleaningTable(rooms, editingRoom, errorText, liveAccessEnabled, isToday);
    const modalHtml = buildAccessStatusModalHtml(accessModal);
    container.innerHTML = tableHtml + modalHtml;
    container.querySelectorAll("button[data-action]").forEach((btn) => {
      btn.addEventListener("click", onAction);
    });
  }

  function findRoom(roomNumber) {
    return rooms.find((r) => r.room_number === roomNumber) || null;
  }

  function handleCancel() {
    // ネットワーク呼び出しは行わない。未保存のtextarea値を破棄して閉じるだけ。
    editingRoom = null;
    errorText = "";
    render();
  }

  // 未退室/清掃可buttonを押した時点では状態変更しない。必ずconfirmation
  // modalを開くだけ(要件10)。
  function handleAccessRequest(roomNumber) {
    const room = findRoom(roomNumber);
    if (!room || !room.roomAccessStatus) return;
    accessModal = {
      roomNumber,
      currentStatus: room.roomAccessStatus,
      targetStatus: nextRoomAccessStatus(room.roomAccessStatus),
      pending: false,
      errorText: "",
    };
    render();
  }

  function handleAccessCancel() {
    // handleCancelと同じ方針: ネットワーク呼び出しは一切行わない。
    accessModal = null;
    render();
  }

  async function handleAccessConfirm() {
    if (!accessModal || accessModal.pending) return;
    const { roomNumber, targetStatus } = accessModal;
    accessModal = { ...accessModal, pending: true, errorText: "" };
    render();

    const result = await performAccessStatusChange({ date, roomNumber, status: targetStatus });
    if (!result.ok) {
      // ネットワーク失敗時は状態を先に変えない(要件12)。modalは開いたまま
      // エラーを表示し、再試行できるようにする。
      accessModal = { ...accessModal, pending: false, errorText: result.error };
      render();
      return;
    }

    const room = findRoom(roomNumber);
    if (room) {
      room.roomAccessStatus = result.status;
      room.roomAccessUpdatedAt = result.updatedAt;
    }
    accessModal = null;
    render();
    showToast(`${roomNumber}号室を${roomAccessStatusLabelJa(result.status)}にしました`);
  }

  async function handleSave(roomNumber) {
    const textarea = container.querySelector(`#csv-edit-input-${CSS.escape(roomNumber)}`);
    const rawValue = textarea ? textarea.value : "";
    const result = await performSave({ date, roomNumber, rawValue });
    if (!result.ok) {
      errorText = result.error;
      render();
      return;
    }
    const room = findRoom(roomNumber);
    if (room) {
      room.effectiveInstruction = result.instruction;
      room.hasOverride = true;
    }
    editingRoom = null;
    errorText = "";
    render();
  }

  async function handleReset(roomNumber) {
    const result = await performReset({ date, roomNumber });
    if (!result.ok) {
      errorText = result.error;
      render();
      return;
    }
    const room = findRoom(roomNumber);
    if (room) {
      room.effectiveInstruction = room.source_instruction || "";
      room.hasOverride = false;
    }
    editingRoom = null;
    errorText = "";
    render();
  }

  function onAction(e) {
    const action = e.currentTarget.getAttribute("data-action");
    const roomNumber = e.currentTarget.getAttribute("data-room");
    if (action === "edit") {
      editingRoom = roomNumber;
      errorText = "";
      render();
    } else if (action === "cancel") {
      handleCancel();
    } else if (action === "save") {
      handleSave(roomNumber);
    } else if (action === "reset") {
      handleReset(roomNumber);
    } else if (action === "access-request") {
      handleAccessRequest(roomNumber);
    } else if (action === "access-cancel") {
      handleAccessCancel();
    } else if (action === "access-confirm") {
      handleAccessConfirm();
    }
  }

  async function load() {
    container.innerHTML = `<div class="csv-loading">読み込み中…</div>`;
    try {
      const res = await fetch(`/api/cleaning?date=${encodeURIComponent(date)}`, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();
        assertNoFinancialKeys(data);
        assertNoForbiddenCleaningKeys(data);
        rooms = Array.isArray(data.rooms) ? data.rooms : [];
      } else {
        rooms = [];
      }
    } catch (e) {
      rooms = [];
    }
    render();
  }

  load();
}
