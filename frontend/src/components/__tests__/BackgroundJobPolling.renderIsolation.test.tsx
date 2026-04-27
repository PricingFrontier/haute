import { act, cleanup, render } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import BackgroundJobPolling from "../BackgroundJobPolling"
import useGraphStore from "../../stores/useGraphStore"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useToastStore from "../../stores/useToastStore"
import { makeNode } from "../../test-utils/factories"

function resetStores() {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    structuralVersion: 0,
    structuralFingerprint: 'nodes:||edges:||preamble:""',
    panelContextVersion: 0,
    panelContextFingerprint: "nodes:||edges:",
    persistedFingerprint: 'nodes:[]|edges:[]|preamble:""',
    savedPersistedFingerprint: null,
    dirty: false,
  })
  useNodeResultsStore.setState({
    previews: {},
    columnCache: {},
    solveResults: {},
    solveJobs: {},
    trainResults: {},
    trainJobs: {},
  })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

describe("BackgroundJobPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    resetStores()
  })

  afterEach(() => {
    cleanup()
    resetStores()
    vi.useRealTimers()
  })

  it("isolates job progress store updates from the parent editor render path", () => {
    const EditorShell = vi.fn(function EditorShell() {
      const nodeCount = useGraphStore((s) => s.nodes.length)
      return (
        <>
          <span data-testid="node-count">{nodeCount}</span>
          <BackgroundJobPolling />
        </>
      )
    })

    const { getByTestId } = render(<EditorShell />)
    expect(EditorShell).toHaveBeenCalledTimes(1)
    expect(getByTestId("node-count")).toHaveTextContent("0")

    act(() => {
      useGraphStore.getState().setNodesRaw([makeNode("graph-node")])
    })
    expect(EditorShell).toHaveBeenCalledTimes(2)
    expect(getByTestId("node-count")).toHaveTextContent("1")

    act(() => {
      useNodeResultsStore.getState().startSolveJob("solve-node", "solve-job", "Solve Node", {}, "hash-a")
    })
    expect(EditorShell).toHaveBeenCalledTimes(2)

    act(() => {
      useNodeResultsStore.getState().updateSolveProgress("solve-node", {
        status: "running",
        progress: 0.5,
        message: "Solving",
        elapsed_seconds: 1,
      })
    })
    expect(EditorShell).toHaveBeenCalledTimes(2)

    act(() => {
      useNodeResultsStore.getState().startTrainJob("train-node", "train-job", "Train Node", "hash-b")
    })
    expect(EditorShell).toHaveBeenCalledTimes(2)

    act(() => {
      useNodeResultsStore.getState().updateTrainProgress("train-node", {
        status: "running",
        progress: 0.25,
        message: "Training",
        iteration: 2,
        total_iterations: 8,
        train_loss: { rmse: 0.4 },
        elapsed_seconds: 2,
      })
    })
    expect(EditorShell).toHaveBeenCalledTimes(2)
  })
})
