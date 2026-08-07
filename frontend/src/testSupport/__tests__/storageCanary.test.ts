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

/** Provenance-passing instance (real prototype chain) with own methods. */
const storageShapedAs = (overrides: Partial<Storage>): Storage =>
  Object.assign(Object.create(Storage.prototype) as Storage, {
    getItem: () => null,
    setItem: () => undefined,
    removeItem: () => undefined,
    clear: () => undefined,
    ...overrides,
  })

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

  it("restores a pre-existing value under the probe key", () => {
    localStorage.setItem("__storage_canary__", "user-data")
    try {
      expect(() => assertStorageProvenance()).not.toThrow()
      expect(localStorage.getItem("__storage_canary__")).toBe("user-data")
    } finally {
      localStorage.removeItem("__storage_canary__")
    }
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

  it("rejects a provenance-passing instance with no usable .clear", () => {
    // instanceof holds, so this exercises the `.clear` disjunct on its own —
    // every not-instanceof shape above fails before reaching it.
    const noClear = storageShapedAs({ clear: undefined as unknown as Storage["clear"] })
    const env = { ...jsdomEnv(), localStorage: noClear }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage is not jsdom's real Storage/,
    )
  })

  it("rejects aliased storages (one object serving both names)", () => {
    // A process-global storage standing in for both passes provenance and
    // round-trips fine — only the identity check catches the broken isolation.
    const env = { ...jsdomEnv(), sessionStorage: globalThis.localStorage }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage and sessionStorage are the same object/,
    )
  })

  it("fails with a named error when Storage itself is not a class", () => {
    for (const notAClass of [undefined, () => undefined]) {
      // The arrow-function shape passes `typeof === "function"` but has no
      // prototype, so an unguarded instanceof would throw a bare TypeError.
      const env = { ...jsdomEnv(), Storage: notAClass }
      expect(() => assertStorageProvenance(env)).toThrow(
        /global Storage is not jsdom's Storage class/,
      )
    }
  })

  it("rejects a provenance-passing storage whose round-trip is broken", () => {
    // Reads never see writes — the inert-backing failure mode, distinct from
    // the shadowing shapes.
    const env = { ...jsdomEnv(), localStorage: storageShapedAs({}) }
    expect(() => assertStorageProvenance(env)).toThrow(
      /localStorage set\/get round-trip failed/,
    )
  })

  it("wraps a storage that throws, keeping the original as cause", () => {
    // An opaque-origin/unusable storage throws DOMException from setItem;
    // bare propagation would lose the pin hint.
    const quotaError = new Error("The quota has been exceeded.")
    const throwing = storageShapedAs({
      setItem: () => {
        throw quotaError
      },
    })
    const env = { ...jsdomEnv(), localStorage: throwing }
    let caught: unknown
    try {
      assertStorageProvenance(env)
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toMatch(
      /localStorage threw during the set\/get round-trip/,
    )
    expect((caught as Error).cause).toBe(quotaError)
  })

  it("leaves no residue when the round-trip fails partway", () => {
    // setItem lands in real backing but getItem lies — cleanup must still run.
    const lying = storageShapedAs({
      setItem: (key: string, value: string) =>
        Storage.prototype.setItem.call(globalThis.localStorage, key, value),
      getItem: () => null,
      removeItem: (key: string) =>
        Storage.prototype.removeItem.call(globalThis.localStorage, key),
    })
    const env = { ...jsdomEnv(), localStorage: lying }
    expect(() => assertStorageProvenance(env)).toThrow(/round-trip failed/)
    expect(globalThis.localStorage.getItem("__storage_canary__")).toBeNull()
  })

  it("names the --no-experimental-webstorage pin in every failure message", () => {
    expect(STORAGE_PIN_HINT).toContain("--no-experimental-webstorage")
    const failures: Array<Record<string, unknown>> = [
      { ...jsdomEnv(), Storage: undefined },
      { ...jsdomEnv(), localStorage: node25StyleStub() },
      { ...jsdomEnv(), sessionStorage: globalThis.localStorage },
      { ...jsdomEnv(), localStorage: storageShapedAs({}) },
      { ...jsdomEnv(), sessionStorage: storageShapedAs({}) },
      {
        ...jsdomEnv(),
        localStorage: storageShapedAs({
          setItem: () => {
            throw new Error("boom")
          },
        }),
      },
    ]
    for (const env of failures) {
      expect(() =>
        assertStorageProvenance(env as unknown as Parameters<typeof assertStorageProvenance>[0]),
      ).toThrow(/--no-experimental-webstorage/)
    }
  })

  // Documented blind spot, adjudicated in the PR #181 review: env.Storage is
  // the same untrusted global as the instances, so a fully self-consistent
  // foreign implementation passes — the canary proves internal consistency,
  // and proves jsdom provenance only while vitest's KEYS behaviour installs
  // jsdom's Storage class on the global. If vitest ever stops doing that,
  // this expected-failure starts passing and the suite flags it here.
  it.fails("cannot detect a self-consistent foreign Storage implementation", () => {
    class ForeignStorage {
      private backing = new Map<string, string>()
      getItem(key: string): string | null {
        return this.backing.get(key) ?? null
      }
      setItem(key: string, value: string): void {
        this.backing.set(key, value)
      }
      removeItem(key: string): void {
        this.backing.delete(key)
      }
      clear(): void {
        this.backing.clear()
      }
    }
    const env = {
      Storage: ForeignStorage,
      localStorage: new ForeignStorage(),
      sessionStorage: new ForeignStorage(),
    }
    expect(() =>
      assertStorageProvenance(env as unknown as Parameters<typeof assertStorageProvenance>[0]),
    ).toThrow()
  })
})
