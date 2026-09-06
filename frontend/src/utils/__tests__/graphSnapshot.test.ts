/**
 * Format pin for the serialized persisted-fingerprint produced by the graph
 * snapshot path (`serializeSnapshot`, which the graph store uses to derive
 * `persistedFingerprint` on every mutation).
 *
 * The fingerprint string is compared verbatim against `savedPersistedFingerprint`
 * to derive the dirty flag, and any change to its serialized shape (key order,
 * stripped fields, scope) invalidates every previously captured baseline. Today
 * that surfaces only as spurious dirty flags / cache misses downstream — these
 * tests make a format drift fail loudly here instead.
 *
 * If a change to this format is intentional, update the literals below and
 * audit every holder of a previously serialized fingerprint.
 */
import { describe, it, expect, beforeEach } from "vitest"
import type { Node } from "@xyflow/react"
import type { PipelineEdge } from "../../types/node"
import {
  serializeSnapshot,
  toCanonicalGraphPayload,
  EMPTY_SNAPSHOT,
} from "../graphSnapshot"
import useGraphStore from "../../stores/useGraphStore"

// ---------------------------------------------------------------------------
// Fixtures — between them the two nodes carry all six stripped React Flow UI
// fields (`selected`, `dragging`, `positionAbsolute`, `measured`, `resizing`,
// `computed`), `_`-prefixed transient node-data metadata, and a *nested*
// `_`-prefixed key that must SURVIVE (the metadata strip is shallow by
// design). The same shapes recur inside submodels. Node array order is
// deliberately non-sorted ("n1" before "n0") to pin caller-order retention.
// ---------------------------------------------------------------------------

const NODE = {
  id: "n1",
  type: "expression",
  position: { x: 10, y: 20 },
  data: { label: "price", alpha: 1, _transient: "dropped" },
  selected: true,
  dragging: true,
  measured: { width: 100, height: 40 },
} as Node

const NODE2 = {
  id: "n0",
  type: "dataInput",
  position: { x: 0, y: 0 },
  data: { label: "src", config: { _kept: true } },
  positionAbsolute: { x: 0, y: 0 },
  resizing: true,
  computed: { width: 1 },
  selected: false,
} as unknown as Node

const EDGE = {
  id: "e1",
  source: "n1",
  sourceHandle: "out",
  target: "n0",
  targetHandle: "in",
  sourcePort: null,
  selected: true,
} as PipelineEdge

const PREAMBLE = "import polars as pl"

const SUBMODELS: Record<string, unknown> = {
  sub1: {
    nodes: [
      {
        id: "s1",
        position: { x: 0, y: 0 },
        data: { kind: "input", _meta: 1 },
        selected: true,
        resizing: true,
        positionAbsolute: { x: 1, y: 1 },
      },
    ],
    edges: [{ source: "s1", target: "s2", selected: true }],
  },
}

/**
 * The pinned serialized format: JSON with alphabetically sorted keys at every
 * level, arrays in caller order (nodes are NOT sorted by id), UI/transient
 * fields stripped (shallow for `_`-metadata — nested `_kept` survives),
 * `null` values preserved.
 */
const EXPECTED_FINGERPRINT =
  '{"edges":[{"id":"e1","source":"n1","sourceHandle":"out","sourcePort":null,"target":"n0","targetHandle":"in"}],' +
  '"nodes":[{"data":{"alpha":1,"label":"price"},"id":"n1","position":{"x":10,"y":20},"type":"expression"},' +
  '{"data":{"config":{"_kept":true},"label":"src"},"id":"n0","position":{"x":0,"y":0},"type":"dataInput"}],' +
  '"preamble":"import polars as pl",' +
  '"submodels":{"sub1":{"edges":[{"source":"s1","target":"s2"}],"nodes":[{"data":{"kind":"input"},"id":"s1","position":{"x":0,"y":0}}]}}}'

const EXPECTED_EMPTY = '{"edges":[],"nodes":[],"preamble":"","submodels":{}}'

