/**
 * Tests for SubmodelNode component.
 *
 * Tests: SUBMODEL identity and name badge, frame-only body,
 * output port labels, per-port handles, opacity when dimmed,
 * border style (dashed vs solid).
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import SubmodelNode from "../../nodes/SubmodelNode"
import type { SubmodelFlowNode, SubmodelNodeData } from "../../types/node"
import { DEFAULT_TARGET_HANDLE } from "../../utils/flowHandles"

afterEach(cleanup)

// ── Helpers ─────────────────────────────────────────────────────

function makeProps(
  data: Partial<SubmodelNodeData> & { label: string },
  overrides: { selected?: boolean } = {},
) {
  const fullData: SubmodelNodeData = {
    description: "",
    nodeType: "submodel",
    ...data,
  }

  // NodeProps shape expected by ReactFlow node components
  return {
    id: "test-node",
    type: "submodel",
    data: fullData,
    selected: overrides.selected ?? false,
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

function renderNode(
  data: Partial<SubmodelNodeData> & { label: string },
  opts: { selected?: boolean } = {},
) {
  const props = makeProps(data, opts)
  return render(
    <ReactFlowProvider>
      <SubmodelNode {...(props as unknown as NodeProps<SubmodelFlowNode>)} />
    </ReactFlowProvider>,
  )
}

// ── Tests ───────────────────────────────────────────────────────

describe("SubmodelNode", () => {
  it("renders its identity and name once in the header", () => {
    renderNode({
      label: "My Submodel",
      config: { file: "submodels/pricing.py" },
    })
    const header = screen.getByTestId("submodel-header")
    expect(header).toHaveTextContent("SUBMODEL")
    expect(header).toHaveTextContent("My Submodel")
    expect(screen.getAllByText("My Submodel")).toHaveLength(1)
  })

  it("does not display the child node count", () => {
    renderNode({
      label: "Pricing",
      config: { childNodeIds: ["a", "b", "c"] },
    })
    expect(screen.queryByText("3 nodes")).toBeNull()
  })

  it("does not display the backing file path", () => {
    renderNode({
      label: "Test",
      config: { file: "submodels/pricing.py" },
    })
    expect(screen.queryByText("submodels/pricing.py")).toBeNull()
  })

  it("does not render a body when there are no exported frames", () => {
    renderNode({ label: "Test" })
    expect(screen.queryByTestId("submodel-body")).toBeNull()
  })

  it("renders output port labels", () => {
    renderNode({
      label: "Test",
      config: { outputPorts: ["premium", "discount"] },
    })
    expect(screen.getByTestId("submodel-body")).toBeTruthy()
    expect(screen.getByText(/premium/)).toBeTruthy()
    expect(screen.getByText(/discount/)).toBeTruthy()
  })

  it("renders per-port input handles for each inputPort", () => {
    const { container } = renderNode({
      label: "Test",
      config: { inputPorts: ["base_rate", "claims"] },
    })
    // Hidden per-port handles have ids like "in__base_rate"
    const handle1 = container.querySelector('[data-handleid="in__base_rate"]')
    const handle2 = container.querySelector('[data-handleid="in__claims"]')
    expect(handle1).toBeTruthy()
    expect(handle2).toBeTruthy()
    expect(handle1).not.toHaveClass("connectable")
    expect(handle2).not.toHaveClass("connectable")
    const defaultTarget = container.querySelector(
      `[data-handleid="${DEFAULT_TARGET_HANDLE}"]`,
    )
    expect(defaultTarget).toHaveClass("connectable")
  })

  it("renders per-port output handles for each outputPort", () => {
    const { container } = renderNode({
      label: "Test",
      config: { outputPorts: ["result_a", "result_b"] },
    })
    const handle1 = container.querySelector('[data-handleid="out__result_a"]')
    const handle2 = container.querySelector('[data-handleid="out__result_b"]')
    expect(handle1).toBeTruthy()
    expect(handle2).toBeTruthy()
  })

  it("sets opacity to 0.3 when _traceDimmed is true", () => {
    const { container } = renderNode({
      label: "Dimmed",
      _traceDimmed: true,
    })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.opacity).toBe("0.3")
  })

  it("sets full opacity when _traceDimmed is false", () => {
    const { container } = renderNode({
      label: "Bright",
      _traceDimmed: false,
    })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.opacity).toBe("1")
  })

  it("uses dashed border when not selected and not traceActive", () => {
    const { container } = renderNode(
      { label: "Default" },
      { selected: false },
    )
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.border).toContain("dashed")
  })

  it("uses solid border when selected", () => {
    const { container } = renderNode(
      { label: "Selected" },
      { selected: true },
    )
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.border).toContain("solid")
    expect(wrapper.style.border).not.toContain("dashed")
  })

  it("uses solid border when _traceActive is true", () => {
    const { container } = renderNode({
      label: "Active",
      _traceActive: true,
    })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.border).toContain("solid")
    expect(wrapper.style.border).not.toContain("dashed")
  })

  it("renders mapped frame labels with row-owned stable output handles", () => {
    renderNode({
      label: "Multi Output",
      config: {
        outputPorts: ["polars_12", "polars_13"],
        outputPortLabels: {
          polars_12: "add_drivers",
          polars_13: "claims",
        },
      },
    })

    const firstRow = screen.getByTestId(
      "submodel-output-frame-row-out__polars_12",
    )
    const secondRow = screen.getByTestId(
      "submodel-output-frame-row-out__polars_13",
    )
    expect(firstRow).toHaveTextContent("add_drivers")
    expect(secondRow).toHaveTextContent("claims")

    const firstLabel = screen.getByTestId(
      "submodel-output-body-label-out__polars_12",
    )
    expect(firstLabel).toHaveClass(
      "font-semibold",
      "text-[13px]",
      "leading-tight",
    )

    const firstHandle = firstRow.querySelector(
      '[data-handleid="out__polars_12"]',
    )
    const secondHandle = secondRow.querySelector(
      '[data-handleid="out__polars_13"]',
    )
    expect(firstHandle).toBeTruthy()
    expect(secondHandle).toBeTruthy()
    expect(firstHandle).toHaveStyle({ top: "50%" })
    expect(secondHandle).toHaveStyle({ top: "50%" })
  })

  it("falls back to stable child ids when output labels are absent", () => {
    renderNode({
      label: "Legacy",
      config: { outputPorts: ["alpha", "beta"] },
    })
    expect(screen.getByText("alpha")).toBeTruthy()
    expect(screen.getByText("beta")).toBeTruthy()
  })

  it("uses the standard full-width coloured header bar", () => {
    renderNode({ label: "Header" })
    const header = screen.getByTestId("submodel-header")
    expect(header).toHaveClass("flex", "items-center")
    expect(header.style.background).not.toBe("")
  })

  it("renders no source handle when no output frames are defined", () => {
    const { container } = renderNode({ label: "No Ports" })
    expect(container.querySelector(".react-flow__handle-right")).toBeNull()
  })

  it("switches from dashed to solid border when _traceActive toggles", () => {
    const { container, rerender } = render(
      <ReactFlowProvider>
        <SubmodelNode {...(makeProps({ label: "Toggle" }) as unknown as NodeProps<SubmodelFlowNode>)} />
      </ReactFlowProvider>,
    )
    const wrapper = () => container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper().style.border).toContain("dashed")

    rerender(
      <ReactFlowProvider>
        <SubmodelNode
          {...(makeProps({ label: "Toggle", _traceActive: true }) as unknown as NodeProps<SubmodelFlowNode>)}
        />
      </ReactFlowProvider>,
    )
    expect(wrapper().style.border).toContain("solid")
    expect(wrapper().style.border).not.toContain("dashed")
  })

  it("truncates very long names in the header badge", () => {
    const longName = "Pricing submodel with a deliberately very long display name"
    renderNode({ label: longName })
    const badge = screen.getByTestId("submodel-name-badge")
    expect(badge).toHaveTextContent(longName)
    expect(badge).toHaveClass("truncate")
    expect(badge).toHaveAttribute("title", longName)
  })

  it("dims node when _hoverDimmed is true", () => {
    const { container } = renderNode({
      label: "Hover Dimmed",
      _hoverDimmed: true,
    })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.opacity).toBe("0.3")
  })

  it("disables trace motion transitions when requested by the tracing hook", () => {
    const { container } = renderNode({
      label: "Motion Lite",
      _traceMotionDisabled: true,
    })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.transition).toBe("none")
  })

  it("has full opacity when neither dimmed flag is set", () => {
    const { container } = renderNode({ label: "Full" })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.opacity).toBe("1")
  })
})
