/**
 * Unit tests for the frontend path-grammar core (`jsonpath.ts`), the mirror of
 * `src/haute/_jsonpath.py` (PATH_GRAMMAR.md).
 *
 * Coverage:
 *   - the acceptance grammar (`parsePath` / `validateOutputPathCore`): the
 *     full-width set (§2.2) accepted, the §3 forbidden set rejected;
 *   - INPUT-mode parsing (`parseDataPath` + the two INPUT validators): the
 *     mandatory `$[:]` root, `allowRoot`, the `$value` reserved leaf, table-vs-
 *     column endpoint rules;
 *   - `isCanonical` (§2.1) and `canonicalForm` incl. the §5 non-identifier
 *     null-guard;
 *   - PARITY tables anchored to the verified backend output (the probe in the
 *     COMMIT-3 session) so the frontend cannot drift from `_jsonpath.py`.
 */
import { describe, it, expect } from "vitest"
import {
  parsePath,
  parseDataPath,
  makeOutputPath,
  isCanonical,
  canonicalForm,
  validateOutputPathCore,
  validateInputTablePath,
  validateInputColumnPath,
  PathError,
  RESERVED_LEAF,
  type Seg,
} from "../../panels/editors/jsonpath"

// ─── parse_path acceptance / §3 rejection (mirror of backend probe) ──

describe("parsePath — acceptance grammar (§2.2)", () => {
  // Anchored to the verified backend `parse_path` output.
  const ACCEPT = [
    "$[:].a",
    "$[:].a.b",
    "$[:].a[:].b",
    "$[:]['a']",
    '$[:]["a"]',
    "$.a", // valid core grammar (non-array root) — backend accepts, non-canonical
    "$[:].a[:]",
  ]
  it.each(ACCEPT)("accepts %s", (p) => {
    expect(() => parsePath(p)).not.toThrow()
    expect(validateOutputPathCore(p)).toBeNull()
  })

  it("normalises bracket name to a bare segment name", () => {
    expect(parsePath("$[:]['a']").segments).toEqual<Seg[]>([{ name: "a", isArray: false }])
    expect(parsePath('$[:]["a"]').segments).toEqual<Seg[]>([{ name: "a", isArray: false }])
  })

  it("flags array hops via [:]", () => {
    expect(parsePath("$[:].drivers[:].id").segments).toEqual<Seg[]>([
      { name: "drivers", isArray: true },
      { name: "id", isArray: false },
    ])
  })

  it("records the array-outer root", () => {
    expect(parsePath("$[:].a").rootArray).toBe(true)
    expect(parsePath("$.a").rootArray).toBe(false)
  })
})

describe("parsePath — §3 forbidden set rejected (mirror of backend probe)", () => {
  const REJECT = [
    "$[*]", // wildcard
    "$[:].a[*]", // wildcard after name
    "$[:]..a", // descendant
    "$[:].a[0]", // index
    "$[:].a[0:5]", // range
    "$[:].:", // the dropped `.:` dot form
    "a.b", // missing root
    "$[:].a b", // incidental whitespace
    "$[:].a.*", // non-array wildcard
    "$", // bare root names no leaf (OUTPUT core)
    "$[:]", // bare root array names no leaf (OUTPUT core)
    "", // empty
  ]
  it.each(REJECT)("rejects %s", (p) => {
    expect(() => parsePath(p)).toThrow(PathError)
    expect(validateOutputPathCore(p)).not.toBeNull()
  })

  it("throws a PathError carrying the offending path", () => {
    try {
      parsePath("$[*]")
      throw new Error("should have thrown")
    } catch (e) {
      expect(e).toBeInstanceOf(PathError)
      expect((e as PathError).path).toBe("$[*]")
    }
  })
})

// ─── parseDataPath — INPUT mode (mirror of backend probe) ────────────

describe("parseDataPath — INPUT mode", () => {
  it("bare root with allowRoot → zero segments (table root level)", () => {
    const r = parseDataPath("$[:]", { allowRoot: true })
    expect(r.segments).toEqual([])
    expect(r.rootArray).toBe(true)
  })

  it("bare root WITHOUT allowRoot is rejected (a column path must name a leaf)", () => {
    expect(() => parseDataPath("$[:]", { allowRoot: false })).toThrow(PathError)
  })

  it("rejects a bare-$ object root (a different transport, §5)", () => {
    expect(() => parseDataPath("$.a")).toThrow(/array-outer document via '\$\[:\]'/)
  })

  it("normalises bracket names in data paths", () => {
    expect(parseDataPath("$[:]['drivers'][:]").segments).toEqual<Seg[]>([
      { name: "drivers", isArray: true },
    ])
  })

  it("accepts the $value reserved leaf as a trailing object hop", () => {
    expect(parseDataPath("$[:].xs.$value", { reservedLeaf: RESERVED_LEAF }).segments).toEqual<Seg[]>([
      { name: "xs", isArray: false },
      { name: "$value", isArray: false },
    ])
  })

  it("accepts $value sitting directly on the root array", () => {
    expect(parseDataPath("$[:].$value", { reservedLeaf: RESERVED_LEAF }).segments).toEqual<Seg[]>([
      { name: "$value", isArray: false },
    ])
  })

  it("$value is NOT an identifier, so it is rejected without the reservedLeaf seam", () => {
    expect(() => parseDataPath("$[:].$value")).toThrow(PathError)
  })
})

