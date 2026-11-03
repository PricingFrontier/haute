/**
 * Frontend type-safety contracts.
 *
 * Pins two refactors in the hooks layer that replace implicit `as` casts
 * with parse-time narrowing that fails loudly. Both were identified as
 * hidden sources of type-drift bugs:
 *
 *   #116 — `usePipelineAPI.ts` read `node.data._columns` through
 *           `as Record<string, unknown>` casts.  A canonical graph is now
 *           checked by its generated validator and read as the editor's
 *           React Flow graph by `pipelineGraphFromContract`, which throws
 *           naming the field when the backend drifts.
 *
 *   #117 — `useSubmodelNavigation.ts` constructs port-nodes via
 *           `{ id, type, position, data } as Node` casts.  The cast
 *           hides the fact that these object literals _must_ satisfy
 *           the ReactFlow `Node` contract.  Replacement: a
 *           `validateReactFlowNode` guard that narrows any value to
 *           `Node` and throws on malformed shape.
 *
 * A third contract for `useUIStore.viewStack` (#128) was removed because
 * the slice shipped with zero production consumers, so the
 * store-side abstraction was deleted rather than migrated onto. The
 * existing consumer pattern (`useSubmodelNavigation`'s local
 * `useState<ViewLevel[]>`) remains the single source of truth.
 *
 * ── Test structure — lazy imports ─────────────────────────────────────
 *
 * The guard module (`../../types/guards`) does not exist yet.  If the
 * test file imported it statically, the whole suite would fail to load
 * and no individual test would be reported.  Instead we load the module
 * dynamically inside each test via `loadGuards()`.  This gives TDD-
 * friendly red output: as each symbol is implemented, the matching
 * tests go green one by one.
 *
 * ── Ambiguities parked for the implementer ─────────────────────────────
 *
 *   Location of the type guards.  The brief asked whether the guards
 *   belong in `frontend/src/types/` or inline.  The test file imports
 *   them from `../../types/guards` (a new module) on the assumption
 *   that co-locating all narrowing helpers in one file is the most
 *   reusable home.  If the implementer prefers another location, only
 *   the import path in this file needs updating — the contract the
 *   tests pin is identical.
 */

import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import type { Node } from "@xyflow/react"
import { validatePipelineGraph } from "../../generated/api-contracts.pipeline-graph.validators.mjs"
import { pipelineGraphFromContract } from "../../types/guards"

// ---------------------------------------------------------------------------
// Lazy module loaders.  Using dynamic `import()` lets the test file parse
// even when the target module is missing; each test that needs a symbol
// awaits its loader and fails with a clear "module not found" message.
// ---------------------------------------------------------------------------

interface GuardsModule {
  validateReactFlowNode: (v: unknown) => Node
}

/**
 * The guards module path is assembled from a template so that Vite's
 * static import-analysis plugin does not try to pre-resolve it.  At the
 * time this test was written the module does not exist and we want the
 * resolution failure to surface per-test as a thrown error rather than a
 * collection-time import failure that aborts the whole file.
 */
const GUARDS_MODULE_PATH = "../../types/guards"

async function loadGuards(): Promise<GuardsModule> {
  // Use a variable-based dynamic import + vite-ignore so the resolver
  // does not inline this at build time.
  return (await import(/* @vite-ignore */ GUARDS_MODULE_PATH)) as unknown as GuardsModule
}

// ---------------------------------------------------------------------------
// Helpers — on-disk grep assertions.  We check the source files verbatim
// to ensure the forbidden casts have truly been removed, not merely
// worked-around in a different way that still type-punts.
// ---------------------------------------------------------------------------

const THIS_DIR = dirname(fileURLToPath(import.meta.url))
const HOOKS_DIR = resolve(THIS_DIR, "..")
const PIPELINE_API_PATH = resolve(HOOKS_DIR, "usePipelineAPI.ts")
const SUBMODEL_NAV_PATH = resolve(HOOKS_DIR, "useSubmodelNavigation.ts")

function readSource(path: string): string {
  return readFileSync(path, "utf-8")
}

// ===========================================================================
// #116 — a canonical graph read as the editor's (generated validator +
// pipelineGraphFromContract)
// ===========================================================================

