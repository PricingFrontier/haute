/**
 * Phase 3 Wave 6 — package 6D, item #95.
 *
 * usePipelineAPI.ts currently calls `columnsEqual(a, b)` (line 50) on every
 * preview completion to decide whether the downstream cascade must propagate.
 * That helper does an O(n) array walk every call. When columns are compared
 * dozens of times per second during cascades over wide frames, the walk
 * dominates the cascade-decision cost.
 *
 * The fix is to memoize a column-set fingerprint on the node (stored in
 * `node.data` alongside `_columns`) and compare fingerprints. Comparing two
 * strings (or a cached reference) is O(1) average and the memoized value
 * only needs to be recomputed when the column list itself changes.
 *
 * These tests pin the contract of the new `columnFingerprint` helper:
 *
 *   1. Fingerprint is stable — same input produces equal output.
 *   2. Name change → different fingerprint.
 *   3. Dtype change → different fingerprint.
 *   4. Reorder (order matters in the pipeline, so the fingerprint MUST
 *      detect it as different — a "set" fingerprint would be wrong).
 *   5. Empty list has a stable fingerprint distinct from undefined.
 *   6. Separator is collision-safe: `{name:"a|b", dtype:"x"}` must NOT
 *      collide with `{name:"a", dtype:"b|x"}`.
 *   7. Steady-state usage: fingerprints are precomputed once per column
 *      list and repeated comparisons use string equality.
 *
 * The helper MUST be exported from `../usePipelineAPI` (or an adjacent
 * module). If the production fix chooses a different module, update the
 * import below — DO NOT weaken these assertions.
 *
 * The contract:
 *   type ColumnDef = { name: string; dtype: string }
 *   export function columnFingerprint(cols: ColumnDef[] | undefined): string
 *
 * Returning `""` for `undefined` is acceptable as long as a non-empty list
 * never produces `""`. Returning a deterministic string means two nodes
 * with the same column list share one canonical fingerprint — cheap to
 * compare, no allocation overhead beyond the initial string build.
 */
import { describe, it, expect } from "vitest"
// `columnFingerprint` is the helper the production fix must export from
// `../usePipelineAPI` (or an adjacent module — update this import if the
// fix chooses a different home, but do NOT weaken the test contract).
import { columnFingerprint } from "../usePipelineAPI"

type ColumnDef = { name: string; dtype: string }

// The legacy O(n) implementation we are replacing, inlined here so the
// deterministic compatibility checks can compare against the old semantics.
// Must match the behaviour of the helper at `usePipelineAPI.ts:50` before
// the refactor.
function columnsEqualArrayWalk(
  a: ColumnDef[] | undefined,
  b: ColumnDef[] | undefined,
): boolean {
  if (!a && !b) return true
  if (!a || !b || a.length !== b.length) return false
  return a.every((col, i) => col.name === b[i].name && col.dtype === b[i].dtype)
}

// ─── Fixtures ──────────────────────────────────────────────────────────────

const single: ColumnDef[] = [{ name: "price", dtype: "f64" }]
const duo: ColumnDef[] = [
  { name: "price", dtype: "f64" },
  { name: "region", dtype: "str" },
]

function make50Columns(seed = 0): ColumnDef[] {
  const dtypes = ["f64", "i64", "str", "bool", "date"]
  return Array.from({ length: 50 }, (_, i) => ({
    name: `col_${seed}_${i}`,
    dtype: dtypes[i % dtypes.length],
  }))
}

// ─── Correctness ───────────────────────────────────────────────────────────

