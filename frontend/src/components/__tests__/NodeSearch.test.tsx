import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, cleanup, fireEvent } from "@testing-library/react"
import { ReactFlowProvider } from "@xyflow/react"
import NodeSearch, {
  NODE_SEARCH_OVERSCAN_ROWS,
  NODE_SEARCH_RESULT_ROW_HEIGHT,
  NODE_SEARCH_VISIBLE_ROWS,
} from "../NodeSearch"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { makeNode } from "../../test-utils/factories"

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockSetCenter = vi.fn()
let mockNodes = [
  makeNode("n1", NODE_TYPES.DATA_SOURCE, { data: { label: "Load Claims", nodeType: NODE_TYPES.DATA_SOURCE, config: {} } }),
  makeNode("n2", NODE_TYPES.POLARS, { data: { label: "Clean Data", nodeType: NODE_TYPES.POLARS, config: {} }, position: { x: 200, y: 100 } }),
  makeNode("n3", NODE_TYPES.MODEL_SCORE, { data: { label: "Score Model", nodeType: NODE_TYPES.MODEL_SCORE, config: {} } }),
]
const defaultMockNodes = mockNodes

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
    mockNodes = defaultMockNodes
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

  it("does not select anything when navigating an empty result set", () => {
    const { onClose, onSelectNode } = renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    fireEvent.change(input, { target: { value: "zzz_no_match" } })

    fireEvent.keyDown(input, { key: "ArrowDown" })
    fireEvent.keyDown(input, { key: "Enter" })

    expect(onSelectNode).not.toHaveBeenCalled()
    expect(onClose).not.toHaveBeenCalled()
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

  it("exposes combobox state and the active result to assistive technology", () => {
    renderSearch()
    const input = screen.getByRole("combobox", { name: /search nodes/i })

    expect(input).toHaveAttribute("aria-expanded", "true")
    expect(input).toHaveAttribute("aria-controls", "node-search-results")
    expect(input).toHaveAttribute("aria-activedescendant", "node-search-result-n1")

    fireEvent.keyDown(input, { key: "ArrowDown" })

    expect(input).toHaveAttribute("aria-activedescendant", "node-search-result-n2")
  })

  it("keeps aria-activedescendant mounted when manual scrolling virtualizes the active row", () => {
    mockNodes = Array.from({ length: 90 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Accessible Node ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index, y: index },
      }),
    )
    renderSearch()
    const input = screen.getByRole("combobox", { name: /search nodes/i })
    const listbox = screen.getByRole("listbox")

    expect(input).toHaveAttribute("aria-activedescendant", "node-search-result-node-0")
    fireEvent.scroll(listbox, { target: { scrollTop: 60 * NODE_SEARCH_RESULT_ROW_HEIGHT } })

    const activeId = input.getAttribute("aria-activedescendant")
    expect(activeId).toBe("node-search-result-node-0")
    expect(document.getElementById(activeId!)).not.toBeNull()
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

  it("renders a bounded result window for very large graphs", () => {
    mockNodes = Array.from({ length: 3000 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Rating Step ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index * 10, y: index * 5 },
      }),
    )

    renderSearch()

    const options = screen.getAllByRole("option")
    expect(options.length).toBeGreaterThan(0)
    expect(options.length).toBeLessThanOrEqual(NODE_SEARCH_VISIBLE_ROWS + NODE_SEARCH_OVERSCAN_ROWS * 2)
    expect(screen.getByText("Rating Step 0")).toBeInTheDocument()
    expect(screen.queryByText("Rating Step 2999")).not.toBeInTheDocument()
  })

  it("can keyboard-select a far result that was initially outside the rendered window", () => {
    mockNodes = Array.from({ length: 120 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Large Graph Node ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index * 10, y: index * 5 },
      }),
    )
    const { onSelectNode, onClose } = renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")

    for (let i = 0; i < 75; i += 1) {
      fireEvent.keyDown(input, { key: "ArrowDown" })
    }
    fireEvent.keyDown(input, { key: "Enter" })

    expect(onSelectNode).toHaveBeenCalledWith("node-75")
    expect(onClose).toHaveBeenCalledOnce()
    expect(mockSetCenter).toHaveBeenCalledWith(850, 400, { zoom: 0.8, duration: 300 })
  })

  it("uses the measured list height when keyboard scrolling in a short viewport", () => {
    mockNodes = Array.from({ length: 30 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Short View Node ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index, y: index },
      }),
    )
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    const listbox = screen.getByRole("listbox")
    Object.defineProperty(listbox, "clientHeight", {
      configurable: true,
      value: NODE_SEARCH_RESULT_ROW_HEIGHT * 3,
    })

    for (let i = 0; i < 5; i += 1) {
      fireEvent.keyDown(input, { key: "ArrowDown" })
    }

    expect(listbox.scrollTop).toBe(3 * NODE_SEARCH_RESULT_ROW_HEIGHT)
    expect(screen.getByText("Short View Node 5").closest("[role='option']")).toHaveAttribute("aria-selected", "true")
  })

  it("keeps the keyboard-active row rendered after programmatic scroll emits a fractional scrollTop", () => {
    mockNodes = Array.from({ length: 90 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Fractional Scroll Node ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index, y: index },
      }),
    )
    renderSearch()
    const input = screen.getByPlaceholderText("Search nodes by name or type...")
    const listbox = screen.getByRole("listbox")

    for (let i = 0; i < 46; i += 1) {
      fireEvent.keyDown(input, { key: "ArrowDown" })
    }

    let currentScrollTop = listbox.scrollTop
    Object.defineProperty(listbox, "scrollTop", {
      configurable: true,
      get: () => currentScrollTop,
      set: (value) => {
        currentScrollTop = value + 0.25
        fireEvent.scroll(listbox)
      },
    })

    fireEvent.keyDown(input, { key: "ArrowDown" })

    expect(screen.getByText("Fractional Scroll Node 47").closest("[role='option']")).toHaveAttribute("aria-selected", "true")
  })

  it("scrolls the virtual window so far results remain mouse-selectable", () => {
    mockNodes = Array.from({ length: 300 }, (_, index) =>
      makeNode(`node-${index}`, NODE_TYPES.POLARS, {
        data: { label: `Scrollable Node ${index}`, nodeType: NODE_TYPES.POLARS, config: {} },
        position: { x: index, y: index },
      }),
    )
    const { onSelectNode } = renderSearch()
    const listbox = screen.getByRole("listbox")

    fireEvent.scroll(listbox, { target: { scrollTop: 80 * NODE_SEARCH_RESULT_ROW_HEIGHT } })

    fireEvent.click(screen.getByText("Scrollable Node 80"))
    expect(onSelectNode).toHaveBeenCalledWith("node-80")
  })
})
