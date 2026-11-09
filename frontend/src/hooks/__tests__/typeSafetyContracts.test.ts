/**
 * Frontend type-safety contracts.
 *
 * Pins two refactors in the hooks layer that removed implicit `as` casts.
 * Both were identified as
 * hidden sources of type-drift bugs:
 *
 *   #116 — `usePipelineAPI.ts` read `node.data._columns` through
 *           `as Record<string, unknown>` casts.  A canonical graph is now
 *           checked by its generated validator and read as the editor's
 *           React Flow graph by `pipelineGraphFromContract`, which throws
 *           naming the field when the backend drifts.
 *
 *   #117 — `useSubmodelNavigation.ts` built port-nodes through
 *           `{ id, type, position, data } as Node` casts; the literals now
 *           satisfy the React Flow `Node` contract without one.
 *
 * A third contract for `useUIStore.viewStack` (#128) was removed because
 * the slice shipped with zero production consumers, so the
 * store-side abstraction was deleted rather than migrated onto. The
 * existing consumer pattern (`useSubmodelNavigation`'s local
 * `useState<ViewLevel[]>`) remains the single source of truth.
 */

import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve, dirname } from "node:path"
import { fileURLToPath } from "node:url"
import { validatePipelineGraph } from "../../generated/api-contracts.pipeline-graph.validators.mjs"
import { pipelineGraphFromContract } from "../../types/guards"

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
      data: { label: "n1", description: "", nodeType: "dataInput", config: { input_type: "file", format: "parquet", path: "" }, editor: { steps_error: null, steps_discarded: null } },
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
    const refused = { ...node, data: { ...node.data, config: { input_type: "file", format: "parquet", path: 3 } } }
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
// #117 — port-node literals need no cast
// ===========================================================================

describe("#117 - port-node literals", () => {
  it("useSubmodelNavigation.ts contains no `as Node` casts", () => {
    const source = readSource(SUBMODEL_NAV_PATH)
    // Match the literal cast token, as in ` as Node)`.
    // A type annotation like `newNodes: Node[]` or `(n: Node) => ...`
    // does not have a leading `as` token and is not matched.
    expect(source).not.toMatch(/\bas\s+Node\b(?!\w)/)
  })
})
