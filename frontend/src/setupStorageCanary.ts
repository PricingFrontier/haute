// Canary: the test environment's web storage must be jsdom's real Storage.
//
// Node >= 22.4 can register its own web-storage keys on globalThis
// (default-on from Node 25; shape varies — a file-less localStorage stub on
// 25, an undefined-returning accessor from 26, and a real but process-global
// sessionStorage that leaks state across test files). Vitest's jsdom
// environment skips window keys already present on globalThis, so the key's
// mere presence silently shadows the real thing.
//
// Guarded by `test.execArgv: ["--no-experimental-webstorage"]` in
// vitest.config.ts; if this file throws, that pin (or its successor) has
// stopped holding. (If Node instead REJECTS the flag one day — e.g. a
// future major drops the legacy alias — workers crash at spawn on an
// unrecognised option before any setup file runs; recognise that as the
// pin too.)
//
// This file MUST stay FIRST in setupFiles, and its ONLY import must be the
// canary module below (itself import-free, with no module-scope storage
// reads): ESM hoisting evaluates imports before top-level code, so any
// other imported module reading storage at module scope would run before
// the canary. The check body lives in testSupport/storageCanary.ts so it
// is unit-tested and coverage-visible — this setup file is
// coverage-excluded, so a refactor here could otherwise no-op the canary
// silently.

import { assertStorageProvenance } from "./testSupport/storageCanary"

assertStorageProvenance()