describe("#116 - a canonical graph read as the editor's", () => {
  const graph = (overrides: Record<string, unknown> = {}) => ({
    nodes: [{
      id: "n1",
      type: "dataInput",
      position: { x: 0, y: 0 },
      data: { label: "n1", description: "", nodeType: "dataInput", config: { inputType: "file", format: "parquet", path: "" }, editor: { steps_error: null, steps_discarded: null, discarded_sidecar: null } },
    }],
    edges: [],
    pipeline_name: "x",
    pipeline_description: null,
    preamble: "y",
    preserved_blocks: [],
    global_constants: [],
    global_constants_error: null,
    source_file: null,
    source_revision: null,
    submodels: null,
    sources: ["live"],
    active_source: "live",
    warning: null,
    ...overrides,
  })
  const read = (value: unknown) => pipelineGraphFromContract(validatePipelineGraph, value)

  it("reads a canonical graph's nodes, edges and metadata", () => {
    const parsed = read(graph())
    expect(parsed.nodes.map((node) => node.id)).toEqual(["n1"])
    expect(parsed.pipeline_name).toBe("x")
    expect(parsed.sources).toEqual(["live"])
  })

  it("refuses a node whose React Flow type is not a pipeline node type", () => {
    expect(() => read(graph({
      nodes: [{ ...graph().nodes[0], type: "mystery" }],
    }))).toThrow('graph.nodes[0].type: "mystery" is not a pipeline node type')
  })

  it("refuses a node whose data names an unknown node type", () => {
    const [node] = graph().nodes
    expect(() => read(graph({ nodes: [{ ...node, data: { ...node.data, nodeType: "mystery" } }] })))
      // A node's data is a union tagged by nodeType: a type it does not name matches no branch.
      .toThrow("PipelineGraph: invalid contract at /nodes/0/data")
  })

  it("refuses a graph without its edges", () => {
    const { edges: _omitted, ...withoutEdges } = graph()
    expect(() => read(withoutEdges)).toThrow("PipelineGraph: invalid contract at /edges: required")
  })

  // A submodel definition holds a graph of its own, checked as the root graph is.
  const withSubmodel = (inner: Record<string, unknown>) => graph({
    submodels: {
      pricing: { definitionId: "pricing", file: "modules/pricing.py", inputPorts: [], outputPorts: [], graph: inner },
    },
  })

  it("accepts a graph holding a submodel's graph", () => {
    expect(validatePipelineGraph(withSubmodel(graph()))).toBe(true)
  })

  it("refuses a malformed graph inside a submodel definition", () => {
    expect(() => read(withSubmodel({ ...graph(), nodes: "not an array" })))
      .toThrow("PipelineGraph: invalid contract at /submodels/pricing/graph/nodes: type")
  })

  it("refuses a node config inside a submodel's graph that its node type's model refuses", () => {
    const [node] = graph().nodes
    const refused = { ...node, data: { ...node.data, config: { inputType: "file", format: "parquet", path: 3 } } }
    expect(() => read(withSubmodel(graph({ nodes: [refused] }))))
      .toThrow("PipelineGraph: invalid contract at /submodels/pricing/graph/nodes/0/data")
  })

  // The bare casts that triggered #116 must not appear in the source. Ref
  // declarations typed as an opaque bag are not casts; `x as Record<string, unknown>`
  // on a value is.
  it("usePipelineAPI.ts contains no `as Record<string, unknown>` casts", () => {
    expect(readSource(PIPELINE_API_PATH)).not.toMatch(/\bas\s+Record<string,\s*unknown>/)
  })
})

// ===========================================================================
// #117 — validateReactFlowNode
// ===========================================================================

