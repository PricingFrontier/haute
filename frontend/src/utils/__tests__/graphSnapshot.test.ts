/**
 * Format pin for the serialized persisted-fingerprint produced by the graph
 * snapshot path (`serializeSnapshot`, reached in the store via
 * `computePersistedFingerprint`).
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
import { serializeSnapshot, EMPTY_SNAPSHOT } from "../graphSnapshot"
import useGraphStore from "../../stores/useGraphStore"

// ---------------------------------------------------------------------------
// Fixtures — deliberately include everything the serializer must strip:
// React Flow UI fields (`selected`, `dragging`, `measured`), `_`-prefixed
// transient node-data metadata, and the same shapes nested inside submodels.
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

const EDGE = {
  id: "e1",
  source: "n1",
  sourceHandle: "out",
  target: "n2",
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
      },
    ],
    edges: [{ source: "s1", target: "s2", selected: true }],
  },
}

/**
 * The pinned serialized format: JSON with alphabetically sorted keys at every
 * level, arrays in caller order, UI/transient fields stripped, `null` values
 * preserved.
 */
const EXPECTED_FINGERPRINT =
  '{"edges":[{"id":"e1","source":"n1","sourceHandle":"out","sourcePort":null,"target":"n2","targetHandle":"in"}],' +
  '"nodes":[{"data":{"alpha":1,"label":"price"},"id":"n1","position":{"x":10,"y":20},"type":"expression"}],' +
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
        nodes: [NODE],
        edges: [EDGE],
        preamble: PREAMBLE,
        submodels: SUBMODELS,
      }),
    ).toBe(EXPECTED_FINGERPRINT)
  })

  it("is insensitive to object key insertion order", () => {
    const reorderedNode = {
      measured: { width: 100, height: 40 },
      data: { _transient: "dropped", alpha: 1, label: "price" },
      position: { y: 20, x: 10 },
      type: "expression",
      dragging: true,
      selected: true,
      id: "n1",
    } as Node

    expect(
      serializeSnapshot({
        nodes: [reorderedNode],
        edges: [EDGE],
        preamble: PREAMBLE,
        submodels: SUBMODELS,
      }),
    ).toBe(EXPECTED_FINGERPRINT)
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
      persistedFingerprint: EMPTY_SNAPSHOT,
      savedPersistedFingerprint: null,
      dirty: false,
    })
  })

  it("initial-state fingerprint is the empty sentinel", () => {
    expect(useGraphStore.getState().persistedFingerprint).toBe(EXPECTED_EMPTY)
  })

  it("store mutations recompute the fingerprint in the pinned format", () => {
    const store = useGraphStore.getState()
    store.setNodes([NODE])
    store.setEdges([EDGE])
    store.setPreamble(PREAMBLE)
    store.setSubmodelsRaw(SUBMODELS)

    expect(useGraphStore.getState().persistedFingerprint).toBe(EXPECTED_FINGERPRINT)
  })
})