// ─── INPUT table / column validators ─────────────────────────────────

describe("validateInputTablePath — ends at an array or the root", () => {
  it.each(["$[:]", "$[:].drivers[:]", "$[:].proposer.claims[:]", "$[:]['drivers'][:]"])(
    "accepts %s",
    (p) => expect(validateInputTablePath(p)).toBeNull(),
  )

  it("rejects a table path that ends at a bare object key", () => {
    expect(validateInputTablePath("$[:].drivers")).toMatch(/must end at an array/)
  })

  it.each(["$.drivers[:]", "$[*]", "$[:].a[0]"])("rejects invalid %s", (p) =>
    expect(validateInputTablePath(p)).not.toBeNull(),
  )
})

describe("validateInputColumnPath — names a leaf", () => {
  it.each([
    "$[:].quote_id",
    "$[:].quote_metadata.quote_id",
    "$[:].drivers[:].driver_id",
    "$[:].proposer.claims[:].amount",
    "$[:].xs.$value",
  ])("accepts %s", (p) => expect(validateInputColumnPath(p)).toBeNull())

  it("rejects a column path that names no leaf (ends at [:])", () => {
    expect(validateInputColumnPath("$[:].drivers[:]")).toMatch(/names no leaf/)
  })

  it("rejects the bare root iterator (names no leaf)", () => {
    expect(validateInputColumnPath("$[:]")).not.toBeNull()
  })
})

// ─── isCanonical (§2.1) — anchored to the verified backend output ────

describe("isCanonical — canonicality predicate (§2.1)", () => {
  const TRUE = ["$[:].a", "$[:].a.b", "$[:].a[:].b", "$[:].first[:]", "$[:].a[:].b.c"]
  const FALSE = [
    "$[:]['a']", // bracket form — non-canonical
    "$.a", // bare-$ root — non-canonical
    "$[:]", // names no leaf (rejected) — non-canonical
    "$[*]", // invalid — non-canonical
    "", // invalid — non-canonical
  ]
  it.each(TRUE)("%s is canonical", (p) => expect(isCanonical(p)).toBe(true))
  it.each(FALSE)("%s is NOT canonical", (p) => expect(isCanonical(p)).toBe(false))

  it("makeOutputPath output is always canonical", () => {
    const segs: Seg[] = [
      { name: "drivers", isArray: true },
      { name: "id", isArray: false },
    ]
    const out = makeOutputPath(segs)
    expect(out).toBe("$[:].drivers[:].id")
    expect(isCanonical(out)).toBe(true)
  })
})

// ─── canonicalForm — incl. the §5 non-identifier null-guard ──────────

describe("canonicalForm — the safe canonical rewrite, or null", () => {
  it("rewrites bracket identifier forms to the dotted canonical", () => {
    expect(canonicalForm("$[:]['a']")).toBe("$[:].a")
    expect(canonicalForm('$[:]["drivers"]["id"]')).toBe("$[:].drivers.id")
  })

  it("rewrites a bare-$ root to the array-outer canonical (safe — not a corruption)", () => {
    expect(canonicalForm("$.a")).toBe("$[:].a")
    expect(canonicalForm("$.a.b")).toBe("$[:].a.b")
  })

  it("is a no-op (identity) on an already-canonical path", () => {
    expect(canonicalForm("$[:].a.b")).toBe("$[:].a.b")
  })

  it("returns null for a non-identifier bracket name (§5 designed-out case)", () => {
    // `.first.last` would corrupt one key into two hops — never suggest it.
    expect(canonicalForm("$[:]['first.last']")).toBeNull()
    expect(canonicalForm("$[:]['2024']")).toBeNull()
    expect(canonicalForm("$[:]['has space']")).toBeNull()
  })

  it("returns null for an invalid path", () => {
    expect(canonicalForm("$[*]")).toBeNull()
    expect(canonicalForm("")).toBeNull()
  })

  it("the §5 case is non-canonical but has no safe rewrite (parity contract)", () => {
    // Mirrors the backend carve-out: is_canonical correctly false, but no safe
    // normalisation exists yet, so canonicalForm is null (don't warn).
    expect(isCanonical("$[:]['first.last']")).toBe(false)
    expect(canonicalForm("$[:]['first.last']")).toBeNull()
  })
})
