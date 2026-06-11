/**
 * tooltips-descriptions §5.2-H — NodePanel type-identity chip.
 *
 * Mandatory surface (Nick's ruling): once a node is on the canvas and its
 * label edited, the panel otherwise never says what TYPE is being edited.
 * For edgeJoin the chip is also the only non-canvas descriptive surface —
 * the type has no palette entry (it is created by the drop-a-connection-
 * on-an-edge gesture), so the chip and the join-node marker tooltip are
 * its only two surfaces.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"

import NodePanel from "../NodePanel"
import { GraphProvider } from "../GraphContext"
import type { SimpleNode } from "../editors"
import useUIStore from "../../stores/useUIStore"
import { NODE_TYPE_META, NODE_TYPES } from "../../utils/nodeTypes"

vi.mock("../LazyNodeEditors", () => ({
  LazyEditorBoundary: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  DataSourceEditor: () => <div data-testid="DataSourceEditor" />,
  TransformEditor: () => <div data-testid="TransformEditor" />,
  EdgeJoinEditor: () => <div data-testid="EdgeJoinEditor" />,
  ExploreCodeEditor: () => <div data-testid="ExploreCodeEditor" />,
  ExploreOverviewConfig: () => <div data-testid="ExploreOverviewConfig" />,
  ModelScoreEditor: () => <div data-testid="ModelScoreEditor" />,
  BandingEditor: () => <div data-testid="BandingEditor" />,
  RatingStepEditor: () => <div data-testid="RatingStepEditor" />,
  OutputEditor: () => <div data-testid="OutputEditor" />,
  ExternalFileEditor: () => <div data-testid="ExternalFileEditor" />,
  ApiInputEditor: () => <div data-testid="ApiInputEditor" />,
  LiveSwitchEditor: () => <div data-testid="LiveSwitchEditor" />,
  SinkEditor: () => <div data-testid="SinkEditor" />,
  ScenarioExpanderEditor: () => <div data-testid="ScenarioExpanderEditor" />,
  OptimiserApplyEditor: () => <div data-testid="OptimiserApplyEditor" />,
  ConstantEditor: () => <div data-testid="ConstantEditor" />,
  SubmodelEditor: () => <div data-testid="SubmodelEditor" />,
  ColumnsTab: () => <div data-testid="ColumnsTab" />,
  GroupedColumnsTab: () => <div data-testid="GroupedColumnsTab" />,
  ModellingConfig: () => <div data-testid="ModellingConfig" />,
  OptimiserConfig: () => <div data-testid="OptimiserConfig" />,
}))

const PANEL_TOOLTIP_DELAY_MS = 300 // Tooltip default — panel is a browsing surface

function makeNode(nodeType: string, label = "My Node"): SimpleNode {
  return {
    id: "node_1",
    data: { label, description: "", nodeType, config: {} },
  }
}

function renderPanel(node: SimpleNode) {
  return render(
    <GraphProvider allNodes={[]} edges={[]}>
      <NodePanel node={node} onClose={vi.fn()} onUpdateNode={vi.fn()} />
    </GraphProvider>,
  )
}

describe("NodePanel type-identity chip", () => {
  beforeEach(() => {
    Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
    useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true, explorePanes: {}, explorePreviewPanes: {} })
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("renders the chip for known types with the type's accent tint", () => {
    renderPanel(makeNode(NODE_TYPES.POLARS))
    const chip = screen.getByTestId("node-panel-type-chip")
    expect(chip).toHaveAttribute("aria-label", `Node type: ${NODE_TYPE_META.polars.name}`)
    // Same chip idiom as the palette: 18-alpha accent tint square.
    expect(chip.style.background.length).toBeGreaterThan(0)
    expect(chip).toHaveAttribute("tabindex", "0")
  })

  it("hovering the chip surfaces the exact NODE_TYPE_META description", () => {
    renderPanel(makeNode(NODE_TYPES.POLARS))
    fireEvent.mouseEnter(screen.getByTestId("node-panel-type-chip"))
    act(() => {
      vi.advanceTimersByTime(PANEL_TOOLTIP_DELAY_MS)
    })
    const tooltip = screen.getByTestId("node-type-tooltip")
    expect(tooltip.querySelector('[data-node-type="polars"]')).not.toBeNull()
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.polars.description,
    )
  })

  it("renders for edgeJoin — its only non-canvas descriptive surface", () => {
    renderPanel(makeNode(NODE_TYPES.EDGE_JOIN, "Edge Join"))
    fireEvent.mouseEnter(screen.getByTestId("node-panel-type-chip"))
    act(() => {
      vi.advanceTimersByTime(PANEL_TOOLTIP_DELAY_MS)
    })
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.edgeJoin.description,
    )
  })

  it("opens the tooltip on keyboard focus without delay", () => {
    renderPanel(makeNode(NODE_TYPES.DATA_SOURCE))
    fireEvent.focus(screen.getByTestId("node-panel-type-chip"))
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
  })

  it("does NOT render a chip for unknown node types (isKnownNodeType guard)", () => {
    // Named absence — an unknown type has no NODE_TYPE_META entry to
    // describe; a chip with a broken icon/colour would be worse than none.
    renderPanel(makeNode("mystery"))
    expect(screen.queryByTestId("node-panel-type-chip")).not.toBeInTheDocument()
  })

  it("keeps the existing header controls intact around the chip", () => {
    renderPanel(makeNode(NODE_TYPES.POLARS))
    expect(screen.getByTestId("node-panel-label-input")).toBeInTheDocument()
    expect(screen.getByTestId("node-panel-close")).toBeInTheDocument()
  })
})
