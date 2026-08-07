// The storage provenance check behind setupStorageCanary.ts (which see for
// the setup-ordering constraints). Extracted here so the check itself is
// unit-testable and coverage-visible — setup files are coverage-excluded, so
// a refactor could otherwise silently no-op the canary.
//
// This module MUST stay import-free and free of module-scope storage reads:
// ESM hoisting evaluates it before the canary setup file's own top-level
// code, i.e. before the provenance of the storage globals has been asserted.
// (webstoragePin.meta.test.ts gates the import-free invariant.)
//
// Why `instanceof Storage`: vitest (4.x) installs jsdom's `Storage` class
// itself onto the global (it is in vitest's KEYS list, so jsdom's class wins
// even if Node defines one), meaning only jsdom-created storages pass. That
// is a vitest behaviour, not a jsdom guarantee — hence the constructor-shape
// guard, so a changed vitest fails with the named error below rather than an
// opaque TypeError. Known blind spot: because the class checked against is
// itself the untrusted global, a fully self-consistent foreign Storage
// implementation (foreign class AND matching instances) would pass — the
// check proves internal consistency, and proves jsdom provenance only while
// vitest's KEYS behaviour holds. The deliberately-failing test in
// storageCanary.test.ts documents that shape and trips if vitest changes.

export const STORAGE_PIN_HINT =
  "Check the `--no-experimental-webstorage` pin in vitest.config.ts " +
  "test.execArgv."

const CANARY_KEY = "__storage_canary__"

// Fields are `unknown` on purpose: the whole point is to interrogate
// globals whose provenance is in doubt (Node stubs, undefined-returning
// accessors), so nothing here may assume the lib.dom types hold.
export interface StorageEnvironment {
  Storage: unknown
  localStorage: unknown
  sessionStorage: unknown
}

/**
 * Assert both web storages are jsdom's real, functioning, distinct Storage
 * instances, throwing a named error (pointing at the webstorage pin) on the
 * first that is not. Failure paths leave no canary-key residue, and a
 * pre-existing value under the probe key is restored. The default argument
 * reads globalThis at call time, never at module scope.
 */
export function assertStorageProvenance(
  env: StorageEnvironment = globalThis as unknown as StorageEnvironment,
): void {
  const StorageClass = env.Storage
  const prototype = (StorageClass as { prototype?: unknown } | undefined)?.prototype
  // The prototype guard matters: a non-constructible function (arrow, bound)
  // passes `typeof === "function"` but makes `instanceof` throw a native
  // TypeError instead of the named error.
  if (typeof StorageClass !== "function" || typeof prototype !== "object" || prototype === null) {
    throw new Error(
      "global Storage is not jsdom's Storage class — vitest's jsdom global " +
        `population has changed, or Node's web-storage global displaced it. ${STORAGE_PIN_HINT}`,
    )
  }

  if (env.localStorage === env.sessionStorage) {
    throw new Error(
      "localStorage and sessionStorage are the same object — storage " +
        "isolation is broken (a process-global storage is standing in for " +
        `both). ${STORAGE_PIN_HINT}`,
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
    let priorValue: string | null = null
    let roundTrip: string | null
    try {
      priorValue = provenStorage.getItem(CANARY_KEY)
      provenStorage.setItem(CANARY_KEY, "ok")
      roundTrip = provenStorage.getItem(CANARY_KEY)
    } catch (error) {
      throw new Error(
        `${name} threw during the set/get round-trip — the test ` +
          `environment's Storage is not usable. ${STORAGE_PIN_HINT}`,
        { cause: error },
      )
    } finally {
      try {
        if (priorValue === null) {
          provenStorage.removeItem(CANARY_KEY)
        } else {
          provenStorage.setItem(CANARY_KEY, priorValue)
        }
      } catch {
        // Best-effort cleanup: the throw in flight already names the problem.
      }
    }
    if (roundTrip !== "ok") {
      throw new Error(
        `${name} set/get round-trip failed — the test environment's Storage ` +
          `is not functional. ${STORAGE_PIN_HINT}`,
      )
    }
  }
}
