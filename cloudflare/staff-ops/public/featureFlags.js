// featureFlags.js — single source of truth for feature gates in this app.
//
// CLEANING_VISUAL_READY: the cleaning-instruction sheet's print/mobile visual
// is still a PLACEHOLDER pending the source photo of the real paper form
// (see cleaningSheetTemplate.js's own top-of-file comment). Until that photo
// is analyzed and the real visual is reproduced and accepted, the
// provisional table must NEVER be reachable through normal staff
// navigation — only Daily Ops (arrivals/departures/stayovers) and the guest
// register print are production-ready for general staff use.
//
// Flip this to `true` only after the real cleaning-sheet visual has been
// built from the source photo and has passed acceptance review. Internal
// reviewers can still preview the provisional pages directly by appending
// `?preview=1` to the print/mobile cleaning URLs — that escape hatch is for
// this team's own QA only, never something to hand to staff.
export const CLEANING_VISUAL_READY = false;

export function isPreviewRequested() {
  if (typeof window === "undefined") return false;
  return new URLSearchParams(window.location.search).get("preview") === "1";
}

export function cleaningVisualAllowed() {
  return CLEANING_VISUAL_READY || isPreviewRequested();
}
