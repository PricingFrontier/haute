import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"
import Toolbar from "../Toolbar"
import useSettingsStore from "../../stores/useSettingsStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode } from "../../test-utils/factories"

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
    onRun: vi.fn(),
    runBusy: false,
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

  it("clicking Save calls onSave", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByText("Save"))
    expect(props.onSave).toHaveBeenCalledOnce()
  })

  it("clicking Run runs the default mode", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-run-button"))
    expect(props.onRun).toHaveBeenCalledWith("default")
  })

  it("the run dropdown offers the other three modes", () => {
    const props = makeProps()
    render(<Toolbar {...props} />)
    fireEvent.click(screen.getByTestId("toolbar-run-menu"))
    fireEvent.click(screen.getByTestId("toolbar-run-all-export"))
    expect(props.onRun).toHaveBeenCalledWith("all-export")
  })

  it("shows Running and disables Run while a run is in flight", () => {
    render(<Toolbar {...makeProps({ runBusy: true })} />)
    const runBtn = screen.getByTestId("toolbar-run-button")
    expect(runBtn).toBeDisabled()
    expect(runBtn).toHaveTextContent("Running")
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

  // ── Save button dirty-state surfacing (Nick 2026-06-12) ─────────────────
  // The Save button visually dulls when the pipeline is NOT dirty (matches
  // last save) and is prominent when dirty. Purely visual: the button stays
  // clickable in both states (NOT disabled). State is asserted via the
  // data-dirty attribute (not brittle computed style) plus the opacity class.

  it("Save button reflects clean state with data-dirty=false and dulled class", () => {
    render(<Toolbar {...makeProps({ dirty: false })} />)
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(saveBtn).toHaveAttribute("data-dirty", "false")
    expect(saveBtn.className).toContain("opacity-40")
  })

  it("Save button reflects dirty state with data-dirty=true and no dulled class", () => {
    render(<Toolbar {...makeProps({ dirty: true })} />)
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(saveBtn).toHaveAttribute("data-dirty", "true")
    expect(saveBtn.className).not.toContain("opacity-40")
  })

  it("Save button stays clickable when not dirty (never disabled)", () => {
    const props = makeProps({ dirty: false })
    render(<Toolbar {...props} />)
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(saveBtn).not.toBeDisabled()
    fireEvent.click(saveBtn)
    expect(props.onSave).toHaveBeenCalledOnce()
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

// ───────────────────────────────────────────────────────────────────────────
// Save-button dirty surfacing — driven through the REAL useGraphStore, not a
// mocked prop, so the editor→store→button chain is exercised end to end (per
// §UI Test Assertions: drive a non-mocked store action, assert at the rendered
// boundary). A tiny wrapper subscribes to useGraphStore.dirty exactly as
// App.tsx does and feeds it into <Toolbar>, mirroring production wiring.

function resetGraphStore() {
  useGraphStore.setState({
    nodes: [],
    edges: [],
    preamble: "",
    lastSavedSnapshot: null,
    undoStack: [],
    redoStack: [],
    structuralVersion: 0,
    structuralFingerprint: "nodes:||edges:||preamble:\"\"",
    panelContextVersion: 0,
    panelContextFingerprint: "nodes:||edges:",
    persistedFingerprint: "nodes:[]|edges:[]|preamble:\"\"",
    savedPersistedFingerprint: null,
    dirty: false,
  })
}

function StoreWiredToolbar() {
  const dirty = useGraphStore((s) => s.dirty)
  return (
    <Toolbar
      nodeCount={1}
      dirty={dirty}
      canUndo={false}
      canRedo={false}
      onUndo={vi.fn()}
      onRedo={vi.fn()}
      onZoomIn={vi.fn()}
      onZoomOut={vi.fn()}
      onOpenUtility={vi.fn()}
      onOpenImports={vi.fn()}
      onOpenGit={vi.fn()}
      onCentre={vi.fn()}
      onAutoLayout={vi.fn()}
      isAutoLayouting={false}
      onSave={vi.fn()}
      wsStatus="connected"
    />
  )
}

describe("Toolbar Save button — dirty surfacing via real useGraphStore", () => {
  beforeEach(() => {
    useSettingsStore.setState({
      rowLimit: 1000,
      streamingChunkSize: 500_000,
      sources: ["live"],
      activeSource: "live",
    })
    resetGraphStore()
  })

  afterEach(cleanup)

  it("clean store renders the Save button dulled (data-dirty=false)", () => {
    render(<StoreWiredToolbar />)
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(saveBtn).toHaveAttribute("data-dirty", "false")
    expect(saveBtn.className).toContain("opacity-40")
  })

  it("a non-mocked graph mutation flips the Save button to active (data-dirty=true)", () => {
    render(<StoreWiredToolbar />)
    // Real store action — adds a node, which computeDirty marks dirty.
    act(() => {
      useGraphStore.getState().setNodes([makeNode("n1")])
    })
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(saveBtn).toHaveAttribute("data-dirty", "true")
    expect(saveBtn.className).not.toContain("opacity-40")
  })

  it("a save round-trip (markSaved) returns the Save button to dulled", () => {
    render(<StoreWiredToolbar />)
    act(() => {
      useGraphStore.getState().setNodes([makeNode("n1")])
    })
    expect(screen.getByTestId("toolbar-save")).toHaveAttribute("data-dirty", "true")
    // markSaved() is exactly what handleSave() invokes after a successful save.
    act(() => {
      useGraphStore.getState().markSaved()
    })
    const saveBtn = screen.getByTestId("toolbar-save")
    expect(useGraphStore.getState().dirty).toBe(false)
    expect(saveBtn).toHaveAttribute("data-dirty", "false")
    expect(saveBtn.className).toContain("opacity-40")
  })
})
