import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import { ReactFlowProvider } from "@xyflow/react"
import NodeSearch from "../NodeSearch"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { makeNode } from "../../test-utils/factories"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockSetCenter = vi.fn()
const mockNodes = [
  makeNode("n1", NODE_TYPES.DATA_SOURCE, { data: { label: "Load Claims", nodeType: NODE_TYPES.DATA_SOURCE, config: {} } }),
  makeNode("n2", NODE_TYPES.POLARS, { data: { label: "Clean Data", nodeType: NODE_TYPES.POLARS, config: {} }, position: { x: 200, y: 100 } }),
  makeNode("n3", NODE_TYPES.MODEL_SCORE, { data: { label: "Score Model", nodeType: NODE_TYPES.MODEL_SCORE, config: {} } }),
]

// jsdom does not implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn()

vi.mock("@xyflow/react", async () => {
  const actual = await vi.importActual("@xyflow/react")
  return {
    ...actual,
    useReactFlow: () => ({
      setCenter: mockSetCenter,
    }),
    useNodes: () => mockNodes,
  }
})

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderSearch(overrides: Partial<{ onClose: () => void; onSelectNode: (id: string) => void }> = {}) {
  const onClose = overrides.onClose ?? vi.fn()
  const onSelectNode = overrides.onSelectNode ?? vi.fn()
  return {
    onClose,
    onSelectNode,
    ...render(
      <ReactFlowProvider>
        <NodeSearch onClose={onClose} onSelectNode={onSelectNode} />
      </ReactFlowProvider>,
    ),
  }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("NodeSearch", () => {
  afterEach(() => {
    cleanup()
    mockSetCenter.mockClear()
  })

  it("renders the search dialog with input", () => {
    renderSearch()
    expect(screen.getByRole("dialog")).toBeInTheDocument()
    expect(screen.getByPlaceholderText("Search nodes by name or type...")).toBeInTheDocument()
  })

  it("shows all nodes when query is empty", () => {
    renderSearch()
    expect(screen.getByText("Load Claims")).toBeInTheDocument()
    expect(screen.getByText("Clean Data")).toBeInTheDocument()
    expect(screen.getByText("Score Model")).toBeInTheDocument()
  })

  it("filters nodes by label", () => {
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.change(input, { target: { value: "clean" } })
    expect(screen.getByText("Clean Data")).toBeInTheDocument()
    expect(screen.queryByText("Load Claims")).not.toBeInTheDocument()
    expect(screen.queryByText("Score Model")).not.toBeInTheDocument()
  })

  it("filters nodes by type name", () => {
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.change(input, { target: { value: "scoring" } })
    expect(screen.getByText("Score Model")).toBeInTheDocument()
    expect(screen.queryByText("Load Claims")).not.toBeInTheDocument()
  })

  it("shows empty state when no matches", () => {
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.change(input, { target: { value: "zzz_no_match" } })
    expect(screen.getByText("No matching nodes")).toBeInTheDocument()
  })

  it("calls onClose on Escape", () => {
    const { onClose } = renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.keyDown(input, { key: "Escape" })
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("selects node on Enter and calls onSelectNode + onClose", () => {
    const { onClose, onSelectNode } = renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    // First result is selected by default — press Enter
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onSelectNode).toHaveBeenCalledWith("n1")
    expect(onClose).toHaveBeenCalledOnce()
    expect(mockSetCenter).toHaveBeenCalledOnce()
  })

  it("navigates with arrow keys", () => {
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    // Move down to second result
    fireEvent.keyDown(input, { key: "ArrowDown" })
    // The second option should now be active
    const options = screen.getAllByRole("option")
    expect(options[1]).toHaveAttribute("aria-selected", "true")
    expect(options[0]).toHaveAttribute("aria-selected", "false")
  })

  it("ArrowUp does not go below zero", () => {
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.keyDown(input, { key: "ArrowUp" })
    const options = screen.getAllByRole("option")
    expect(options[0]).toHaveAttribute("aria-selected", "true")
  })

  it("selects node on click", () => {
    const { onSelectNode, onClose } = renderSearch()
    fireEvent.click(screen.getByText("Clean Data"))
    expect(onSelectNode).toHaveBeenCalledWith("n2")
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("closes when clicking backdrop", () => {
    const { onClose, container } = renderSearch()
    // The outermost div is the backdrop wrapper (fixed inset-0)
    const backdrop = container.querySelector(".fixed") as HTMLElement
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledOnce()
  })
})
