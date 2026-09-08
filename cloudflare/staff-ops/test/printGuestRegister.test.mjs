// guestRegisterTemplate.js の純粋関数テスト + guest-register.html のソース内容テスト。
// 2026-08-30: Beds24実運用版デザインへの移植後のテスト（寸法・文言はsource of truth
// として固定されているため、このテストは「意図通りにportされているか」を確認する）。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  buildGuestRegisterDocumentHtml,
  renderGuestRegisterSheet,
  cleanGuestRegisterValue,
  buildGuestRegisterAddress,
  guestRegisterValueSizeClass,
} from "../public/guestRegisterTemplate.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const arrivals = fixture.dates["2026-08-30"].arrivals; // [山田太郎(住所/電話あり,room_number null), 佐藤花子(住所/電話null)]
const htmlPath = path.join(dir, "../public/ops/print/guest-register.html");
const html = readFileSync(htmlPath, "utf-8");
const bootstrapJs = readFileSync(path.join(dir, "../public/ops/print/print-guest-register.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------- cleanGuestRegisterValue ----------------

await check("cleanGuestRegisterValue: null/undefined/blank/None/null/undefined/N-A literals all -> ''", async () => {
  for (const v of [null, undefined, "", "   ", "None", "null", "undefined", "N/A", "n/a"]) {
    assert.equal(cleanGuestRegisterValue(v), "", `expected '' for ${JSON.stringify(v)}`);
  }
});

await check("cleanGuestRegisterValue: a real value passes through trimmed", async () => {
  assert.equal(cleanGuestRegisterValue("  山田 太郎  "), "山田 太郎");
});

// ---------------- buildGuestRegisterAddress ----------------

await check("buildGuestRegisterAddress: full address -> '〒postcode prefecture+city+address'", async () => {
  const addr = buildGuestRegisterAddress({ postcode: "990-2301", prefecture: "山形県", city: "山形市", address: "蔵王温泉935-25" });
  assert.equal(addr, "〒990-2301 山形県山形市蔵王温泉935-25");
});

await check("buildGuestRegisterAddress: falls back to `state` when `prefecture` is absent", async () => {
  const addr = buildGuestRegisterAddress({ postcode: "990-2301", state: "山形県", city: "山形市", address: "蔵王温泉935-25" });
  assert.equal(addr, "〒990-2301 山形県山形市蔵王温泉935-25");
});

await check("buildGuestRegisterAddress: no postcode, no locality -> '' (fully blank, no bare 〒)", async () => {
  assert.equal(buildGuestRegisterAddress({}), "");
  assert.equal(buildGuestRegisterAddress({ postcode: null, prefecture: null, city: null, address: null }), "");
});

await check("buildGuestRegisterAddress: postcode present, locality empty -> '〒postcode' only, no trailing space", async () => {
  assert.equal(buildGuestRegisterAddress({ postcode: "990-2301" }), "〒990-2301");
});

// ---------------- renderGuestRegisterSheet: fixed design elements ----------------

await check("renderGuestRegisterSheet root element is .guest-register-sheet (new port), not the old .grf-page", async () => {
  const page = renderGuestRegisterSheet({ bookingId: "1", guestName: "テスト", room: "5", totalGuests: 1, checkIn: "2026-08-30", checkOut: "2026-08-31" });
  assert.ok(page.includes('class="guest-register-sheet"'));
  assert.ok(!page.includes("grf-page"));
});

await check("renderGuestRegisterSheet includes the fixed facility block (ホテル喜らく / Zao Spa hotel Kiraku, 〒990-2301, 山形県山形市蔵王温泉935-25)", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(page.includes("ホテル喜らく / Zao Spa hotel Kiraku"));
  assert.ok(page.includes("〒990-2301"));
  assert.ok(page.includes("山形県山形市蔵王温泉935-25"));
});

await check("renderGuestRegisterSheet logo image is 76px wide with the fixed external URL", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(page.includes('src="https://media.xmlcal.com/pic/p0033/0695/02.png"'));
  assert.ok(page.includes('width="76"'));
  assert.ok(page.includes("width:76px"));
});

