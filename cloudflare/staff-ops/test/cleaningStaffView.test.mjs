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
  performSave, performReset, MAX_INSTRUCTION_LEN,
} from "../public/cleaningStaffView.js";
import { mergeCleaningOverrides } from "../src/cleaningOverrides.js";

const dir = path.dirname(fileURLToPath(import.meta.url));
const fixture = JSON.parse(readFileSync(path.join(dir, "fixtures/staff_ops_snapshot.sample.json"), "utf-8"));
const rawRooms = fixture.dates["2026-08-30"].cleaning.rooms;
const rooms = mergeCleaningOverrides(rawRooms, {});
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
