/**
 * Tests for the non-modal non-canonical highlight logic (PATH_GRAMMAR.md §4):
 * the `nonCanonicalHint` predicate (which valid paths get flagged, and whether a
 * safe canonical form exists to name) and the shared `nonCanonicalNote` wording.
 *
 * This replaced a per-session-global MODAL keyed on manual commit + a
 * sessionStorage dismissal. The new surface is a derived, always-on highlight,
 * so there is no provider, no commit-boundary hook, and no dismissal to test —
 * just the pure predicate. The editor render-level assertions (that the note
 * actually appears on a non-canonical field) live in OutputEditor.test.tsx and
 * ApiInputEditor.test.tsx, next to those editors' harnesses.
 */
import { describe, it, expect } from "vitest"
import { nonCanonicalHint, nonCanonicalNote } from "../../panels/editors/pathCanonicalWarning"

describe("nonCanonicalHint — the §4 highlight predicate", () => {
  it("flags a valid non-canonical path and names its safe canonical form", () => {
    expect(nonCanonicalHint("$[:]['a']")).toEqual({ canonical: "$[:].a" })
    expect(nonCanonicalHint('$[:].drivers[:]["name"]')).toEqual({
      canonical: "$[:].drivers[:].name",
    })
  })

  it("returns null for an already-canonical path (nothing to highlight)", () => {
    expect(nonCanonicalHint("$[:].a")).toBeNull()
    expect(nonCanonicalHint("$[:].a.b")).toBeNull()
    expect(nonCanonicalHint("$[:].drivers[:].name")).toBeNull()
  })

  it("STILL flags the §5 non-identifier case — with no canonical form to offer", () => {
    // Broadened vs the old modal, which suppressed this case (it had no safe
    // rewrite to show). The new surface is informational: a non-identifier key
    // is exactly the signal to fix something upstream, so it IS highlighted —
    // just without a canonical spelling (`canonical: null`).
    expect(nonCanonicalHint("$[:]['first.last']")).toEqual({ canonical: null })
    expect(nonCanonicalHint("$[:]['2024']")).toEqual({ canonical: null })
  })

  it("returns null for an empty path", () => {
    expect(nonCanonicalHint("")).toBeNull()
  })

  it("returns null for a path that does not parse (invalid, not non-canonical)", () => {
    // An invalid path surfaces its grammar error instead; it must never be
    // mislabelled 'non-canonical'.
    expect(nonCanonicalHint("$[*]")).toBeNull()
    expect(nonCanonicalHint("$[0].a")).toBeNull()
    expect(nonCanonicalHint("not a path")).toBeNull()
  })
})

describe("nonCanonicalNote — the shared informational wording", () => {
  it("names the canonical form when one exists", () => {
    expect(nonCanonicalNote({ canonical: "$[:].a" })).toBe(
      "Non-canonical path — assembles identically. Canonical form: $[:].a",
    )
  })

  it("states the no-safe-spelling case without offering a rewrite", () => {
    expect(nonCanonicalNote({ canonical: null })).toBe(
      "Non-canonical path — assembles identically (no simpler spelling exists for non-identifier keys).",
    )
  })
})