await check("renderGuestRegisterSheet title is 宿泊者名簿 / GUEST REGISTRATION FORM, no OTA anywhere", async () => {
  const page = renderGuestRegisterSheet({ ...toBookingLike(arrivals[0]) });
  assert.ok(page.includes("宿泊者名簿"));
  assert.ok(page.includes("GUEST REGISTRATION FORM"));
  assert.ok(!/楽天トラベル|じゃらん|Booking\.com|Direct|OTA/i.test(page), "OTA must never appear on the guest register");
});

await check("renderGuestRegisterSheet shows 宿泊人数 as '計 N 名' only (no 大人/子供 breakdown)", async () => {
  const page = renderGuestRegisterSheet({ totalGuests: 3 });
  assert.ok(page.includes("3 名"));
  assert.ok(!/大人|子供/.test(page), "adults/children breakdown must not appear on the guest register (Daily Ops only)");
});

await check("renderGuestRegisterSheet falls back to adults+children when totalGuests is not finite", async () => {
  const page = renderGuestRegisterSheet({ adults: 2, children: 1 });
  assert.ok(page.includes("3 名"));
});

await check("renderGuestRegisterSheet room field is blank (not a room-TYPE label) when room is absent", async () => {
  const page = renderGuestRegisterSheet({ room: null });
  // The pre-filled Room No. cell must render with nothing between its opening tag and </td>,
  // i.e. no fallback text like "タイプ: ..." was ever introduced here.
  assert.ok(!/タイプ:/.test(page));
});

await check("renderGuestRegisterSheet stay dates use the Japanese-weekday format (2026年8月29日, 土曜日)", async () => {
  const page = renderGuestRegisterSheet({ checkIn: "2026-08-29", checkOut: "2026-08-30" });
  assert.ok(page.includes("2026年8月29日, 土曜日"));
  assert.ok(page.includes("2026年8月30日, 日曜日"));
});

await check("renderGuestRegisterSheet never contains the literal words null/undefined/None/N-A for a booking with missing phone/address", async () => {
  const page = renderGuestRegisterSheet(toBookingLike(arrivals[1])); // 佐藤花子: phone=null, address=null
  assert.ok(!/\bnull\b|\bundefined\b|\bNone\b|N\/A/i.test(page), `page must not contain placeholder literals: ${page}`);
});

await check("renderGuestRegisterSheet has exactly 5 companion rows (78mm section)", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(page.includes("height:78mm"));
  const rowMatches = page.match(/<tr style="height:14mm;">/g) || [];
  // 1 age row + 5 companion rows all share height:14mm
  assert.equal(rowMatches.length, 6, "expected 1 age row + 5 companion rows at height:14mm");
});

await check("renderGuestRegisterSheet confirmation sentence and SIGNATURE section are present", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(page.includes("上記の記載内容および印字された登録内容を確認し、相違ありません。"));
  assert.ok(page.includes("I confirm that the information above is complete and correct."));
  assert.ok(page.includes("ご署名"));
  assert.ok(page.includes("SIGNATURE"));
});

await check("renderGuestRegisterSheet has no nationality/passport fields", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(!/国籍|パスポート|nationality|passport/i.test(page));
});

await check("renderGuestRegisterSheet has no financial/price/OTA-commission wording", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(!/revenue|price|payment|invoice|commission|ADR|RevPAR|円|¥/i.test(page));
});

await check("renderGuestRegisterSheet escapes HTML-special characters in guest name", async () => {
  const page = renderGuestRegisterSheet({ guestName: '<script>alert(1)</script>&"\'' });
  assert.ok(!page.includes("<script>alert(1)</script>"));
  assert.ok(page.includes("&lt;script&gt;"));
});

// ---------------- 2026-09: value-only print typography ----------------
// 現場フィードバック「印刷するとゲスト情報の文字が小さく見づらい」対応。
// 対象は7つの実データ値セルのみ(ラベル/見出し/記入欄は一切変更しない)。

await check("guestRegisterValueSizeClass: short values get only the base class (no -long modifier)", async () => {
  assert.equal(guestRegisterValueSizeClass("reservation", "89381508"), "gr-value-reservation");
  assert.equal(guestRegisterValueSizeClass("name", "佐藤 太郎"), "gr-value-name");
  assert.equal(guestRegisterValueSizeClass("address", "〒990-2301 山形県山形市蔵王温泉935-25"), "gr-value-address");
  assert.equal(guestRegisterValueSizeClass("phone", "090-1234-5678"), "gr-value-phone");
});

