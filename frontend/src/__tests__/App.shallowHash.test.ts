/**
 * Phase 3 Wave 6 Package 6E — Item #90:
 *
 * App.tsx:188 builds a fingerprint over every node's `data` using
 * ``JSON.stringify(n.data)`` on every render.  This dominates render cost
 * when nodes accumulate large config payloads, runtime column lists, and
 * schema-warning arrays — *none of which* gate the downstream work the
 * fingerprint actually governs.
 *
 * The fingerprint is compared against ``prevStructureRef.current`` and, if
 * changed, increments ``structuralVersion`` to invalidate the node-results
 * preview cache.  Only **input-identity** keys should gate this bump:
 *
 *   INPUT KEYS — change these and downstream must re-run:
 *     - nodeType   (the node's functional identity)
 *     - label      (rename may change graph shape / submodel lookups)
 *     - description
 *     - config     (the configuration the codegen / executor reads)
 *     - code       (user-provided code for polars / external nodes)
 *     - func_name  (the codegen-visible function identifier)
 *
 *   RESULT KEYS — downstream products of prior previews; ignoring them
 *   is the whole point of the optimisation:
 *     - _columns, _availableColumns, _schemaWarnings  (preview results)
 *     - _status, _traceActive, _traceDimmed, _hoverDimmed, _traceValue
 *       (trace / hover UI state that is downstream of, not input to,
 *       the preview computation)
 *
 * If a shallow hash over the input-key set is equal, the downstream
 * preview does not need to rerun — the executor's cache keyed on graph
 * fingerprint will return the same DataFrame.
 *
 * These tests pin:
 *   1. Identical input keys produce identical hashes (even when result
 *      keys differ).
 *   2. Any input-key change produces a different hash.
 *   3. Result-key changes are invisible (hash stays equal).
 *   4. The graph fingerprint avoids serializing large result-only payloads
 *      that made the pre-fix ``JSON.stringify`` path expensive.
 */
import { describe, it, expect } from "vitest"
import { shallowNodeDataHash } from "../utils/shallowNodeHash"

// ---------------------------------------------------------------------------
// Target functions under test
// ---------------------------------------------------------------------------
//
// Production ``shallowNodeDataHash`` lives in ``frontend/src/utils/shallowNodeHash.ts``
// and is imported directly above.  This test file exercises that production
// function — no inline reference copy — so drift is impossible by construction.
//
// The INPUT_KEYS contract (nodeType, label, description, config, code,
// func_name) is documented at the production call site.  See
// ``src/utils/shallowNodeHash.ts`` for the rationale about which keys are
// input-identity vs. result-only.

type NodeLike = { id: string; data: Record<string, unknown>; position?: { x: number; y: number } }
type EdgeLike = { id: string; source: string; target: string }

/**
 * Fingerprint builder used by App.tsx — mirrors the App.tsx effect so the
 * benchmark measures the same composition production builds (per-node
 * ``shallowNodeDataHash`` call + edge fingerprint concatenation).
 * Takes nodes + edges; returns a single string that can be compared
 * against ``prevStructureRef.current``.
 */
function graphFingerprintShallow(nodes: NodeLike[], edges: EdgeLike[]): string {
  const nodeFingerprint = nodes.map((n) => `${n.id}:${shallowNodeDataHash(n.data)}`).join("|")
  const edgeFingerprint = edges.map((e) => `${e.id}:${e.source}:${e.target}`).join("|")
  return `${nodeFingerprint}||${edgeFingerprint}`
}

