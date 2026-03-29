import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePalette from "../NodePalette"
import type { Node } from "@xyflow/react"
import { NODE_TYPES } from "../../utils/nodeTypes"

describe("NodePalette", () => {
  afterEach(cleanup)

  it("renders the Nodes heading", () => {
    render(<NodePalette />)
    expect(screen.getByText("Nodes")).toBeInTheDocument()
  })

  it("renders all node type templates", () => {
    render(<NodePalette />)
    expect(screen.getByText("Quote Input")).toBeInTheDocument()
    expect(screen.getByText("Data Source")).toBeInTheDocument()
    expect(screen.getByText("Polars")).toBeInTheDocument()
    expect(screen.getByText("Quote Response")).toBeInTheDocument()
    expect(screen.getByText("Model Scoring")).toBeInTheDocument()
    expect(screen.getByText("Banding")).toBeInTheDocument()
  })

  it("shows collapse button when onCollapse provided", () => {
    const onCollapse = vi.fn()
    render(<NodePalette onCollapse={onCollapse} />)
    const collapseBtn = screen.getByTitle("Collapse palette")
    expect(collapseBtn).toBeInTheDocument()
    fireEvent.click(collapseBtn)
    expect(onCollapse).toHaveBeenCalledOnce()
  })

  it("does not show collapse button when onCollapse not provided", () => {
    render(<NodePalette />)
    expect(screen.queryByTitle("Collapse palette")).not.toBeInTheDocument()
  })

  it("disables singleton types already present in graph", () => {
    const nodes: Node[] = [
      { id: "ai1", data: { label: "Quote Input", nodeType: NODE_TYPES.API_INPUT } } as unknown as Node,
    ]
    render(<NodePalette nodes={nodes} />)
    // The Quote Input item should have a "Only one" title indicating it's disabled
    const apiInputItem = screen.getByTitle(/Only one Quote Input/i)
    expect(apiInputItem).toBeInTheDocument()
    expect(apiInputItem).toHaveClass("cursor-not-allowed")
  })

  it("non-singleton items are draggable", () => {
    render(<NodePalette nodes={[]} />)
    // Data Source is not a singleton and should be draggable
    const item = screen.getByText("Data Source").closest("[draggable]")
    expect(item).toHaveAttribute("draggable", "true")
  })

  it("sets drag data on drag start for non-disabled items", () => {
    render(<NodePalette nodes={[]} />)
    const transformItem = screen.getByText("Polars").closest("[draggable]")!
    const setData = vi.fn()
    fireEvent.dragStart(transformItem, {
      dataTransfer: { setData, effectAllowed: "" },
    })
    expect(setData).toHaveBeenCalledWith("application/reactflow-type", NODE_TYPES.POLARS)
    expect(setData).toHaveBeenCalledWith("application/reactflow-config", expect.any(String))
  })

  it("renders Rating Step template", () => {
    render(<NodePalette />)
    expect(screen.getByText("Rating Step")).toBeInTheDocument()
  })

  it("renders all palette node type templates", () => {
    render(<NodePalette />)
    const expectedNames = [
      "Quote Input", "Source Switch", "Quote Response",
      "Data Source", "Data Sink", "Load File", "Constant",
      "Polars", "Expander", "Banding", "Rating Step",
      "Model Training", "Model Scoring",
      "Optimisation", "Apply Optimisation",
    ]
    for (const name of expectedNames) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
  })

  it("disabled singleton shows visual disabled state", () => {
    const nodes: Node[] = [
      { id: "ai1", data: { label: "Quote Input", nodeType: NODE_TYPES.API_INPUT } } as unknown as Node,
    ]
    render(<NodePalette nodes={nodes} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    // Class assertion kept intentionally: opacity is the visible indicator of disabled state,
    // and there is no ARIA attribute or other behavioral signal to assert on here.
    expect(item).toHaveClass("opacity-35")
  })

  it("disabled singleton item is not draggable", () => {
    const nodes: Node[] = [
      { id: "ai1", data: { label: "Quote Input", nodeType: NODE_TYPES.API_INPUT } } as unknown as Node,
    ]
    render(<NodePalette nodes={nodes} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    expect(item).not.toHaveAttribute("draggable", "true")
  })

  it("drag start does not set data for disabled items", () => {
    const nodes: Node[] = [
      { id: "ai1", data: { label: "Quote Input", nodeType: NODE_TYPES.API_INPUT } } as unknown as Node,
    ]
    render(<NodePalette nodes={nodes} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    const setData = vi.fn()
    fireEvent.dragStart(item, {
      dataTransfer: { setData, effectAllowed: "" },
    })
    expect(setData).not.toHaveBeenCalled()
  })

  it("sets effectAllowed to move on drag start", () => {
    render(<NodePalette nodes={[]} />)
    const transformItem = screen.getByText("Polars").closest("[draggable]")!
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" }
    fireEvent.dragStart(transformItem, { dataTransfer })
    expect(dataTransfer.effectAllowed).toBe("move")
  })
})