await check("guestRegisterValueSizeClass: fields with no configured threshold (guests/stay/room) always return only the base class", async () => {
  assert.equal(guestRegisterValueSizeClass("guests", "2 名"), "gr-value-guests");
  assert.equal(guestRegisterValueSizeClass("stay", "x".repeat(500)), "gr-value-stay");
  assert.equal(guestRegisterValueSizeClass("room", "x".repeat(500)), "gr-value-room");
});

await check("guestRegisterValueSizeClass: a long reservation number adds the -long modifier", async () => {
  assert.equal(guestRegisterValueSizeClass("reservation", "RESV-2026-0910-INTL-0042"), "gr-value-reservation gr-value-reservation-long");
});

await check("guestRegisterValueSizeClass: a moderately long English guest name (33 half-width chars) stays at the base size — only truly long names shrink", async () => {
  assert.equal(guestRegisterValueSizeClass("name", "ALEXANDER CHRISTOPHER WASHINGTON"), "gr-value-name");
});

await check("guestRegisterValueSizeClass: a very long guest name adds the -long modifier", async () => {
  assert.equal(guestRegisterValueSizeClass("name", "Maria Fernanda Gonzalez-Rodriguez de la Torre"), "gr-value-name gr-value-name-long");
});

await check("guestRegisterValueSizeClass: a long international address adds the -long modifier", async () => {
  const longAddress = "Avenida Paseo de la Reforma 1234, Colonia Lomas de Chapultepec, Edificio Torre Norte, Piso 15, Departamento 1502";
  assert.equal(guestRegisterValueSizeClass("address", longAddress), "gr-value-address gr-value-address-long");
});

await check("guestRegisterValueSizeClass: a long phone number (with extension notes) adds the -long modifier", async () => {
  assert.equal(
    guestRegisterValueSizeClass("phone", "+52-55-1234-5678 ext. 9012 (mobile, please call after 6pm JST)"),
    "gr-value-phone gr-value-phone-long",
  );
});

await check("guestRegisterValueSizeClass: empty/null values never get the -long modifier (never crash on an empty cell)", async () => {
  assert.equal(guestRegisterValueSizeClass("phone", null), "gr-value-phone");
  assert.equal(guestRegisterValueSizeClass("address", ""), "gr-value-address");
  assert.equal(guestRegisterValueSizeClass("name", undefined), "gr-value-name");
});

await check("renderGuestRegisterSheet: all 7 value cells carry their gr-value-* class", async () => {
  const page = renderGuestRegisterSheet(toBookingLike(arrivals[0]));
  assert.ok(/class="gr-value-reservation[^"]*"/.test(page));
  assert.ok(/class="gr-value-guests"/.test(page));
  assert.ok(/class="gr-value-stay"/.test(page));
  assert.ok(/class="gr-value-room"/.test(page));
  assert.ok(/class="gr-value-name[^"]*"/.test(page));
  assert.ok(/class="gr-value-address[^"]*"/.test(page));
  assert.ok(/class="gr-value-phone[^"]*"/.test(page));
});

await check("renderGuestRegisterSheet: a long guest name renders with the gr-value-name-long modifier applied", async () => {
  const page = renderGuestRegisterSheet({ guestName: "Maria Fernanda Gonzalez-Rodriguez de la Torre" });
  assert.ok(page.includes('class="gr-value-name gr-value-name-long"'));
});

await check("renderGuestRegisterSheet: no gr-value-* class ever appears on a label cell (予約番号/宿泊人数/宿泊期間/客室番号/代表者氏名/住所/電話番号 labels are untouched)", async () => {
  const page = renderGuestRegisterSheet(toBookingLike(arrivals[0]));
  for (const label of ["予約番号", "宿泊人数", "宿泊期間", "客室番号", "代表者氏名", "住所", "電話番号"]) {
    const idx = page.indexOf(label);
    assert.ok(idx > -1, `expected label ${label} to be present`);
    const cellStart = page.lastIndexOf("<td", idx);
    const cellOpenTag = page.slice(cellStart, page.indexOf(">", cellStart) + 1);
    assert.ok(!cellOpenTag.includes("gr-value"), `label cell for ${label} must not carry a gr-value-* class: ${cellOpenTag}`);
  }
});

