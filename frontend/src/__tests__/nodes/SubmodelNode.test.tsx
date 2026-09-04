/**
 * Tests for SubmodelNode component.
 *
 * Tests: SUBMODEL identity and name badge, single collapsed input socket,
 * output port labels, canonical edge anchors, opacity when dimmed,
 * border style (dashed vs solid).
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import SubmodelNode from "../../nodes/SubmodelNode"
import type { SubmodelDefinition, SubmodelFlowNode, SubmodelNodeData } from "../../types/node"
import {
  DEFAULT_TARGET_HANDLE,
  SUBMODEL_INPUT_HANDLE,
} from "../../utils/flowHandles"
import useGraphStore from "../../stores/useGraphStore"

const DEFINITION_ID = "definition_pricing"

function graphNode(id: string) {
  return {
    id,
    position: { x: 0, y: 0 },
    data: { label: id, nodeType: "polars", config: {} },
  }
}

function setDefinition(overrides: Partial<SubmodelDefinition> = {}) {
  const definition: SubmodelDefinition = {
    definitionId: DEFINITION_ID,
    file: "submodels/pricing.py",
    graph: { nodes: [], edges: [] },
    inputPorts: [],
    outputPorts: [],
    ...overrides,
  }
  useGraphStore.setState({ submodels: { [DEFINITION_ID]: definition } })
}

beforeEach(() => setDefinition())
afterEach(() => {
  cleanup()
  useGraphStore.setState({ submodels: {} })
})

// ── Helpers ─────────────────────────────────────────────────────

function makeProps(
  data: Partial<Omit<SubmodelNodeData, "config">> & { label: string; config?: Record<string, unknown> },
  overrides: { selected?: boolean; isConnectable?: boolean } = {},
) {
  const fullData = {
    description: "",
    nodeType: "submodel",
    config: { definitionId: DEFINITION_ID, alias: "pricing" },
    ...data,
  }

  // NodeProps shape expected by ReactFlow node components
  return {
    id: "test-node",
    type: "submodel",
    data: fullData,
    selected: overrides.selected ?? false,
    isConnectable: overrides.isConnectable ?? true,
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
  data: Partial<Omit<SubmodelNodeData, "config">> & { label: string; config?: Record<string, unknown> },
  opts: { selected?: boolean; isConnectable?: boolean } = {},
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
    renderNode({ label: "My Submodel" })
    const header = screen.getByTestId("submodel-header")
    expect(header).toHaveTextContent("SUBMODEL")
    expect(header).toHaveTextContent("My Submodel")
    expect(screen.getAllByText("My Submodel")).toHaveLength(1)
  })
  it("keeps the definition child count in accessibility text, not the body", () => {
    setDefinition({
      graph: {
        nodes: [graphNode("a"), graphNode("b"), graphNode("c")],
        edges: [],
      },
    })
    renderNode({ label: "Pricing" })
    expect(screen.queryByText("3 nodes")).toBeNull()
    expect(screen.getByRole("button")).toHaveAccessibleName(/3 child nodes/)
  })
  it("does not display the definition file path", () => {
    renderNode({ label: "Test" })
    expect(screen.queryByText("submodels/pricing.py")).toBeNull()
  })
  it("renders one generic input socket when its interface is empty", () => {
    const { container } = renderNode({ label: "Test" })
    const row = screen.getByTestId("submodel-input-row")
    const handle = screen.getByTestId("submodel-input-handle")

    expect(screen.getByTestId("submodel-body")).toContainElement(row)
    expect(row).toHaveTextContent("inputs")
    expect(handle).toHaveClass("react-flow__handle-left", "input-origin-handle")
    expect(handle).toHaveAttribute("data-handleid", SUBMODEL_INPUT_HANDLE)
    expect(container.querySelector(".react-flow__handle-right")).toBeNull()
  })

  it("keeps the one structural input socket on copies and read-only cards", () => {
    const { unmount } = renderNode({
      label: "Copy",
      config: {
        definitionId: DEFINITION_ID,
        alias: "copy",
        instanceOf: "owner",
      },
    })
    expect(screen.getByTestId("submodel-input-handle")).toBeTruthy()
    unmount()

    renderNode({ label: "Owner" }, { isConnectable: false })
    expect(screen.getByTestId("submodel-input-handle")).toBeTruthy()
  })

  it("renders definition-owned output port labels", () => {
    setDefinition({
      graph: { nodes: [graphNode("premium"), graphNode("discount")], edges: [] },
      outputPorts: [
        {
          portId: "premium",
          label: "Premium",
          source: { nodeId: "premium", handleId: null },
        },
        {
          portId: "discount",
          label: "Discount",
          source: { nodeId: "discount", handleId: null },
        },
      ],
    })
    renderNode({ label: "Test" })
    expect(screen.getByTestId("submodel-body")).toBeTruthy()
    expect(screen.getByText("Premium")).toBeTruthy()
    expect(screen.getByText("Discount")).toBeTruthy()
  })
  it("co-locates canonical input anchors under one visible socket", () => {
    setDefinition({
      graph: { nodes: [graphNode("base_rate"), graphNode("claims")], edges: [] },
      inputPorts: [
        {
          portId: "base_rate",
          label: "Base rate",
          targets: [{ nodeId: "base_rate", handleId: null }],
        },
        {
          portId: "claims",
          label: "Claims",
          targets: [{ nodeId: "claims", handleId: null }],
        },
      ],
    })
    const { container } = renderNode({ label: "Test" })
    const handle1 = container.querySelector('[data-handleid="in__base_rate"]')
    const handle2 = container.querySelector('[data-handleid="in__claims"]')
    const genericHandle = screen.getByTestId("submodel-input-handle")
    expect(handle1).toBeTruthy()
    expect(handle2).toBeTruthy()
    expect(handle1).toHaveClass("submodel-input-edge-anchor")
    expect(handle2).toHaveClass("submodel-input-edge-anchor")
    expect(handle1).not.toHaveClass("input-origin-handle")
    expect(handle2).not.toHaveClass("input-origin-handle")
    expect(handle1).toHaveStyle({ pointerEvents: "none", top: "50%" })
    expect(handle2).toHaveStyle({ pointerEvents: "none", top: "50%" })
    expect(handle1?.parentElement).toBe(genericHandle.parentElement)
    expect(handle2?.parentElement).toBe(genericHandle.parentElement)
    expect(container.querySelectorAll(".input-origin-handle")).toHaveLength(1)
    // React Flow breaks equal-distance overlap ties by measured DOM order.
    // The interactive generic socket must win over its canonical edge anchors.
    expect(container.querySelectorAll(".react-flow__handle.target")[0]).toBe(genericHandle)
    expect(screen.queryByText("Base rate")).toBeNull()
    expect(screen.queryByText("Claims")).toBeNull()
    expect(container.querySelector(
      '[data-handleid="' + DEFAULT_TARGET_HANDLE + '"]',
    )).toBeNull()
  })
  it("renders per-port output handles from the definition contract", () => {
    setDefinition({
      graph: { nodes: [graphNode("result_a"), graphNode("result_b")], edges: [] },
      outputPorts: [
        {
          portId: "result_a",
          label: "Result A",
          source: { nodeId: "result_a", handleId: null },
        },
        {
          portId: "result_b",
          label: "Result B",
          source: { nodeId: "result_b", handleId: null },
        },
      ],
    })
    const { container } = renderNode({ label: "Test" })
    expect(container.querySelector('[data-handleid="out__result_a"]')).toBeTruthy()
    expect(container.querySelector('[data-handleid="out__result_b"]')).toBeTruthy()
  })
  it("renders definition-owned public ports without exposing internal node ids", () => {
    const previousSubmodels = useGraphStore.getState().submodels
    useGraphStore.setState({
      submodels: {
        definition_scoring: {
          definitionId: "definition_scoring",
          file: "modules/scoring.py",
          graph: {
            nodes: [
              {
                id: "internal_input_17",
                position: { x: 0, y: 0 },
                data: { label: "Input", nodeType: "polars", config: {} },
              },
              {
                id: "internal_output_42",
                position: { x: 100, y: 0 },
                data: { label: "Output", nodeType: "polars", config: {} },
              },
            ],
            edges: [],
          },
          inputPorts: [{
            portId: "policy",
            label: "Policy data",
            targets: [{ nodeId: "internal_input_17", handleId: null }],
          }],
          outputPorts: [{
            portId: "premium",
            label: "Written premium",
            source: { nodeId: "internal_output_42", handleId: null },
          }],
        },
      },
    })

    try {
      const { container } = renderNode({
        label: "Scoring",
        config: {
          definitionId: "definition_scoring",
          alias: "scoring",
        },
      })

      expect(container.querySelector('[data-handleid="in__policy"]')).toBeTruthy()
      expect(container.querySelector('[data-handleid="out__premium"]')).toBeTruthy()
      expect(container.querySelector(
        `[data-handleid="${DEFAULT_TARGET_HANDLE}"]`,
      )).toBeNull()
      expect(container.querySelector(
        `[data-handleid="${SUBMODEL_INPUT_HANDLE}"]`,
      )).toBeTruthy()
      expect(screen.queryByText("Policy data")).toBeNull()
      expect(screen.getByText("Written premium")).toBeTruthy()
      expect(screen.getByRole("button")).toHaveAccessibleName(/2 child nodes/)
      expect(container.querySelector('[data-handleid*="internal_input_17"]')).toBeNull()
      expect(container.querySelector('[data-handleid*="internal_output_42"]')).toBeNull()
    } finally {
      useGraphStore.setState({ submodels: previousSubmodels })
    }
  })

  it("shows an invalid-definition state instead of silently rendering no ports", () => {
    const previousSubmodels = useGraphStore.getState().submodels
    useGraphStore.setState({ submodels: {} })

    try {
      const { container } = renderNode({
        label: "Broken scoring",
        config: {
          definitionId: "definition_missing",
          alias: "scoring",
        },
      })

      expect(screen.getByRole("alert")).toHaveTextContent("Definition unavailable or invalid")
      expect(container.querySelector(
        `[data-handleid="${DEFAULT_TARGET_HANDLE}"]`,
      )).toBeNull()
      expect(container.querySelector(
        `[data-handleid="${SUBMODEL_INPUT_HANDLE}"]`,
      )).toBeNull()
    } finally {
      useGraphStore.setState({ submodels: previousSubmodels })
    }
  })


  it("treats a partial canonical identity as invalid instead of legacy", () => {
    const { container } = renderNode({
      label: "Broken scoring",
      config: { alias: "scoring" },
    })

    expect(screen.getByRole("alert")).toHaveTextContent("Definition unavailable or invalid")
    expect(container.querySelector(
      `[data-handleid="${DEFAULT_TARGET_HANDLE}"]`,
    )).toBeNull()
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

  it("renders explicit output labels with row-owned stable handles", () => {
    setDefinition({
      graph: { nodes: [graphNode("internal_a"), graphNode("internal_b")], edges: [] },
      outputPorts: [
        {
          portId: "polars_12",
          label: "add_drivers",
          source: { nodeId: "internal_a", handleId: null },
        },
        {
          portId: "polars_13",
          label: "claims",
          source: { nodeId: "internal_b", handleId: null },
        },
      ],
    })
    renderNode({ label: "Multi Output" })

    const firstRow = screen.getByTestId(
      "submodel-output-frame-row-out__polars_12",
    )
    const secondRow = screen.getByTestId(
      "submodel-output-frame-row-out__polars_13",
    )
    const inputHandle = screen.getByTestId("submodel-input-handle")
    expect(firstRow).toHaveTextContent("add_drivers")
    expect(firstRow).toHaveTextContent("inputs")
    expect(firstRow).toContainElement(inputHandle)
    expect(secondRow).toHaveTextContent("claims")
    expect(secondRow).not.toHaveTextContent("inputs")
    expect(screen.queryByTestId("submodel-input-row")).toBeNull()

    const firstLabel = screen.getByTestId(
      "submodel-output-body-label-out__polars_12",
    )
    expect(firstLabel).toHaveClass(
      "font-semibold",
      "text-[13px]",
      "leading-tight",
    )

    expect(firstRow.querySelector(
      '[data-handleid="out__polars_12"]',
    )).toHaveStyle({ top: "50%" })
    expect(secondRow.querySelector(
      '[data-handleid="out__polars_13"]',
    )).toHaveStyle({ top: "50%" })
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
