// featureFlags.js — single source of truth for feature gates in this app.
//
// CLEANING_VISUAL_READY: the cleaning-instruction sheet (print/mobile/Staff
// cleaning list) is implemented to FINAL spec and has passed acceptance
// verification against every criterion in that spec (canonical 18-room
// master, real room-number resolution, A4 print layout, 全体通信・引継ぎ
// box, mobile view, override editing, financial/PII guards, production
// auth gates) on 2026-08-30. It is now reachable through normal staff
// navigation.
export const CLEANING_VISUAL_READY = true;

// CLEANING_LIVE_ACCESS_READY: the real-time 在室確認(room_access_status)
// feature — front-desk 未退室/清掃可 button + confirmation modal in the
// Staff cleaning list, and the live badge + WebSocket updates on
// /cleaning/today. Kept as its own flag, separate from
// CLEANING_VISUAL_READY, per spec (do not reuse the print/mobile-sheet flag
// for a different feature). Flipped to true on 2026-09-08 after
// implementation + full test suite + local two-browser acceptance + deploy
// + a real production ?preview=1 QA pass against an actual today-departing
// room (403/502/602) confirmed the exact modal copy, <2s realtime
// propagation to the cleaner view (~315ms confirm, ~339ms undo measured),
// reconnect+full-resync correctness, cross-room isolation, and unauthenticated
// 401s on every new endpoint — mirrors how CLEANING_VISUAL_READY itself was
// rolled out.
export const CLEANING_LIVE_ACCESS_READY = true;

export function isPreviewRequested() {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("preview") === "1";
}

export function cleaningVisualAllowed() {
  return CLEANING_VISUAL_READY || isPreviewRequested();
}

export function cleaningLiveAccessAllowed() {
  return CLEANING_LIVE_ACCESS_READY || isPreviewRequested();
}
