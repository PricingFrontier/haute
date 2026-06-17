/**
 * Regression (BUGS.md "Data [Quote] Source [Sink] rendering hard-to-remove
 * sidebar/banner"): when a node editor's lazy dynamic import REJECTS (a stale
 * build's chunk 404s, a network blip), React throws past the Suspense
 * boundary. The error boundary that catches it must be scoped to the editor
 * BODY so the panel header — and its close button — keep rendering. Catching
 * at the whole-panel level replaced the panel with a banner that sat over the
 * close button, leaving the user no way to dismiss the sidepane.
 *
 * Here ApiInputEditor throws on render (standing in for the dynamic-import
 * failure); we assert the panel chrome survives and Close still works.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePanel from "../NodePanel"
import { GraphProvider } from "../GraphContext"
import type { SimpleNode } from "../editors"
import useUIStore from "../../stores/useUIStore"

// LazyEditorBoundary passes children through; ApiInputEditor throws to
// simulate a failed dynamic import. Every other export is an inert stub so
// the module shape matches what NodePanel imports.
vi.mock("../LazyNodeEditors", () => {
  const stub = (name: string) => () => <div data-testid={name} />
  return {
    LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
    ApiInputEditor: () => {
      throw new Error(
        "error loading dynamically imported module: /assets/ApiInputEditor-DEADBEEF.js",
      )
    },
    DataSourceEditor: stub("DataSourceEditor"),
    TransformEditor: stub("TransformEditor"),
    EdgeJoinEditor: stub("EdgeJoinEditor"),
    ExploreCodeEditor: stub("ExploreCodeEditor"),
    ExploreOverviewConfig: stub("ExploreOverviewConfig"),
    ModelScoreEditor: stub("ModelScoreEditor"),
    BandingEditor: stub("BandingEditor"),
    RatingStepEditor: stub("RatingStepEditor"),
    OutputEditor: stub("OutputEditor"),
    ExternalFileEditor: stub("ExternalFileEditor"),
    LiveSwitchEditor: stub("LiveSwitchEditor"),
    SinkEditor: stub("SinkEditor"),
    ScenarioExpanderEditor: stub("ScenarioExpanderEditor"),
    OptimiserApplyEditor: stub("OptimiserApplyEditor"),
    ConstantEditor: stub("ConstantEditor"),
    SubmodelEditor: stub("SubmodelEditor"),
    ColumnsTab: stub("ColumnsTab"),
    GroupedColumnsTab: stub("GroupedColumnsTab"),
    ModellingConfig: stub("ModellingConfig"),
    OptimiserConfig: stub("OptimiserConfig"),
  }
})

function apiInputNode(): SimpleNode {
  return {
    id: "api_1",
    data: { label: "Quote In", description: "", nodeType: "apiInput", config: {} },
  }
}

function renderCrashedPanel() {
  const onClose = vi.fn()
  const result = render(
    <GraphProvider allNodes={[]} edges={[]}>
      <NodePanel node={apiInputNode()} onClose={onClose} onUpdateNode={vi.fn()} />
    </GraphProvider>,
  )
  return { ...result, onClose }
}

describe("NodePanel — editor load failure keeps the panel closable", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
    useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true, explorePanes: {}, explorePreviewPanes: {} })
    // ErrorBoundary.componentDidCatch logs the caught error; silence it.
    vi.spyOn(console, "error").mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("renders the failure banner inside the body, not over the header", () => {
    renderCrashedPanel()
    // The scoped boundary caught the throw and showed its fallback…
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    // …but the header survived: the type label input is still editable.
    expect(screen.getByDisplayValue("Quote In")).toBeInTheDocument()
  })

  it("keeps the close button rendered and working after an editor crash", () => {
    const { onClose } = renderCrashedPanel()
    const closeButton = screen.getByTestId("node-panel-close")
    expect(closeButton).toBeInTheDocument()
    fireEvent.click(closeButton)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
