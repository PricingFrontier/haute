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
 * changed, calls ``bumpGraphVersion()`` to invalidate the node-results
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
 *   4. The hash is ≥10× faster than the pre-fix ``JSON.stringify`` on a
 *      realistic 200-node graph.
 */
import { describe, it, expect } from "vitest"

// ---------------------------------------------------------------------------
// Target functions under test
// ---------------------------------------------------------------------------
//
// The shallow-hash helper the implementer will add to App.tsx (or a small
// util module).  We inline the function here so the test can pin its
// contract without depending on wiring-up details.  Post-fix, the real
// implementation is expected to be identical (character-for-character) to
// this reference — and the App.tsx fingerprint line will call it.
//
// INPUT_KEYS is the minimal set of keys that, if all unchanged, means
// downstream preview work does not need to rerun.  Do not add result-only
// keys here; do not remove input keys.

const INPUT_KEYS = ["nodeType", "label", "description", "config", "code", "func_name"] as const

type NodeLike = { id: string; data: Record<string, unknown>; position?: { x: number; y: number } }
type EdgeLike = { id: string; source: string; target: string }

/**
 * Reference implementation of the shallow hash used by App.tsx.
 *
 * Only inspects keys in INPUT_KEYS, JSON-stringifying their individual
 * values.  For primitive-valued keys (label, nodeType, code, func_name)
 * this is cheaper than full ``JSON.stringify(n.data)`` which walks every
 * key including _columns arrays and nested schema_warning lists.
 *
 * For the ``config`` key we still call ``JSON.stringify`` because configs
 * are structured objects whose content genuinely matters.  But we skip
 * all result-only sibling keys.
 */
function shallowNodeHash(data: Record<string, unknown>): string {
  const parts: string[] = []
  for (const key of INPUT_KEYS) {
    const v = data[key]
    if (v === undefined) {
      parts.push("")
      continue
    }
    parts.push(typeof v === "object" ? JSON.stringify(v) : String(v))
  }
  return parts.join("\u0001")
}

/**
 * Fingerprint builder used by App.tsx — mirrors the post-fix shape.
 * Takes nodes + edges; returns a single string that can be compared
 * against ``prevStructureRef.current``.
 */
function graphFingerprintShallow(nodes: NodeLike[], edges: EdgeLike[]): string {
  const nodeFingerprint = nodes.map((n) => `${n.id}:${shallowNodeHash(n.data)}`).join("|")
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

describe("shallowNodeHash — input-identity invariants", () => {
  it("two nodes with identical input keys produce identical hashes", () => {
    const a = { label: "A", nodeType: "polars", config: { code: "x + 1" } }
    const b = { label: "A", nodeType: "polars", config: { code: "x + 1" } }
    expect(shallowNodeHash(a)).toBe(shallowNodeHash(b))
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
    expect(shallowNodeHash(a)).toBe(shallowNodeHash(b))
  })

  it("a node with runtime status flags mutated does not invalidate its hash", () => {
    const base = { label: "N", nodeType: "polars", config: { c: 1 } }
    const before = shallowNodeHash(base)
    const afterTraceActive = shallowNodeHash({
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
    const before = shallowNodeHash({ label: "N", nodeType: "polars", config: {} })
    const after = shallowNodeHash({
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
    expect(shallowNodeHash(a)).toBe(shallowNodeHash(b))
  })
})

// ===========================================================================
// 2. Sensitivity — any input-key change flips the hash
// ===========================================================================

describe("shallowNodeHash — input-key sensitivity", () => {
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
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("label change flips the hash", () => {
    const changed = { ...base, label: "Different" }
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("description change flips the hash", () => {
    const changed = { ...base, description: "new description" }
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("config content change flips the hash", () => {
    const changed = { ...base, config: { code: "a + 2" } }
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("config with nested-object change flips the hash", () => {
    const nestedBase = { ...base, config: { nested: { a: 1, b: 2 } } }
    const nestedChanged = { ...base, config: { nested: { a: 1, b: 3 } } }
    expect(shallowNodeHash(nestedChanged)).not.toBe(shallowNodeHash(nestedBase))
  })

  it("code change flips the hash", () => {
    const changed = { ...base, code: "print(42)" }
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("func_name change flips the hash", () => {
    const changed = { ...base, func_name: "renamed_node" }
    expect(shallowNodeHash(changed)).not.toBe(shallowNodeHash(base))
  })

  it("key collisions across input keys are avoided (the delimiter is non-empty)", () => {
    // If the implementer joined input-key values with "" (no delimiter),
    // two different splits (e.g. "abc" + "def" vs. "ab" + "cdef") would
    // collide.  This pins that a delimiter is used.
    const a = { label: "abc", nodeType: "def", config: {} }
    const b = { label: "ab", nodeType: "cdef", config: {} }
    expect(shallowNodeHash(a)).not.toBe(shallowNodeHash(b))
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
    // populated after execution.  Without the fix, this re-bumps
    // graphVersion, which invalidates the preview cache — defeating
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
// 4. Benchmark — shallow hash must be ≥10× faster than full stringify
// ===========================================================================

describe("shallowNodeHash — benchmark", () => {
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

  it("shallow hash on a 200-node graph is >10x faster than full JSON.stringify", () => {
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

    // Pin the benchmark result so the CI log captures the ratio.
    console.log(
      `shallowHash benchmark: full=${fullElapsed.toFixed(1)}ms shallow=${shallowElapsed.toFixed(1)}ms ratio=${(fullElapsed / Math.max(shallowElapsed, 0.01)).toFixed(1)}x`
    )

    // Target: the shallow hash is at least 10× faster.
    // Guard against zero-divide on extremely fast shallow runs.
    const speedup = fullElapsed / Math.max(shallowElapsed, 0.01)
    expect(speedup).toBeGreaterThan(10)
  })

  it("shallow hash for a single realistic node is microseconds, not milliseconds", () => {
    const node = makeRealisticNode(0)
    const ITERATIONS = 10_000

    // Warm-up
    for (let w = 0; w < 100; w++) shallowNodeHash(node.data)

    const start = performance.now()
    for (let i = 0; i < ITERATIONS; i++) shallowNodeHash(node.data)
    const elapsed = performance.now() - start

    // 10k iterations should comfortably finish in under 50ms
    // (i.e. average <5 microseconds per call).
    expect(elapsed).toBeLessThan(50)
  })
})
