/**
 * Unit tests for the frontend path-grammar core (`jsonpath.ts`), the mirror of
 * `src/haute/_jsonpath.py` (PATH_GRAMMAR.md).
 *
 * Coverage:
 *   - the canonical grammar (`parsePath` / `validateOutputPathCore`);
 *   - INPUT-mode parsing (`parseDataPath` + the two INPUT validators): the
 *     mandatory `$[:]` root, `allowRoot`, the `$value` reserved leaf, table-vs-
 *     column endpoint rules;
 *   - PARITY tables anchored to the verified backend output (the probe in the
 *     COMMIT-3 session) so the frontend cannot drift from `_jsonpath.py`.
 */
import { describe, it, expect } from "vitest"
import {
  parsePath,
  parseDataPath,
  validateOutputPathCore,
  validateInputTablePath,
  validateInputColumnPath,
  PathError,
  RESERVED_LEAF,
  COLUMN_PATH,
  type Seg,
} from "../../panels/editors/jsonpath"

// ─── parse_path acceptance / §3 rejection (mirror of backend probe) ──

describe("parsePath - acceptance grammar (§2.2)", () => {
  // Anchored to the verified backend `parse_path` output.
  const ACCEPT = [
    "$[:].a",
    "$[:].a.b",
    "$[:].a[:].b",
    "$[:].a[:]",
  ]
  it.each(ACCEPT)("accepts %s", (p) => {
    expect(() => parsePath(p)).not.toThrow()
    expect(validateOutputPathCore(p)).toBeNull()
  })

  it("flags array hops via [:]", () => {
    expect(parsePath("$[:].drivers[:].id").segments).toEqual<Seg[]>([
      { name: "drivers", isArray: true },
      { name: "id", isArray: false },
    ])
  })

})

describe("parsePath - §3 forbidden set rejected (mirror of backend probe)", () => {
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
    "$[:]['a']", // alternate object selector
    "$.a", // non-array root
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

describe("parseDataPath - INPUT mode", () => {
  it("bare root with allowRoot → zero segments (table root level)", () => {
    const r = parseDataPath("$[:]", { noun: COLUMN_PATH, allowRoot: true })
    expect(r.segments).toEqual([])
  })

  it("bare root WITHOUT allowRoot is rejected (a column path must name a leaf)", () => {
    expect(() => parseDataPath("$[:]", { noun: COLUMN_PATH, allowRoot: false })).toThrow(PathError)
  })

  it("rejects a bare-$ object root", () => {
    expect(() => parseDataPath("$.a", { noun: COLUMN_PATH })).toThrow(/must start with '\$\[:\]'/)
  })

  it("accepts the $value reserved leaf as a trailing hop after an array", () => {
    expect(parseDataPath("$[:].xs[:].$value", { noun: COLUMN_PATH, reservedLeaf: RESERVED_LEAF }).segments).toEqual<Seg[]>([
      { name: "xs", isArray: true },
      { name: "$value", isArray: false },
    ])
  })

  it.each(["$[:].xs.$value", "$[:].xs[:].a.$value"])("refuses $value after an object hop (%s)", (raw) => {
    expect(() => parseDataPath(raw, { noun: COLUMN_PATH, reservedLeaf: RESERVED_LEAF })).toThrow(/must follow '\[:\]'/)
  })

  it("accepts $value sitting directly on the root array", () => {
    expect(parseDataPath("$[:].$value", { noun: COLUMN_PATH, reservedLeaf: RESERVED_LEAF }).segments).toEqual<Seg[]>([
      { name: "$value", isArray: false },
    ])
  })

  it("$value is NOT an identifier, so it is rejected without the reservedLeaf seam", () => {
    expect(() => parseDataPath("$[:].$value", { noun: COLUMN_PATH })).toThrow(PathError)
  })
})

// ─── INPUT table / column validators ─────────────────────────────────

describe("validateInputTablePath - ends at an array or the root", () => {
  it.each(["$[:]", "$[:].drivers[:]", "$[:].proposer.claims[:]"])(
    "accepts %s",
    (p) => expect(validateInputTablePath(p)).toBeNull(),
  )

  it("rejects a table path that ends at a bare object key", () => {
    expect(validateInputTablePath("$[:].drivers")).toMatch(/must end at an array/)
  })

  it.each(["$.drivers[:]", "$[*]", "$[:].a[0]"])("rejects invalid %s", (p) =>
    expect(validateInputTablePath(p)).not.toBeNull(),
  )

  it.each([
    ["$.drivers[:]", "table path must start with '$[:]'"],
    ["$[:].a-b[:]", "malformed table path"],
    ["$[:].5[:]", "unsupported table-path selector"],
    ["$[:]['a'][:]", "malformed table path"],
  ])("names %s's error as a table path's", (p, message) =>
    expect(validateInputTablePath(p)).toContain(message),
  )
})

describe("validateInputColumnPath - names a leaf", () => {
  it.each([
    "$[:].quote_id",
    "$[:].quote_metadata.quote_id",
    "$[:].drivers[:].driver_id",
    "$[:].proposer.claims[:].amount",
    "$[:].xs[:].$value",
  ])("accepts %s", (p) => expect(validateInputColumnPath(p)).toBeNull())

  it("rejects $value after an object hop", () => {
    expect(validateInputColumnPath("$[:].xs.$value")).toMatch(/must follow '\[:\]'/)
  })

  it("rejects a column path that names no leaf (ends at [:])", () => {
    expect(validateInputColumnPath("$[:].drivers[:]")).toMatch(/names no leaf/)
  })

  it("rejects the bare root iterator (names no leaf)", () => {
    expect(validateInputColumnPath("$[:]")).not.toBeNull()
  })

  it.each([
    ["quote_id", "column path must start with '$[:]'"],
    ["$[:].a b", "malformed column path"],
    ["$[:].a.5", "unsupported column-path selector"],
    ['$[:]["2024"]', "malformed column path"],
  ])("names %s's error as a column path's", (p, message) =>
    expect(validateInputColumnPath(p)).toContain(message),
  )
})
