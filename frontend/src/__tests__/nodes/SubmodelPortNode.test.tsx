/**
 * Tests for SubmodelPortNode component.
 *
 * Tests: portName text, input port handle placement (source on right),
 * output port handle placement (target on left), opacity when dimmed,
 * border change when traceActive.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import SubmodelPortNode from "../../nodes/SubmodelPortNode"
import type { SubmodelPortData, SubmodelPortFlowNode } from "../../types/node"
import { DEFAULT_TARGET_HANDLE } from "../../utils/flowHandles"

afterEach(cleanup)

// ── Helpers ─────────────────────────────────────────────────────

type PortPropsData = Partial<SubmodelPortData> & {
  portDirection: "input" | "output"
  portName?: string
}

function makeProps(data: PortPropsData) {
  const { portName, ...typedData } = data
  const fallbackFrameLabel = portName || data.label || "frame"
  const fullData: SubmodelPortData = {
    ...typedData,
    label: data.portDirection === "input" ? "INPUT" : "OUTPUT",
    portDirection: data.portDirection,
    ports: data.ports ?? (
      portName === undefined
        ? []
        : [{ id: portName || fallbackFrameLabel, label: fallbackFrameLabel }]
    ),
    externalNodeIds: data.externalNodeIds ?? [],
  }
  return {
    id: "test-port-node",
    type: "submodelPort",
    data: fullData,
    selected: false,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    zIndex: 0,
    dragging: false,
    dragHandle: undefined,
    parentId: undefined,
    sourcePosition: undefined,
    targetPosition: undefined,
    width: 240,
    height: 70,
  }
}

function renderPortNode(data: PortPropsData) {
  const props = makeProps(data)
  return render(
    <ReactFlowProvider>
      <SubmodelPortNode {...(props as unknown as NodeProps<SubmodelPortFlowNode>)} />
    </ReactFlowProvider>,
  )
}

// ── Tests ───────────────────────────────────────────────────────

describe("SubmodelPortNode", () => {
  it("renders the portName text", () => {
    renderPortNode({ portDirection: "input", portName: "base_rate" })
    expect(screen.getByText("base_rate")).toBeTruthy()
  })

  it("falls back to label when portName is empty", () => {
    renderPortNode({ portDirection: "input", portName: "", label: "fallback_label" })
    expect(screen.getByText("fallback_label")).toBeTruthy()
  })

  it("renders ArrowRight icon for input port", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "rate",
    })
    // Lucide ArrowRight renders as svg with lucide-arrow-right class
    const icon = container.querySelector("svg.lucide-arrow-right")
    expect(icon).toBeTruthy()
  })

  it("renders the same ArrowRight icon for output port", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "result",
    })
    expect(container.querySelector("svg.lucide-arrow-right")).toBeTruthy()
    expect(container.querySelector("svg.lucide-arrow-left")).toBeNull()
  })

  it("renders source handle (right side) for input port direction", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "data_in",
    })
    // ReactFlow source handles have class "react-flow__handle-right" or data-handlepos="right"
    const sourceHandle = container.querySelector(".react-flow__handle-right")
    expect(sourceHandle).toBeTruthy()
  })

  it("renders target handle (left side) for output port direction", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "data_out",
    })
    const targetHandle = container.querySelector(".react-flow__handle-left")
    expect(targetHandle).toBeTruthy()
  })

  it("does NOT render target handle for input port direction", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "data_in",
    })
    const targetHandle = container.querySelector(".react-flow__handle-left")
    expect(targetHandle).toBeNull()
  })

  it("does NOT render source handle for output port direction", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "data_out",
    })
    const sourceHandle = container.querySelector(".react-flow__handle-right")
    expect(sourceHandle).toBeNull()
  })

  it("reduces opacity when _traceDimmed is true", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "dimmed",
      _traceDimmed: true,
    })
    const wrapper = screen.getByTestId("submodel-boundary-card")
    expect(wrapper.style.opacity).toBe("0.3")
    expect(container).toContainElement(wrapper)
  })

  it("has full opacity when _traceDimmed is false", () => {
    renderPortNode({
      portDirection: "input",
      portName: "bright",
      _traceDimmed: false,
    })
    expect(screen.getByTestId("submodel-boundary-card").style.opacity).toBe("1")
  })

  it("uses the standard solid card border", () => {
    renderPortNode({
      portDirection: "output",
      portName: "inactive",
      _traceActive: false,
    })
    const wrapper = screen.getByTestId("submodel-boundary-card")
    expect(wrapper.style.border).toContain("solid")
    expect(wrapper.style.border).not.toContain("dashed")
  })

  it("disables trace motion transitions when requested by the tracing hook", () => {
    renderPortNode({
      portDirection: "input",
      portName: "motion_lite",
      _traceMotionDisabled: true,
    })
    expect(screen.getByTestId("submodel-boundary-card").style.transition).toBe("none")
  })

  it("has an accent glow when _traceActive is true", () => {
    renderPortNode({
      portDirection: "input",
      portName: "glowing",
      _traceActive: true,
    })
    const wrapper = screen.getByTestId("submodel-boundary-card")
    expect(wrapper.style.boxShadow).not.toBe("none")
  })

  it("renders all Input frames as individually connectable source rows", () => {
    renderPortNode({
      portDirection: "input",
      ports: [
        { id: "api-a:quote", label: "quote" },
        { id: "api-a:claims", label: "claims" },
      ],
    })

    expect(screen.getByText("INPUT")).toBeTruthy()
    const quoteRow = screen.getByTestId(
      "submodel-input-frame-row-api-a:quote",
    )
    const claimsRow = screen.getByTestId(
      "submodel-input-frame-row-api-a:claims",
    )
    expect(quoteRow.querySelector('[data-handleid="api-a:quote"]')).toHaveClass(
      "react-flow__handle-right",
    )
    expect(claimsRow.querySelector('[data-handleid="api-a:claims"]')).toHaveClass(
      "react-flow__handle-right",
    )
  })

  it("renders exactly one shared Output target and no exported-frame rows", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      ports: [
        { id: "out__claims", label: "claims" },
        { id: "out__quote", label: "quote" },
      ],
    })

    expect(screen.getByText("OUTPUT")).toBeTruthy()
    expect(screen.getByText("Connect frames to export")).toBeTruthy()
    expect(screen.queryByText("claims")).toBeNull()
    expect(screen.queryByText("quote")).toBeNull()
    const targets = container.querySelectorAll(".react-flow__handle-left")
    expect(targets).toHaveLength(1)
    expect(targets[0]).toHaveAttribute("data-handleid", DEFAULT_TARGET_HANDLE)
  })

  it("renders the Input empty state without a handle row", () => {
    renderPortNode({ portDirection: "input", ports: [] })
    expect(screen.getByText("No input frames")).toBeTruthy()
    expect(screen.queryByRole("button", { name: /frame/i })).toBeNull()
  })

  it("keeps the shared Output target when no frame is exported", () => {
    const { container } = renderPortNode({ portDirection: "output", ports: [] })
    expect(screen.getByText("Connect frames to export")).toBeTruthy()
    expect(container.querySelectorAll(".react-flow__handle-left")).toHaveLength(1)
  })

  it("fails loudly for an invalid boundary direction", () => {
    const props = makeProps({
      portDirection: undefined as unknown as "input",
      portName: "no_direction",
    })
    expect(() => render(
      <ReactFlowProvider>
        <SubmodelPortNode {...(props as unknown as NodeProps<SubmodelPortFlowNode>)} />
      </ReactFlowProvider>,
    )).toThrow(/portDirection/)
  })
})
