/**
 * The comparison view's editors run nothing (specs/frontend-shared/low-level.md,
 * "Request gateway"): `ReadOnlyNodeConfig` gives them no request scope, so an
 * editor that asks the server on mount for an estimate sends no request at all,
 * while the open pipeline is loaded beside it. The same editor, rendered the
 * same way under the open pipeline's scope, does ask.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen, waitFor } from "@testing-library/react"

import ReadOnlyNodeConfig from "../ReadOnlyNodeConfig"
import { GraphProvider } from "../../panels/GraphContext"
import { LazyEditorBoundary } from "../../panels/LazyNodeEditors"
import { NodeConfigEditor } from "../../panels/NodeConfigEditor"
import useGraphStore from "../../stores/useGraphStore"
import useToastStore from "../../stores/useToastStore"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { clearTrainedJobHandle, readTrainedJobHandle, writeTrainedJobHandle } from "../../utils/trainedJobHandles"
import { makeNode } from "../../test-utils/factories"
import { NODE_TYPES, type NodeTypeValue } from "../../utils/nodeTypes"
import { documentRequestScope } from "../../utils/requestGateway"

/** Every request the page sends, by URL. */
const sent: string[] = []

const CASES: Array<{ type: NodeTypeValue; config: Record<string, unknown>; estimate: string }> = [
  {
    type: NODE_TYPES.MODELLING,
    config: { target: "loss_ratio", task: "regression", algorithm: "catboost", loss_function: "RMSE", feature_columns: ["age"] },
    estimate: "/api/modelling/estimate",
  },
  {
    type: NODE_TYPES.OPTIMISER,
    config: { mode: "online", objective: "premium", constraints: { loss_ratio: { max: 1 } } },
    estimate: "/api/optimiser/estimate",
  },
]

beforeEach(() => {
  sent.length = 0
  useGraphStore.getState().resetForTests()
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    sent.push(String(url))
    return new Response(JSON.stringify({}), { status: 500 })
  }))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

/** The open pipeline, holding the node the saved version shows. */
function loadDocument(type: string, config: Record<string, unknown>) {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [makeNode("source", "dataInput"), makeNode("n1", type, { data: { config } })],
    edges: [{ id: "e1", source: "source", target: "n1" }],
    preamble: "",
    submodels: {},
  })
}

/** The editor as `ReadOnlyNodeConfig` renders it, under the open pipeline's scope instead of none. */
function renderUnderOpenPipeline(type: NodeTypeValue, config: Record<string, unknown>) {
  const ok = () => ({ ok: true as const })
  render(
    <GraphProvider allNodes={[]} edges={[]} submodels={{}} preamble="" requestScope={documentRequestScope}>
      <LazyEditorBoundary>
        <NodeConfigEditor
          readOnly
          nodeType={type}
          config={config}
          node={{ id: "n1", data: { label: "", description: "", nodeType: type, config } }}
          onUpdateConfig={ok}
          onReplaceConfig={ok}
          onDeleteEdge={ok}
          inputSources={[]}
          upstreamColumns={[]}
          pivotColumns={[]}
          activeExplorePane="overview"
          activeModellingPane="target"
          activeOptimiserPane="data"
          ratingSection="all"
          onShowPivots={() => {}}
          loadPivotFilterMembers={() => Promise.reject(new Error("No filter members here."))}
          exploreConfigHash={null}
          reservedApiInputFrameLabels={new Set()}
          inputBindingViolations={[]}
          accentColor="#000"
        />
      </LazyEditorBoundary>
    </GraphProvider>,
  )
}

/** The lazy editor resolved, then long enough for its mount-time request to have gone out. */
async function settle() {
  await waitFor(
    () => expect(screen.queryByTestId("editor-loading")).not.toBeInTheDocument(),
    { timeout: 10000 },
  )
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 1000))
  })
}

describe("ReadOnlyNodeConfig requests", () => {
  for (const { type, config, estimate } of CASES) {
    it(`lets the ${type} editor ask for its estimate under the open pipeline's scope`, async () => {
      loadDocument(type, config)
      renderUnderOpenPipeline(type, config)
      await settle()

      expect(sent).toContain(estimate)
    })

    it(`sends no request from the ${type} editor of a saved version, and reports no failure`, async () => {
      useToastStore.setState({ toasts: [] })
      loadDocument(type, config)
      render(<ReadOnlyNodeConfig nodeType={type} config={config} nodeId="n1" />)
      await settle()

      // The editor itself, not the config dump a type without a read-only editor gets.
      expect(screen.queryByTestId("readonly-config-fallback")).toBeNull()
      expect(sent).toEqual([])
      // A saved version runs nothing, so nothing failed to run.
      expect(useToastStore.getState().toasts).toEqual([])
    })
  }

  it("leaves the open pipeline's remembered training to it: a saved version's Modelling editor asks nothing and keeps the handle", async () => {
    const { type, config } = CASES[0]
    const handle = { jobId: "job-live", configHash: "hash", source: "live", lineage: "lineage" }
    useDocumentStatusStore.setState({ sourceFile: "rating/main.py" })
    writeTrainedJobHandle("rating/main.py", "n1", handle)
    try {
      loadDocument(type, config)
      render(<ReadOnlyNodeConfig nodeType={type} config={config} nodeId="n1" />)
      await settle()

      expect(sent).toEqual([])
      expect(readTrainedJobHandle("rating/main.py", "n1")).toEqual(handle)
    } finally {
      clearTrainedJobHandle("rating/main.py", "n1")
      useDocumentStatusStore.getState().reset()
    }
  })
})