describe("persisted-fingerprint serialized format", () => {
  it("pins the empty-workspace sentinel byte-for-byte", () => {
    expect(EMPTY_SNAPSHOT).toBe(EXPECTED_EMPTY)
  })

  it("pins the full serialized shape byte-for-byte", () => {
    expect(
      serializeSnapshot({
        nodes: [NODE, NODE2],
        edges: [EDGE],
        preamble: PREAMBLE,
        submodels: SUBMODELS,
      }),
    ).toBe(EXPECTED_FINGERPRINT)
  })

  it("strips server-owned edge identities without dropping semantic edge data", () => {
    const identityOnly = {
      ...EDGE,
      data: { _inputName: "server_input" },
    } as PipelineEdge
    const semantic = {
      ...EDGE,
      id: "semantic",
      data: {
        _inputName: "server_input",
        routing: { mode: "explicit", _semanticKey: true },
      },
    } as PipelineEdge

    const parsed = JSON.parse(serializeSnapshot({
      nodes: [],
      edges: [identityOnly, semantic],
      preamble: "",
      submodels: {},
    })) as { edges: Array<Record<string, unknown>> }
    expect(parsed.edges[0]).not.toHaveProperty("data")
    expect(parsed.edges[1].data).toEqual({
      routing: { mode: "explicit", _semanticKey: true },
    })
  })

  it("is insensitive to object key insertion order at every level", () => {
    const reorderedNode = {
      measured: { width: 100, height: 40 },
      data: { _transient: "dropped", alpha: 1, label: "price" },
      position: { y: 20, x: 10 },
      type: "expression",
      dragging: true,
      selected: true,
      id: "n1",
    } as Node

    const reorderedEdge = {
      targetHandle: "in",
      sourcePort: null,
      target: "n0",
      selected: true,
      sourceHandle: "out",
      source: "n1",
      id: "e1",
    } as PipelineEdge

    const reorderedSubmodels: Record<string, unknown> = {
      sub1: {
        edges: [{ selected: true, target: "s2", source: "s1" }],
        nodes: [
          {
            positionAbsolute: { x: 1, y: 1 },
            resizing: true,
            selected: true,
            data: { _meta: 1, kind: "input" },
            position: { y: 0, x: 0 },
            id: "s1",
          },
        ],
      },
    }

    expect(
      serializeSnapshot({
        nodes: [reorderedNode, NODE2],
        edges: [reorderedEdge],
        preamble: PREAMBLE,
        submodels: reorderedSubmodels,
      }),
    ).toBe(EXPECTED_FINGERPRINT)
  })
})

describe("canonical graph request projection", () => {
  it("recursively strips editor metadata without mutating the live graph", () => {
    const rootNode = {
      ...NODE,
      data: {
        ...NODE.data,
        config: { expression: "pl.col('price')", _semanticOption: true },
        _functionName: "server_price",
        _defaultInputName: "server_price",
        _sourceHandleInputNames: {},
      },
    } as Node
    const rootEdge = {
      ...EDGE,
      data: { _inputName: "server_price", routing: { mode: "explicit" } },
    } as PipelineEdge
    const childNode = {
      id: "child",
      type: "polars",
      position: { x: 1, y: 2 },
      selected: true,
      data: {
        label: "Child",
        nodeType: "polars",
        config: {},
        _functionName: "server_child",
        _defaultInputName: "server_child",
        _sourceHandleInputNames: {},
      },
    } as Node
    const childEdge = {
      id: "child-edge",
      source: "child",
      target: "sink",
      selected: true,
      data: { _inputName: "server_child" },
    } as PipelineEdge
    const input = {
      nodes: [rootNode],
      edges: [rootEdge],
      preamble: PREAMBLE,
      submodels: {
        pricing: {
          definitionId: "pricing",
          file: "modules/pricing.py",
          graph: { nodes: [childNode], edges: [childEdge] },
          inputPorts: [],
          outputPorts: [],
        },
      },
    }

    const projected = toCanonicalGraphPayload(input)

    expect(projected.nodes[0]).not.toHaveProperty("selected")
    expect(projected.nodes[0].data).toEqual({
      label: "price",
      alpha: 1,
      config: { expression: "pl.col('price')", _semanticOption: true },
    })
    expect(projected.edges[0]).not.toHaveProperty("selected")
    expect(projected.edges[0].data).toEqual({ routing: { mode: "explicit" } })
    const definition = projected.submodels?.pricing as Record<string, unknown>
    const graph = definition.graph as { nodes: Node[]; edges: PipelineEdge[] }
    expect(graph.nodes[0]).not.toHaveProperty("selected")
    expect(graph.nodes[0].data).not.toHaveProperty("_functionName")
    expect(graph.edges[0]).not.toHaveProperty("selected")
    expect(graph.edges[0]).not.toHaveProperty("data")
    expect(input.nodes[0].data._functionName).toBe("server_price")
  })
})

