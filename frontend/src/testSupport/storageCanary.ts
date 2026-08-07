// The storage provenance check behind setupStorageCanary.ts (which see for
// the setup-ordering constraints). Extracted here so the check itself is
// unit-testable and coverage-visible — setup files are coverage-excluded, so
// a refactor could otherwise silently no-op the canary.
//
// This module MUST stay import-free and free of module-scope storage reads:
// ESM hoisting evaluates it before the canary setup file's own top-level
// code, i.e. before the provenance of the storage globals has been asserted.
//
// Why `instanceof Storage` is the provenance check: vitest (4.x) installs
// jsdom's `Storage` class itself onto the global (it is in vitest's KEYS
// list, so jsdom's class wins even if Node defines one), meaning only
// jsdom-created storages pass. That is a vitest behaviour, not a jsdom
// guarantee — hence the `typeof Storage` guard, so a changed vitest fails
// with the named error below rather than an opaque "instanceof is not
// callable" TypeError.

export const STORAGE_PIN_HINT =
  "Check the `--no-experimental-webstorage` pin in vitest.config.ts " +
  "test.execArgv."

// Fields are `unknown` on purpose: the whole point is to interrogate
// globals whose provenance is in doubt (Node stubs, undefined-returning
// accessors), so nothing here may assume the lib.dom types hold.
export interface StorageEnvironment {
  Storage: unknown
  localStorage: unknown
  sessionStorage: unknown
}

/**
 * Assert both web storages are jsdom's real, functioning Storage instances,
 * throwing a named error (pointing at the webstorage pin) on the first that
 * is not. The default argument reads globalThis at call time, never at
 * module scope.
 */
export function assertStorageProvenance(
  env: StorageEnvironment = globalThis as unknown as StorageEnvironment,
): void {
  const StorageClass = env.Storage
  if (typeof StorageClass !== "function") {
    throw new Error(
      "global Storage is not jsdom's Storage class — vitest's jsdom global " +
        `population has changed, or Node's web-storage global displaced it. ${STORAGE_PIN_HINT}`,
    )
  }

  for (const name of ["localStorage", "sessionStorage"] as const) {
    const storage = env[name]
    if (!(storage instanceof StorageClass) || typeof (storage as Storage).clear !== "function") {
      throw new Error(
        `${name} is not jsdom's real Storage — Node's experimental ` +
          "web-storage global is shadowing it, or vitest's global population " +
          `changed. ${STORAGE_PIN_HINT}`,
      )
    }
    const provenStorage = storage as Storage
    provenStorage.setItem("__storage_canary__", "ok")
    if (provenStorage.getItem("__storage_canary__") !== "ok") {
      throw new Error(
        `${name} set/get round-trip failed — the test environment's Storage ` +
          `is not functional. ${STORAGE_PIN_HINT}`,
      )
    }
    provenStorage.removeItem("__storage_canary__")
  }
}
