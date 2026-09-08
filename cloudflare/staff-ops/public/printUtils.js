// printUtils.js — shared helpers for the print-only pages (guest register,
// cleaning sheet). Kept separate from jst.js/financialGuard.js since these
// are print-page-specific concerns.

const PLACEHOLDER_LITERALS = new Set(["none", "null", "undefined", "n/a", "na", "-", "—"]);

// Returns "" for any nullish/blank/placeholder-literal input, otherwise the
// trimmed string form of the value. This guarantees the printed page never
// shows the literal words "None"/"null"/"undefined"/"N/A" for a missing
// field (missing phone, address, room_number, etc. are all normal/expected —
// they should render as a blank line on paper, not a placeholder word).
export function printField(value) {
  if (value == null) return "";
  const s = String(value).trim();
  if (s === "") return "";
  if (PLACEHOLDER_LITERALS.has(s.toLowerCase())) return "";
  return s;
}

// Minimal HTML-escaping for values interpolated into template-literal HTML
// (guest name, address, phone, etc. come from Beds24 and are not otherwise
// sanitized before this point).
export function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Approximates a string's printed width as full-width(=2)/half-width(=1)
// glyph units, so a length-based adaptive font-size decision doesn't treat a
// short Japanese name and a long English name as equally "wide" just
// because they have similar character counts. Used by both the cleaning
// sheet and the guest register templates for their own (independently
// tuned) adaptive value-sizing helpers.
const FULL_WIDTH_RANGES = [
  [0x1100, 0x115f], [0x2e80, 0xa4cf], [0xac00, 0xd7a3],
  [0xf900, 0xfaff], [0xff00, 0xff60], [0xffe0, 0xffe6],
];
function isFullWidthChar(codePoint) {
  return FULL_WIDTH_RANGES.some(([lo, hi]) => codePoint >= lo && codePoint <= hi);
}
export function effectiveTextWidth(str) {
  let width = 0;
  for (const ch of String(str)) {
    width += isFullWidthChar(ch.codePointAt(0)) ? 2 : 1;
  }
  return width;
}

// Waits for web fonts to be ready and for a logo <img> element to settle
// (load or error — either is fine, we just don't want to print mid-decode),
// then resolves. Races against a short timeout so a slow/broken external
// logo image (the guest-register logo is an external URL outside our
// control) can never block printing indefinitely.
export function waitForPrintReady(logoImg, { timeoutMs = 2000 } = {}) {
  const fontsReady = (typeof document !== "undefined" && document.fonts && document.fonts.ready)
    ? document.fonts.ready
    : Promise.resolve();

  const imgReady = logoImg
    ? new Promise((resolve) => {
        if (logoImg.complete) {
          resolve();
          return;
        }
        logoImg.addEventListener("load", () => resolve(), { once: true });
        logoImg.addEventListener("error", () => resolve(), { once: true });
      })
    : Promise.resolve();

  const timeout = new Promise((resolve) => setTimeout(resolve, timeoutMs));

  return Promise.race([Promise.all([fontsReady, imgReady]), timeout]);
}
