/**
 * tooltips-descriptions §5.2-E / E2 — canvas node-type tooltips.
 *
 * Nick's Q1 ruling: the trigger is the WHOLE NODE BODY (hover anywhere on
 * the node card) at every zoom level, with a long delay
 * (CANVAS_TOOLTIP_DELAY_MS) and hard suppression during any
 * drag/connection/selection gesture.  The suppression tests here are the
 * load-bearing consequences of that ruling — a tooltip that opens
 * mid-gesture or alters drag behaviour fights the canvas.
 *
 * E2: the edge-join node (zoom-independent marker branch) anchors the
 * tooltip on its root div — the join-node marker is the node's entire
 * on-canvas appearance — kept clear of its three connectors.
 */
import { useLayoutEffect } from "react"
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import { ReactFlowProvider, useStoreApi, type NodeProps, type ReactFlowState } from "@xyflow/react"

import PipelineNode, { CANVAS_TOOLTIP_DELAY_MS } from "../PipelineNode"
import type { PipelineFlowNode, PipelineNodeData } from "../../types/node"
import { NODE_TYPE_META, NODE_TYPES } from "../../utils/nodeTypes"

type StorePatch = Partial<ReactFlowState>

function StoreSeed({ patch }: { patch: StorePatch }) {
  const store = useStoreApi()
  useLayoutEffect(() => {
    store.setState(patch as never)
  }, [patch, store])
  return null
}

function renderNode(
  data: Partial<PipelineNodeData> & { label: string; nodeType: string },
  { dragging = false, storePatch }: { dragging?: boolean; storePatch?: StorePatch } = {},
) {
  const fullData: PipelineNodeData = { description: "", ...data }
  const props = {
    id: "test-node",
    type: "custom",
    data: fullData,
    selected: false,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    zIndex: 0,
    dragging,
    deletable: true,
    selectable: true,
    parentId: undefined,
    sourcePosition: undefined,
    targetPosition: undefined,
    dragHandle: undefined,
  }
  return render(
    <ReactFlowProvider>
      {storePatch && <StoreSeed patch={storePatch} />}
      <PipelineNode {...(props as unknown as NodeProps<PipelineFlowNode>)} />
    </ReactFlowProvider>,
  )
}

function hoverAndWait(el: HTMLElement) {
  fireEvent.mouseEnter(el)
  act(() => {
    vi.advanceTimersByTime(CANVAS_TOOLTIP_DELAY_MS)
  })
}

/** Minimal in-progress connection drag, shaped like React Flow's store slice. */
function connectionInProgress() {
  return {
    connection: {
      inProgress: true,
      isValid: null,
      from: { x: 0, y: 0 },
      fromHandle: { id: null, nodeId: "other", type: "source", position: "right", x: 0, y: 0, width: 1, height: 1 },
      fromPosition: "right",
      fromNode: null,
      to: { x: 10, y: 10 },
      toHandle: null,
      toPosition: "left",
      toNode: null,
      pointer: { x: 10, y: 10 },
    } as unknown as ReactFlowState["connection"],
  }
}

