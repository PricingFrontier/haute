import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { renderHook, cleanup, act, waitFor } from "@testing-library/react"
import type { Node, Edge } from "@xyflow/react"
import usePipelineAPI from "../usePipelineAPI"
import useToastStore from "../../stores/useToastStore"

vi.mock("../../api/client", () => ({
  loadPipeline: vi.fn(() => Promise.resolve({ nodes: [], edges: [], preamble: "" })),
  previewNode: vi.fn(),
  savePipeline: vi.fn(),
  runPipeline: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number
    detail?: string
    constructor(message: string, status = 0, detail?: string) {
      super(message)
      this.status = status
      this.detail = detail
    }
  },
  ApiTimeoutError: class ApiTimeoutError extends Error {},
}))

vi.mock("../../utils/buildGraph", () => ({
  resolveGraphFromRefs: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

import { runPipeline as clientRunPipeline } from "../../api/client"
const mockRun = vi.mocked(clientRunPipeline)

function node(id: string, selected: boolean): Node {
  return { id, position: { x: 0, y: 0 }, data: {}, selected } as Node
}

function makeParams(nodes: Node[]) {
  return {
    selectedNode: null as Node | null,
    graphRef: { current: { nodes, edges: [] as Edge[] } },
    parentGraphRef: { current: null },
    submodelsRef: { current: {} },
    setNodesRaw: vi.fn(),
    setEdgesRaw: vi.fn(),
    setPreamble: vi.fn(),
    preambleRef: { current: "" },
    pipelineNameRef: { current: "test" },
    descriptionRef: { current: "" },
    sourceFileRef: { current: "test.py" },
    nodeIdCounter: { current: 0 },
  }
}

const toastTexts = () => useToastStore.getState().toasts.map((t) => t.text)

describe("usePipelineAPI.runPipeline", () => {
  beforeEach(() => {
    mockRun.mockReset()
    useToastStore.setState({ toasts: [] })
  })
  afterEach(cleanup)

  it("posts the mode + canvas multi-selection and marks the written sink", async () => {
    mockRun.mockResolvedValue({
      mode: "default",
      ran_node_ids: ["a", "sink"],
      node_statuses: { a: "ok", sink: "ok" },
      exported: [
        { node_id: "sink", label: "Sink", status: "ok", row_count: 5, path: "out.parquet", format: "parquet" },
      ],
    } as never)
    const params = makeParams([node("a", true), node("sink", false)])
    const { result } = renderHook(() => usePipelineAPI(params as never))

    act(() => {
      result.current.runPipeline("default")
    })

    await waitFor(() => expect(mockRun).toHaveBeenCalledOnce())
    // Sends the canvas selection (node.selected), not the open-panel node.
    expect(mockRun.mock.calls[0][0]).toMatchObject({ mode: "default", selectedNodeIds: ["a"] })

    await waitFor(() => expect(params.setNodesRaw).toHaveBeenCalled())
    const updater = params.setNodesRaw.mock.calls.at(-1)![0] as (n: Node[]) => Node[]
    const next = updater([node("sink", false)])
    expect((next[0].data as Record<string, unknown>)._exportState).toBe("done")

    await waitFor(() => expect(toastTexts().some((t) => /wrote 1 sink/.test(t))).toBe(true))
  })

  it("toasts an error and writes nothing when the run reports an error", async () => {
    mockRun.mockResolvedValue({ mode: "all-no-export", error: "boom" } as never)
    const params = makeParams([node("a", true)])
    const { result } = renderHook(() => usePipelineAPI(params as never))

    act(() => {
      result.current.runPipeline("all-no-export")
    })

    await waitFor(() => expect(toastTexts().some((t) => /Run failed: boom/.test(t))).toBe(true))
    // The mount-load calls setNodesRaw with an array; runPipeline applies its
    // result via a function updater — none of those on the error path.
    expect(params.setNodesRaw.mock.calls.some((c) => typeof c[0] === "function")).toBe(false)
  })
})
