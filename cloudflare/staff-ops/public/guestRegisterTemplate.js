// guestRegisterTemplate.js — one arrival booking -> one printed "宿泊者名簿"
// page (HTML string). No DOM, no fetch — fully unit-testable.
//
// 2026-08-30: this is a byte-for-byte port of the Beds24-proven guest
// register design (see the task history for the exact spec) — dimensions,
// margins, logo size, title position, font sizes, column widths, row
// heights, borders, background colors, section order, companion/signature
// sections, wording, and English bilingual lines are ALL fixed to match the
// existing Beds24 printout. Do not "modernize" or "align with the Staff Ops
// component style" — this file's HTML/inline-styles are the source of
// truth handed down from that design, not something to be refactored for
// its own sake. If it ever needs to change again, the change must come from
// a fresh comparison against the real paper/Beds24 original, not from
// this codebase's own design sense.
//
// Nationality/passport fields remain intentionally excluded per the
// existing product decision (unchanged from before this port).

import { escapeHtml } from "./printUtils.js";
import { formatJapaneseDateWithWeekday } from "./jst.js";

const COMPANION_ROW_COUNT = 5;

export function cleanGuestRegisterValue(value) {
  if (value == null) return "";

  const v = String(value).trim();

  if (
    !v ||
    /^(none|null|undefined|n\/a)$/i.test(v)
  ) {
    return "";
  }

  return v;
}

export function buildGuestRegisterAddress(booking) {
  const postcode = cleanGuestRegisterValue(booking.postcode);
  const prefecture = cleanGuestRegisterValue(
    booking.prefecture ?? booking.state
  );
  const city = cleanGuestRegisterValue(booking.city);
  const address = cleanGuestRegisterValue(booking.address);

  const locality = `${prefecture}${city}${address}`.trim();

  if (!postcode && !locality) return "";

  return `${postcode ? `〒${postcode} ` : ""}${locality}`.trim();
}

function formatJapaneseDateJst(dateStr) {
  return formatJapaneseDateWithWeekday(dateStr);
}

export function renderCompanionRow(number, isLast) {
  const bottomBorder = isLast
    ? ""
    : "border-bottom:1px solid #dddddd;";

  return `
<tr style="height:14mm;">

  <td style="border:0;${bottomBorder}padding:0 2mm;text-align:center;color:#777777;vertical-align:middle;">
    ${number}
  </td>

  <td style="border:0;${bottomBorder}padding:0 2mm;">
    &nbsp;
  </td>

  <td style="border:0;border-left:1px solid #dddddd;${bottomBorder}padding:0 2mm;text-align:right;vertical-align:middle;">
    歳
  </td>

</tr>
`;
}