await check("renderGuestRegisterSheet: title/section headings/sub-labels/handwritten section retain their exact original inline font-sizes (unchanged)", async () => {
  const page = renderGuestRegisterSheet({});
  assert.ok(page.includes("font-size:26px;font-weight:600;letter-spacing:0.16em;color:#1f1f1f;"), "宿泊者名簿 title font-size must be unchanged");
  assert.ok(page.includes("font-size:10.5px;font-weight:600;letter-spacing:0.03em;color:#222222"), "ご予約情報 heading font-size must be unchanged");
  assert.ok(page.includes("font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;"), "English sub-labels (Reservation No. etc.) font-size must be unchanged");
  assert.ok(page.includes("font-size:16px;font-weight:600;letter-spacing:0.07em;"), "ご署名 heading font-size must be unchanged");
  assert.ok(!/年齢[\s\S]{0,120}gr-value/.test(page), "the handwritten 年齢/同行者 section must never carry a gr-value-* class");
});

await check("renderGuestRegisterSheet: companion-row structure (5 rows, height:14mm) is unaffected by the value-typography change", async () => {
  const page = renderGuestRegisterSheet({});
  const rowMatches = page.match(/<tr style="height:14mm;">/g) || [];
  assert.equal(rowMatches.length, 6);
});

await check("renderGuestRegisterSheet: row heights (11mm/11mm/11mm/12mm/16mm/11mm) for the 7 value rows are unchanged", async () => {
  const page = renderGuestRegisterSheet({});
  const heights = [...page.matchAll(/<tr style="height:(\d+mm);">/g)].map((m) => m[1]);
  // first 5 pre-filled rows: reservation/guests(11mm), stay(11mm), room(11mm), name(12mm), address(16mm), phone(11mm)
  assert.deepEqual(heights.slice(0, 6), ["11mm", "11mm", "11mm", "12mm", "16mm", "11mm"]);
});

await check("renderGuestRegisterSheet: escapes HTML-special characters in a long guest name too (adaptive sizing does not bypass escaping)", async () => {
  const page = renderGuestRegisterSheet({ guestName: "<script>alert(1)</script>".repeat(2) });
  assert.ok(!page.includes("<script>alert(1)</script>"));
  assert.ok(page.includes("&lt;script&gt;"));
});

await check("guest-register.html: the 7 gr-value-* base rules exist with the expected font-size, and no rule regresses below the old 11.5px baseline", async () => {
  const rules = {
    "gr-value-reservation": 14,
    "gr-value-guests": 15,
    "gr-value-stay": 14,
    "gr-value-room": 15,
    "gr-value-name": 16,
    "gr-value-address": 14,
    "gr-value-phone": 14.5,
  };
  for (const [cls, expected] of Object.entries(rules)) {
    const m = html.match(new RegExp(`\\.${cls}\\s*{[^}]*font-size:\\s*([\\d.]+)px`));
    assert.ok(m, `expected a .${cls} rule with font-size in guest-register.html`);
    assert.equal(Number(m[1]), expected, `.${cls} font-size mismatch`);
    assert.ok(Number(m[1]) > 11.5, `.${cls} must be strictly larger than the old 11.5px base`);
  }
});

await check("guest-register.html: the -long modifiers exist, are smaller than their base but still above the old 11.5px baseline", async () => {
  const longRules = {
    "gr-value-reservation-long": 12.5,
    "gr-value-name-long": 14,
    "gr-value-address-long": 13,
    "gr-value-phone-long": 13,
  };
  for (const [cls, expected] of Object.entries(longRules)) {
    const m = html.match(new RegExp(`\\.${cls}\\s*{[^}]*font-size:\\s*([\\d.]+)px`));
    assert.ok(m, `expected a .${cls} rule with font-size in guest-register.html`);
    assert.equal(Number(m[1]), expected, `.${cls} font-size mismatch`);
    assert.ok(Number(m[1]) > 11.5, `.${cls} must stay strictly larger than the old 11.5px base even when shrunk for long values`);
  }
});

