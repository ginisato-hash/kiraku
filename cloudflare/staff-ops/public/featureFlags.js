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

export function isPreviewRequested() {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("preview") === "1";
}

export function cleaningVisualAllowed() {
  return CLEANING_VISUAL_READY || isPreviewRequested();
}
