/**
 * SubmodelNode rendering — focused on the hover-through glow (#3): a wrapper that
 * sits on the hovered data-path glows to signal "the path runs through here",
 * even though its internals aren't drawn on the canvas.
 */
import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import { ReactFlowProvider, type NodeProps } from "@xyflow/react"
import SubmodelNode from "../SubmodelNode"
import type { SubmodelFlowNode, SubmodelNodeData } from "../../types/node"

function renderSubmodel(data: Partial<SubmodelNodeData> = {}, selected = false) {
  const fullData: SubmodelNodeData = {
    label: "MyWrapper",
    description: "",
    nodeType: "submodel",
    config: { childNodeIds: ["c1", "c2"], inputPorts: [], outputPorts: [] },
    ...data,
  }
  const props = {
    id: "submodel__MyWrapper",
    type: "submodel",
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
  } as unknown as NodeProps<SubmodelFlowNode>
  render(
    <ReactFlowProvider>
      <SubmodelNode {...props} />
    </ReactFlowProvider>,
  )
  return screen.getByLabelText(/Wrapper node/)
}

describe("SubmodelNode hover-through glow", () => {
  afterEach(cleanup)

  it("has a dashed border and plain shadow at rest", () => {
    const el = renderSubmodel()
    expect(el.style.border).toContain("dashed")
    expect(el.style.boxShadow).toBe("var(--node-shadow)")
  })

  it("glows with a solid accent border when _hoverThrough is set", () => {
    const el = renderSubmodel({ _hoverThrough: true })
    expect(el.style.border).toContain("solid")
    expect(el.style.boxShadow).toContain("12px")
  })

  it("is dimmed (opacity 0.3) when _hoverDimmed and not on the path", () => {
    const el = renderSubmodel({ _hoverDimmed: true })
    expect(el.style.opacity).toBe("0.3")
  })

  it("stays full opacity and glows when on the hovered path", () => {
    const el = renderSubmodel({ _hoverThrough: true, _hoverDimmed: false })
    expect(el.style.opacity).toBe("1")
    expect(el.style.boxShadow).toContain("12px")
  })
})
