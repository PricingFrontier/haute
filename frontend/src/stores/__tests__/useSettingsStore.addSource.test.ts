/**
 * addSource mints the persisted data-source key through the blessed
 * sanitizeName — not an ad-hoc fold (sanitizer-proliferation class).
 *
 * The previous mint, trim().toLowerCase().replace(/\s+/g, "_"), was a
 * coarser identity than the blessed one: "My Src" and "my src" silently
 * became the SAME persisted key, and the folding rule could drift from the
 * blessed rules unnoticed.  These tests pin the blessed identity: case is
 * preserved (case-distinct labels are distinct sources), labels that DO
 * collide under sanitizeName are rejected at creation (never silently
 * merged), and keys already persisted in sidecars keep resolving.
 */
import { beforeEach, describe, expect, it } from "vitest"
import useSettingsStore from "../useSettingsStore"
import { sanitizeName } from "../../utils/sanitizeName"

const reset = () =>
  useSettingsStore.setState({ sources: ["live"], activeSource: "live" })

beforeEach(reset)

describe("addSource", () => {
  it("mints the key with the blessed sanitizer, preserving case", () => {
    expect(useSettingsStore.getState().addSource("My Src")).toBe("My_Src")
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src"])
  })

  it("keeps case-distinct labels distinct (the old fold merged them)", () => {
    const a = useSettingsStore.getState().addSource("My Src")
    const b = useSettingsStore.getState().addSource("my src")
    expect(a).toBe("My_Src")
    expect(b).toBe("my_src")
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src", "my_src"])
  })

  it("rejects (not silently merges) labels that collide under the blessed identity", () => {
    // Space and hyphen both map to underscore in sanitizeName, so these two
    // labels ARE one key under the blessed identity — the second add must
    // return null rather than coexist or clobber.
    expect(useSettingsStore.getState().addSource("data lake")).toBe("data_lake")
    expect(useSettingsStore.getState().addSource("data-lake")).toBeNull()
    expect(useSettingsStore.getState().sources).toEqual(["live", "data_lake"])
  })

  it("collides against keys already persisted in a loaded sidecar", () => {
    // Keys read back from a saved pipeline are opaque strings; a new label
    // minting the same key must be rejected against them too.
    useSettingsStore.setState({ sources: ["live", "my_src"] })
    expect(useSettingsStore.getState().addSource("my src")).toBeNull()
    expect(useSettingsStore.getState().sources).toEqual(["live", "my_src"])
  })

  it("returns null for empty and whitespace-only names", () => {
    expect(useSettingsStore.getState().addSource("")).toBeNull()
    expect(useSettingsStore.getState().addSource("   ")).toBeNull()
    expect(useSettingsStore.getState().sources).toEqual(["live"])
  })

  it("stays extensionally equal to sanitizeName on whatever it accepts", () => {
    for (const label of ["Motor Book", "quote-feed", "Q4 2025", "café"]) {
      reset()
      expect(useSettingsStore.getState().addSource(label)).toBe(sanitizeName(label))
    }
  })

  it("pins the documented digit-leading edge: sanitizeName prefixes node_", () => {
    // Cosmetic but deliberate: the blessed identifier sanitizer prefixes
    // digit-leading names.  Pinned so a future "fix" of the prefix is a
    // conscious identity change, not a drift.
    expect(useSettingsStore.getState().addSource("2024")).toBe("node_2024")
  })
})
