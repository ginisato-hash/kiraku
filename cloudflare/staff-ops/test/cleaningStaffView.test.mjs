// cleaningStaffView.js のテスト。
//
// このファイルは「純粋関数(render/validate/body-builder/performSave/performReset)」と
// 「DOM配線(mountCleaningStaffView)」を明確に分離して書かれている。純粋関数はDOM無しの
// Node環境から直接importしてテストできる。mountCleaningStaffView自体はthinな
// bootstrap(document/window前提)なので、他画面のprint/mobile bootstrapと同じく
// ソーステキストの確認(cancelがネットワーク呼び出しを一切しないこと)に留める。
import assert from "node:assert";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import {
  renderStaffCleaningTable, validateInstructionInput,
  buildOverrideSaveBody, buildOverrideDeleteBody,
  buildAccessStatusBody, buildAccessStatusModalHtml,
  performSave, performReset, performAccessStatusChange, MAX_INSTRUCTION_LEN,
} from "../public/cleaningStaffView.js";
import { mergeCleaningOverrides } from "../src/cleaningOverrides.js";
import { applyRoomAccessStatus } from "../src/roomAccessState.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
// applyRoomAccessStatus({}) mirrors what GET /api/cleaning returns when no
// live record exists yet: departing rooms (401 TURNOVER, 403 CHECKOUT) get
// WAITING_CHECKOUT, everything else gets null.
const rooms = applyRoomAccessStatus(mergeCleaningOverrides(rawRooms, {}), {});
const src = readFileSync(path.join(dir, "../public/cleaningStaffView.js"), "utf-8");

let passed = 0;
async function check(name, fn) { await fn(); passed++; console.log("ok -", name); }

// ---------------- renderStaffCleaningTable ----------------

await check("renderStaffCleaningTable renders exactly 18 room rows in canonical order (no UNASSIGNED row)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  const matches = [...out.matchAll(/<tr data-room-number="(\d+)">/g)].map((m) => m[1]);
  assert.equal(matches.length, 18);
  assert.equal(matches[0], "401");
  assert.equal(matches[17], "607");
  assert.ok(!out.includes("中村 光"), "UNASSIGNED guest must never appear in the staff list table");
});

await check("no edit panel is shown when editingRoom is null", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  assert.ok(!out.includes("csv-edit-panel"));
});

await check("edit control opens and shows a date+room-scoped save/reset/cancel form for the target room only", async () => {
  const out = renderStaffCleaningTable(rooms, "402", "");
  assert.ok(out.includes('csv-edit-row" data-room-number="402"'));
  assert.ok(out.includes("402号室"));
  assert.ok(out.includes("追加・変更指示"));
  assert.ok(out.includes("<textarea"));
  assert.ok(/data-action="save" data-room="402"/.test(out));
  assert.ok(/data-action="reset" data-room="402"/.test(out));
  assert.ok(/data-action="cancel" data-room="402"/.test(out));
  // only one edit panel, scoped to the editing room
  assert.equal((out.match(/csv-edit-panel/g) || []).length, 1);
  assert.ok(!/csv-edit-row" data-room-number="401"/.test(out));
});

await check("an inline error for the editing room is shown inside its own edit panel", async () => {
  const out = renderStaffCleaningTable(rooms, "402", "指示を入力してください（空欄のまま保存はできません）");
  assert.ok(out.includes("csv-edit-error"));
  assert.ok(out.includes("指示を入力してください"));
});

// ---------------- 2026-09追加: 大人/子供内訳・お知らせ・現地決済（override編集機能は不変）----------------

await check("staff table adds 大人/子供 breakdown, お知らせ, 現地決済 columns without touching the existing 10 columns", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  assert.ok(out.includes(">大人/子供<"));
  assert.ok(out.includes(">お知らせ<"));
  assert.ok(out.includes(">現地決済<"));
  // existing columns (guest edit workflow) still present and unchanged
  assert.ok(out.includes(">RoomNo<"));
  assert.ok(out.includes(">IN<"));
  assert.ok(out.includes(">OUT<"), "Staff cleaning list keeps its own full IN/OUT display (only print/mobile simplify status)");
  assert.ok(out.includes(">指示<"));
});

await check("a room with guest_notice/onsite payment shows both in its row (401 from the fixture)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  assert.ok(out.includes("到着が少し遅れます"));
  assert.ok(out.includes("現地 ¥18,000"));
  assert.ok(out.includes("大人2 子供1"));
});

