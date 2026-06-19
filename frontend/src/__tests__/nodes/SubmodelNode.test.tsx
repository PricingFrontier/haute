/**
 * Tests for SubmodelNode component.
 *
 * Tests: label rendering, SUBMODEL badge, child count, file path display,
 * output port labels, per-port handles, opacity when dimmed,
 * border style (dashed vs solid).
 */
import { describe, it, expect, afterEach, vi } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import SubmodelNode from "../../nodes/SubmodelNode"
import useUIStore from "../../stores/useUIStore"
import useGraphStore from "../../stores/useGraphStore"
import type { SubmodelFlowNode, SubmodelNodeData } from "../../types/node"

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
  it("renders the label text", () => {
    renderNode({ label: "My Submodel" })
    expect(screen.getByText("My Submodel")).toBeTruthy()
  })

  it('renders the "WRAPPER" badge', () => {
    renderNode({ label: "Test" })
    expect(screen.getByText("WRAPPER")).toBeTruthy()
  })

  it("renders the child node count", () => {
    renderNode({
      label: "Test",
      config: { childNodeIds: ["a", "b", "c"] },
    })
    expect(screen.getByText("3 nodes")).toBeTruthy()
  })

  it("renders 0 nodes when no childNodeIds", () => {
    renderNode({ label: "Test" })
    expect(screen.getByText("0 nodes")).toBeTruthy()
  })

  it("renders file path when config.file is set", () => {
    renderNode({
      label: "Test",
      config: { file: "submodels/pricing.py" },
    })
    expect(screen.getByText("submodels/pricing.py")).toBeTruthy()
  })

  it("does not render file path when config.file is not set", () => {
    renderNode({ label: "Test" })
    // Only the port labels use --text-muted; without a file, there should be none
    // in the header area. We verify by ensuring the specific text is absent.
    expect(screen.queryByText("submodels/pricing.py")).toBeNull()
  })

  it("renders output port labels", () => {
    renderNode({
      label: "Test",
      config: { outputPorts: ["premium", "discount"] },
    })
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

  it("positions multiple output port handles at different top percentages", () => {
    const { container } = renderNode({
      label: "Multi Output",
      config: { outputPorts: ["alpha", "beta", "gamma"] },
    })
    const handleA = container.querySelector('[data-handleid="out__alpha"]') as HTMLElement
    const handleB = container.querySelector('[data-handleid="out__beta"]') as HTMLElement
    const handleC = container.querySelector('[data-handleid="out__gamma"]') as HTMLElement
    expect(handleA).toBeTruthy()
    expect(handleB).toBeTruthy()
    expect(handleC).toBeTruthy()
    const topA = handleA.style.top
    const topB = handleB.style.top
    const topC = handleC.style.top
    expect(topA).toBe("25%")
    expect(topB).toBe("50%")
    expect(topC).toBe("75%")
  })

  it("renders a single default source handle when no output ports defined", () => {
    const { container } = renderNode({ label: "No Ports" })
    const sourceHandle = container.querySelector(".react-flow__handle-right")
    expect(sourceHandle).toBeTruthy()
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

  it("renders very long file paths with truncation", () => {
    const longPath = "submodels/deeply/nested/directory/structure/with/many/levels/pricing_model_v2.py"
    renderNode({
      label: "Long Path",
      config: { file: longPath },
    })
    expect(screen.getByText(longPath)).toBeTruthy()
    const el = screen.getByText(longPath)
    expect(el.classList.contains("truncate")).toBe(true)
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
    const stripe = container.querySelector(".absolute.left-0") as HTMLElement
    expect(wrapper.style.transition).toBe("none")
    expect(stripe.style.transition).toBe("none")
  })

  it("has full opacity when neither dimmed flag is set", () => {
    const { container } = renderNode({ label: "Full" })
    const wrapper = container.querySelector(".rounded-xl") as HTMLElement
    expect(wrapper.style.opacity).toBe("1")
  })

  // ── Node-explosion peek trigger (T5) ────────────────────────────
  describe("peek trigger (node-explosion)", () => {
    afterEach(() => {
      useUIStore.setState({ peek: null })
      useGraphStore.setState({ lastSavedSnapshot: null, undoStack: [], redoStack: [], nodes: [], edges: [] })
    })

    it("renders a node-peek-trigger-<label> button", () => {
      renderNode({ label: "Pricing" })
      expect(screen.getByTestId("node-peek-trigger-Pricing")).toBeInTheDocument()
    })

    it("the trigger carries the nodrag class (this is what stops the drag-start)", () => {
      renderNode({ label: "Pricing" })
      // nodrag is React Flow's noDragClassName filter — the only mechanism that
      // stops d3-drag, since nodeDragThreshold defaults to 1. Asserted on its own.
      expect(screen.getByTestId("node-peek-trigger-Pricing").classList.contains("nodrag")).toBe(true)
    })

    it("clicking the trigger opens the peek and leaves the node visually unselected", () => {
      // NOTE on scope: this renders a bare <ReactFlowProvider>, which does NOT
      // wrap the node in React Flow's NodeWrapper, so no d3-drag listener is
      // attached and a synthetic pointer wobble here cannot start a drag. The
      // drag-suppression invariant (a press-with-wobble on the trigger must not
      // start a node drag → select+nudge → dirty) is therefore NOT provable in
      // this harness; it is pinned structurally by the `nodrag` class test
      // above (React Flow's only drag-stop mechanism, given nodeDragThreshold=1)
      // and by the stopPropagation/bubbling test below. This test pins the
      // positive behaviour: a clean click opens the peek and does not flip the
      // node's selection visual.
      const { container } = renderNode({ label: "Pricing" }, { selected: false })
      const trigger = screen.getByTestId("node-peek-trigger-Pricing")
      const wrapper = container.querySelector(".rounded-xl") as HTMLElement

      fireEvent.click(trigger)

      expect(useUIStore.getState().peek).toEqual({ nodeId: "test-node" })
      // Still dashed border = unselected (the peek did not select the node).
      expect(wrapper.style.border).toContain("dashed")
    })

    it("pointerdown/mousedown/click on the trigger do not bubble to the node (selection suppressed)", () => {
      const ancestorPointerDown = vi.fn()
      const ancestorMouseDown = vi.fn()
      const ancestorClick = vi.fn()
      const props = makeProps({ label: "Pricing" })
      render(
        <ReactFlowProvider>
          {/* Bubbling-phase listeners on the ancestor stand in for the App-level
              node-press / node-click handlers; the trigger's stopPropagation in
              pointerdown/mousedown/click must keep them from firing. */}
          <div
            onPointerDown={ancestorPointerDown}
            onMouseDown={ancestorMouseDown}
            onClick={ancestorClick}
          >
            <SubmodelNode {...(props as unknown as NodeProps<SubmodelFlowNode>)} />
          </div>
        </ReactFlowProvider>,
      )
      const trigger = screen.getByTestId("node-peek-trigger-Pricing")
      fireEvent.pointerDown(trigger, { clientX: 100, clientY: 100 })
      fireEvent.mouseDown(trigger, { clientX: 100, clientY: 100 })
      fireEvent.click(trigger)
      // None of the ancestor (node-level) handlers saw the event.
      expect(ancestorPointerDown).not.toHaveBeenCalled()
      expect(ancestorMouseDown).not.toHaveBeenCalled()
      expect(ancestorClick).not.toHaveBeenCalled()
      // But the peek did open.
      expect(useUIStore.getState().peek).toEqual({ nodeId: "test-node" })
    })
  })
})