await check("guest-register.html: base .guest-register-sheet dimensions/margins are unchanged by this typography-only change", async () => {
  assert.ok(html.includes("width: 186mm;"));
  assert.ok(html.includes("height: 267mm;"));
  assert.ok(/@page\s*{[^}]*margin:\s*10mm\s+12mm/s.test(html));
});

function toBookingLike(arrival) {
  const addr = (arrival.address && typeof arrival.address === "object") ? arrival.address : {};
  return {
    bookingId: arrival.booking_id,
    guestName: arrival.guest_name,
    room: arrival.room_number,
    adults: arrival.adults,
    children: arrival.children,
    totalGuests: arrival.total_guests,
    checkIn: arrival.checkin_date,
    checkOut: arrival.checkout_date,
    phone: arrival.phone,
    postcode: addr.postcode,
    prefecture: addr.prefecture,
    city: addr.city,
    address: addr.rest,
  };
}

// ---------------- buildGuestRegisterDocumentHtml ----------------

await check("buildGuestRegisterDocumentHtml: one .guest-register-sheet per arrival, siblings (no wrapping element per item)", async () => {
  const doc = buildGuestRegisterDocumentHtml(arrivals); // 2 arrivals
  const sheetCount = (doc.match(/class="guest-register-sheet"/g) || []).length;
  assert.equal(sheetCount, arrivals.length);
});

await check("buildGuestRegisterDocumentHtml: 0 arrivals -> empty string, no crash", async () => {
  assert.equal(buildGuestRegisterDocumentHtml([]), "");
});

await check("buildGuestRegisterDocumentHtml: real fixture data renders with no null/undefined leakage across both arrivals", async () => {
  const doc = buildGuestRegisterDocumentHtml(arrivals);
  assert.ok(!/\bnull\b|\bundefined\b/i.test(doc));
});

// ---------------- source-text assertions on guest-register.html ----------------

await check("guest-register.html declares @page size: A4 portrait, margin 10mm 12mm", async () => {
  assert.ok(/@page\s*{[^}]*size:\s*A4\s+portrait/s.test(html));
  assert.ok(/@page\s*{[^}]*margin:\s*10mm\s+12mm/s.test(html));
});

await check("guest-register.html declares the exact .guest-register-sheet dimensions (186mm x 267mm)", async () => {
  assert.ok(html.includes("width: 186mm;"));
  assert.ok(html.includes("height: 267mm;"));
});

await check("guest-register.html declares break-after:page for .guest-register-sheet and break-after:auto for :last-child", async () => {
  assert.ok(/@media print\s*{[\s\S]*\.guest-register-sheet\s*{[^}]*break-after:\s*page/.test(html));
  assert.ok(/\.guest-register-sheet:last-child\s*{[^}]*break-after:\s*auto/.test(html));
});

await check("guest-register.html hides .no-print under @media print", async () => {
  assert.ok(/@media print\s*{[\s\S]*\.no-print\s*{\s*display:\s*none/.test(html));
});

await check("guest-register.html has no nationality/passport fields", async () => {
  assert.ok(!/国籍|パスポート|nationality|passport/i.test(html));
});

await check("print bootstrap waits for print-ready (fonts+logo) before calling window.print()", async () => {
  const mainBody = bootstrapJs.slice(bootstrapJs.indexOf("async function main"));
  const printIdx = mainBody.indexOf("window.print()");
  const waitIdx = mainBody.indexOf("await waitForPrintReady");
  assert.ok(waitIdx >= 0, "main() should call waitForPrintReady");
  assert.ok(printIdx >= 0, "main() should call window.print()");
  assert.ok(waitIdx < printIdx, "waitForPrintReady must be awaited before window.print()");
});

await check("print bootstrap does not navigate away or blank content on afterprint", async () => {
  assert.ok(bootstrapJs.includes('addEventListener("afterprint"'));
  const body = bootstrapJs.slice(bootstrapJs.indexOf('addEventListener("afterprint"'));
  assert.ok(!body.includes("location.href"), "afterprint handler must not navigate away");
  assert.ok(!body.includes("innerHTML = \"\""), "afterprint handler must not blank the page");
});

console.log(`\n${passed} print guest-register checks passed`);