await check("a room with no guest_notice/onsite payment shows blank cells for those columns (402)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  const row402 = out.match(/<tr data-room-number="402">[\s\S]*?<\/tr>/)[0];
  assert.ok(!row402.includes("現地"));
});

await check("the edit panel's colspan matches the new total column count (15, including 在室確認) so it still spans the full row", async () => {
  const out = renderStaffCleaningTable(rooms, "402", "");
  assert.ok(out.includes('colspan="15"'));
});

// ---------------- validateInstructionInput / body builders ----------------

await check("validateInstructionInput rejects empty and whitespace-only values", async () => {
  assert.equal(validateInstructionInput("").ok, false);
  assert.equal(validateInstructionInput("   ").ok, false);
  assert.equal(validateInstructionInput(null).ok, false);
  assert.equal(validateInstructionInput(undefined).ok, false);
});

await check("validateInstructionInput rejects a value over MAX_INSTRUCTION_LEN chars", async () => {
  const result = validateInstructionInput("x".repeat(MAX_INSTRUCTION_LEN + 1));
  assert.equal(result.ok, false);
});

await check("validateInstructionInput trims and accepts a normal value", async () => {
  const result = validateInstructionInput("  タオル追加  ");
  assert.equal(result.ok, true);
  assert.equal(result.value, "タオル追加");
});

await check("buildOverrideSaveBody / buildOverrideDeleteBody produce the exact API body shapes", async () => {
  assert.deepEqual(buildOverrideSaveBody("2026-08-31", "607", "水回り重点清掃"), {
    date: "2026-08-31", roomNumber: "607", instruction: "水回り重点清掃",
  });
  assert.deepEqual(buildOverrideDeleteBody("2026-08-31", "607"), {
    date: "2026-08-31", roomNumber: "607",
  });
});

// ---------------- performSave / performReset (fetchImpl injected, no real network) ----------------

await check("performSave posts the right body shape to POST /api/cleaning/override", async () => {
  let capturedUrl = null;
  let capturedInit = null;
  const fetchImpl = async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return { ok: true };
  };
  const result = await performSave({ date: "2026-08-30", roomNumber: "402", rawValue: "追加タオル希望", fetchImpl });
  assert.equal(result.ok, true);
  assert.equal(capturedUrl, "/api/cleaning/override");
  assert.equal(capturedInit.method, "POST");
  assert.deepEqual(JSON.parse(capturedInit.body), { date: "2026-08-30", roomNumber: "402", instruction: "追加タオル希望" });
});

await check("performSave is blocked client-side for an empty instruction BEFORE any fetch happens", async () => {
  let fetchCalled = false;
  const fetchImpl = async () => { fetchCalled = true; return { ok: true }; };
  const result = await performSave({ date: "2026-08-30", roomNumber: "402", rawValue: "   ", fetchImpl });
  assert.equal(result.ok, false);
  assert.equal(fetchCalled, false, "fetch must never be called for an empty/whitespace-only instruction");
});

await check("performSave is blocked client-side for an over-length instruction BEFORE any fetch happens", async () => {
  let fetchCalled = false;
  const fetchImpl = async () => { fetchCalled = true; return { ok: true }; };
  const result = await performSave({
    date: "2026-08-30", roomNumber: "402", rawValue: "x".repeat(MAX_INSTRUCTION_LEN + 1), fetchImpl,
  });
  assert.equal(result.ok, false);
  assert.equal(fetchCalled, false);
});

