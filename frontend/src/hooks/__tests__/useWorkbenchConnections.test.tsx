/**
 * Holding a Workbench Input's and a Workbench Output's connections to the tables the
 * workbench store fetched (specs/workbench): which fetches they are held to, when, in which
 * graph, outside the undo history, and what never removes one.
 */
import { useLayoutEffect } from "react"
import { act, cleanup, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import useWorkbenchConnections from "../useWorkbenchConnections"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { makeNode } from "../../test-utils/factories"

const port = (name: string) => ({ name, rows: "one" as const, columns: [{ name: "c", type: "int" as const }] })
const edge = (id: string, source: string, sourceHandle: string | null, target: string, targetHandle: string | null = null): Edge =>
  ({ id, source, sourceHandle, target, targetHandle })

const input = makeNode("wb_in", "workbenchInput", { data: { label: "wb_in", nodeType: "workbenchInput", config: {} } })
const priced = makeNode("priced")
const output = makeNode("wb_out", "workbenchOutput", { data: { label: "wb_out", nodeType: "workbenchOutput", config: {} } })
const policy = edge("e_policy", "wb_in", "policy", "priced")
const account = edge("e_account", "wb_in", "account", "priced")
const filled = edge("e_filled", "priced", null, "wb_out", "pricing_output")

describe("useWorkbenchConnections", () => {
  let fetches: number
  const graphRef: { current: { nodes: Node[]; edges: Edge[] } } = { current: { nodes: [], edges: [] } }
  const addToast = vi.fn()
  const commitEdges = (edges: Edge[]) => useGraphStore.getState().setEdgesRaw(edges)
  const edgeIds = () => useGraphStore.getState().edges.map((each) => each.id)

  /** Publishes a fetch's tables, as the store does when one settles. */
  const publish = (fetch: number, inputs: string[], outputs: string[] = ["pricing_output"]) =>
    act(() =>
      useWorkbenchStore.setState({
        tables: { tables: inputs.map(port), sample: {}, responseTables: outputs.map(port), fetch },
      }))
  const adopt = (generation: number) => act(() => useDocumentStatusStore.setState({ executionGeneration: generation }))
  const show = (nodes: Node[], edges: Edge[]) =>
    act(() => useGraphStore.getState().loadGraphSnapshot({ nodes, edges, preamble: "", submodels: {} }))
  /** The hook as the editor mounts it, its graph ref following the graph the store shows. */
  const render = (editable = true) =>
    renderHook(({ editable: canEdit }) => {
      const nodes = useGraphStore((state) => state.nodes)
      const edges = useGraphStore((state) => state.edges)
      useLayoutEffect(() => {
        graphRef.current = { nodes, edges }
      }, [nodes, edges])
      return useWorkbenchConnections({ graphRef, commitEdges, editable: canEdit, addToast })
    }, { initialProps: { editable } })

  beforeEach(() => {
    fetches = 0
    addToast.mockReset()
    useGraphStore.getState().resetForTests()
    useDocumentStatusStore.setState({ executionGeneration: 1 })
    useWorkbenchStore.setState({ enabled: true, tables: null, refreshTables: vi.fn(() => ++fetches) })
    show([input, priced, output], [policy, account, filled])
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("fetches when the workbench is enabled and on each document adoption", () => {
    useWorkbenchStore.setState({ enabled: false })
    render()
    expect(useWorkbenchStore.getState().refreshTables).not.toHaveBeenCalled()

    act(() => useWorkbenchStore.setState({ enabled: true }))
    expect(fetches).toBe(1)
    adopt(2)
    expect(fetches).toBe(2)
  })

  it("removes a connection to a table the newest fetch no longer has, outside the undo history, and says so as a request input's edit does", () => {
    render()
    publish(1, ["policy", "account"])
    expect(edgeIds()).toEqual(["e_policy", "e_account", "e_filled"])
    // An edit, so the history has a step to keep as it was.
    act(() => useGraphStore.getState().setNodes((nodes) => [...nodes, makeNode("later")]))
    const { undoStack, redoStack } = useGraphStore.getState()

    publish(2, ["account"], ["premiums"])

    expect(edgeIds()).toEqual(["e_account"])
    expect(useGraphStore.getState().undoStack).toBe(undoStack)
    expect(useGraphStore.getState().redoStack).toBe(redoStack)
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(addToast.mock.calls).toEqual([
      ["warning", "Disconnected 1 edge from wb_in: the workbench no longer has the table it read."],
      ["warning", "Disconnected 1 edge from wb_out: the workbench no longer has the table it filled."],
    ])
  })

  it("removes again a connection an undo brings back, and keeps one whose table remains wherever it moved", () => {
    render()
    publish(1, ["policy", "account"])
    act(() => useGraphStore.getState().setNodes((nodes) => [...nodes, makeNode("later")]))
    publish(2, ["account", "policy_details"])
    expect(edgeIds()).toEqual(["e_account", "e_filled"])
    addToast.mockReset()

    // The step undone is from before the table went: it holds the connection to it.
    act(() => useGraphStore.getState().undo())

    expect(edgeIds()).toEqual(["e_account", "e_filled"])
    expect(addToast).toHaveBeenCalledWith(
      "warning",
      "Disconnected 1 edge from wb_in: the workbench no longer has the table it read.",
    )
    expect(useGraphStore.getState().nodes.map((node) => node.id)).not.toContain("later")
  })

  it("removes nothing before the first fetch lands", () => {
    render()
    show([input, priced, output], [edge("e_any", "wb_in", "anything", "priced")])

    expect(edgeIds()).toEqual(["e_any"])
    expect(addToast).not.toHaveBeenCalled()
  })

  it("holds a document only to a fetch made since it was adopted, removing nothing while that fetch fails", () => {
    render()
    publish(1, ["policy", "account"])
    // Another branch's document, whose form has a table the earlier one's had not; its
    // own fetch has not landed, or failed, so the tables are still the earlier form's.
    adopt(2)
    show([input, priced], [edge("e_extra", "wb_in", "extra", "priced")])
    expect(edgeIds()).toEqual(["e_extra"])
    act(() => useGraphStore.getState().setNodes((nodes) => [...nodes, makeNode("later")]))
    expect(edgeIds()).toEqual(["e_extra"])

    publish(fetches, ["extra"])
    expect(edgeIds()).toEqual(["e_extra"])
    expect(addToast).not.toHaveBeenCalled()
  })

  it("waits while the document cannot change", () => {
    const hook = render(false)
    publish(1, ["account"], [])
    expect(edgeIds()).toEqual(["e_policy", "e_account", "e_filled"])

    hook.rerender({ editable: true })
    expect(edgeIds()).toEqual(["e_account"])
  })

  it("holds the connections at once for a save, before any render, and cannot while the document's own fetch has not landed", () => {
    const hook = render()
    publish(1, ["policy", "account"])

    // The save's own fetch lands; no render has run since.
    act(() => {
      const fetch = useWorkbenchStore.getState().refreshTables()!
      useWorkbenchStore.setState({
        tables: { tables: [port("account")], sample: {}, responseTables: [port("pricing_output")], fetch },
      })
      expect(hook.result.current.holdConnections()).toBe(true)
      expect(graphRef.current.edges.map((each) => each.id)).toEqual(["e_account", "e_filled"])
      expect(edgeIds()).toEqual(["e_account", "e_filled"])
    })

    adopt(2)
    expect(hook.result.current.holdConnections()).toBe(false)
  })

  it("holds the connections of a Workbench Input inside the submodel the editor shows, leaving one through its boundary to the definition", () => {
    const outputCard = makeNode("boundary_out", "submodelPort", {
      data: { label: "OUTPUT", nodeType: "submodelPort", portDirection: "output", ports: [] },
    })
    const throughBoundary: Edge = {
      ...edge("e_boundary", "wb_in", "policy", "boundary_out"),
      data: { submodelBoundary: { direction: "output", parentConsumerEdges: [], name: "policy" } },
    }
    show([input, priced, outputCard], [policy, account, throughBoundary])
    render()

    publish(1, ["account"])

    expect(edgeIds()).toEqual(["e_account", "e_boundary"])
  })

  it("never changes a Quote Input's connections", () => {
    const quote = makeNode("quote", "apiInput", { data: { label: "quote", nodeType: "apiInput", config: {} } })
    show([quote, priced], [edge("e_quote", "quote", "policy", "priced")])
    render()
    publish(1, [], [])

    expect(edgeIds()).toEqual(["e_quote"])
  })
})
