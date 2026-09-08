// roomAccessStatus.js — browser-side copy of the tiny, presentation-only
// pieces of src/roomAccessState.js (the enum values + their Japanese
// labels). This intentionally duplicates rather than imports src/
// roomAccessState.js: public/ static assets cannot import src/ directly
// (the ASSETS binding only serves public/ — see roomMaster.js's comment on
// the one file that DOES need worker.js to dynamically re-serve it, because
// the 18-room physical master must never drift out of sync between Python/
// JS/browser). A 2-value enum + 2 fixed Japanese labels is not that kind of
// drift-prone shared source of truth, so it is duplicated by hand here
// instead — any change to the enum values themselves must be mirrored in
// src/roomAccessState.js by hand (mirrors src/jstDate.js's duplication of
// public/jst.js's todayJst() in the other direction).
export const WAITING_CHECKOUT = "WAITING_CHECKOUT";
export const CLEANING_ALLOWED = "CLEANING_ALLOWED";

export function roomAccessStatusLabelJa(status) {
  if (status === WAITING_CHECKOUT) return "未退室";
  if (status === CLEANING_ALLOWED) return "清掃可";
  return "";
}

export function nextRoomAccessStatus(currentStatus) {
  return currentStatus === CLEANING_ALLOWED ? WAITING_CHECKOUT : CLEANING_ALLOWED;
}