await check("performSave surfaces a server-side rejection (non-ok response) as an error, not a thrown exception", async () => {
  const fetchImpl = async () => ({ ok: false });
  const result = await performSave({ date: "2026-08-30", roomNumber: "402", rawValue: "x", fetchImpl });
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

await check("performReset issues a DELETE with the right body shape", async () => {
  let capturedUrl = null;
  let capturedInit = null;
  const fetchImpl = async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return { ok: true };
  };
  const result = await performReset({ date: "2026-08-30", roomNumber: "402", fetchImpl });
  assert.equal(result.ok, true);
  assert.equal(capturedUrl, "/api/cleaning/override");
  assert.equal(capturedInit.method, "DELETE");
  assert.deepEqual(JSON.parse(capturedInit.body), { date: "2026-08-30", roomNumber: "402" });
});

await check("performReset surfaces a server-side rejection as an error, not a thrown exception", async () => {
  const fetchImpl = async () => ({ ok: false });
  const result = await performReset({ date: "2026-08-30", roomNumber: "402", fetchImpl });
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

// ---------------- 在室確認(room_access_status): 未退室/清掃可 ----------------

await check("staff table adds a 在室確認 column", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  assert.ok(out.includes(">在室確認<"));
});

await check("a departing room (401, TURNOVER) with no live record yet shows a 未退室 button", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  const row401 = out.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(/data-action="access-request" data-room="401"/.test(row401));
  assert.ok(row401.includes(">未退室<"));
  assert.ok(row401.includes("csv-access-btn-WAITING_CHECKOUT"));
});

await check("a non-departing room (404, STAYOVER) shows a blank 在室確認 cell (no button)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "");
  const row404 = out.match(/<tr data-room-number="404">[\s\S]*?<\/tr>/)[0];
  assert.ok(!/data-action="access-request"/.test(row404));
});

await check("liveAccessEnabled=false hides the button entirely even for a departing room, but keeps the column (no colspan change)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "", false, true);
  assert.ok(!/data-action="access-request"/.test(out));
  assert.ok(out.includes(">在室確認<"));
});

await check("isToday=false replaces the button with a static badge + '当日のみ変更できます' note (read-only for other dates)", async () => {
  const out = renderStaffCleaningTable(rooms, null, "", true, false);
  const row401 = out.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(!/data-action="access-request"/.test(row401));
  assert.ok(row401.includes("csv-access-badge-WAITING_CHECKOUT"));
  assert.ok(row401.includes("当日のみ変更できます"));
});

await check("a room already CLEANING_ALLOWED shows a 清掃可 button styled distinctly from 未退室", async () => {
  const allowedRooms = rooms.map((r) => (r.room_number === "401" ? { ...r, roomAccessStatus: "CLEANING_ALLOWED" } : r));
  const out = renderStaffCleaningTable(allowedRooms, null, "");
  const row401 = out.match(/<tr data-room-number="401">[\s\S]*?<\/tr>/)[0];
  assert.ok(row401.includes(">清掃可<"));
  assert.ok(row401.includes("csv-access-btn-CLEANING_ALLOWED"));
});

await check("buildAccessStatusModalHtml returns '' when modal state is null (no modal in the DOM)", async () => {
  assert.equal(buildAccessStatusModalHtml(null), "");
});

await check("buildAccessStatusModalHtml (WAITING_CHECKOUT -> CLEANING_ALLOWED) shows a large room number, the confirmation copy, and an unambiguous confirm label", async () => {
  const html = buildAccessStatusModalHtml({ roomNumber: "601", targetStatus: "CLEANING_ALLOWED", pending: false, errorText: "" });
  assert.ok(html.includes("601号室"));
  assert.ok(html.includes("この部屋を「清掃可」にしますか？"));
  assert.ok(html.includes("宿泊者が退室済みであることを確認してから変更してください。"));
  assert.ok(/data-action="access-cancel"[^>]*>キャンセル/.test(html));
  assert.ok(/data-action="access-confirm"[^>]*>清掃可にする/.test(html));
  // no ambiguous "OK"/"Cancel" wording (要件10)
  assert.ok(!/>OK</.test(html));
});

await check("buildAccessStatusModalHtml (CLEANING_ALLOWED -> WAITING_CHECKOUT, undo) asks to revert and labels the confirm button accordingly", async () => {
  const html = buildAccessStatusModalHtml({ roomNumber: "601", targetStatus: "WAITING_CHECKOUT", pending: false, errorText: "" });
  assert.ok(html.includes("「未退室」に戻しますか？"));
  assert.ok(/data-action="access-confirm"[^>]*>未退室に戻す/.test(html));
});

await check("buildAccessStatusModalHtml disables both buttons and shows '更新中…' while pending", async () => {
  const html = buildAccessStatusModalHtml({ roomNumber: "601", targetStatus: "CLEANING_ALLOWED", pending: true, errorText: "" });
  assert.ok(/data-action="access-cancel" disabled/.test(html));
  assert.ok(/data-action="access-confirm" disabled/.test(html));
  assert.ok(html.includes("更新中…"));
});

await check("buildAccessStatusModalHtml surfaces a network-failure error inline without hiding the modal", async () => {
  const html = buildAccessStatusModalHtml({
    roomNumber: "601", targetStatus: "CLEANING_ALLOWED", pending: false,
    errorText: "更新できませんでした。もう一度お試しください",
  });
  assert.ok(html.includes("更新できませんでした"));
});

