/**
 * Tests for BoxSelectionReset: React Flow's box-selection group survives updates to its own
 * nodes and ends as soon as the selection changes by any other means. The React Flow store is a
 * vanilla zustand store shaped like the fields the component reads.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render } from "@testing-library/react"
import { createStore, type StoreApi } from "zustand/vanilla"

import BoxSelectionReset from "../BoxSelectionReset"

type FakeNode = {
  id: string
  position: { x: number; y: number }
  selected?: boolean
  measured?: { width: number; height: number }
}

type FakeFlowState = {
  nodesSelectionActive: boolean
  nodes: FakeNode[]
}

const flow = vi.hoisted(() => ({
  store: null as unknown as StoreApi<FakeFlowState>,
}))

vi.mock("@xyflow/react", async () => {
  const { useStore } = await vi.importActual<typeof import("zustand")>("zustand")
  return {
    useStoreApi: () => flow.store,
    useStore: <T,>(selector: (state: FakeFlowState) => T) => useStore(flow.store, selector),
  }
})

function node(id: string, x: number, selected = false): FakeNode {
  return { id, position: { x, y: 0 }, selected, measured: { width: 240, height: 60 } }
}

/** Selects `ids` with a box: React Flow selects while the box is drawn, then activates the group on release. */
function boxSelect(ids: readonly string[]) {
  act(() => {
    flow.store.setState((state) => ({
      nodes: state.nodes.map((n) => ({ ...n, selected: ids.includes(n.id) })),
    }))
  })
  act(() => {
    flow.store.setState({ nodesSelectionActive: true })
  })
}

/** Replaces the controlled nodes, as the editor does through React Flow's `nodes` prop. */
function setNodes(nodes: FakeNode[]) {
  act(() => {
    flow.store.setState({ nodes })
  })
}

function groupActive(): boolean {
  return flow.store.getState().nodesSelectionActive
}

beforeEach(() => {
  flow.store = createStore<FakeFlowState>(() => ({
    nodesSelectionActive: false,
    nodes: [node("a", 0), node("b", 300), node("c", 600)],
  }))
})

afterEach(cleanup)

describe("BoxSelectionReset", () => {
  it("keeps the group while its nodes move, are re-measured, or reorder", () => {
    render(<BoxSelectionReset />)
    boxSelect(["a", "b"])

    setNodes([
      { ...node("b", 340, true), measured: { width: 260, height: 72 } },
      node("a", 40, true),
      node("c", 600),
    ])

    expect(groupActive()).toBe(true)
  })

  it.each([
    ["a just-created node is selected exclusively", [node("a", 0), node("b", 300), node("c", 600), { id: "d", position: { x: 900, y: 0 }, selected: true }]],
    ["select-all adds a node to the selection", [node("a", 0, true), node("b", 300, true), node("c", 600, true)]],
    ["undo or a reload clears the selection", [node("a", 0), node("b", 300), node("c", 600)]],
  ])("ends the group when %s", (_case, nextNodes) => {
    render(<BoxSelectionReset />)
    boxSelect(["a", "b"])

    setNodes(nextNodes)

    expect(groupActive()).toBe(false)
  })

  it("tracks the next box selection afresh", () => {
    render(<BoxSelectionReset />)
    boxSelect(["a", "b"])
    setNodes([node("a", 0), node("b", 300), node("c", 600, true)])
    expect(groupActive()).toBe(false)

    boxSelect(["b", "c"])
    setNodes([node("a", 0), node("b", 320, true), node("c", 620, true)])

    expect(groupActive()).toBe(true)
  })
})
