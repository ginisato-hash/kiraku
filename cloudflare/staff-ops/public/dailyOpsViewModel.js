// dailyOpsViewModel.js — 喜らく Daily Ops screen: raw /api/daily-ops JSON ->
// view-model objects. dailyOps.js should not read raw booking fields
// directly; it should go through the functions here (mirrors bi-web's
// app.js -> biViewModel.js -> components.js layering).
//
// This module intentionally never touches or forwards anything
// revenue/price/commission/ADR/RevPAR/payment/invoice-shaped — the daily-ops
// data contract has none of that on purpose.

const DASH = "—";

function isNil(v) {
  return v == null || v === "";
}

function safeNum(v) {
  return isNil(v) ? 0 : Number(v) || 0;
}

// "2026-08-30" -> "2026年8月30日"
export function formatDateJp(dateStr) {
  const m = typeof dateStr === "string" ? /^(\d{4})-(\d{2})-(\d{2})/.exec(dateStr) : null;
  if (!m) return DASH;
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`;
}

// Summary card math shared by arrivals/departures/stayovers: room count +
// total guest count + adults/children breakdown.
export function summarizeBookings(list) {
  const rows = Array.isArray(list) ? list : [];
  let adults = 0;
  let children = 0;
  for (const r of rows) {
    adults += safeNum(r && r.adults);
    children += safeNum(r && r.children);
  }
  return {
    roomCount: rows.length,
    adults,
    children,
    totalGuests: adults + children,
  };
}

// One arrivals/departures/stayovers row, normalized for display. Every
// nullable contract field (room_number, arrival_time, phone, notes,
// address, ota_name) gets a graceful fallback here so the UI never has to
// special-case "undefined"/"null" itself.
export function buildBookingRow(raw) {
  const r = raw || {};
  const roomLabel = !isNil(r.room_number) ? String(r.room_number) : null;
  return {
    bookingId: r.booking_id || DASH,
    guestName: r.guest_name || "氏名未取得",
    otaName: r.ota_name || "Direct",
    roomTypeKey: r.room_type_key || null,
    roomTypeLabel: r.room_type_label || r.room_type_key || "未分類",
    roomNumber: roomLabel,
    // Whether we have a real assigned room number to show, or must fall
    // back to the room TYPE label (room_number is null in essentially all
    // real cases today — Beds24 only exposes room TYPE for this property).
    hasRoomNumber: roomLabel != null,
    adults: safeNum(r.adults),
    children: safeNum(r.children),
    totalGuests: !isNil(r.total_guests) ? safeNum(r.total_guests) : safeNum(r.adults) + safeNum(r.children),
    checkinDate: r.checkin_date || DASH,
    checkoutDate: r.checkout_date || DASH,
    arrivalTime: r.arrival_time || null,
    phone: r.phone || null,
    notes: r.notes || null,
    address: (r.address && typeof r.address === "object") ? r.address : null,
    status: r.status || "unknown",
  };
}

function buildSummaryCard(label, summary, extra) {
  return {
    label,
    roomCount: summary.roomCount,
    totalGuests: summary.totalGuests,
    adults: summary.adults,
    children: summary.children,
    ...extra,
  };
}

// dayData is the raw object returned by GET /api/daily-ops?date=... (i.e.
// snapshot.dates[date] from the Python-produced snapshot). date is the
// requested date string, used as a fallback if dayData.date is absent.
export function buildDailyOpsViewModel(dayData, date) {
  const d = dayData || {};
  const arrivalsRaw = Array.isArray(d.arrivals) ? d.arrivals : [];
  const departuresRaw = Array.isArray(d.departures) ? d.departures : [];
  const stayoversRaw = Array.isArray(d.stayovers) ? d.stayovers : [];

  const arrivalsSummary = summarizeBookings(arrivalsRaw);
  const departuresSummary = summarizeBookings(departuresRaw);
  const stayoversSummary = summarizeBookings(stayoversRaw);

  return {
    date: d.date || date || null,
    dateLabelJp: formatDateJp(d.date || date),
    summaryCards: [
      buildSummaryCard("本日の到着", arrivalsSummary),
      buildSummaryCard("本日の出発", departuresSummary),
      buildSummaryCard("連泊", stayoversSummary),
    ],
    arrivals: arrivalsRaw.map(buildBookingRow),
    departures: departuresRaw.map(buildBookingRow),
    stayovers: stayoversRaw.map(buildBookingRow),
    hasArrivals: arrivalsRaw.length > 0,
    hasDepartures: departuresRaw.length > 0,
    hasStayovers: stayoversRaw.length > 0,
  };
}

// Freshness indicator — same shape/logic pattern as bi-web's
// biViewModel.formatFreshness (nowMs passed explicitly by the caller so this
// stays a pure, testable function).
const STALE_THRESHOLD_MS = 30 * 60 * 1000; // 30分

export function formatFreshness(generatedAtJstIso, nowMs) {
  if (!generatedAtJstIso) return { text: "", stale: false };
  const generated = new Date(generatedAtJstIso);
  if (Number.isNaN(generated.getTime())) return { text: "", stale: false };
  const ageMs = nowMs - generated.getTime();
  const ageMin = Math.max(0, Math.round(ageMs / 60000));
  const stale = ageMs > STALE_THRESHOLD_MS;
  const agoText = ageMin <= 0 ? "1分未満前" : `${ageMin}分前`;
  const pad2 = (n) => String(n).padStart(2, "0");
  const dateLabel = `${generated.getFullYear()}/${pad2(generated.getMonth() + 1)}/${pad2(generated.getDate())} `
    + `${pad2(generated.getHours())}:${pad2(generated.getMinutes())}`;
  const text = stale
    ? `最終更新: ${dateLabel}（${agoText}・更新が遅れています）`
    : `最終更新: ${dateLabel}（${agoText}）`;
  return { text, stale, ageMin };
}