/** The pre-fix behaviour for the benchmark comparison. */
function graphFingerprintFull(nodes: NodeLike[], edges: EdgeLike[]): string {
  const nodeFingerprint = nodes.map((n) => `${n.id}:${JSON.stringify(n.data)}`).join("|")
  const edgeFingerprint = edges.map((e) => `${e.id}:${e.source}:${e.target}`).join("|")
  return `${nodeFingerprint}||${edgeFingerprint}`
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeNode(id: string, data: Record<string, unknown> = {}): NodeLike {
  return {
    id,
    data: { label: id, nodeType: "polars", ...data },
    position: { x: 0, y: 0 },
  }
}

// ===========================================================================
// 1. Input-identity invariants — equal input keys → equal hash
// ===========================================================================

describe("shallowNodeDataHash — input-identity invariants", () => {
  it("two nodes with identical input keys produce identical hashes", () => {
    const a = { label: "A", nodeType: "polars", config: { code: "x + 1" } }
    const b = { label: "A", nodeType: "polars", config: { code: "x + 1" } }
    expect(shallowNodeDataHash(a)).toBe(shallowNodeDataHash(b))
  })

  it("two nodes with identical input keys but different result keys produce identical hashes", () => {
    const a = {
      label: "A",
      nodeType: "polars",
      config: { sort_by: "id" },
      _columns: [{ name: "x", dtype: "f64" }],
      _availableColumns: [{ name: "x", dtype: "f64" }, { name: "y", dtype: "i64" }],
      _schemaWarnings: [],
    }
    const b = {
      label: "A",
      nodeType: "polars",
      config: { sort_by: "id" },
      _columns: [{ name: "different_col", dtype: "str" }],
      _availableColumns: [],
      _schemaWarnings: [{ column: "x", status: "missing" }],
    }
    expect(shallowNodeDataHash(a)).toBe(shallowNodeDataHash(b))
  })

  it("a node with runtime status flags mutated does not invalidate its hash", () => {
    const base = { label: "N", nodeType: "polars", config: { c: 1 } }
    const before = shallowNodeDataHash(base)
    const afterTraceActive = shallowNodeDataHash({
      ...base,
      _status: "ok",
      _traceActive: true,
      _traceDimmed: false,
      _hoverDimmed: true,
      _traceValue: 42,
    })
    expect(afterTraceActive).toBe(before)
  })

  it("a node with a newly populated _columns array does not invalidate its hash", () => {
    const before = shallowNodeDataHash({ label: "N", nodeType: "polars", config: {} })
    const after = shallowNodeDataHash({
      label: "N",
      nodeType: "polars",
      config: {},
      _columns: [{ name: "col_a", dtype: "f64" }, { name: "col_b", dtype: "str" }],
    })
    expect(after).toBe(before)
  })

  it("optional input keys absent vs. explicitly undefined produce equal hashes", () => {
    const a = { label: "A", nodeType: "polars", config: { x: 1 } }
    const b = { label: "A", nodeType: "polars", config: { x: 1 }, description: undefined }
    expect(shallowNodeDataHash(a)).toBe(shallowNodeDataHash(b))
  })
})

// ===========================================================================
// 2. Sensitivity — any input-key change flips the hash
// ===========================================================================

describe("shallowNodeDataHash — input-key sensitivity", () => {
  const base = {
    label: "N",
    nodeType: "polars",
    description: "desc",
    config: { code: "a + 1" },
    code: "print(1)",
    func_name: "my_node",
  }

  it("nodeType change flips the hash", () => {
    const changed = { ...base, nodeType: "dataSource" }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("label change flips the hash", () => {
    const changed = { ...base, label: "Different" }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("description change flips the hash", () => {
    const changed = { ...base, description: "new description" }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("config content change flips the hash", () => {
    const changed = { ...base, config: { code: "a + 2" } }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("config with nested-object change flips the hash", () => {
    const nestedBase = { ...base, config: { nested: { a: 1, b: 2 } } }
    const nestedChanged = { ...base, config: { nested: { a: 1, b: 3 } } }
    expect(shallowNodeDataHash(nestedChanged)).not.toBe(shallowNodeDataHash(nestedBase))
  })

  it("code change flips the hash", () => {
    const changed = { ...base, code: "print(42)" }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("func_name change flips the hash", () => {
    const changed = { ...base, func_name: "renamed_node" }
    expect(shallowNodeDataHash(changed)).not.toBe(shallowNodeDataHash(base))
  })

  it("key collisions across input keys are avoided (the delimiter is non-empty)", () => {
    // If the implementer joined input-key values with "" (no delimiter),
    // two different splits (e.g. "abc" + "def" vs. "ab" + "cdef") would
    // collide.  This pins that a delimiter is used.
    const a = { label: "abc", nodeType: "def", config: {} }
    const b = { label: "ab", nodeType: "cdef", config: {} }
    expect(shallowNodeDataHash(a)).not.toBe(shallowNodeDataHash(b))
  })
})

// ===========================================================================
// 3. Graph-level fingerprint invariants
// ===========================================================================

describe("graphFingerprintShallow — graph-level invariants", () => {
  it("position-only changes do not affect the fingerprint", () => {
    const nodes = [makeNode("n1"), makeNode("n2")]
    const edges = [{ id: "e1", source: "n1", target: "n2" }]
    const moved = nodes.map((n) => ({ ...n, position: { x: 999, y: 999 } }))
    expect(graphFingerprintShallow(moved, edges)).toBe(graphFingerprintShallow(nodes, edges))
  })

  it("result-key changes on any node do not affect the fingerprint", () => {
    const nodes = [makeNode("n1"), makeNode("n2")]
    const edges = [{ id: "e1", source: "n1", target: "n2" }]
    const withResults = nodes.map((n) => ({
      ...n,
      data: {
        ...n.data,
        _columns: [{ name: "x", dtype: "f64" }],
        _availableColumns: [{ name: "y", dtype: "i64" }],
        _schemaWarnings: [{ column: "z", status: "missing" }],
        _status: "ok",
      },
    }))
    expect(graphFingerprintShallow(withResults, edges)).toBe(graphFingerprintShallow(nodes, edges))
  })

  it("adding a node changes the fingerprint", () => {
    const edges: EdgeLike[] = []
    const a = [makeNode("n1")]
    const b = [makeNode("n1"), makeNode("n2")]
    expect(graphFingerprintShallow(a, edges)).not.toBe(graphFingerprintShallow(b, edges))
  })

  it("adding an edge changes the fingerprint", () => {
    const nodes = [makeNode("n1"), makeNode("n2")]
    const before: EdgeLike[] = []
    const after: EdgeLike[] = [{ id: "e1", source: "n1", target: "n2" }]
    expect(graphFingerprintShallow(nodes, before)).not.toBe(graphFingerprintShallow(nodes, after))
  })

  it("config change on one of many nodes still flips the fingerprint", () => {
    const nodesBefore = Array.from({ length: 10 }, (_, i) =>
      makeNode(`n${i}`, { config: { v: i } })
    )
    const nodesAfter = nodesBefore.map((n, i) =>
      i === 5 ? { ...n, data: { ...n.data, config: { v: 999 } } } : n
    )
    expect(graphFingerprintShallow(nodesBefore, [])).not.toBe(graphFingerprintShallow(nodesAfter, []))
  })

  it("preview results cascading through the graph do not invalidate structure", () => {
    // Simulates the preview pipeline's effect: every node gets _columns
    // populated after execution.  Without the fix, this changes
    // structuralVersion, which invalidates the preview cache — defeating
    // the cache entirely on every preview completion.
    const nodesFresh = Array.from({ length: 50 }, (_, i) =>
      makeNode(`n${i}`, { config: { v: i } })
    )
    const edges: EdgeLike[] = nodesFresh.slice(1).map((n, i) => ({
      id: `e${i}`,
      source: `n${i}`,
      target: n.id,
    }))
    const nodesPrevied = nodesFresh.map((n) => ({
      ...n,
      data: {
        ...n.data,
        _columns: [{ name: "a", dtype: "f64" }, { name: "b", dtype: "i64" }],
        _availableColumns: [{ name: "a", dtype: "f64" }, { name: "b", dtype: "i64" }],
        _schemaWarnings: [],
      },
    }))
    expect(graphFingerprintShallow(nodesFresh, edges)).toBe(
      graphFingerprintShallow(nodesPrevied, edges)
    )
  })
})

// ===========================================================================
// 4. Regression guard: result-only payloads stay out of the graph hash
// ===========================================================================

describe("shallowNodeDataHash — benchmark", () => {
  function makeRealisticNode(i: number): NodeLike {
    // Realistic node: modest config, populated _columns / _availableColumns
    // arrays (these are the chief cost of the pre-fix full-stringify path).
    return {
      id: `node_${i}`,
      data: {
        label: `Node ${i}`,
        nodeType: i % 3 === 0 ? "dataSource" : "polars",
        description: `This is node ${i} with a short description`,
        config: {
          code: `df = df.with_columns(x_${i}=pl.col('x') * ${i})`,
          sort_by: "id",
          selected_columns: [`col_${i}_a`, `col_${i}_b`, `col_${i}_c`],
        },
        func_name: `node_fn_${i}`,
        // The large result-only arrays that dominate full-stringify cost:
        _columns: Array.from({ length: 40 }, (_, j) => ({
          name: `column_${j}`,
          dtype: j % 2 === 0 ? "f64" : "str",
        })),
        _availableColumns: Array.from({ length: 80 }, (_, j) => ({
          name: `available_column_${j}`,
          dtype: j % 2 === 0 ? "f64" : "i64",
        })),
        _schemaWarnings: Array.from({ length: 5 }, (_, j) => ({
          column: `warn_col_${j}`,
          status: "missing" as const,
        })),
        _status: "ok",
      },
      position: { x: i * 50, y: i * 30 },
    }
  }

  it("shallow graph hash excludes the large result-only payloads full stringify includes", () => {
    const NODE_COUNT = 200
    const ITERATIONS = 100
    const nodes = Array.from({ length: NODE_COUNT }, (_, i) => makeRealisticNode(i))
    const edges: EdgeLike[] = Array.from({ length: NODE_COUNT - 1 }, (_, i) => ({
      id: `e${i}`,
      source: `node_${i}`,
      target: `node_${i + 1}`,
    }))

    // Warm-up — avoid JIT noise skewing the first few samples.
    for (let w = 0; w < 5; w++) {
      graphFingerprintFull(nodes, edges)
      graphFingerprintShallow(nodes, edges)
    }

    const startFull = performance.now()
    for (let i = 0; i < ITERATIONS; i++) {
      graphFingerprintFull(nodes, edges)
    }
    const fullElapsed = performance.now() - startFull

    const startShallow = performance.now()
    for (let i = 0; i < ITERATIONS; i++) {
      graphFingerprintShallow(nodes, edges)
    }
    const shallowElapsed = performance.now() - startShallow

    // Keep the benchmark result in the CI log, but assert deterministic payload exclusion.
    console.log(
      `shallowHash benchmark: full=${fullElapsed.toFixed(1)}ms shallow=${shallowElapsed.toFixed(1)}ms ratio=${(fullElapsed / Math.max(shallowElapsed, 0.01)).toFixed(1)}x`
    )

    const fullFingerprint = graphFingerprintFull(nodes, edges)
    const shallowFingerprint = graphFingerprintShallow(nodes, edges)

    expect(fullFingerprint).toContain("available_column_79")
    expect(fullFingerprint).toContain("warn_col_4")
    expect(shallowFingerprint).not.toContain("available_column_79")
    expect(shallowFingerprint).not.toContain("warn_col_4")
    expect(shallowFingerprint.length).toBeLessThan(fullFingerprint.length / 10)
  })

  it("shallow hash for a single realistic node stays bounded by input payload size", () => {
    const node = makeRealisticNode(0)
    const fullData = JSON.stringify(node.data)
    const shallowData = shallowNodeDataHash(node.data)

    expect(fullData).toContain("available_column_79")
    expect(shallowData).not.toContain("available_column_79")
    expect(shallowData.length).toBeLessThan(fullData.length / 10)
  })
})
