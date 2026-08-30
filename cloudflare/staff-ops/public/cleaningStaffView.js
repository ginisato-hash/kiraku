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
  statusLabel, guestNameFor, guestCountFor, nightProgressFor, arrivalTimeFor,
  otaFor, cleanValue, inMark, outMark, roomsByCanonicalOrder,
} from "./cleaningSheetTemplate.js";
import { escapeHtml } from "./printUtils.js";
import { assertNoFinancialKeys, assertNoForbiddenCleaningKeys } from "./financialGuard.js";

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

function buildEditPanelRow(room, errorText) {
  const instruction = cleanValue(room.effectiveInstruction);
  return `<tr class="csv-edit-row" data-room-number="${escapeHtml(room.room_number)}">
    <td colspan="11">
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

function buildRoomTableRow(room, editingRoom, errorText) {
  const instruction = cleanValue(room.effectiveInstruction);
  const baseRow = `<tr data-room-number="${escapeHtml(room.room_number)}">
    <td>${escapeHtml(room.room_number)}</td>
    <td>${escapeHtml(guestNameFor(room))}</td>
    <td>${escapeHtml(statusLabel(room.status))}</td>
    <td>${escapeHtml(guestCountFor(room))}</td>
    <td>${escapeHtml(nightProgressFor(room))}</td>
    <td>${inMark(room)}</td>
    <td>${outMark(room)}</td>
    <td>${escapeHtml(arrivalTimeFor(room))}</td>
    <td>${escapeHtml(cleanValue(otaFor(room)))}</td>
    <td>${escapeHtml(instruction)}</td>
    <td><button type="button" class="csv-edit-btn" data-action="edit" data-room="${escapeHtml(room.room_number)}">指示を編集</button></td>
  </tr>`;

  if (room.room_number !== editingRoom) return baseRow;
  return baseRow + buildEditPanelRow(room, errorText);
}

// rooms: GET /api/cleaning?date=... のマージ済みroom配列(18室 + 任意のUNASSIGNED
// 行 — UNASSIGNED行はroomsByCanonicalOrder内部で除外される)。
// editingRoom: 現在編集中の room_number（無ければnull）。
// errorText: editingRoom用のインライン検証エラー（無ければ空文字）。
export function renderStaffCleaningTable(rooms, editingRoom, errorText) {
  const canonicalRows = roomsByCanonicalOrder(rooms);
  const bodyRows = canonicalRows
    .map((room) => buildRoomTableRow(room, editingRoom, room.room_number === editingRoom ? errorText : ""))
    .join("");
  return `<table class="csv-table">
    <thead>
      <tr>
        <th>RoomNo</th><th>guest</th><th>status</th><th>人数</th><th>泊数</th>
        <th>IN</th><th>OUT</th><th>arrival</th><th>source</th><th>指示</th><th></th>
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

// --- DOM配線(mountCleaningStaffView) ---
// このセクションはthinなbootstrapで、他の画面のprint-cleaning.js/today.jsと同様、
// 個別のDOM単体テストは行わない(上記の純粋関数群が実際のロジックを担う)。
// キャンセルは状態を戻して再描画するだけで、ネットワーク呼び出しは一切行わない。
export function mountCleaningStaffView(container, date) {
  let rooms = [];
  let editingRoom = null;
  let errorText = "";

  function render() {
    container.innerHTML = renderStaffCleaningTable(rooms, editingRoom, errorText);
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
