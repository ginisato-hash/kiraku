// timingSafeEqual.js — constant-time string comparison for secret/token checks.
//
// Cloudflare Workers expose the Web Crypto (SubtleCrypto) API, not Node's
// crypto.timingSafeEqual. This does a manual constant-time byte comparison
// (accumulate XOR over every byte, never short-circuit on the first
// mismatch) so that comparing a request-supplied token against a Worker
// Secret does not leak how many leading bytes matched via response timing.
// A length mismatch DOES exit early — only the byte *content* of a
// same-length secret needs constant-time treatment; leaking the expected
// secret's length is not considered sensitive here.
export function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string") return false;
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  if (ab.length !== bb.length || ab.length === 0) return false;
  let diff = 0;
  for (let i = 0; i < ab.length; i++) diff |= ab[i] ^ bb[i];
  return diff === 0;
}
