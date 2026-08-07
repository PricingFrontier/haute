import { describe, expect, it } from "vitest"

import { STORAGE_PIN_HINT, assertStorageProvenance } from "../storageCanary"

// The shapes below reproduce the real shadowing incident (PR #158 /
// docs/CI_MIRROR.md §Environment drift): Node's experimental web-storage
// globals land on globalThis before jsdom sets up, and vitest's jsdom
// environment skips keys that are already present.

/** Node 25 shape: a file-less stub object — storage-ish methods, but not an
 * instance of jsdom's Storage and no usable `.clear`. */
const node25StyleStub = () => {
  const backing = new Map<string, string>()
  return {
    getItem: (key: string) => backing.get(key) ?? null,
    setItem: (key: string, value: string) => backing.set(key, value),
    removeItem: (key: string) => backing.delete(key),
  }
}

const jsdomEnv = () => ({
  Storage: globalThis.Storage,
  localStorage: globalThis.localStorage,
  sessionStorage: globalThis.sessionStorage,
})

describe("assertStorageProvenance", () => {
  it("passes on jsdom's real storages and leaves no residue", () => {
    expect(() => assertStorageProvenance()).not.toThrow()
    expect(localStorage.getItem("__storage_canary__")).toBeNull()
    expect(sessionStorage.getItem("__storage_canary__")).toBeNull()
  })

  it("rejects a Node-25-style file-less stub object", () => {
    const env = { ...jsdomEnv(), localStorage: node25StyleStub() }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage is not jsdom's real Storage/,
    )
  })

  it("rejects a Node-26-style undefined-returning accessor", () => {
    const env = {
      Storage: globalThis.Storage,
      sessionStorage: globalThis.sessionStorage,
      // Accessor, not a plain `undefined` property: the incident class is a
      // getter Node installs on globalThis that yields undefined under jsdom.
      get localStorage(): unknown {
        return undefined
      },
    }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage is not jsdom's real Storage/,
    )
  })

  it("checks sessionStorage too, not just localStorage", () => {
    const env = { ...jsdomEnv(), sessionStorage: node25StyleStub() }
    expect(() => assertStorageProvenance(env)).toThrow(
      /sessionStorage is not jsdom's real Storage/,
    )
  })

  it("fails with a named error when Storage itself is not a class", () => {
    const env = { ...jsdomEnv(), Storage: undefined }
    expect(() => assertStorageProvenance(env)).toThrow(
      /global Storage is not jsdom's Storage class/,
    )
  })

  it("rejects a provenance-passing storage whose round-trip is broken", () => {
    // instanceof Storage holds (prototype chain) but reads never see writes —
    // the inert-backing failure mode, distinct from the shadowing shapes.
    const inert = Object.create(Storage.prototype) as Storage
    Object.assign(inert, {
      getItem: () => null,
      setItem: () => undefined,
      removeItem: () => undefined,
      clear: () => undefined,
    })
    const env = { ...jsdomEnv(), localStorage: inert }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage set\/get round-trip failed/,
    )
  })

  it("names the --no-experimental-webstorage pin in every failure message", () => {
    expect(STORAGE_PIN_HINT).toContain("--no-experimental-webstorage")
    const failures = [
      { ...jsdomEnv(), Storage: undefined },
      { ...jsdomEnv(), localStorage: node25StyleStub() },
    ]
    for (const env of failures) {
      expect(() => assertStorageProvenance(env)).toThrow(
        /--no-experimental-webstorage/,
      )
    }
  })
})
