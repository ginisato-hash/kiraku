// PLACEHOLDER TEMPLATE — pending source photo of the original paper cleaning
// sheet. Do NOT treat this visual as final. Replace this function's
// implementation once the photo is analyzed; keep the same function
// signature so worker.js/print page wiring doesn't need to change.
//
// Field/row order here is a sane default (declared array order from
// /api/cleaning, effectively room_type_key order) — NOT yet the real paper
// form's field order. Revisit once the photo is analyzed.

import { printField } from "./printUtils.js";

export const STATE_LABELS_JP = {
  TURNOVER: "引継ぎ清掃",
  STAYOVER: "連泊",
  CHECKIN: "チェックイン",
  CHECKOUT: "チェックアウト",
  VACANT: "空室",
  CANCELLED: "キャンセル",
  UNASSIGNED: "未分類",
};

export function stateLabel(state) {
  return STATE_LABELS_JP[state] || STATE_LABELS_JP.UNASSIGNED;
}

// room_number is null in essentially all real cases — show the room TYPE
// label instead so the sheet never shows a blank dash with no context.
// Exported so both the print table and the mobile card list (a genuinely
// different presentation) share this exact fallback rule.
export function roomDisplayLabel(room) {
  const roomNumber = printField(room.room_number);
  if (roomNumber) return roomNumber;
  return room.room_type_label || room.room_type_key || "—";
}

export function guestCountText(room) {
  const adults = room.adults ?? 0;
  const children = room.children ?? 0;
  const total = room.total_guests ?? (adults + children);
  return `大人 ${adults}名　子供 ${children}名　計 ${total}名`;
}

// cleaningRooms: the merged room list from GET /api/cleaning?date=... (base
// snapshot rows + any KV overrides already applied server-side).
export function renderCleaningSheetTemplate(cleaningRooms, date) {
  const rooms = Array.isArray(cleaningRooms) ? cleaningRooms : [];
  const rows = rooms.map((room) => `<tr>
      <td class="cs-room">${roomDisplayLabel(room)}</td>
      <td class="cs-state">${stateLabel(room.state)}</td>
      <td class="cs-guests">${guestCountText(room)}</td>
      <td class="cs-notes">${printField(room.notes)}</td>
    </tr>`).join("");

  const bodyRows = rows || `<tr><td colspan="4" class="cs-empty">対象日の清掃データがありません</td></tr>`;

  return `<table class="cleaning-sheet-table" data-date="${date || ""}">
    <thead>
      <tr><th>客室</th><th>状態</th><th>人数</th><th>備考</th></tr>
    </thead>
    <tbody>${bodyRows}</tbody>
  </table>`;
}

// Separate presentation function for the mobile view (large cards instead
// of a print table) — but it consumes the exact same merged cleaning-room
// list object as renderCleaningSheetTemplate, with no divergent filtering,
// so overrides (room_number/notes) show consistently in both places.
export function renderMobileCleaningRooms(cleaningRooms) {
  const rooms = Array.isArray(cleaningRooms) ? cleaningRooms : [];
  if (!rooms.length) {
    return `<div class="mc-empty">対象日の清掃データがありません</div>`;
  }
  return rooms.map((room) => {
    const state = room.state || "UNASSIGNED";
    return `<div class="mc-room-card">
      <div class="mc-room-label">${roomDisplayLabel(room)}</div>
      <span class="mc-state-badge mc-state-${state}">${stateLabel(state)}</span>
      <div class="mc-guests">${guestCountText(room)}</div>
      ${printField(room.notes) ? `<div class="mc-notes">備考: ${printField(room.notes)}</div>` : ""}
    </div>`;
  }).join("");
}
