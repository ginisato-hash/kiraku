// featureFlags.js — single source of truth for feature gates in this app.
//
// CLEANING_VISUAL_READY: the cleaning-instruction sheet (print/mobile/Staff
// cleaning list) is implemented to FINAL spec, but must stay unreachable
// through normal staff navigation until acceptance against every criterion
// in the final spec has been personally verified and this flag is flipped
// by hand — never by an agent. Until then, only Daily Ops
// (arrivals/departures/stayovers) and the guest register print are
// production-ready for general staff use.
//
// Flip this to `true` only after that verification is complete. Internal
// reviewers can still preview the pages directly by appending `?preview=1`
// to the print/mobile/Daily-Ops cleaning URLs — that escape hatch is for
// this team's own QA only, never something to hand to staff.
export const CLEANING_VISUAL_READY = false;

export function isPreviewRequested() {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("preview") === "1";
}

export function cleaningVisualAllowed() {
  return CLEANING_VISUAL_READY || isPreviewRequested();
}
