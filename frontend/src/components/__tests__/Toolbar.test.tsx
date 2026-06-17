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
    onOpenGit: vi.fn(),
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
      sources: ["live"],
      activeSource: "live",
    })
  })

  afterEach(cleanup)

  it("renders Haute brand name", () => {
    render(<Toolbar {...makeProps()} />)
    expect(screen.getByText("Haute")).toBeInTheDocument()
  })

  it("clicking Save calls onSave", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Save"))
    expect(props.onSave).toHaveBeenCalledOnce()
  })

  it("clicking Save & commit (in the save dropdown) calls onSaveCommit", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-save-menu")) // open the split-button menu
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))
    expect(props.onSaveCommit).toHaveBeenCalledOnce()
  })

  it("Layout button is disabled when nodeCount is 0", () => {
    render(<Toolbar {...makeProps({ nodeCount: 0 })} />)
    const layoutBtn = screen.getByText("Layout")
    expect(layoutBtn).toBeDisabled()
  })

  it("clicking Layout calls onAutoLayout", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Layout"))
    expect(props.onAutoLayout).toHaveBeenCalledOnce()
  })

  it("shows a busy disabled Layout button while auto-layout is running", () => {
    const props = makeProps({ isAutoLayouting: true })
    render(<Toolbar {...props} />)
    const layoutBtn = screen.getByRole("button", { name: /laying out/i })

    expect(layoutBtn).toBeDisabled()
    expect(layoutBtn).toHaveAttribute("aria-busy", "true")

    fireEvent.click(layoutBtn)
    expect(props.onAutoLayout).not.toHaveBeenCalled()
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

  it("row limit input changes the store value", () => {
    render(<Toolbar {...makeProps()} />)
    const input = screen.getByRole("spinbutton")
    fireEvent.change(input, { target: { value: "500" } })
    expect(useSettingsStore.getState().rowLimit).toBe(500)
  })

  it("row limit clamps negative values to 0", () => {
    render(<Toolbar {...makeProps()} />)
    const input = screen.getByRole("spinbutton")
    fireEvent.change(input, { target: { value: "-50" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit treats NaN input as 0", () => {
    render(<Toolbar {...makeProps()} />)
    const input = screen.getByRole("spinbutton")
    fireEvent.change(input, { target: { value: "abc" } })
    expect(useSettingsStore.getState().rowLimit).toBe(0)
  })

  it("row limit input shows current store value", () => {
    useSettingsStore.setState({ rowLimit: 2000 })
    render(<Toolbar {...makeProps()} />)
    const input = screen.getByRole("spinbutton") as HTMLInputElement
    expect(input.value).toBe("2000")
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
