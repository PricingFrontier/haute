import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import Toolbar from "../Toolbar"
import useSettingsStore from "../../stores/useSettingsStore"

function makeProps(overrides: Partial<Parameters<typeof Toolbar>[0]> = {}) {
  return {
    nodeCount: 5,
    dirty: false,
    canUndo: true,
    canRedo: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onOpenUtility: vi.fn(),
    onOpenImports: vi.fn(),
    canCreateSubmodel: true,
    onCreateSubmodel: vi.fn(),
    canCreateInstance: true,
    onCreateInstance: vi.fn(),
    onCentre: vi.fn(),
    onAutoLayout: vi.fn(),
    isAutoLayouting: false,
    onSave: vi.fn(),
    onSaveCommit: vi.fn(),
    wsStatus: "connected" as const,
    ...overrides,
  }
}

describe("Toolbar", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      rowLimit: 1000,
      streamingChunkSize: 500_000,
      sources: ["live"],
      activeSource: "live",
    })
  })

  afterEach(cleanup)

  it("renders Haute brand name", () => {
    render(<Toolbar {...makeProps()} />)
    expect(screen.getByText("Haute")).toBeInTheDocument()
  })

  it("renders the package-derived browser version", () => {
    render(<Toolbar {...makeProps()} />)
    expect(screen.getByText("v999.0.0-test")).toBeInTheDocument()
  })

  it("clicking Save calls onSave", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Save"))
    expect(props.onSave).toHaveBeenCalledOnce()
  })

  it("clicking Commit calls onSaveCommit", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    // Commit is a plain sibling of Save now — no split-button menu to open.
    expect(screen.queryByTestId("toolbar-save-menu")).toBeNull()
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))
    expect(props.onSaveCommit).toHaveBeenCalledOnce()
  })

  it("Layout button is disabled when nodeCount is 0", () => {
    render(<Toolbar {...makeProps({ nodeCount: 0 })} />)
    // The label lives in a span inside the button (it's overlaid by the busy
    // spinner), so target the button itself rather than the text node.
    const layoutBtn = screen.getByTestId("toolbar-layout")
    expect(layoutBtn).toHaveTextContent("Layout")
    expect(layoutBtn).toBeDisabled()
  })

  it("clicking Layout calls onAutoLayout", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-layout"))
    expect(props.onAutoLayout).toHaveBeenCalledOnce()
  })

  it("shows a busy disabled Layout button while auto-layout is running", () => {
    const props = makeProps({ isAutoLayouting: true })
    render(<Toolbar {...props} />)
    const layoutBtn = screen.getByRole("button", { name: /laying out/i })

    expect(layoutBtn).toBeDisabled()
    expect(layoutBtn).toHaveAttribute("aria-busy", "true")
    // The visible label is width-stable across states; only the accessible
    // name and the spinner change while running.
    expect(layoutBtn).toHaveTextContent("Layout")

    fireEvent.click(layoutBtn)
    expect(props.onAutoLayout).not.toHaveBeenCalled()
  })

  it("clicking Submodel groups the selection", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-submodel"))
    expect(props.onCreateSubmodel).toHaveBeenCalledOnce()
  })

  it("clicking Instance creates an instance of the selection", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-instance"))
    expect(props.onCreateInstance).toHaveBeenCalledOnce()
  })

  it("greys out the selection actions when the selection cannot support them", () => {
    const props = makeProps({ canCreateSubmodel: false, canCreateInstance: false })
    render(<Toolbar {...props} />)

    const submodel = screen.getByTestId("toolbar-submodel")
    const instance = screen.getByTestId("toolbar-instance")
    expect(submodel).toHaveAttribute("aria-disabled", "true")
    expect(instance).toHaveAttribute("aria-disabled", "true")

    fireEvent.click(submodel)
    fireEvent.click(instance)
    expect(props.onCreateSubmodel).not.toHaveBeenCalled()
    expect(props.onCreateInstance).not.toHaveBeenCalled()
  })

  it("keeps unavailable selection actions reachable so they can explain themselves", () => {
    render(<Toolbar {...makeProps({ canCreateSubmodel: false, canCreateInstance: false })} />)
    const submodel = screen.getByTestId("toolbar-submodel")
    // Not the `disabled` attribute: that would drop the button from the tab
    // order and suppress the title that states the requirement.
    expect(submodel).not.toBeDisabled()
    expect(submodel).toHaveAttribute("title", expect.stringContaining("select 2 or more"))
  })

  it("enables the two selection actions independently", () => {
    render(<Toolbar {...makeProps({ canCreateSubmodel: false, canCreateInstance: true })} />)
    // One node selected: instancing works, grouping needs a second node.
    expect(screen.getByTestId("toolbar-submodel")).toHaveAttribute("aria-disabled", "true")
    expect(screen.getByTestId("toolbar-instance")).toHaveAttribute("aria-disabled", "false")
  })

  it("places the selection actions to the left of Utility", () => {
    render(<Toolbar {...makeProps()} />)
    const order = [
      screen.getByTestId("toolbar-submodel"),
      screen.getByTestId("toolbar-instance"),
      screen.getByTestId("toolbar-utility"),
    ]
    for (let i = 0; i < order.length - 1; i += 1) {
      // Node.DOCUMENT_POSITION_FOLLOWING === 4
      expect(order[i].compareDocumentPosition(order[i + 1]) & 4).toBeTruthy()
    }
  })

  it("disables graph mutation controls in a read-only instance", () => {
    const props = makeProps({ editingDisabled: true, canUndo: true, canRedo: true })
    render(<Toolbar {...props} />)

    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-redo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-layout")).toBeDisabled()
    expect(screen.getByTestId("toolbar-utility")).toBeDisabled()
    expect(screen.getByTestId("toolbar-imports")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeDisabled()
    fireEvent.click(screen.getByTestId("toolbar-layout"))
    fireEvent.click(screen.getByTestId("toolbar-utility"))
    fireEvent.click(screen.getByTestId("toolbar-imports"))
    expect(props.onAutoLayout).not.toHaveBeenCalled()
    expect(props.onOpenUtility).not.toHaveBeenCalled()
    expect(props.onOpenImports).not.toHaveBeenCalled()
  })

  it("Centre button is disabled when nodeCount is 0", () => {
    render(<Toolbar {...makeProps({ nodeCount: 0 })} />)
    const centreBtn = screen.getByText("Centre")
    expect(centreBtn).toBeDisabled()
  })

  it("clicking Centre calls onCentre", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Centre"))
    expect(props.onCentre).toHaveBeenCalledOnce()
  })

  it("clicking Imports calls onOpenImports", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Imports"))
    expect(props.onOpenImports).toHaveBeenCalledOnce()
  })

  it("clicking Utility calls onOpenUtility", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Utility"))
    expect(props.onOpenUtility).toHaveBeenCalledOnce()
  })

  it("undo button calls onUndo", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    // Find by title
    const undoBtn = screen.getByTitle("Undo (Ctrl+Z)")
    fireEvent.click(undoBtn)
    expect(props.onUndo).toHaveBeenCalledOnce()
  })

  it("undo button has an accessible name", () => {
    render(<Toolbar {...makeProps()} />)
    expect(screen.getByRole("button", { name: "Undo" })).toBeInTheDocument()
  })

  it("redo button is disabled when canRedo is false", () => {
    render(<Toolbar {...makeProps({ canRedo: false })} />)
    const redoBtn = screen.getByLabelText("Redo")
    expect(redoBtn).toBeDisabled()
  })

  it("shows unsaved indicator when dirty", () => {
    render(<Toolbar {...makeProps({ dirty: true })} />)
    expect(screen.getByTitle("Unsaved changes")).toBeInTheDocument()
  })

  function getRowLimitInput(): HTMLInputElement {
    return screen.getByLabelText("Rows") as HTMLInputElement
  }

  function getChunkInput(): HTMLInputElement {
    return screen.getByLabelText("Chunk") as HTMLInputElement
  }

  it("row limit input changes the store value", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "500" } })
    expect(useSettingsStore.getState().rowLimit).toBe(500)
  })

  it("row limit clamps negative values to 0", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "-50" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit treats NaN input as 0", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getRowLimitInput(), { target: { value: "abc" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit input shows current store value", () => {
    useSettingsStore.setState({ rowLimit: 2000 })
    render(<Toolbar {...makeProps()} />)
    expect(getRowLimitInput().value).toBe("2000")
  })

  it("chunk input renders with the current streaming chunk size", () => {
    useSettingsStore.setState({ streamingChunkSize: 250_000 })
    render(<Toolbar {...makeProps()} />)
    expect(getChunkInput().value).toBe("250000")
  })

  it("chunk input updates the streaming chunk size in the store", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getChunkInput(), { target: { value: "100000" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(100_000)
  })

  it("chunk input clamps sub-1000 values up to 1000", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getChunkInput(), { target: { value: "5" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(1000)
  })

  it("chunk input ignores non-numeric input (no setter call, value preserved)", () => {
    useSettingsStore.setState({ streamingChunkSize: 250_000 })
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getChunkInput(), { target: { value: "abc" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(250_000)
  })

  it("chunk input accepts scientific notation (5e5 -> 500000)", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getChunkInput(), { target: { value: "5e5" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(500_000)
  })

  it("chunk input clamps over-max values to the backend bound (10_000_000)", () => {
    render(<Toolbar {...makeProps()} />)
    fireEvent.change(getChunkInput(), { target: { value: "15000000" } })
    expect(useSettingsStore.getState().streamingChunkSize).toBe(10_000_000)
  })

  it("chunk input has max attribute matching the backend bound", () => {
    render(<Toolbar {...makeProps()} />)
    expect(getChunkInput()).toHaveAttribute("max", "10000000")
  })

  it("zoom in button calls onZoomIn", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    const btn = screen.getByLabelText("Zoom in")
    fireEvent.click(btn)
    expect(props.onZoomIn).toHaveBeenCalledOnce()
  })

  it("zoom out button calls onZoomOut", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    const btn = screen.getByLabelText("Zoom out")
    fireEvent.click(btn)
    expect(props.onZoomOut).toHaveBeenCalledOnce()
  })

  it("undo button is disabled when canUndo is false", () => {
    render(<Toolbar {...makeProps({ canUndo: false })} />)
    const undoBtn = screen.getByTitle("Undo (Ctrl+Z)")
    expect(undoBtn).toBeDisabled()
  })

  it("redo button is enabled when canRedo is true", () => {
    render(<Toolbar {...makeProps({ canRedo: true })} />)
    const redoBtn = screen.getByLabelText("Redo")
    expect(redoBtn).not.toBeDisabled()
  })

  it("redo button calls onRedo when clicked", () => {
    const props = makeProps({ canRedo: true })
    render(<Toolbar {...props} />)
    const redoBtn = screen.getByLabelText("Redo")
    fireEvent.click(redoBtn)
    expect(props.onRedo).toHaveBeenCalledOnce()
  })

  it("shows websocket connected status dot", () => {
    render(<Toolbar {...makeProps({ wsStatus: "connected" })} />)
    const dot = screen.getByTitle("Live sync connected")
    expect(dot).toBeInTheDocument()
  })

  it("shows websocket reconnecting status dot", () => {
    render(<Toolbar {...makeProps({ wsStatus: "reconnecting" })} />)
    const dot = screen.getByTitle("Reconnecting to server\u2026")
    expect(dot).toBeInTheDocument()
  })

  it("shows websocket disconnected status dot", () => {
    render(<Toolbar {...makeProps({ wsStatus: "disconnected" })} />)
    const dot = screen.getByTitle("Server unreachable \u2014 restart haute serve")
    expect(dot).toBeInTheDocument()
  })

  it("does not show unsaved indicator when not dirty", () => {
    render(<Toolbar {...makeProps({ dirty: false })} />)
    expect(screen.queryByTitle("Unsaved changes")).not.toBeInTheDocument()
  })

  it("source selector shows active source on trigger button", () => {
    render(<Toolbar {...makeProps()} />)
    const trigger = screen.getByTitle("Data source")
    expect(trigger.textContent).toContain("live")
  })

  it("source selector shows all sources when opened", () => {
    useSettingsStore.setState({ sources: ["live", "test_scenario"], activeSource: "live" })
    render(<Toolbar {...makeProps()} />)
    fireEvent.click(screen.getByTitle("Data source"))
    // "live" appears in trigger + dropdown item, so check both exist
    expect(screen.getAllByText("live").length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText("test_scenario")).toBeInTheDocument()
  })

  it("switching source updates store", () => {
    useSettingsStore.setState({ sources: ["live", "test_scenario"], activeSource: "live" })
    render(<Toolbar {...makeProps()} />)
    fireEvent.click(screen.getByTitle("Data source"))
    fireEvent.click(screen.getByText("test_scenario"))
    expect(useSettingsStore.getState().activeSource).toBe("test_scenario")
  })

  it("shows remove option for non-live sources", () => {
    useSettingsStore.setState({ sources: ["live", "test_scenario"], activeSource: "test_scenario" })
    render(<Toolbar {...makeProps()} />)
    fireEvent.click(screen.getByTitle("Data source"))
    expect(screen.getByText(/Remove "test_scenario"/)).toBeInTheDocument()
  })

  it("does not show remove option when live is active", () => {
    useSettingsStore.setState({ sources: ["live"], activeSource: "live" })
    render(<Toolbar {...makeProps()} />)
    fireEvent.click(screen.getByTitle("Data source"))
    expect(screen.queryByText(/Remove/)).not.toBeInTheDocument()
  })
})