describe("#117 - validateReactFlowNode", () => {
  describe("acceptance", () => {
    it("accepts a minimal valid ReactFlow node and returns it narrowed", async () => {
      const { validateReactFlowNode } = await loadGuards()
      const raw = {
        id: "n1",
        position: { x: 0, y: 0 },
        data: { label: "N1" },
      }
      const node = validateReactFlowNode(raw)
      expect(node.id).toBe("n1")
      expect(node.position).toEqual({ x: 0, y: 0 })
      expect(node.data).toEqual({ label: "N1" })
    })

    it("accepts a node with optional fields (type, width, height)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      const raw = {
        id: "port_in__x",
        type: "submodelPort",
        position: { x: 100, y: 200 },
        data: { label: "In", portDirection: "input", portName: "In" },
        width: 150,
        height: 40,
      }
      const node = validateReactFlowNode(raw)
      expect(node.id).toBe("port_in__x")
      expect(node.type).toBe("submodelPort")
    })

    it("accepts fractional position coordinates (ReactFlow rounds on render)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      const raw = { id: "n", position: { x: 12.5, y: -3.7 }, data: {} }
      const node = validateReactFlowNode(raw)
      expect(node.position.x).toBe(12.5)
      expect(node.position.y).toBe(-3.7)
    })

    it("return type satisfies the ReactFlow Node contract", async () => {
      const { validateReactFlowNode } = await loadGuards()
      const raw = { id: "x", position: { x: 0, y: 0 }, data: {} }
      const n: Node = validateReactFlowNode(raw)
      expect(n.id).toBe("x")
    })
  })

  describe("rejection", () => {
    it("throws on null", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() => validateReactFlowNode(null)).toThrow()
    })

    it("throws on undefined", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() => validateReactFlowNode(undefined)).toThrow()
    })

    it("throws on a non-object primitive (string)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() => validateReactFlowNode("n1")).toThrow()
    })

    it("throws on a non-object primitive (number)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() => validateReactFlowNode(42)).toThrow()
    })

    it("throws when id is missing", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ position: { x: 0, y: 0 }, data: {} }),
      ).toThrow(/id/i)
    })

    it("throws when id is not a string", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: 42, position: { x: 0, y: 0 }, data: {} }),
      ).toThrow(/id/i)
    })

    it("throws when id is an empty string", async () => {
      const { validateReactFlowNode } = await loadGuards()
      // Empty-string IDs cause ReactFlow edges to silently not render — they
      // should never be accepted.
      expect(() =>
        validateReactFlowNode({ id: "", position: { x: 0, y: 0 }, data: {} }),
      ).toThrow()
    })

    it("throws when position is missing", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() => validateReactFlowNode({ id: "n", data: {} })).toThrow(
        /position/i,
      )
    })

    it("throws when position is null", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: "n", position: null, data: {} }),
      ).toThrow(/position/i)
    })

    it("throws when position is malformed (missing x)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: "n", position: { y: 0 }, data: {} }),
      ).toThrow(/position/i)
    })

    it("throws when position is malformed (missing y)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: "n", position: { x: 0 }, data: {} }),
      ).toThrow(/position/i)
    })

    it("throws when position.x is not a number", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({
          id: "n",
          position: { x: "0", y: 0 },
          data: {},
        }),
      ).toThrow(/position/i)
    })

    it("throws when position.y is not a number", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({
          id: "n",
          position: { x: 0, y: "0" },
          data: {},
        }),
      ).toThrow(/position/i)
    })

    it("throws when data is missing", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: "n", position: { x: 0, y: 0 } }),
      ).toThrow(/data/i)
    })

    it("throws when data is null", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({
          id: "n",
          position: { x: 0, y: 0 },
          data: null,
        }),
      ).toThrow(/data/i)
    })

    it("throws when data is a primitive (string)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({
          id: "n",
          position: { x: 0, y: 0 },
          data: "oops",
        }),
      ).toThrow(/data/i)
    })

    it("throws when data is an array (arrays are objects but not the Node.data shape)", async () => {
      const { validateReactFlowNode } = await loadGuards()
      expect(() =>
        validateReactFlowNode({ id: "n", position: { x: 0, y: 0 }, data: [] }),
      ).toThrow(/data/i)
    })
  })

  // -------------------------------------------------------------------------
  // Source-level: the lingering `as Node` casts on the port-node literals
  // must be gone.  After #117 these literals run through
  // `validateReactFlowNode` and need no cast at all.
  // -------------------------------------------------------------------------

  describe("source-level assertion (post-refactor)", () => {
    it("useSubmodelNavigation.ts contains no `as Node` casts", () => {
      const source = readSource(SUBMODEL_NAV_PATH)
      // Match ` as Node)` or ` as Node\n` etc. — the literal cast token.
      // A type annotation like `newNodes: Node[]` or `(n: Node) => ...`
      // does not have a leading `as` token and is not matched.
      expect(source).not.toMatch(/\bas\s+Node\b(?!\w)/)
    })
  })
})
