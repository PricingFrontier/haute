/**
 * Keeping a Workbench Input's and a Workbench Output's copies of the workbench's tables up
 * to date (specs/workbench): which fetches apply, to which document and node, when, and
 * what never runs it.
 */
import { act, cleanup, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest"

import useWorkbenchTables from "../useWorkbenchTables"
import useGraphCommitController, { type GraphCommitController } from "../useGraphCommitController"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

const tables = (label: string) => [{ path: "$[:]", label, emit: true, row_id_column: null, columns: [] }]

function requestInput(nodeType: "workbenchInput" | "apiInput", config: Record<string, unknown>, id: string): Node {
  return { id, type: nodeType, position: { x: 0, y: 0 }, data: { label: id, nodeType, config } }
}
const workbenchInput = (config: Record<string, unknown>, id = "workbench") => requestInput("workbenchInput", config, id)
const quoteInput = (config: Record<string, unknown>, id = "quote") => requestInput("apiInput", config, id)

describe("useWorkbenchTables", () => {
  let fetches: number
  let onUpdateNode: Mock<GraphCommitController["onUpdateNode"]>
  /** The config each update sent, by node. */
  const updates = () =>
    onUpdateNode.mock.calls.map(([id, data]) => [id, (data.config as Record<string, unknown>).tables])
  let graphRef: { current: { nodes: Node[]; edges: Edge[] } }

  /** Publishes tables, and a sample, as the store does when a fetch settles. */
  const publish = (fetch: number, label: string, sample: Record<string, unknown> = {}) =>
    act(() =>
      useWorkbenchStore.setState({
        tables: { tables: tables(label), sample, responseTables: [], fetch },
      }))
  const adopt = (generation: number) => act(() => useDocumentStatusStore.setState({ executionGeneration: generation }))
  const render = (editable = true) =>
    renderHook(({ editable: canEdit }) => useWorkbenchTables({ graphRef, onUpdateNode, editable: canEdit }), {
      initialProps: { editable },
    })

  beforeEach(() => {
    fetches = 0
    onUpdateNode = vi.fn<GraphCommitController["onUpdateNode"]>(() => ({ ok: true }))
    graphRef = { current: { nodes: [workbenchInput({ tables: [] })], edges: [] } }
    useDocumentStatusStore.setState({ executionGeneration: 1 })
    useWorkbenchStore.setState({
      enabled: true,
      tables: null,
      refreshTables: vi.fn(() => ++fetches),
    })
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

  it("updates the Workbench Input once per fetch, for the document it was fetched for", () => {
    render()
    publish(1, "policy_details")

    expect(onUpdateNode).toHaveBeenCalledOnce()
    const [nodeId, data, options] = onUpdateNode.mock.calls[0]
    const isCurrent = options?.isCurrent
    expect(nodeId).toBe("workbench")
    expect(data.config).toEqual({ tables: tables("policy_details"), sample: {} })
    expect(isCurrent?.()).toBe(true)

    act(() => useWorkbenchStore.setState({ enabled: true }))
    expect(onUpdateNode).toHaveBeenCalledOnce()

    adopt(2)
    expect(isCurrent?.()).toBe(false)
  })

  it("brings the sample quote with the tables, and a changed sample alone", () => {
    const sample = { policy_details: { state: "NY" } }
    render()
    publish(1, "policy_details", sample)
    expect((onUpdateNode.mock.calls[0][1].config as Record<string, unknown>).sample).toEqual(sample)

    graphRef.current.nodes = [workbenchInput({ tables: tables("policy_details"), sample })]
    publish(2, "policy_details", { policy_details: { state: "CA" } })

    expect(onUpdateNode).toHaveBeenCalledTimes(2)
    expect(onUpdateNode.mock.calls[1][1].config).toEqual({
      tables: tables("policy_details"),
      sample: { policy_details: { state: "CA" } },
    })
  })

  it("never applies tables fetched before the current document was adopted", () => {
    render()
    adopt(2)
    publish(1, "stale")
    expect(onUpdateNode).not.toHaveBeenCalled()

    publish(2, "fresh")
    expect(updates()).toEqual([["workbench", tables("fresh")]])
  })

  it("never changes a Quote Input, whatever its tables", () => {
    graphRef.current.nodes = [quoteInput({ path: "", tables: [] }), quoteInput({ path: "" }, "bare")]
    render()
    publish(1, "policy_details")

    expect(onUpdateNode).not.toHaveBeenCalled()
  })

  it("changes nothing that already matches, and waits while the document can't change", () => {
    graphRef.current.nodes = [workbenchInput({ tables: tables("policy_details") }, "matching")]
    const hook = render(false)
    publish(1, "policy_details")
    expect(onUpdateNode).not.toHaveBeenCalled()

    graphRef.current.nodes = [...graphRef.current.nodes, workbenchInput({ tables: [] }, "stale")]
    hook.rerender({ editable: true })
    expect(onUpdateNode.mock.calls.map(([id]) => id)).toEqual(["stale"])
  })

  it("leaves an undone update undone: a graph change does not run it again", () => {
    const hook = render()
    publish(1, "policy_details")
    expect(onUpdateNode).toHaveBeenCalledOnce()

    graphRef.current = { nodes: [workbenchInput({ tables: [] })], edges: [] }
    hook.rerender({ editable: true })
    expect(onUpdateNode).toHaveBeenCalledOnce()
  })

  it("tries a refresh again once the top level shows, when a submodel opened before it committed", async () => {
    // The real commit controller: a request input's update returns at once and commits
    // only after its identity resolves, by which time the canvas may show a submodel.
    const node = workbenchInput({ tables: [] })
    const resolutions: { nodes: readonly Node[]; resolve: (nodes: Node[]) => void }[] = []
    const resolveNodeIdentities = vi.fn((nodes: readonly Node[]) =>
      new Promise<Node[]>((resolve) => { resolutions.push({ nodes, resolve }) }))
    const commitGraph = vi.fn()
    graphRef.current = { nodes: [node], edges: [] }
    const hook = renderHook(({ editable }) => {
      const controller = useGraphCommitController({
        graphRef,
        submodelsRef: { current: {} },
        readDocumentIdentity: () => "document",
        readOnly: false,
        reservedApiInputFrameLabels: new Set<string>(),
        resolveNodeIdentities,
        resolveRenameIdentities: resolveNodeIdentities,
        readNamingContextKey: () => "naming context",
        commitGraph,
        setSelectedNode: vi.fn(),
        addToast: vi.fn(),
      })
      useWorkbenchTables({ graphRef, onUpdateNode: controller.onUpdateNode, editable })
      return controller
    }, { initialProps: { editable: true } })

    publish(1, "policy_details")
    expect(resolutions).toHaveLength(1)
    // A submodel opens while the identity resolves: the update finds no node and fails.
    graphRef.current = { nodes: [], edges: [] }
    hook.rerender({ editable: false })
    await act(async () => {
      resolutions[0].resolve([...resolutions[0].nodes])
      await hook.result.current.waitForPendingCommits()
    })
    expect(commitGraph).not.toHaveBeenCalled()

    // Back at the top level, the same fetch's tables are sent again, and commit.
    graphRef.current = { nodes: [node], edges: [] }
    hook.rerender({ editable: true })
    expect(resolutions).toHaveLength(2)
    expect((resolutions[1].nodes[0].data.config as Record<string, unknown>).tables).toEqual(tables("policy_details"))
    await act(async () => {
      resolutions[1].resolve([...resolutions[1].nodes])
      await hook.result.current.waitForPendingCommits()
    })
    expect(commitGraph).toHaveBeenCalledOnce()
  })

  it("updates each Workbench Output with the response's tables in the same pass, never a Quote Response", () => {
    const output = (nodeType: "workbenchOutput" | "output", id: string, config: Record<string, unknown>): Node => ({
      id,
      type: nodeType,
      position: { x: 0, y: 0 },
      data: { label: id, nodeType, config },
    })
    graphRef.current.nodes = [
      workbenchInput({ tables: [] }),
      output("workbenchOutput", "response", { tables: [] }),
      output("output", "quote_response", { outputMapping: [], outputFormat: "json" }),
    ]
    render()
    act(() =>
      useWorkbenchStore.setState({
        tables: {
          tables: tables("policy_details"),
          sample: {},
          responseTables: tables("pricing_output"),
          fetch: 1,
        },
      }))

    expect(updates()).toEqual([
      ["workbench", tables("policy_details")],
      ["response", tables("pricing_output")],
    ])
    expect(onUpdateNode.mock.calls[1][1].config).toEqual({ tables: tables("pricing_output") })

    // A copy that matches is left alone.
    graphRef.current.nodes = [output("workbenchOutput", "response", { tables: tables("pricing_output") })]
    adopt(2)
    act(() =>
      useWorkbenchStore.setState({
        tables: {
          tables: [],
          sample: {},
          responseTables: tables("pricing_output"),
          fetch: 2,
        },
      }))
    expect(onUpdateNode).toHaveBeenCalledTimes(2)
  })

  it("gives a Workbench Input reported as created the tables fetched while it was being made", () => {
    graphRef.current.nodes = []
    const hook = render()
    publish(1, "policy_details")
    expect(onUpdateNode).not.toHaveBeenCalled()

    graphRef.current.nodes = [workbenchInput({ tables: [] }, "new")]
    act(() => hook.result.current.nodeCreated("new"))

    expect(updates()).toEqual([["new", tables("policy_details")]])
  })
})
