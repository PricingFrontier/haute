import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import PipelineNode from "../PipelineNode"
import type { PipelineFlowNode, PipelineNodeData } from "../../types/node"
import { NODE_TYPES, nodeTypeLabels } from "../../utils/nodeTypes"
import useSettingsStore from "../../stores/useSettingsStore"
import { STATUS_COLORS } from "../../theme/colors"

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Render PipelineNode inside a ReactFlowProvider (required for Handles). */
function renderNode(
  data: Partial<PipelineNodeData> & { label: string; nodeType: string },
  selected = false,
) {
  const fullData: PipelineNodeData = {
    description: "",
    ...data,
  }
  // NodeProps expects `id`, `data`, `type`, plus some internals.
  // We cast to `any` to satisfy the memo wrapper while testing render output.
  const props = {
    id: "test-node",
    type: "custom",
    data: fullData,
    selected,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    zIndex: 0,
    dragging: false,
    deletable: true,
    selectable: true,
    parentId: undefined,
    sourcePosition: undefined,
    targetPosition: undefined,
    dragHandle: undefined,
  }
  return render(
    <ReactFlowProvider>
      <PipelineNode {...(props as unknown as NodeProps<PipelineFlowNode>)} />
    </ReactFlowProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("PipelineNode", () => {
  afterEach(cleanup)

  // ── Render per node type ───────────────────────────────────────────

  it("renders a transform node with label and type badge", () => {
    renderNode({ label: "Clean Data", nodeType: NODE_TYPES.POLARS })
    expect(screen.getByText("Clean Data")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.POLARS])).toBeInTheDocument()
  })

  it("renders a dataSource node", () => {
    renderNode({ label: "Load CSV", nodeType: NODE_TYPES.DATA_SOURCE })
    expect(screen.getByText("Load CSV")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.DATA_SOURCE])).toBeInTheDocument()
  })

  it("renders an apiInput node with API badge", () => {
    renderNode({ label: "Quote Input", nodeType: NODE_TYPES.API_INPUT, config: { row_id_column: "id" } })
    expect(screen.getByText("Quote Input")).toBeInTheDocument()
    expect(screen.getByText("API")).toBeInTheDocument()
  })

  it("renders an output node", () => {
    renderNode({ label: "Final Output", nodeType: NODE_TYPES.OUTPUT })
    expect(screen.getByText("Final Output")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.OUTPUT])).toBeInTheDocument()
  })

  it("renders a dataSink node", () => {
    renderNode({ label: "Write Parquet", nodeType: NODE_TYPES.DATA_SINK })
    expect(screen.getByText("Write Parquet")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.DATA_SINK])).toBeInTheDocument()
  })

  it("renders a modelScore node", () => {
    renderNode({ label: "Score Model", nodeType: NODE_TYPES.MODEL_SCORE })
    expect(screen.getByText("Score Model")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.MODEL_SCORE])).toBeInTheDocument()
  })

  it("renders a modelling node", () => {
    renderNode({ label: "Train XGBoost", nodeType: NODE_TYPES.MODELLING })
    expect(screen.getByText("Train XGBoost")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.MODELLING])).toBeInTheDocument()
  })

  it("renders an optimiser node", () => {
    renderNode({ label: "Optimise Portfolio", nodeType: NODE_TYPES.OPTIMISER })
    expect(screen.getByText("Optimise Portfolio")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.OPTIMISER])).toBeInTheDocument()
  })

  it("renders a banding node", () => {
    renderNode({ label: "Age Bands", nodeType: NODE_TYPES.BANDING })
    expect(screen.getByText("Age Bands")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.BANDING])).toBeInTheDocument()
  })

  // ── Handles (source/target) ────────────────────────────────────────

  it("source-only types do NOT render a target handle", () => {
    const { container } = renderNode({ label: "Source", nodeType: NODE_TYPES.DATA_SOURCE })
    // ReactFlow renders handles as div with class containing "target" or "source"
    const targetHandle = container.querySelector(".react-flow__handle-left")
    expect(targetHandle).toBeNull()
    // Should have a source handle on the right
    const sourceHandle = container.querySelector(".react-flow__handle-right")
    expect(sourceHandle).not.toBeNull()
  })

  it("sink-only types do NOT render a source handle", () => {
    const { container } = renderNode({ label: "Sink", nodeType: NODE_TYPES.OUTPUT })
    const sourceHandle = container.querySelector(".react-flow__handle-right")
    expect(sourceHandle).toBeNull()
    // Should have a target handle on the left
    const targetHandle = container.querySelector(".react-flow__handle-left")
    expect(targetHandle).not.toBeNull()
  })

  it("transform nodes render both source and target handles", () => {
    const { container } = renderNode({ label: "Transform", nodeType: NODE_TYPES.POLARS })
    expect(container.querySelector(".react-flow__handle-left")).not.toBeNull()
    expect(container.querySelector(".react-flow__handle-right")).not.toBeNull()
  })

  // ── Selection state ────────────────────────────────────────────────

  it("applies accent border when selected", () => {
    const { container } = renderNode(
      { label: "Selected", nodeType: NODE_TYPES.POLARS },
      true,
    )
    // The outer rendered div is the node root with inline style
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    // Selected border is 3px solid accent
    expect(rawStyle).toContain("3px solid")
    expect(rawStyle).not.toContain("var(--border-bright)")
  })

  it("applies default border when not selected", () => {
    const { container } = renderNode(
      { label: "Not Selected", nodeType: NODE_TYPES.POLARS },
      false,
    )
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    // Default border uses 3px accent-tinted color, not plain var(--border-bright)
    expect(rawStyle).toContain("3px solid")
  })

  // ── Node label ─────────────────────────────────────────────────────

  it("displays the node label text", () => {
    renderNode({ label: "My Custom Label", nodeType: NODE_TYPES.POLARS })
    expect(screen.getByText("My Custom Label")).toBeInTheDocument()
  })

  // ── Error / status state ───────────────────────────────────────────

  it("shows a status indicator for ok status", () => {
    const { container } = renderNode({
      label: "OK Node",
      nodeType: NODE_TYPES.POLARS,
      _status: "ok",
    })
    const allSpans = Array.from(container.querySelectorAll("span"))
    const greenDot = allSpans.find((s) => {
      const style = s.getAttribute("style") || ""
      return style.includes("var(--success)") || style.includes("rgb(34, 197, 94)")
    })
    expect(greenDot).toBeTruthy()
  })

  it("shows a status indicator for error status", () => {
    const { container } = renderNode({
      label: "Error Node",
      nodeType: NODE_TYPES.POLARS,
      _status: "error",
    })
    const allSpans = Array.from(container.querySelectorAll("span"))
    const redDot = allSpans.find((s) => {
      const style = s.getAttribute("style") || ""
      return style.includes("var(--danger)") || style.includes("rgb(239, 68, 68)")
    })
    expect(redDot).toBeTruthy()
  })

  it("shows a pulsing dot for running status", () => {
    const { container } = renderNode({
      label: "Running Node",
      nodeType: NODE_TYPES.POLARS,
      _status: "running",
    })
    const dot = container.querySelector(".animate-pulse-dot") as HTMLElement
    expect(dot).not.toBeNull()
    const rawStyle = dot.getAttribute("style") || ""
    expect(rawStyle).toContain(STATUS_COLORS.running)
  })

  // ── Instance badge ─────────────────────────────────────────────────

  it("shows Instance badge when config.instanceOf is set", () => {
    renderNode({
      label: "Instance Node",
      nodeType: NODE_TYPES.POLARS,
      config: { instanceOf: "base_transform" },
    })
    expect(screen.getByText("Instance")).toBeInTheDocument()
  })

  it("uses dashed border for instance nodes", () => {
    const { container } = renderNode({
      label: "Instance",
      nodeType: NODE_TYPES.POLARS,
      config: { instanceOf: "base" },
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    expect(rawStyle).toContain("dashed")
  })

  // ── Source switch mode badge ────────────────────────────────────────

  it("shows LIVE badge when active source is live", () => {
    useSettingsStore.setState({ activeSource: "live" })
    renderNode({
      label: "Switch",
      nodeType: NODE_TYPES.LIVE_SWITCH,
    })
    expect(screen.getByText("LIVE")).toBeInTheDocument()
  })

  it("hides LIVE badge when active source is not live", () => {
    useSettingsStore.setState({ activeSource: "backtest" })
    renderNode({
      label: "Switch",
      nodeType: NODE_TYPES.LIVE_SWITCH,
    })
    expect(screen.queryByText("LIVE")).not.toBeInTheDocument()
  })

  // ── Trace state ────────────────────────────────────────────────────

  it("dims node when _traceDimmed is true", () => {
    const { container } = renderNode({
      label: "Dimmed",
      nodeType: NODE_TYPES.POLARS,
      _traceDimmed: true,
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    expect(rawStyle).toContain("opacity: 0.25")
  })

  it("dims node when _hoverDimmed is true", () => {
    const { container } = renderNode({
      label: "Hover Dimmed",
      nodeType: NODE_TYPES.POLARS,
      _hoverDimmed: true,
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    expect(rawStyle).toContain("opacity: 0.25")
  })

  it("disables trace motion transitions when requested by the tracing hook", () => {
    const { container } = renderNode({
      label: "Motion Lite",
      nodeType: NODE_TYPES.POLARS,
      _traceMotionDisabled: true,
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    expect(nodeEl.style.transition).toBe("none")
  })

  it("shows trace value when _traceActive and _traceValue are set", () => {
    renderNode({
      label: "Traced",
      nodeType: NODE_TYPES.POLARS,
      _traceActive: true,
      _traceValue: 42.5,
    })
    // formatValueCompact(42.5) -> "42.5"
    expect(screen.getByText("42.5")).toBeInTheDocument()
  })

  // ── Missing node type renders ─────────────────────────────────

  it("renders a ratingStep node", () => {
    renderNode({ label: "Premium Rating", nodeType: NODE_TYPES.RATING_STEP })
    expect(screen.getByText("Premium Rating")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.RATING_STEP])).toBeInTheDocument()
  })

  it("renders an externalFile node", () => {
    renderNode({ label: "Load Pickle", nodeType: NODE_TYPES.EXTERNAL_FILE })
    expect(screen.getByText("Load Pickle")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.EXTERNAL_FILE])).toBeInTheDocument()
  })

  it("renders a scenarioExpander node", () => {
    renderNode({ label: "Price Grid", nodeType: NODE_TYPES.SCENARIO_EXPANDER })
    expect(screen.getByText("Price Grid")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.SCENARIO_EXPANDER])).toBeInTheDocument()
  })

  it("renders an optimiserApply node", () => {
    renderNode({ label: "Apply Lambdas", nodeType: NODE_TYPES.OPTIMISER_APPLY })
    expect(screen.getByText("Apply Lambdas")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.OPTIMISER_APPLY])).toBeInTheDocument()
  })

  it("renders a constant node", () => {
    renderNode({ label: "Base Rate", nodeType: NODE_TYPES.CONSTANT })
    expect(screen.getByText("Base Rate")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.CONSTANT])).toBeInTheDocument()
  })

  it("renders a liveSwitch node", () => {
    useSettingsStore.setState({ activeSource: "backtest" })
    renderNode({ label: "Source Toggle", nodeType: NODE_TYPES.LIVE_SWITCH })
    expect(screen.getByText("Source Toggle")).toBeInTheDocument()
    expect(screen.getByText(nodeTypeLabels[NODE_TYPES.LIVE_SWITCH])).toBeInTheDocument()
  })

  // ── Trace active border ────────────────────────────────────────

  it("applies solid accent border when _traceActive is true", () => {
    const { container } = renderNode({
      label: "Trace Active",
      nodeType: NODE_TYPES.POLARS,
      _traceActive: true,
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    expect(rawStyle).toContain("3px solid")
    expect(rawStyle).not.toContain("dashed")
  })

  it("does not show trace value when _traceActive is false", () => {
    renderNode({
      label: "No Trace",
      nodeType: NODE_TYPES.POLARS,
      _traceActive: false,
      _traceValue: 99,
    })
    expect(screen.queryByText("99")).not.toBeInTheDocument()
  })

  it("does not show trace value when _traceValue is undefined", () => {
    renderNode({
      label: "No Value",
      nodeType: NODE_TYPES.POLARS,
      _traceActive: true,
    })
    const nodeEl = screen.getByRole("button")
    const monoDivs = nodeEl.querySelectorAll(".font-mono")
    expect(monoDivs.length).toBe(0)
  })

  // ── Warning indicator ──────────────────────────────────────────

  it("shows warning indicator when _schemaWarnings present", () => {
    renderNode({
      label: "Warned",
      nodeType: NODE_TYPES.POLARS,
      _schemaWarnings: [{ column: "age", status: "missing" }],
    })
    expect(screen.getByLabelText("Node has schema warnings")).toBeInTheDocument()
  })

  it("hides warning indicator when _schemaWarnings is empty", () => {
    renderNode({
      label: "No Warnings",
      nodeType: NODE_TYPES.POLARS,
      _schemaWarnings: [],
    })
    expect(screen.queryByLabelText("Node has schema warnings")).not.toBeInTheDocument()
  })

  it("hides warning indicator when status is error", () => {
    renderNode({
      label: "Error Overrides",
      nodeType: NODE_TYPES.POLARS,
      _status: "error",
      _schemaWarnings: [{ column: "x", status: "extra" }],
    })
    expect(screen.queryByLabelText("Node has schema warnings")).not.toBeInTheDocument()
  })

  // ── Status dots ────────────────────────────────────────────────

  it("running status has animate-pulse-dot class", () => {
    const { container } = renderNode({
      label: "Running",
      nodeType: NODE_TYPES.POLARS,
      _status: "running",
    })
    const dot = container.querySelector(".animate-pulse-dot")
    expect(dot).not.toBeNull()
  })

  it("ok status does not have animate-pulse-dot class", () => {
    const { container } = renderNode({
      label: "OK",
      nodeType: NODE_TYPES.POLARS,
      _status: "ok",
    })
    const dot = container.querySelector(".animate-pulse-dot")
    expect(dot).toBeNull()
  })

  // ── Opacity (no dimming) ───────────────────────────────────────

  it("has full opacity when neither _traceDimmed nor _hoverDimmed", () => {
    const { container } = renderNode({
      label: "Normal",
      nodeType: NODE_TYPES.POLARS,
    })
    const nodeEl = container.querySelector(".rounded-xl") as HTMLElement
    const rawStyle = nodeEl.getAttribute("style") || ""
    expect(rawStyle).toContain("opacity: 1")
  })

  // ── LIVE badge on API_INPUT ────────────────────────────────────

  it("shows API badge on API_INPUT node", () => {
    renderNode({ label: "Quote", nodeType: NODE_TYPES.API_INPUT })
    expect(screen.getByText("API")).toBeInTheDocument()
  })

  // ── Aria label includes trace active ───────────────────────────

  it("includes trace active in aria-label when active", () => {
    renderNode({
      label: "Traced Node",
      nodeType: NODE_TYPES.POLARS,
      _traceActive: true,
    })
    const nodeEl = screen.getByRole("button")
    expect(nodeEl.getAttribute("aria-label")).toContain("trace active")
  })

  it("includes instance in aria-label when instanceOf set", () => {
    renderNode({
      label: "Instance Node",
      nodeType: NODE_TYPES.POLARS,
      config: { instanceOf: "base" },
    })
    const nodeEl = screen.getByRole("button")
    expect(nodeEl.getAttribute("aria-label")).toContain("instance")
  })
})