await check("buildAccessStatusBody produces the exact API body shape", async () => {
  assert.deepEqual(buildAccessStatusBody("2026-09-08", "601", "CLEANING_ALLOWED"), {
    date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED",
  });
});

await check("performAccessStatusChange posts the right body shape to POST /api/cleaning/access-status", async () => {
  let capturedUrl = null;
  let capturedInit = null;
  const fetchImpl = async (url, init) => {
    capturedUrl = url;
    capturedInit = init;
    return { ok: true, json: async () => ({ ok: true, previousStatus: "WAITING_CHECKOUT", status: "CLEANING_ALLOWED", updatedAt: "2026-09-08T00:00:00.000Z" }) };
  };
  const result = await performAccessStatusChange({ date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED", fetchImpl });
  assert.equal(result.ok, true);
  assert.equal(result.status, "CLEANING_ALLOWED");
  assert.equal(capturedUrl, "/api/cleaning/access-status");
  assert.equal(capturedInit.method, "POST");
  assert.deepEqual(JSON.parse(capturedInit.body), { date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED" });
});

await check("performAccessStatusChange surfaces a server-side rejection as a retry-friendly error, not a thrown exception (状態を先に変えない)", async () => {
  const fetchImpl = async () => ({ ok: false });
  const result = await performAccessStatusChange({ date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED", fetchImpl });
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

await check("performAccessStatusChange surfaces a network exception (fetch throwing) the same way", async () => {
  const fetchImpl = async () => { throw new Error("network down"); };
  const result = await performAccessStatusChange({ date: "2026-09-08", roomNumber: "601", status: "CLEANING_ALLOWED", fetchImpl });
  assert.equal(result.ok, false);
  assert.ok(result.error);
});

await check("handleAccessCancel (在室確認モーダルのキャンセル) never calls fetch/performAccessStatusChange", async () => {
  const start = src.indexOf("function handleAccessCancel()");
  assert.ok(start > -1, "expected a handleAccessCancel() function");
  const braceStart = src.indexOf("{", start);
  let depth = 0;
  let end = braceStart;
  for (let i = braceStart; i < src.length; i++) {
    if (src[i] === "{") depth++;
    if (src[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  const body = src.slice(braceStart, end + 1);
  assert.ok(!/fetch\(/.test(body), "handleAccessCancel must not call fetch()");
  assert.ok(!/performAccessStatusChange/.test(body), "handleAccessCancel must not call performAccessStatusChange");
});

await check("handleAccessRequest (clicking 未退室/清掃可) never calls fetch/performAccessStatusChange — it only opens the confirmation modal", async () => {
  const start = src.indexOf("function handleAccessRequest(roomNumber)");
  assert.ok(start > -1, "expected a handleAccessRequest(roomNumber) function");
  const braceStart = src.indexOf("{", start);
  let depth = 0;
  let end = braceStart;
  for (let i = braceStart; i < src.length; i++) {
    if (src[i] === "{") depth++;
    if (src[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  const body = src.slice(braceStart, end + 1);
  assert.ok(!/fetch\(/.test(body), "handleAccessRequest must not call fetch()");
  assert.ok(!/performAccessStatusChange/.test(body), "handleAccessRequest must not call performAccessStatusChange (mutation only happens on confirm)");
});

// ---------------- cancel: source-level confirmation of "no network call" ----------------

await check("handleCancel (the 'キャンセル' action) never calls fetch/performSave/performReset", async () => {
  const start = src.indexOf("function handleCancel()");
  assert.ok(start > -1, "expected a handleCancel() function");
  const braceStart = src.indexOf("{", start);
  // naive matching-brace scan (function body has no nested braces beyond simple statements)
  let depth = 0;
  let end = braceStart;
  for (let i = braceStart; i < src.length; i++) {
    if (src[i] === "{") depth++;
    if (src[i] === "}") { depth--; if (depth === 0) { end = i; break; } }
  }
  const body = src.slice(braceStart, end + 1);
  assert.ok(!/fetch\(/.test(body), "handleCancel must not call fetch()");
  assert.ok(!/performSave|performReset/.test(body), "handleCancel must not call performSave/performReset");
});

console.log(`\n${passed} cleaningStaffView checks passed`);
