// guestRegisterTemplate.js — pure function: one arrival booking -> one
// printed "宿泊者名簿 / GUEST REGISTRATION FORM" page (HTML string). No DOM,
// no fetch — fully unit-testable. Consumed by
// public/ops/print/print-guest-register.js, which does the fetch/DOM/print
// orchestration.
//
// Nationality/passport fields are intentionally excluded per spec.

import { printField } from "./printUtils.js";

const COMPANION_ROW_COUNT = 5;

function roomCellHtml(arrival) {
  const roomNumber = printField(arrival.room_number);
  if (roomNumber) return roomNumber;
  const typeLabel = printField(arrival.room_type_label) || printField(arrival.room_type_key);
  // Label this clearly as a room TYPE so nobody mistakes it for an assigned
  // room number (room_number is null in essentially all real cases today).
  return typeLabel ? `タイプ: ${typeLabel}` : "";
}

function addressHtml(address) {
  const a = (address && typeof address === "object") ? address : {};
  const postcode = printField(a.postcode);
  const line1 = postcode ? `郵便番号: ${postcode}` : "";
  const line2 = [printField(a.prefecture), printField(a.city), printField(a.rest)]
    .filter(Boolean).join("");
  return [line1, line2].filter(Boolean).join("<br>");
}

function guestCountText(arrival) {
  const adults = arrival.adults ?? 0;
  const children = arrival.children ?? 0;
  const total = arrival.total_guests ?? (adults + children);
  return `大人 ${adults}名　子供 ${children}名　計 ${total}名`;
}

function companionRowsHtml() {
  let rows = "";
  for (let i = 1; i <= COMPANION_ROW_COUNT; i++) {
    rows += `<tr><td class="grf-companion-no">${i}</td><td></td><td></td></tr>`;
  }
  return rows;
}

// isLast controls whether this page gets break-after:page (all pages except
// the very last one, so we never emit a trailing blank page).
export function buildGuestRegisterPageHtml(arrival, isLast) {
  const a = arrival || {};
  const pageClass = isLast ? "grf-page" : "grf-page grf-page-break";
  return `<section class="${pageClass}" data-booking-id="${printField(a.booking_id)}">
    <header class="grf-header">
      <img class="grf-logo" src="https://media.xmlcal.com/pic/p0033/0695/02.png" alt="喜らく"
        crossorigin="anonymous" onerror="this.style.display='none'" />
      <div class="grf-property">
        <div>PROPERTY: 喜らく</div>
        <div>〒990-2301 山形県山形市蔵王温泉935-25</div>
      </div>
    </header>

    <h1 class="grf-title">宿泊者名簿<br><span class="grf-title-en">GUEST REGISTRATION FORM</span></h1>

    <section class="grf-printed">
      <table class="grf-printed-table">
        <tbody>
          <tr><th>予約番号</th><td>${printField(a.booking_id)}</td></tr>
          <tr><th>宿泊期間</th><td>${printField(a.checkin_date)} → ${printField(a.checkout_date)}</td></tr>
          <tr><th>客室</th><td>${roomCellHtml(a)}</td></tr>
          <tr><th>宿泊人数</th><td>${guestCountText(a)}</td></tr>
          <tr><th>代表者氏名</th><td>${printField(a.guest_name)}</td></tr>
          <tr><th>住所</th><td>${addressHtml(a.address)}</td></tr>
          <tr><th>電話番号</th><td>${printField(a.phone)}</td></tr>
        </tbody>
      </table>
    </section>

    <section class="grf-handwritten">
      <div class="grf-handwritten-label">手書き記入欄 / TO BE COMPLETED BY GUEST</div>
      <div class="grf-age-line">年齢 Age: <span class="grf-age-blank"></span></div>
      <table class="grf-companion-table">
        <thead><tr><th>No.</th><th>氏名 Name</th><th>年齢 Age</th></tr></thead>
        <tbody>${companionRowsHtml()}</tbody>
      </table>
      <p class="grf-confirmation">
        上記の記載内容および印字された登録内容を確認し、相違ありません。<br>
        I confirm that the information above is complete and correct.
      </p>
      <div class="grf-signature">
        <span class="grf-signature-label">ご署名 / SIGNATURE</span>
        <div class="grf-signature-line"></div>
      </div>
    </section>
  </section>`;
}

export function buildGuestRegisterDocumentHtml(arrivals) {
  const list = Array.isArray(arrivals) ? arrivals : [];
  return list.map((a, i) => buildGuestRegisterPageHtml(a, i === list.length - 1)).join("");
}
