import { describe, it, expect, vi, afterEach } from "vitest"
import { act, render, screen, fireEvent, cleanup } from "@testing-library/react"
import NodePalette from "../NodePalette"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
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

  describe("while the project's workbench is enabled", () => {
    const tables = [{ path: "$[:]", label: "policy_details", emit: true, row_id_column: null, columns: [] }]
    const responseTables = [{ path: "$[:]", label: "pricing_output", emit: true, row_id_column: null, columns: [] }]
    const dragConfig = (name: string, type: string) => {
      const setData = vi.fn()
      fireEvent.dragStart(screen.getByText(name).closest("[draggable]")!, {
        dataTransfer: { setData, effectAllowed: "" },
      })
      expect(setData).toHaveBeenCalledWith("application/reactflow-type", type)
      const [, json] = setData.mock.calls.find(([format]) => format === "application/reactflow-config")!
      return JSON.parse(json)
    }
    afterEach(() => useWorkbenchStore.setState({ enabled: false, tables: null }))

    it("shows the Workbench Input and Workbench Output in the Quote Input's and Quote Response's places, and those otherwise", () => {
      render(<NodePalette />)
      expect(screen.getByText("Quote Input")).toBeInTheDocument()
      expect(screen.getByText("Quote Response")).toBeInTheDocument()
      expect(screen.queryByText("Workbench Input")).not.toBeInTheDocument()
      expect(screen.queryByText("Workbench Output")).not.toBeInTheDocument()

      act(() => useWorkbenchStore.setState({ enabled: true }))
      expect(screen.getByText("Workbench Input")).toBeInTheDocument()
      expect(screen.getByText("Workbench Output")).toBeInTheDocument()
      expect(screen.queryByText("Quote Input")).not.toBeInTheDocument()
      expect(screen.queryByText("Quote Response")).not.toBeInTheDocument()
      const items = [...document.querySelectorAll("[data-testid^='node-palette-item-']")]
      expect(items[0]).toHaveAttribute("data-testid", `node-palette-item-${NODE_TYPES.WORKBENCH_INPUT}`)
    })

    it("drags a Workbench Input with the newest tables and sample fetched, or none until they are", () => {
      useWorkbenchStore.setState({ enabled: true, tables: null })
      render(<NodePalette />)
      expect(dragConfig("Workbench Input", NODE_TYPES.WORKBENCH_INPUT)).toEqual({ tables: [], sample: {} })

      const sample = { policy_details: { state: "NY" } }
      act(() => useWorkbenchStore.setState({ tables: { tables, sample, responseTables: [], fetch: 1 } }))
      expect(dragConfig("Workbench Input", NODE_TYPES.WORKBENCH_INPUT)).toEqual({ tables, sample })
    })

    it("drags a Workbench Output with the newest response tables fetched, or none until they are", () => {
      useWorkbenchStore.setState({ enabled: true, tables: null })
      render(<NodePalette />)
      expect(dragConfig("Workbench Output", NODE_TYPES.WORKBENCH_OUTPUT)).toEqual({ tables: [] })

      act(() => useWorkbenchStore.setState({ tables: { tables: [], sample: {}, responseTables, fetch: 1 } }))
      expect(dragConfig("Workbench Output", NODE_TYPES.WORKBENCH_OUTPUT)).toEqual({ tables: responseTables })
    })

    it("greys out the Workbench Input while the pipeline has either request input", () => {
      useWorkbenchStore.setState({ enabled: true })
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

    it("greys out the Workbench Output while the pipeline has either response node", () => {
      useWorkbenchStore.setState({ enabled: true })
      for (const present of [NODE_TYPES.OUTPUT, NODE_TYPES.WORKBENCH_OUTPUT]) {
        const { unmount } = render(
          <NodePalette existingSingletonTypes={new Set([present, NODE_TYPES.WORKBENCH_OUTPUT])} />,
        )
        const item = screen.getByTitle("Only one Quote Response or Workbench Output allowed per pipeline")
        expect(item).toHaveTextContent("Workbench Output")
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
