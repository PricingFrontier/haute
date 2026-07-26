/**
 * Frontend type-safety contracts.
 *
 * Pins two refactors in the hooks layer that replace implicit `as` casts
 * with parse-time narrowing that fails loudly. Both were identified as
 * hidden sources of type-drift bugs:
 *
 *   #116 — `usePipelineAPI.ts` reads `node.data._columns` through
 *           `as Record<string, unknown>` casts.  These cover up the fact
 *           that the field is absent on first preview and has an
 *           app-specific shape (`ColumnDef[]`).  Replacement: a typed
 *           `isPipelineResponse` guard + `parsePipelineResponse` parser
 *           that narrow the top-level API response and throw with a
 *           clear "expected field X" message when the backend drifts.
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

// ---------------------------------------------------------------------------
// Lazy module loaders.  Using dynamic `import()` lets the test file parse
// even when the target module is missing; each test that needs a symbol
// awaits its loader and fails with a clear "module not found" message.
// ---------------------------------------------------------------------------

interface GuardsModule {
  isPipelineResponse: (v: unknown) => boolean
  parsePipelineResponse: (v: unknown) => unknown
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
// #116 — isPipelineResponse / parsePipelineResponse
// ===========================================================================

describe("#116 — isPipelineResponse / parsePipelineResponse", () => {
  // -------------------------------------------------------------------------
  // Shape-level acceptance
  // -------------------------------------------------------------------------

  describe("isPipelineResponse acceptance", () => {
    it("returns true for a minimal valid pipeline response", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [], edges: [] })).toBe(true)
    })

    it("returns true for a fully populated pipeline response", async () => {
      const { isPipelineResponse } = await loadGuards()
      const full = {
        nodes: [{ id: "n1", position: { x: 0, y: 0 }, data: { label: "N1" } }],
        edges: [{ id: "e1", source: "n1", target: "n2" }],
        pipeline_name: "pricing",
        pipeline_description: "Test pricing model",
        preamble: "import polars as pl",
        source_file: "pricing.py",
        submodels: { child: {} },
        warning: "something changed",
        sources: ["live", "test"],
        active_source: "test",
      }
      expect(isPipelineResponse(full)).toBe(true)
    })

    it("accepts a response with an empty preamble string", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [], edges: [], preamble: "" })).toBe(true)
    })

    it("accepts nullable metadata fields emitted by the backend response model", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(
        isPipelineResponse({
          nodes: [],
          edges: [],
          pipeline_name: null,
          pipeline_description: null,
          preamble: null,
          source_file: null,
          submodels: null,
          warning: null,
        }),
      ).toBe(true)
    })
  })

  // -------------------------------------------------------------------------
  // Shape-level rejection — malformed payloads must fail the guard.  These
  // cover the class of drift where the backend has been changed and the
  // frontend silently destructured `undefined` (the bug class #116 was
  // designed to surface).
  // -------------------------------------------------------------------------

  describe("isPipelineResponse rejection", () => {
    it("returns false for null", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse(null)).toBe(false)
    })

    it("returns false for undefined", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse(undefined)).toBe(false)
    })

    it("returns false for a non-object (string)", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse("pipeline")).toBe(false)
    })

    it("returns false for a non-object (number)", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse(42)).toBe(false)
    })

    it("returns false when `nodes` is missing", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ edges: [] })).toBe(false)
    })

    it("returns false when `edges` is missing", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [] })).toBe(false)
    })

    it("returns false when `nodes` is not an array", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: "oops", edges: [] })).toBe(false)
    })

    it("returns false when `edges` is not an array", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [], edges: {} })).toBe(false)
    })

    it.each([
      ["React Flow type", { type: "removedNodeType", data: {} }],
      ["node data type", { data: { nodeType: "removedNodeType" } }],
    ])("returns false for an unknown %s", async (_field, nodeFields) => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({
        nodes: [{
          id: "legacy",
          position: { x: 0, y: 0 },
          ...nodeFields,
        }],
        edges: [],
      })).toBe(false)
    })

    it("returns false when `preamble` is present but not a string", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [], edges: [], preamble: 42 })).toBe(false)
    })

    it("returns false when `pipeline_name` is present but not a string", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(
        isPipelineResponse({ nodes: [], edges: [], pipeline_name: 123 }),
      ).toBe(false)
    })

    it("returns false when `sources` is present but not an array", async () => {
      const { isPipelineResponse } = await loadGuards()
      expect(isPipelineResponse({ nodes: [], edges: [], sources: "live" })).toBe(
        false,
      )
    })
  })

  // -------------------------------------------------------------------------
  // parsePipelineResponse — throws with a helpful message on drift.
  //
  // The error message must name the offending field so the developer can
  // fix the backend contract without diffing protobufs by eye.
  // -------------------------------------------------------------------------

  describe("parsePipelineResponse", () => {
    it("returns the narrowed value on valid input", async () => {
      const { parsePipelineResponse } = await loadGuards()
      const input = { nodes: [], edges: [], preamble: "import x" }
      const parsed = parsePipelineResponse(input) as {
        nodes: unknown[]
        edges: unknown[]
        preamble: string
      }
      // Value equality (reference pass-through is an acceptable
      // implementation choice, but the test pins only semantic equality).
      expect(parsed.preamble).toBe("import x")
      expect(parsed.nodes).toEqual([])
      expect(parsed.edges).toEqual([])
    })

    it("does not reject backend null warnings as object-shaped drift", async () => {
      const { parsePipelineResponse } = await loadGuards()
      const parsed = parsePipelineResponse({
        nodes: [],
        edges: [],
        warning: null,
      }) as { warning?: string | null }

      expect(parsed.warning).toBeNull()
    })

    it("throws on null input", async () => {
      const { parsePipelineResponse } = await loadGuards()
      expect(() => parsePipelineResponse(null)).toThrow()
    })

    it("throws on undefined input", async () => {
      const { parsePipelineResponse } = await loadGuards()
      expect(() => parsePipelineResponse(undefined)).toThrow()
    })

    it("throws with a message naming the missing `nodes` field", async () => {
      const { parsePipelineResponse } = await loadGuards()
      expect(() => parsePipelineResponse({ edges: [] })).toThrow(/nodes/i)
    })

    it("throws with a message naming the missing `edges` field", async () => {
      const { parsePipelineResponse } = await loadGuards()
      expect(() => parsePipelineResponse({ nodes: [] })).toThrow(/edges/i)
    })

    it("throws with a message indicating the type mismatch for `nodes`", async () => {
      const { parsePipelineResponse } = await loadGuards()
      expect(() =>
        parsePipelineResponse({ nodes: "not-array", edges: [] }),
      ).toThrow(/nodes/i)
    })

    it("throws an Error (not a plain string) on bad shape", async () => {
      const { parsePipelineResponse } = await loadGuards()
      let caught: unknown
      try {
        parsePipelineResponse("hello")
      } catch (err) {
        caught = err
      }
      expect(caught).toBeInstanceOf(Error)
      const msg = (caught as Error).message
      expect(msg.length).toBeGreaterThan(0)
    })
  })

  // -------------------------------------------------------------------------
  // Narrowing — after parsePipelineResponse, the caller needs no further
  // `as` casts.  We assert at runtime that the returned value exposes the
  // typed fields — the static narrowing is enforced by TypeScript.
  // -------------------------------------------------------------------------

  describe("narrowing", () => {
    it("provides field access without further casts", async () => {
      const { parsePipelineResponse } = await loadGuards()
      const parsed = parsePipelineResponse({
        nodes: [],
        edges: [],
        pipeline_name: "x",
        preamble: "y",
        sources: ["live"],
      }) as {
        nodes: unknown[]
        edges: unknown[]
        pipeline_name?: string
        preamble?: string
        sources?: string[]
      }
      expect(parsed.nodes).toEqual([])
      expect(parsed.edges).toEqual([])
      expect(parsed.pipeline_name).toBe("x")
      expect(parsed.preamble).toBe("y")
      expect(parsed.sources).toEqual(["live"])
    })
  })

  // -------------------------------------------------------------------------
  // Static grep assertion — the bare casts that triggered #116 must not
  // appear in the source anymore.  We tolerate type-annotations on ref
  // declarations (`parentGraphRef: MutableRefObject<... submodels:
  // Record<string, unknown> ...>`) because those are not casts — they are
  // legitimate "treat this as an opaque bag" declarations.  What we ban
  // is the pattern `x as Record<string, unknown>` on a value.
  // -------------------------------------------------------------------------

  describe("source-level assertion (post-refactor)", () => {
    it("usePipelineAPI.ts contains no `as Record<string, unknown>` casts", () => {
      const source = readSource(PIPELINE_API_PATH)
      // Match an actual cast expression: ` as Record<string, unknown>`.
      // Ref-declaration type annotations of the same type are untouched
      // (they contain no leading `as` token).
      expect(source).not.toMatch(/\bas\s+Record<string,\s*unknown>/)
    })
  })
})

// ===========================================================================
// #117 — validateReactFlowNode
// ===========================================================================

describe("#117 — validateReactFlowNode", () => {
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
