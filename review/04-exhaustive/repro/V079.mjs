/*
 * Repro for V079 — Ctrl+Shift+Z / Cmd+Shift+Z redo is unreachable.
 *
 * This is a FRONTEND (TS/React) bug, so a Python repro is not applicable:
 * the defect depends on browser KeyboardEvent semantics. This isolated Node
 * harness uses jsdom (already a dev dependency of frontend/) to:
 *
 *   1. Demonstrate the load-bearing browser fact: in a real browser, pressing
 *      Shift+Z on a standard layout sets KeyboardEvent.key === "Z" (uppercase),
 *      per the W3C UI Events spec ("key" reflects the value accounting for
 *      modifier state). The hook's unit test instead fires an *artificial*
 *      event with key:"z" + shiftKey:true that no real browser ever emits.
 *
 *   2. Run the EXACT branch logic copied verbatim from
 *      frontend/src/hooks/useKeyboardShortcuts.ts lines 42/49/54/60 against the
 *      realistic event values and assert the WRONG behaviour: a real-browser
 *      Cmd+Shift+Z (macOS) matches NO handler, so redo() is never called and
 *      preventDefault() is never invoked.
 *
 * ISOLATION: no project src/tests files are imported or written; only jsdom is
 * used to construct standards-compliant KeyboardEvent objects. The handler
 * logic below is a byte-for-byte transcription of the relevant branches; its
 * fidelity is independently confirmed by reading the source file.
 *
 * Run from frontend/ so the local jsdom resolves:
 *   node --experimental-vm-modules ../review/04-exhaustive/repro/V079.mjs
 * (the runner script below invokes node with cwd=frontend).
 */

import { createRequire } from "node:module";
import assert from "node:assert/strict";
import path from "node:path";

// Resolve jsdom from frontend/node_modules regardless of this script's location.
// FRONTEND_DIR is passed by the runner; default to ../../../frontend from here.
const frontendDir = process.env.FRONTEND_DIR || path.resolve(process.cwd());
const require = createRequire(path.join(frontendDir, "package.json"));
const { JSDOM } = require("jsdom");

const { window } = new JSDOM("<!DOCTYPE html><body></body>");
const KeyboardEvent = window.KeyboardEvent;

// --- Verbatim branch logic from useKeyboardShortcuts.ts (lines 42/49/54/60) ---
// Returns the action name a non-typing handler would dispatch, or null.
function dispatchAction(e) {
  const isTyping = false; // simulate keypress on the canvas (not an input)
  const mod = e.ctrlKey || e.metaKey;
  let prevented = false;
  const preventDefault = () => { prevented = true; };

  // Ctrl+S / Cmd+S → save  (line 42)
  if (mod && e.key === "s") { preventDefault(); return { action: "save", prevented }; }
  // Ctrl+Z → undo  (line 49)
  if (mod && e.key === "z" && !e.shiftKey && !isTyping) { preventDefault(); return { action: "undo", prevented }; }
  // Ctrl+Shift+Z → redo  (line 54)  <-- the disputed branch
  if (mod && e.key === "z" && e.shiftKey && !isTyping) { preventDefault(); return { action: "redo", prevented }; }
  // Ctrl+Y → redo (Windows convention)  (line 60)
  if (mod && e.key === "y" && !isTyping) { preventDefault(); return { action: "redo", prevented }; }

  return { action: null, prevented };
}

// === 1. Browser-semantics fact (what jsdom/real browsers store) ===
const realShiftZ = new KeyboardEvent("keydown", { key: "Z", ctrlKey: true, shiftKey: true });
const fakeShiftZ = new KeyboardEvent("keydown", { key: "z", ctrlKey: true, shiftKey: true });
console.log(`[fact] real-browser Shift+Z key=${JSON.stringify(realShiftZ.key)} shiftKey=${realShiftZ.shiftKey}`);
console.log(`[fact] unit-test artificial  key=${JSON.stringify(fakeShiftZ.key)} shiftKey=${fakeShiftZ.shiftKey}`);
assert.equal(realShiftZ.key, "Z", "real-browser Shift+Z must yield uppercase 'Z'");

// === 2. The artificial event the unit test uses DOES reach redo (false green) ===
const fakeResult = dispatchAction(fakeShiftZ);
console.log(`[test-event]  Ctrl+Shift+Z(key="z") -> action=${fakeResult.action} prevented=${fakeResult.prevented}`);
assert.equal(fakeResult.action, "redo", "artificial test event reaches redo (this is why the test passes)");

// === 3. The REAL macOS redo (Cmd+Shift+Z) reaches NOTHING ===
const macRedo = new KeyboardEvent("keydown", { key: "Z", metaKey: true, shiftKey: true });
const macResult = dispatchAction(macRedo);
console.log(`[real macOS] Cmd+Shift+Z(key="Z") -> action=${macResult.action} prevented=${macResult.prevented}`);
// EXPECTED (correct behaviour): action === "redo".
// ACTUAL (bug): action === null  => redo never fires, default not prevented.
assert.equal(
  macResult.action,
  "redo",
  `BUG REPRODUCED: real-browser Cmd+Shift+Z dispatched action=${macResult.action} (expected "redo"). ` +
    `macOS has no Ctrl+Y fallback, so keyboard redo is dead.`,
);

console.log("UNEXPECTED: macOS redo worked — bug NOT reproduced.");