describe("graph store produces the pinned format", () => {
  beforeEach(() => {
    useGraphStore.setState({
      nodes: [],
      edges: [],
      preamble: "",
      submodels: {},
      lastSavedSnapshot: null,
      undoStack: [],
      redoStack: [],
      // Deliberately wrong: every assertion below proves the store's own
      // mutation path RECOMPUTED the fingerprint, not that the reset seeded it.
      persistedFingerprint: "STALE-SENTINEL",
      savedPersistedFingerprint: null,
      dirty: false,
    })
  })

  it("every mutation recomputes the fingerprint in the pinned format", () => {
    const store = useGraphStore.getState()
    const fingerprint = () => useGraphStore.getState().persistedFingerprint

    store.setNodes([NODE, NODE2])
    expect(fingerprint()).toBe(
      serializeSnapshot({ nodes: [NODE, NODE2], edges: [], preamble: "", submodels: {} }),
    )

    store.setEdges([EDGE])
    expect(fingerprint()).toBe(
      serializeSnapshot({ nodes: [NODE, NODE2], edges: [EDGE], preamble: "", submodels: {} }),
    )

    store.setPreamble(PREAMBLE)
    expect(fingerprint()).toBe(
      serializeSnapshot({
        nodes: [NODE, NODE2],
        edges: [EDGE],
        preamble: PREAMBLE,
        submodels: {},
      }),
    )

    store.setSubmodelsRaw(SUBMODELS)
    expect(fingerprint()).toBe(EXPECTED_FINGERPRINT)
  })

  it("retains server identities in live undo snapshots", () => {
    const node = {
      ...NODE,
      data: {
        ...NODE.data,
        _functionName: "server_price",
        _defaultInputName: "server_price",
        _sourceHandleInputNames: {},
        _columns: [{ name: "price", dtype: "Float64" }],
        _status: "ok",
        _traceValue: { price: 10 },
      },
    } as Node
    const edge = {
      ...EDGE,
      data: { _inputName: "server_price", _loadAvailability: "ready" },
    } as PipelineEdge
    const submodels = {
      definition: {
        definitionId: "definition",
        graph: {
          nodes: [{
            id: "child",
            position: { x: 0, y: 0 },
            data: {
              label: "Child",
              _functionName: "server_child",
              _defaultInputName: "server_child",
              _sourceHandleInputNames: {},
              _columns: [{ name: "child", dtype: "Int64" }],
            },
          }],
          edges: [],
        },
      },
    }
    const store = useGraphStore.getState()
    store.loadGraphSnapshot({ nodes: [node], edges: [edge], preamble: "", submodels })
    store.setNodes((nodes) => nodes.map((candidate) => ({
      ...candidate,
      data: { ...candidate.data, label: "renamed" },
    })))

    useGraphStore.getState().undo()

    const restored = useGraphStore.getState()
    expect(restored.nodes[0]?.data).toMatchObject({
      _functionName: "server_price",
      _defaultInputName: "server_price",
      _sourceHandleInputNames: {},
    })
    expect(restored.nodes[0]?.data).not.toHaveProperty("_columns")
    expect(restored.nodes[0]?.data).not.toHaveProperty("_status")
    expect(restored.nodes[0]?.data).not.toHaveProperty("_traceValue")
    expect(restored.edges[0]?.data).toEqual({ _inputName: "server_price" })
    const restoredDefinition = restored.submodels.definition as Record<string, unknown>
    const restoredChild = (
      restoredDefinition.graph as { nodes: Node[] }
    ).nodes[0]
    expect(restoredChild?.data).toMatchObject({
      _functionName: "server_child",
      _defaultInputName: "server_child",
      _sourceHandleInputNames: {},
    })
    expect(restoredChild?.data).not.toHaveProperty("_columns")
  })
})