export function renderGuestRegisterSheet(booking) {
  const bookingId = escapeHtml(
    cleanGuestRegisterValue(booking.bookingId)
  );

  const guestName = escapeHtml(
    cleanGuestRegisterValue(booking.guestName)
  );

  const room = escapeHtml(
    cleanGuestRegisterValue(booking.room)
  );

  const phone = escapeHtml(
    cleanGuestRegisterValue(booking.phone)
  );

  const address = escapeHtml(
    buildGuestRegisterAddress(booking)
  );

  const totalGuests =
    Number.isFinite(Number(booking.totalGuests))
      ? Number(booking.totalGuests)
      : (
          Number(booking.adults || 0) +
          Number(booking.children || 0)
        );

  const checkIn = escapeHtml(
    formatJapaneseDateJst(booking.checkIn)
  );

  const checkOut = escapeHtml(
    formatJapaneseDateJst(booking.checkOut)
  );

  return `
<div class="guest-register-sheet">

  <!-- HEADER -->
  <table border="0" cellpadding="0" cellspacing="0"
    style="width:100%;border-collapse:collapse;">
    <tbody>
      <tr>
        <td style="width:30%;border:0;padding:0;vertical-align:top;text-align:left;">
          <div style="text-align:left;">
            <img
              src="https://media.xmlcal.com/pic/p0033/0695/02.png"
              alt=""
              width="76"
              style="display:block;width:76px;height:auto;border:0;"
            >
          </div>
        </td>

        <td style="width:70%;border:0;padding:0;vertical-align:top;text-align:right;color:#666666;font-size:8.5px;line-height:1.45;">
          <div style="font-family:'Yu Mincho','Hiragino Mincho ProN',serif;font-size:14px;font-weight:600;color:#222222;letter-spacing:0.04em;margin:0 0 2px 0;">
            ホテル喜らく / Zao Spa hotel Kiraku
          </div>

          <div>
            〒990-2301<br>
            山形県山形市蔵王温泉935-25
          </div>
        </td>
      </tr>
    </tbody>
  </table>


  <!-- TITLE -->
  <div style="text-align:center;">

    <div style="font-family:'Yu Mincho','Hiragino Mincho ProN',serif;font-size:26px;font-weight:600;letter-spacing:0.16em;color:#1f1f1f;">
      宿泊者名簿
    </div>

    <div style="font-family:Georgia,'Times New Roman',serif;font-size:8px;letter-spacing:0.18em;color:#777777;margin-top:2mm;">
      GUEST REGISTRATION FORM
    </div>

    <div style="width:46mm;border-top:1px solid #999999;margin:2mm auto 0 auto;"></div>

  </div>


  <!-- PRE-FILLED INFORMATION -->
  <div>

    <table border="0" cellpadding="0" cellspacing="0"
      style="width:100%;border-collapse:collapse;">
      <tbody>
        <tr>
          <td style="height:8mm;background:#ecebe8;border:1px solid #aaaaaa;padding:0 3mm;font-size:10.5px;font-weight:600;letter-spacing:0.03em;color:#222222;vertical-align:middle;">

            ご予約情報

            <span style="margin-left:2mm;font-family:Arial,sans-serif;font-size:7px;font-weight:400;color:#777777;">
              PRE-FILLED INFORMATION
            </span>

          </td>
        </tr>
      </tbody>
    </table>


    <div style="height:5mm;box-sizing:border-box;padding-top:1mm;font-size:7px;color:#777777;">
      下記はご予約時の情報をもとに印字しています。内容に誤りがある場合はご訂正ください。空欄の項目はご記入をお願いいたします。
    </div>


    <table border="0" cellpadding="0" cellspacing="0"
      style="width:100%;border-collapse:collapse;table-layout:fixed;border:1px solid #aaaaaa;">

      <colgroup>
        <col style="width:18%;">
        <col style="width:32%;">
        <col style="width:18%;">
        <col style="width:32%;">
      </colgroup>

      <tbody>

        <!-- RESERVATION / GUESTS -->
        <tr style="height:11mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            予約番号<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Reservation No.
            </span>
          </td>

          <td style="border:1px solid #bbbbbb;padding:1.5mm 3mm;vertical-align:middle;">
            ${bookingId}
          </td>

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            宿泊人数<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Guests
            </span>
          </td>

          <td style="border:1px solid #bbbbbb;padding:1.5mm 3mm;vertical-align:middle;">
            ${totalGuests} 名
          </td>

        </tr>


        <!-- STAY -->
        <tr style="height:11mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            宿泊期間<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Stay
            </span>
          </td>

          <td colspan="3"
            style="border:1px solid #bbbbbb;padding:1.5mm 3mm;font-size:11.5px;font-weight:600;vertical-align:middle;">
            ${checkIn}&nbsp;&nbsp;―&nbsp;&nbsp;${checkOut}
          </td>

        </tr>


        <!-- ROOM -->
        <tr style="height:11mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            客室番号<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Room No.
            </span>
          </td>

          <td colspan="3"
            style="border:1px solid #bbbbbb;padding:1.5mm 3mm;font-size:12px;font-weight:600;vertical-align:middle;">
            ${room}
          </td>

        </tr>


        <!-- NAME -->
        <tr style="height:12mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            代表者氏名<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Guest Name
            </span>
          </td>

          <td colspan="3"
            style="border:1px solid #bbbbbb;padding:1.5mm 3mm;font-size:13px;font-weight:600;vertical-align:middle;">
            ${guestName}
          </td>

        </tr>


        <!-- ADDRESS -->
        <tr style="height:16mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            住所<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Address
            </span>
          </td>

          <td colspan="3"
            style="border:1px solid #bbbbbb;padding:1.5mm 3mm;vertical-align:middle;line-height:1.55;">
            ${address}
          </td>

        </tr>


        <!-- PHONE -->
        <tr style="height:11mm;">

          <td style="background:#f5f4f1;border:1px solid #bbbbbb;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            電話番号<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Phone
            </span>
          </td>

          <td colspan="3"
            style="border:1px solid #bbbbbb;padding:1.5mm 3mm;vertical-align:middle;">
            ${phone}
          </td>

        </tr>

      </tbody>
    </table>

  </div>


  <!-- HANDWRITTEN INFORMATION -->
  <div>

    <table border="0" cellpadding="0" cellspacing="0"
      style="width:100%;border-collapse:collapse;">
      <tbody>
        <tr>

          <td style="height:8mm;background:#333333;border:1px solid #333333;padding:0 3mm;color:#ffffff;font-size:10.5px;font-weight:600;letter-spacing:0.03em;vertical-align:middle;">

            お客様ご記入欄

            <span style="margin-left:2mm;font-family:Arial,sans-serif;font-size:7px;font-weight:400;color:#dddddd;">
              TO BE COMPLETED BY GUEST
            </span>

          </td>

        </tr>
      </tbody>
    </table>


    <div style="height:5mm;box-sizing:border-box;padding-top:1mm;font-size:7px;color:#777777;">
      下記の空欄をご記入のうえ、最後にご署名をお願いいたします。
    </div>


    <table border="0" cellpadding="0" cellspacing="0"
      style="width:100%;border-collapse:collapse;table-layout:fixed;border:1px solid #999999;">

      <colgroup>
        <col style="width:18%;">
        <col style="width:82%;">
      </colgroup>

      <tbody>

        <!-- AGE -->
        <tr style="height:14mm;">

          <td style="background:#fafafa;border:1px solid #aaaaaa;padding:1.5mm 3mm;font-weight:600;vertical-align:middle;">
            年齢<br>
            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Age
            </span>
          </td>

          <td style="border:1px solid #aaaaaa;padding:1.5mm 3mm;vertical-align:middle;text-align:right;">
            歳
          </td>

        </tr>


        <!-- COMPANIONS -->
        <tr>

          <td style="background:#fafafa;border:1px solid #aaaaaa;padding:2mm 3mm;font-weight:600;vertical-align:top;">
            同行者<br>

            <span style="font-family:Arial,sans-serif;font-size:6.5px;color:#888888;font-weight:400;">
              Accompanying Guests
            </span>
          </td>


          <td style="border:1px solid #aaaaaa;padding:0;height:78mm;vertical-align:top;">

            <table border="0" cellpadding="0" cellspacing="0"
              style="width:100%;height:78mm;border-collapse:collapse;table-layout:fixed;">

              <colgroup>
                <col style="width:9%;">
                <col style="width:69%;">
                <col style="width:22%;">
              </colgroup>

              <tbody>

                <tr style="height:8mm;">

                  <td style="border:0;border-bottom:1px solid #dddddd;padding:0 2mm;color:#777777;font-size:7px;text-align:center;vertical-align:middle;">
                    No.
                  </td>

                  <td style="border:0;border-bottom:1px solid #dddddd;padding:0 2mm;color:#777777;font-size:7px;vertical-align:middle;">
                    氏名 / Name
                  </td>

                  <td style="border:0;border-left:1px solid #dddddd;border-bottom:1px solid #dddddd;padding:0 2mm;color:#777777;font-size:7px;vertical-align:middle;">
                    年齢 / Age
                  </td>

                </tr>

                ${renderCompanionRow(1, false)}
                ${renderCompanionRow(2, false)}
                ${renderCompanionRow(3, false)}
                ${renderCompanionRow(4, false)}
                ${renderCompanionRow(5, true)}

              </tbody>
            </table>

          </td>

        </tr>

      </tbody>
    </table>

  </div>


  <!-- CONFIRMATION -->
  <div style="padding:2.5mm 3mm;border-top:1px solid #aaaaaa;border-bottom:1px solid #aaaaaa;">

    <div style="font-size:9px;color:#333333;">
      上記の記載内容および印字された登録内容を確認し、相違ありません。
    </div>

    <div style="font-family:Arial,sans-serif;font-size:7px;color:#777777;margin-top:0.5mm;">
      I confirm that the information above is complete and correct.
    </div>

  </div>


  <!-- SIGNATURE -->
  <table border="0" cellpadding="0" cellspacing="0"
    style="width:100%;border-collapse:collapse;">

    <tbody>
      <tr>

        <td style="width:20%;border:0;padding:0 3mm 1mm 0;vertical-align:bottom;">

          <div style="font-family:'Yu Mincho','Hiragino Mincho ProN',serif;font-size:16px;font-weight:600;letter-spacing:0.07em;">
            ご署名
          </div>

          <div style="font-family:Arial,sans-serif;font-size:7px;color:#888888;">
            SIGNATURE
          </div>

        </td>


        <td style="border:0;border-bottom:1px solid #444444;height:15mm;padding:0;vertical-align:bottom;">
          &nbsp;
        </td>

      </tr>
    </tbody>

  </table>


  <!-- FOOTER -->
  <div style="padding-top:2mm;border-top:1px solid #dddddd;text-align:center;color:#777777;font-size:7px;">

    ご確認・ご記入にご協力いただき、ありがとうございます。

    <br>

    <span style="font-family:Arial,sans-serif;font-size:6.5px;">
      Thank you for your cooperation.
    </span>

  </div>

</div>
`;
}

// --- Adapter: staff Daily Ops arrival record -> the flat `booking` shape
// renderGuestRegisterSheet()/buildGuestRegisterAddress() expect. This is the
// ONLY part of this file that is specific to this app's actual data shape;
// everything above is the ported design and must not itself be changed to
// "fit" the data — the data is adapted to it, not the other way around.
function toGuestRegisterBooking(arrival) {
  const a = arrival || {};
  const addr = (a.address && typeof a.address === "object") ? a.address : {};
  return {
    bookingId: a.booking_id,
    guestName: a.guest_name,
    room: a.room_number,
    adults: a.adults,
    children: a.children,
    totalGuests: a.total_guests,
    checkIn: a.checkin_date,
    checkOut: a.checkout_date,
    phone: a.phone,
    postcode: addr.postcode,
    prefecture: addr.prefecture,
    city: addr.city,
    address: addr.rest,
  };
}

export function buildGuestRegisterDocumentHtml(arrivals) {
  const list = Array.isArray(arrivals) ? arrivals : [];
  return list.map((a) => renderGuestRegisterSheet(toGuestRegisterBooking(a))).join("");
}
