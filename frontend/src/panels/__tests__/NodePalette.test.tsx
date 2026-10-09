import { describe, it, expect, vi, afterEach } from "vitest"
import { act, render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePalette from "../NodePalette"
import useExtensionsStore from "../../stores/useExtensionsStore"
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
    expect(screen.getByText("Data Input")).toBeInTheDocument()
    expect(screen.getByText("Transform")).toBeInTheDocument()
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
    render(<NodePalette existingSingletonTypes={new Set([NODE_TYPES.API_INPUT])} />)
    // The Quote Input item should have a "Only one" title indicating it's disabled
    const apiInputItem = screen.getByTitle(/Only one Quote Input/i)
    expect(apiInputItem).toBeInTheDocument()
    expect(apiInputItem).toHaveAttribute("aria-disabled", "true")
  })

  it("non-singleton items are draggable", () => {
    render(<NodePalette />)
    // Data Input is not a singleton and should be draggable
    const item = screen.getByText("Data Input").closest("[draggable]")
    expect(item).toHaveAttribute("draggable", "true")
  })

  it("sets drag data on drag start for non-disabled items", () => {
    render(<NodePalette />)
    const transformItem = screen.getByText("Transform").closest("[draggable]")!
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
      "Data Input", "Data Output", "Load File", "Constant",
      "Transform", "Expander", "Banding", "Rating Step", "Explore",
      "Model Training", "Model Scoring",
      "Optimisation", "Apply Optimisation",
    ]
    for (const name of expectedNames) {
      expect(screen.getByText(name)).toBeInTheDocument()
    }
  })

  it("disabled singleton shows visual disabled state", () => {
    render(<NodePalette existingSingletonTypes={new Set([NODE_TYPES.API_INPUT])} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    // haute-ui's palette item fades a disabled item (palette.css) and marks it aria-disabled.
    expect(item).toHaveClass("palette-item-disabled")
    expect(item).toHaveAttribute("aria-disabled", "true")
  })

  it("disabled singleton item is not draggable", () => {
    render(<NodePalette existingSingletonTypes={new Set([NODE_TYPES.API_INPUT])} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    expect(item).not.toHaveAttribute("draggable", "true")
  })

  it("drag start does not set data for disabled items", () => {
    render(<NodePalette existingSingletonTypes={new Set([NODE_TYPES.API_INPUT])} />)
    const item = screen.getByTitle(/Only one Quote Input/i)
    const setData = vi.fn()
    fireEvent.dragStart(item, {
      dataTransfer: { setData, effectAllowed: "" },
    })
    expect(setData).not.toHaveBeenCalled()
  })

  describe("while an installed extension supplies the quote's tables", () => {
    const supplier = {
      name: "obverse",
      label: "Workbench",
      api_base: "/api/extensions/obverse",
      entry_url: "/extensions/obverse/obverse-embed.js",
      ready: true,
      detail: null,
      quote_tables: true,
    }
    const tables = [{ path: "$[:]", label: "policy_details", emit: true, row_id_column: null, columns: [] }]
    const dragConfig = (name: string) => {
      const setData = vi.fn()
      fireEvent.dragStart(screen.getByText(name).closest("[draggable]")!, {
        dataTransfer: { setData, effectAllowed: "" },
      })
      expect(setData).toHaveBeenCalledWith("application/reactflow-type", NODE_TYPES.WORKBENCH_INPUT)
      const [, json] = setData.mock.calls.find(([format]) => format === "application/reactflow-config")!
      return JSON.parse(json)
    }
    afterEach(() => useExtensionsStore.setState({ extensions: [], quoteTables: null }))

    it("shows the Workbench Input in the Quote Input's place, and the Quote Input otherwise", () => {
      render(<NodePalette />)
      expect(screen.getByText("Quote Input")).toBeInTheDocument()
      expect(screen.queryByText("Workbench Input")).not.toBeInTheDocument()

      act(() => useExtensionsStore.setState({ extensions: [supplier] }))
      expect(screen.getByText("Workbench Input")).toBeInTheDocument()
      expect(screen.queryByText("Quote Input")).not.toBeInTheDocument()
      const items = [...document.querySelectorAll("[data-testid^='node-palette-item-']")]
      expect(items[0]).toHaveAttribute("data-testid", `node-palette-item-${NODE_TYPES.WORKBENCH_INPUT}`)
    })

    it("drags a Workbench Input with the newest tables and sample fetched, or none until they are", () => {
      useExtensionsStore.setState({ extensions: [supplier], quoteTables: null })
      render(<NodePalette />)
      expect(dragConfig("Workbench Input")).toEqual({ tables: [], sample: {} })

      const sample = { policy_details: { state: "NY" } }
      act(() => useExtensionsStore.setState({ quoteTables: { extension: "obverse", tables, sample, fetch: 1 } }))
      expect(dragConfig("Workbench Input")).toEqual({ tables, sample })
    })

    it("greys out the Workbench Input while the pipeline has either request input", () => {
      useExtensionsStore.setState({ extensions: [supplier] })
      for (const present of [NODE_TYPES.API_INPUT, NODE_TYPES.WORKBENCH_INPUT]) {
        const { unmount } = render(
          <NodePalette existingSingletonTypes={new Set([present, NODE_TYPES.WORKBENCH_INPUT])} />,
        )
        const item = screen.getByTitle("Only one Quote Input or Workbench Input allowed per pipeline")
        expect(item).toHaveTextContent("Workbench Input")
        expect(item).toHaveAttribute("aria-disabled", "true")
        unmount()
      }
    })
  })

  it("sets effectAllowed to move on drag start", () => {
    render(<NodePalette />)
    const transformItem = screen.getByText("Transform").closest("[draggable]")!
    const dataTransfer = { setData: vi.fn(), effectAllowed: "" }
    fireEvent.dragStart(transformItem, { dataTransfer })
    expect(dataTransfer.effectAllowed).toBe("move")
  })
})