describe("PipelineNode type tooltip (whole-body trigger)", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("hovering the node body at full zoom shows the exact type description after the delay", () => {
    renderNode({ label: "Clean", nodeType: NODE_TYPES.POLARS })
    const node = screen.getByTestId("node-Clean")
    fireEvent.mouseEnter(node)
    act(() => {
      vi.advanceTimersByTime(CANVAS_TOOLTIP_DELAY_MS - 1)
    })
    // Long-delay gate: must not fire during incidental pointer transit.
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    const tooltip = screen.getByTestId("node-type-tooltip")
    expect(tooltip.querySelector('[data-node-type="polars"]')).not.toBeNull()
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.polars.description,
    )
  })

  it("hovering the node body at medium zoom shows the tooltip", () => {
    renderNode(
      { label: "Clean", nodeType: NODE_TYPES.POLARS },
      { storePatch: { transform: [0, 0, 0.4] } },
    )
    hoverAndWait(screen.getByTestId("node-Clean"))
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.polars.description,
    )
  })

  it("hovering the node body at compact zoom shows the tooltip", () => {
    renderNode(
      { label: "Clean", nodeType: NODE_TYPES.POLARS },
      { storePatch: { transform: [0, 0, 0.2] } },
    )
    hoverAndWait(screen.getByTestId("node-Clean"))
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.polars.description,
    )
  })

  it("does not open while a connection drag is in progress", () => {
    // Bug class: a tooltip popping open under an edge drag fights the gesture.
    renderNode(
      { label: "Clean", nodeType: NODE_TYPES.POLARS },
      { storePatch: connectionInProgress() },
    )
    hoverAndWait(screen.getByTestId("node-Clean"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("does not open during rubber-band selection", () => {
    renderNode(
      { label: "Clean", nodeType: NODE_TYPES.POLARS },
      { storePatch: { userSelectionActive: true } },
    )
    hoverAndWait(screen.getByTestId("node-Clean"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("does not open while the node itself is being dragged", () => {
    renderNode({ label: "Clean", nodeType: NODE_TYPES.POLARS }, { dragging: true })
    hoverAndWait(screen.getByTestId("node-Clean"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("pointerdown on the node dismisses an open tooltip (gesture start wins)", () => {
    renderNode({ label: "Clean", nodeType: NODE_TYPES.POLARS })
    const node = screen.getByTestId("node-Clean")
    hoverAndWait(node)
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
    fireEvent.pointerDown(node)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("adds no nodrag/nopan class and keeps the node-<label> testid on the root", () => {
    // Named absence — the whole-body trigger must be pure hover observation:
    // nodrag/nopan classes would change React Flow drag/pan behaviour.
    renderNode({ label: "Clean", nodeType: NODE_TYPES.POLARS })
    const node = screen.getByTestId("node-Clean")
    expect(node.className).not.toMatch(/nodrag|nopan/)
  })

  it("leaves connector testids untouched (multi-frame pin)", () => {
    renderNode({ label: "Clean", nodeType: NODE_TYPES.POLARS })
    expect(screen.getByTestId("input-connector[0]:Clean")).toBeInTheDocument()
    expect(screen.getByTestId("output-connector[0]:Clean")).toBeInTheDocument()
  })

  it("does not open for unknown node types (isKnownNodeType guard)", () => {
    renderNode({ label: "Mystery", nodeType: "mystery" })
    hoverAndWait(screen.getByTestId("node-Mystery"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  // ── E2: edge-join marker branch ────────────────────────────────────

  it("edgeJoin: hovering the join-node marker root shows the Edge Join description", () => {
    renderNode({ label: "Edge Join", nodeType: NODE_TYPES.EDGE_JOIN })
    hoverAndWait(screen.getByTestId("node-type-tooltip-trigger"))
    const tooltip = screen.getByTestId("node-type-tooltip")
    expect(tooltip.querySelector('[data-node-type="edgeJoin"]')).not.toBeNull()
    expect(screen.getByTestId("node-type-tooltip-description").textContent).toBe(
      NODE_TYPE_META.edgeJoin.description,
    )
  })

  it("edgeJoin: does not open while a connector drag is in progress", () => {
    renderNode(
      { label: "Edge Join", nodeType: NODE_TYPES.EDGE_JOIN },
      { storePatch: connectionInProgress() },
    )
    hoverAndWait(screen.getByTestId("node-type-tooltip-trigger"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("edgeJoin: tooltip wiring leaves the three connectors and the marker untouched", () => {
    // Bug class: tooltip wiring disturbing the join node's connector layout.
    renderNode({ label: "Edge Join", nodeType: NODE_TYPES.EDGE_JOIN })
    expect(screen.getByTestId("edge-join-base-handle")).toBeInTheDocument()
    expect(screen.getByTestId("edge-join-join-handle")).toBeInTheDocument()
    expect(screen.getByTestId("edge-join-output-handle")).toBeInTheDocument()
    const marker = screen.getByTestId("edge-join-marker")
    expect(marker).toHaveClass("pointer-events-none")
    const root = screen.getByTestId("node-type-tooltip-trigger")
    expect(root).toHaveClass("edge-join-node-root")
    expect(root.className).not.toMatch(/nodrag|nopan/)
  })
})
