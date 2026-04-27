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

afterEach(cleanup)

// ── Helpers ─────────────────────────────────────────────────────

function makeProps(
  data: Partial<SubmodelPortData> & { portDirection: "input" | "output"; portName: string },
) {
  const fullData: SubmodelPortData = {
    label: data.label ?? data.portName,
    ...data,
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
    width: 120,
    height: 40,
  }
}

function renderPortNode(
  data: Partial<SubmodelPortData> & { portDirection: "input" | "output"; portName: string },
) {
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

  it("renders ArrowLeft icon for output port", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "result",
    })
    const icon = container.querySelector("svg.lucide-arrow-left")
    expect(icon).toBeTruthy()
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
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.opacity).toBe("0.3")
  })

  it("has normal opacity (0.85) when _traceDimmed is false", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "bright",
      _traceDimmed: false,
    })
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.opacity).toBe("0.85")
  })

  it("uses solid border when _traceActive is true", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "active",
      _traceActive: true,
    })
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.border).toContain("solid")
    expect(wrapper.style.border).not.toContain("dashed")
  })

  it("uses dashed border when _traceActive is false", () => {
    const { container } = renderPortNode({
      portDirection: "output",
      portName: "inactive",
      _traceActive: false,
    })
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.border).toContain("dashed")
  })

  it("disables trace motion transitions when requested by the tracing hook", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "motion_lite",
      _traceMotionDisabled: true,
    })
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.transition).toBe("none")
  })

  it("has box-shadow glow when _traceActive is true", () => {
    const { container } = renderPortNode({
      portDirection: "input",
      portName: "glowing",
      _traceActive: true,
    })
    const wrapper = container.querySelector(".rounded-full") as HTMLElement
    expect(wrapper.style.boxShadow).not.toBe("none")
    expect(wrapper.style.boxShadow).toContain("var(--text-accent-glow)")
  })

  it("displays portName when both portName and label are provided", () => {
    renderPortNode({
      portDirection: "input",
      portName: "actual_port",
      label: "label_text",
    })
    expect(screen.getByText("actual_port")).toBeTruthy()
    expect(screen.queryByText("label_text")).toBeNull()
  })

  it("renders with missing portDirection gracefully", () => {
    const props = makeProps({
      portDirection: undefined as unknown as "input",
      portName: "no_direction",
    })
    const { container } = render(
      <ReactFlowProvider>
        <SubmodelPortNode {...(props as unknown as NodeProps<SubmodelPortFlowNode>)} />
      </ReactFlowProvider>,
    )
    expect(screen.getByText("no_direction")).toBeTruthy()
    expect(container.querySelector(".rounded-full")).toBeTruthy()
  })
})
