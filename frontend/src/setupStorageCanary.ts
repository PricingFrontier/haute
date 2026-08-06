// Canary: the test environment's web storage must be jsdom's real Storage.
//
// Node >= 22.4 can register its own web-storage keys on globalThis
// (default-on from Node 25; shape varies — a file-less localStorage stub on
// 25, an undefined-returning accessor from 26, and a real but process-global
// sessionStorage that leaks state across test files). Vitest's jsdom
// environment skips window keys already present on globalThis, so the key's
// mere presence silently shadows the real thing.
//
// `instanceof Storage` is the provenance check: vitest (4.x) installs
// jsdom's `Storage` class itself onto the global (it is in vitest's KEYS
// list, so jsdom's class wins even if Node defines one), meaning only
// jsdom-created storages pass. That is a vitest behaviour, not a jsdom
// guarantee — hence the `typeof Storage` guard, so a changed vitest fails
// with the named error below rather than an opaque "instanceof is not
// callable" TypeError.
//
// Guarded by `test.execArgv: ["--no-experimental-webstorage"]` in
// vitest.config.ts; if this file throws, that pin (or its successor) has
// stopped holding. (If Node instead REJECTS the flag one day — e.g. a
// future major drops the legacy alias — workers crash at spawn on an
// unrecognised option before any setup file runs; recognise that as the
// pin too.)
//
// This file MUST stay import-free and listed FIRST in setupFiles: ESM
// hoisting evaluates imports before top-level code, so any imported module
// reading storage at module scope would run before the canary.

const pinHint =
  "Check the `--no-experimental-webstorage` pin in vitest.config.ts " +
  "test.execArgv."

if (typeof Storage !== "function") {
  throw new Error(
    "global Storage is not jsdom's Storage class — vitest's jsdom global " +
      `population has changed, or Node's web-storage global displaced it. ${pinHint}`,
  )
}

for (const name of ["localStorage", "sessionStorage"] as const) {
  const storage = globalThis[name]
  if (!(storage instanceof Storage) || typeof storage.clear !== "function") {
    throw new Error(
      `${name} is not jsdom's real Storage — Node's experimental ` +
        "web-storage global is shadowing it, or vitest's global population " +
        `changed. ${pinHint}`,
    )
  }
  storage.setItem("__storage_canary__", "ok")
  if (storage.getItem("__storage_canary__") !== "ok") {
    throw new Error(
      `${name} set/get round-trip failed — the test environment's Storage ` +
        `is not functional. ${pinHint}`,
    )
  }
  storage.removeItem("__storage_canary__")
}

export {}