describe("columnFingerprint (#95)", () => {
  it("returns a string for a non-empty list", () => {
    const fp = columnFingerprint(duo)
    expect(typeof fp).toBe("string")
    expect(fp.length).toBeGreaterThan(0)
  })

  it("is stable: same list → same fingerprint (string-equal)", () => {
    const fp1 = columnFingerprint(duo)
    const fp2 = columnFingerprint(duo)
    expect(fp1).toBe(fp2)
  })

  it("is stable across two equal but distinct arrays", () => {
    // A fresh array with the same shape must produce the same fingerprint.
    // This is what makes the per-node memo effective: a new response with
    // the same columns reuses the prior fingerprint via string equality.
    const a: ColumnDef[] = [
      { name: "price", dtype: "f64" },
      { name: "region", dtype: "str" },
    ]
    const b: ColumnDef[] = [
      { name: "price", dtype: "f64" },
      { name: "region", dtype: "str" },
    ]
    expect(columnFingerprint(a)).toBe(columnFingerprint(b))
  })

  it("detects name change", () => {
    const changed: ColumnDef[] = [
      { name: "cost", dtype: "f64" }, // was "price"
      { name: "region", dtype: "str" },
    ]
    expect(columnFingerprint(duo)).not.toBe(columnFingerprint(changed))
  })

  it("detects dtype change", () => {
    const changed: ColumnDef[] = [
      { name: "price", dtype: "i64" }, // was "f64"
      { name: "region", dtype: "str" },
    ]
    expect(columnFingerprint(duo)).not.toBe(columnFingerprint(changed))
  })

  it("detects reorder (column order matters in the pipeline)", () => {
    // Polars cares about column order for positional ops (pl.col("0"),
    // pl.nth, select with list, etc). A fingerprint that hashed a SET
    // would silently miss re-orderings and wrongly halt cascades.
    const reordered: ColumnDef[] = [duo[1], duo[0]]
    expect(columnFingerprint(duo)).not.toBe(columnFingerprint(reordered))
  })

  it("detects added column", () => {
    const extra: ColumnDef[] = [...duo, { name: "new_col", dtype: "i64" }]
    expect(columnFingerprint(duo)).not.toBe(columnFingerprint(extra))
  })

  it("detects removed column", () => {
    expect(columnFingerprint(duo)).not.toBe(columnFingerprint(single))
  })

  it("empty list has a stable fingerprint", () => {
    expect(columnFingerprint([])).toBe(columnFingerprint([]))
  })

  it("empty list fingerprint differs from non-empty", () => {
    expect(columnFingerprint([])).not.toBe(columnFingerprint(single))
  })

  it("undefined is accepted and does not throw", () => {
    // usePipelineAPI passes `oldColumns` through from `node.data._columns`,
    // which is undefined on never-previewed nodes. The helper must handle
    // that silently — no guard statements at callsites.
    expect(() => columnFingerprint(undefined)).not.toThrow()
  })

  it("undefined fingerprint differs from non-empty fingerprint", () => {
    // Otherwise a never-previewed node (undefined) would compare equal to
    // an arbitrary column list — masking the very cascade we need.
    expect(columnFingerprint(undefined)).not.toBe(columnFingerprint(single))
  })

  // ─── Separator-collision invariants ─────────────────────────────────────
  // If the fingerprint encodes columns as `name|dtype` joined naively,
  // a column named "foo|bar" with dtype "baz" would collide with a column
  // "foo" + dtype "bar|baz". The production fix must escape or use a
  // separator that cannot appear inside names/dtypes. These tests pin
  // the collision-safety contract.

  it("is collision-safe against name containing the pipe separator", () => {
    const tricky: ColumnDef[] = [{ name: "a|b", dtype: "x" }]
    const decoy: ColumnDef[] = [{ name: "a", dtype: "b|x" }]
    expect(columnFingerprint(tricky)).not.toBe(columnFingerprint(decoy))
  })

  it("is collision-safe against name containing the column separator", () => {
    // If columns are joined with ",", a single column name "a,b" could
    // collide with two columns ["a", "b"] (same dtype). This is a realistic
    // risk because SQL aliases allow commas in rare cases.
    const tricky: ColumnDef[] = [{ name: "a,b", dtype: "x" }]
    const decoy: ColumnDef[] = [
      { name: "a", dtype: "x" },
      { name: "b", dtype: "x" },
    ]
    expect(columnFingerprint(tricky)).not.toBe(columnFingerprint(decoy))
  })

  // ─── Semantic compatibility with the old array-walk ──────────────────────
  // `columnFingerprint(a) === columnFingerprint(b)` MUST imply the old
  // `columnsEqual(a, b)` — otherwise the cascade logic changes. We validate
  // the implication over a randomised-ish corpus.

  it("fingerprint equality implies array-walk equality", () => {
    const corpus: Array<ColumnDef[] | undefined> = [
      undefined,
      [],
      single,
      duo,
      [...duo],
      [{ name: "a", dtype: "x" }],
      [{ name: "b", dtype: "x" }],
      [{ name: "a", dtype: "y" }],
      [
        { name: "a", dtype: "x" },
        { name: "b", dtype: "y" },
      ],
      [
        { name: "b", dtype: "y" },
        { name: "a", dtype: "x" },
      ],
    ]
    for (const a of corpus) {
      for (const b of corpus) {
        const fpEqual = columnFingerprint(a) === columnFingerprint(b)
        const walkEqual = columnsEqualArrayWalk(a, b)
        expect(fpEqual).toBe(walkEqual)
      }
    }
  })
})

// Steady-state comparison contract. Wall-clock speed ratios are too noisy
// for CI unit tests; this pins the deterministic behaviour the optimisation
// needs: precompute one fingerprint per list, then compare strings.

describe("columnFingerprint vs columnsEqual array walk", () => {
  it("precomputed fingerprint equality matches array-walk results over repeated 50-column compares", () => {
    const ITER = 1000
    const a = make50Columns(0)
    const bSame = make50Columns(0)
    const bDiff = make50Columns(1)

    const fpA = columnFingerprint(a)
    const fpBSame = columnFingerprint(bSame)
    const fpBDiff = columnFingerprint(bDiff)

    let walkTrueCount = 0
    for (let i = 0; i < ITER; i++) {
      if (columnsEqualArrayWalk(a, i % 2 === 0 ? bSame : bDiff)) walkTrueCount++
    }

    let fpTrueCount = 0
    for (let i = 0; i < ITER; i++) {
      if (i % 2 === 0 ? fpA === fpBSame : fpA === fpBDiff) fpTrueCount++
    }

    expect(fpTrueCount).toBe(walkTrueCount)
    expect(fpTrueCount).toBe(ITER / 2)
  })
})
