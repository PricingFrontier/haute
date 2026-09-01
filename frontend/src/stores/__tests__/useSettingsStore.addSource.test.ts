/**
 * addSource mints the persisted data-source key through the blessed
 * portableKey — not an ad-hoc fold.
 *
 * The previous mint, trim().toLowerCase().replace(/\s+/g, "_"), was a
 * coarser identity than the blessed one: "My Src" and "my src" silently
 * became the SAME persisted key, and the folding rule could drift from the
 * blessed rules unnoticed.  These tests pin the blessed identity: case is
 * preserved (case-distinct labels are distinct sources), labels that DO
 * collide under portableKey are rejected at creation (never silently
 * merged), and keys already persisted in sidecars keep resolving.
 *
 * addSource returns a discriminated AddSourceResult so a reject carries WHY
 * (`empty` vs `duplicate`, the latter naming the colliding key) — the caller
 * surfaces the reason instead of treating the old bare `null` as success.
 * The `ok/reason` contract is pinned here alongside the identity.
 */
import { beforeEach, describe, expect, it } from "vitest"
import useSettingsStore from "../useSettingsStore"
import { portableKey } from "../../utils/portableKey"

const reset = () =>
  useSettingsStore.setState({ sources: ["live"], activeSource: "live" })

beforeEach(reset)

describe("addSource — blessed identity", () => {
  it("mints the key with the blessed sanitizer, preserving case", () => {
    expect(useSettingsStore.getState().addSource("My Src")).toEqual({ ok: true, key: "My_Src" })
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src"])
  })

  it("keeps case-distinct labels distinct (the old fold merged them)", () => {
    const a = useSettingsStore.getState().addSource("My Src")
    const b = useSettingsStore.getState().addSource("my src")
    expect(a).toEqual({ ok: true, key: "My_Src" })
    expect(b).toEqual({ ok: true, key: "my_src" })
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src", "my_src"])
  })

  it("rejects (not silently merges) labels that collide under the blessed identity", () => {
    // Space and hyphen both map to underscore in portableKey, so these two
    // labels ARE one key under the blessed identity — the second add must be
    // rejected (naming the colliding key) rather than coexist or clobber.
    expect(useSettingsStore.getState().addSource("data lake")).toEqual({ ok: true, key: "data_lake" })
    expect(useSettingsStore.getState().addSource("data-lake")).toEqual({ ok: false, reason: "duplicate", key: "data_lake" })
    expect(useSettingsStore.getState().sources).toEqual(["live", "data_lake"])
  })

  it("collides against keys already persisted in a loaded sidecar", () => {
    // Keys read back from a saved pipeline are opaque strings; a new label
    // minting the same key must be rejected against them too.
    useSettingsStore.setState({ sources: ["live", "my_src"] })
    expect(useSettingsStore.getState().addSource("my src")).toEqual({ ok: false, reason: "duplicate", key: "my_src" })
    expect(useSettingsStore.getState().sources).toEqual(["live", "my_src"])
  })

  it("stays extensionally equal to portableKey on whatever it accepts", () => {
    for (const label of ["Motor Book", "quote-feed", "Q4 2025", "café"]) {
      reset()
      expect(useSettingsStore.getState().addSource(label)).toEqual({ ok: true, key: portableKey(label) })
    }
  })

  it("pins the documented digit-leading edge: portableKey prefixes item_", () => {
    // Cosmetic but deliberate: the blessed identifier sanitizer prefixes
    // digit-leading names.  Pinned so a future "fix" of the prefix is a
    // conscious identity change, not a drift.
    expect(useSettingsStore.getState().addSource("2024")).toEqual({ ok: true, key: "item_2024" })
  })
})

describe("addSource — discriminated reject reason", () => {
  // The reason is what lets the toolbar word the right feedback instead of
  // closing the form silently (the residual UX sibling of the mint fix).

  it("rejects a blank/whitespace-only name with reason 'empty' (no key, no mutation)", () => {
    expect(useSettingsStore.getState().addSource("")).toEqual({ ok: false, reason: "empty" })
    expect(useSettingsStore.getState().addSource("   ")).toEqual({ ok: false, reason: "empty" })
    expect(useSettingsStore.getState().sources).toEqual(["live"])
  })

  it("rejects a colliding label with reason 'duplicate' and the existing key it collides with", () => {
    useSettingsStore.getState().addSource("My Src")
    const result = useSettingsStore.getState().addSource("My-Src")
    expect(result).toEqual({ ok: false, reason: "duplicate", key: "My_Src" })
    // The colliding key is the one already in the sources list, so the caller
    // can point the user at the source that shadows their label.
    if (!result.ok && result.reason === "duplicate") {
      expect(useSettingsStore.getState().sources).toContain(result.key)
    }
  })

  it("distinguishes the two reject reasons (empty carries no key; duplicate does)", () => {
    const empty = useSettingsStore.getState().addSource("  ")
    useSettingsStore.getState().addSource("live_two")
    const dup = useSettingsStore.getState().addSource("live two")
    expect(empty).not.toHaveProperty("key")
    expect(dup).toHaveProperty("key", "live_two")
  })

  it("narrows to the minted key on success (ok:true carries key)", () => {
    const result = useSettingsStore.getState().addSource("Fresh Source")
    expect(result.ok).toBe(true)
    // Type-narrowing: key is only reachable on the ok branch.
    if (result.ok) {
      expect(result.key).toBe("Fresh_Source")
      expect(useSettingsStore.getState().sources).toContain(result.key)
    }
  })
})
