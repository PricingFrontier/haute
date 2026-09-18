import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"

vi.mock("../MlflowSettingsModal", () => ({
  default: () => <div data-testid="mlflow-modal-stub" />,
}))

import Toolbar from "../Toolbar"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
import useGitStore from "../../stores/useGitStore"

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
    useUIStore.setState({ mlflowSettingsOpen: false })
  })

  afterEach(cleanup)

  it("renders haute brand name with version centered underneath", () => {
    render(<Toolbar {...makeProps()} />)
    const brand = screen.getByTestId("toolbar-brand")
    expect(brand).toBeInTheDocument()

    const heading = screen.getByRole("heading", { level: 1, name: "haute" })
    const version = screen.getByText("v999.0.0-test")
    expect(brand).toContainElement(heading)
    expect(brand).toContainElement(version)

    // Heading and version are stacked in a centered column container
    expect(heading.parentElement).toHaveClass("flex-col", "items-center")
    // Heading precedes version in document order (brand on top, version underneath)
    expect(heading.compareDocumentPosition(version) & 4).toBeTruthy()
  })

  it("renders no MLflow control", () => {
    // The destination is a per-node choice now, so the toolbar carries no
    // MLflow control of its own — it only still mounts the settings modal for
    // the UI flag the node selectors set. Asserting on rendered text rather
    // than the retired chip's test id keeps the guard alive without naming a
    // symbol the codebase no longer has.
    render(<Toolbar {...makeProps()} />)
    expect(screen.queryByText(/mlflow/i)).toBeNull()
    expect(screen.queryByTestId("mlflow-modal-stub")).toBeNull()

    cleanup()
    useUIStore.setState({ mlflowSettingsOpen: true })
    render(<Toolbar {...makeProps()} />)
    expect(screen.queryByText(/mlflow/i)).toBeNull()
    expect(screen.getByTestId("mlflow-modal-stub")).toBeInTheDocument()
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

  it("renders Save and Commit underneath the branch indicator", () => {
    useGitStore.setState({
      status: {
        state: "ready",
        working_branch: "dev",
        clean: true,
        commits_ahead: 0,
        divergence_reason: null,
        storage: "unsupported",
        sync: null,
      } as never,
    })
    render(<Toolbar {...makeProps()} />)
    const branchIndicator = screen.getByTestId("toolbar-branch-indicator")
    const branchNameBtn = screen.getByTestId("branch-indicator-name")
    const saveBtn = screen.getByTestId("toolbar-save")
    const commitBtn = screen.getByTestId("toolbar-save-commit")

    expect(branchIndicator).toContainElement(saveBtn)
    expect(branchIndicator).toContainElement(commitBtn)

    const columnContainer = branchNameBtn.parentElement
    expect(columnContainer).toBeInTheDocument()
    expect(columnContainer).toHaveClass("flex-col")
    expect(columnContainer).toContainElement(saveBtn)
    expect(columnContainer).toContainElement(commitBtn)

    const saveCommitRow = saveBtn.parentElement
    expect(saveCommitRow).toHaveClass("w-full")
    expect(saveBtn).toHaveClass("flex-1")
    expect(commitBtn).toHaveClass("flex-1")
  })

  it("renders Assistant next to the branch name and Documentation underneath with equal width", () => {
    useGitStore.setState({
      status: {
        state: "ready",
        working_branch: "dev",
        clean: true,
        commits_ahead: 0,
        divergence_reason: null,
        storage: "unsupported",
        sync: null,
      } as never,
    })
    render(<Toolbar {...makeProps()} />)
    const assistantBtn = screen.getByTestId("toolbar-assistant")
    const docBtn = screen.getByTestId("toolbar-documentation")
    const branchIndicator = screen.getByTestId("toolbar-branch-indicator")

    expect(docBtn).toHaveAttribute("href", "https://pricingfrontier.github.io/haute/")
    expect(docBtn).toHaveAttribute("target", "_blank")
    expect(docBtn).toHaveAttribute("rel", "noopener noreferrer")
    expect(docBtn).toHaveTextContent(/documentation/i)

    const assistantColumn = assistantBtn.parentElement
    expect(assistantColumn).toBeInTheDocument()
    expect(assistantColumn).toHaveClass("flex-col")
    expect(assistantColumn).toContainElement(docBtn)
    expect(assistantBtn).toHaveClass("w-full")
    expect(docBtn).toHaveClass("w-full")

    expect(assistantColumn).not.toBeNull()
    expect(assistantColumn!.compareDocumentPosition(branchIndicator) & 4).toBeTruthy()
  })

  it("renders Centre on top of Layout in a column next to Submodel/Instance", () => {
    render(<Toolbar {...makeProps()} />)
    const centreBtn = screen.getByTestId("toolbar-centre")
    const layoutBtn = screen.getByTestId("toolbar-layout")
    const submodelBtn = screen.getByTestId("toolbar-submodel")

    const centreColumn = centreBtn.parentElement
    expect(centreColumn).toBeInTheDocument()
    expect(centreColumn).toHaveClass("flex-col")
    expect(centreColumn).toContainElement(layoutBtn)
    expect(centreBtn).toHaveClass("w-full")
    expect(layoutBtn).toHaveClass("w-full")

    expect(centreBtn.compareDocumentPosition(layoutBtn) & 4).toBeTruthy()

    const submodelColumn = submodelBtn.parentElement
    expect(centreColumn).not.toBeNull()
    expect(submodelColumn).not.toBeNull()
    expect(centreColumn!.compareDocumentPosition(submodelColumn!) & 4).toBeTruthy()
  })

  it("renders Zoom In on top of Zoom Out with text labels in a column next to Centre/Layout", () => {
    render(<Toolbar {...makeProps()} />)
    const zoomInBtn = screen.getByTestId("toolbar-zoom-in")
    const zoomOutBtn = screen.getByTestId("toolbar-zoom-out")
    const centreBtn = screen.getByTestId("toolbar-centre")

    const zoomColumn = zoomInBtn.parentElement
    expect(zoomColumn).toBeInTheDocument()
    expect(zoomColumn).toHaveClass("flex-col")
    expect(zoomColumn).toContainElement(zoomOutBtn)

    expect(zoomInBtn).toHaveTextContent(/zoom in/i)
    expect(zoomOutBtn).toHaveTextContent(/zoom out/i)
    expect(zoomInBtn).toHaveClass("w-full")
    expect(zoomOutBtn).toHaveClass("w-full")

    expect(zoomInBtn.compareDocumentPosition(zoomOutBtn) & 4).toBeTruthy()

    const centreColumn = centreBtn.parentElement
    expect(zoomColumn).not.toBeNull()
    expect(centreColumn).not.toBeNull()
    expect(zoomColumn!.compareDocumentPosition(centreColumn!) & 4).toBeTruthy()
  })

  it("renders Utility on top of Imports in a column next to Assistant/Documentation", () => {
    render(<Toolbar {...makeProps()} />)
    const utilityBtn = screen.getByTestId("toolbar-utility")
    const importsBtn = screen.getByTestId("toolbar-imports")
    const assistantBtn = screen.getByTestId("toolbar-assistant")

    const utilityColumn = utilityBtn.parentElement
    expect(utilityColumn).toBeInTheDocument()
    expect(utilityColumn).toHaveClass("flex-col")
    expect(utilityColumn).toContainElement(importsBtn)

    expect(utilityBtn).toHaveTextContent(/utility/i)
    expect(importsBtn).toHaveTextContent(/imports/i)
    expect(utilityBtn).toHaveClass("w-full")
    expect(importsBtn).toHaveClass("w-full")

    expect(utilityBtn.compareDocumentPosition(importsBtn) & 4).toBeTruthy()

    const assistantColumn = assistantBtn.parentElement
    expect(utilityColumn).not.toBeNull()
    expect(assistantColumn).not.toBeNull()
    expect(utilityColumn!.compareDocumentPosition(assistantColumn!) & 4).toBeTruthy()
  })

  it("renders Submodel on top of Instance in a column next to Utility/Imports", () => {
    render(<Toolbar {...makeProps()} />)
    const submodelBtn = screen.getByTestId("toolbar-submodel")
    const instanceBtn = screen.getByTestId("toolbar-instance")
    const utilityBtn = screen.getByTestId("toolbar-utility")

    const submodelColumn = submodelBtn.parentElement
    expect(submodelColumn).toBeInTheDocument()
    expect(submodelColumn).toHaveClass("flex-col")
    expect(submodelColumn).toContainElement(instanceBtn)

    expect(submodelBtn).toHaveTextContent(/submodel/i)
    expect(instanceBtn).toHaveTextContent(/instance/i)
    expect(submodelBtn).toHaveClass("w-full")
    expect(instanceBtn).toHaveClass("w-full")

    expect(submodelBtn.compareDocumentPosition(instanceBtn) & 4).toBeTruthy()

    const utilityColumn = utilityBtn.parentElement
    expect(submodelColumn).not.toBeNull()
    expect(utilityColumn).not.toBeNull()
    expect(submodelColumn!.compareDocumentPosition(utilityColumn!) & 4).toBeTruthy()
  })

  it("renders Timing on top of Memory in a stacked column", () => {
    render(<Toolbar {...makeProps()} />)
    const breakdownsContainer = screen.getByTestId("toolbar-breakdowns")
    expect(breakdownsContainer).toHaveClass("flex-col")

    const timingBtn = screen.getByTitle("Pipeline Timing")
    const memoryBtn = screen.getByTitle("Pipeline Memory")

    expect(breakdownsContainer).toContainElement(timingBtn)
    expect(breakdownsContainer).toContainElement(memoryBtn)
    expect(timingBtn.compareDocumentPosition(memoryBtn) & 4).toBeTruthy()
  })

  it("formats timing in milliseconds without decimal places and with a space before the unit", () => {
    const { rerender } = render(<Toolbar {...makeProps({ timings: [{ node_id: "n1", label: "Node 1", timing_ms: 42.4 }] })} />)
    expect(screen.getByText("42 ms")).toBeInTheDocument()

    rerender(<Toolbar {...makeProps({ timings: [{ node_id: "n1", label: "Node 1", timing_ms: 42.6 }] })} />)
    expect(screen.getByText("43 ms")).toBeInTheDocument()

    rerender(<Toolbar {...makeProps({ timings: [{ node_id: "n1", label: "Node 1", timing_ms: 0 }] })} />)
    expect(screen.getByText("0 ms")).toBeInTheDocument()

    rerender(<Toolbar {...makeProps({ timings: [{ node_id: "n1", label: "Node 1", timing_ms: 1500 }] })} />)
    expect(screen.getByText("1.50 s")).toBeInTheDocument()
  })

  it("renders Undo on top of Redo with text labels leading the right-hand action bar", () => {
    render(<Toolbar {...makeProps()} />)
    const undoRedoContainer = screen.getByTestId("toolbar-undo-redo")
    expect(undoRedoContainer).toHaveClass("flex-col")

    const undoBtn = screen.getByTestId("toolbar-undo")
    const redoBtn = screen.getByTestId("toolbar-redo")
    const zoomInBtn = screen.getByTestId("toolbar-zoom-in")

    expect(undoRedoContainer).toContainElement(undoBtn)
    expect(undoRedoContainer).toContainElement(redoBtn)

    expect(undoBtn).toHaveTextContent(/undo/i)
    expect(redoBtn).toHaveTextContent(/redo/i)
    expect(undoBtn).toHaveClass("w-full")
    expect(redoBtn).toHaveClass("w-full")

    expect(undoBtn.compareDocumentPosition(redoBtn) & 4).toBeTruthy()

    const zoomColumn = zoomInBtn.parentElement
    expect(undoRedoContainer.compareDocumentPosition(zoomColumn!) & 4).toBeTruthy()
  })

  it("renders Preview Rows on top of Chunk Rows in a stacked column to the left of Timing/Memory", () => {
    render(<Toolbar {...makeProps()} />)
    const rowsChunkContainer = screen.getByTestId("toolbar-rows-chunk")
    expect(rowsChunkContainer).toHaveClass("flex-col")

    const rowsInput = screen.getByLabelText(/preview rows/i)
    const chunkInput = screen.getByLabelText(/chunk rows/i)

    expect(rowsChunkContainer).toContainElement(rowsInput)
    expect(rowsChunkContainer).toContainElement(chunkInput)
    expect(rowsInput.compareDocumentPosition(chunkInput) & 4).toBeTruthy()

    const breakdownsContainer = screen.getByTestId("toolbar-breakdowns")
    expect(rowsChunkContainer.compareDocumentPosition(breakdownsContainer) & 4).toBeTruthy()
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

    expect(screen.getByTestId("toolbar-submodel")).toHaveAttribute("aria-disabled", "true")
    expect(screen.getByTestId("toolbar-instance")).toHaveAttribute("aria-disabled", "true")
  })

  it("still calls the handler when unavailable, so the refusal can explain itself", () => {
    const props = makeProps({ canCreateSubmodel: false, canCreateInstance: false })
    render(<Toolbar {...props} />)

    // ``can*`` drives presentation only. The handler owns the policy and
    // answers an unavailable click with a toast — swallowing the click here
    // would make the toolbar the one entry point that refuses in silence.
    fireEvent.click(screen.getByTestId("toolbar-submodel"))
    fireEvent.click(screen.getByTestId("toolbar-instance"))
    expect(props.onCreateSubmodel).toHaveBeenCalledOnce()
    expect(props.onCreateInstance).toHaveBeenCalledOnce()
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

  it("places the websocket status dot to the left of the unsaved indicator", () => {
    render(<Toolbar {...makeProps({ dirty: true, wsStatus: "connected" })} />)
    const wsDot = screen.getByTitle("Live sync connected")
    const unsavedDot = screen.getByTitle("Unsaved changes")
    expect(wsDot.compareDocumentPosition(unsavedDot) & 4).toBeTruthy()
  })

  it("reserves space for the unsaved indicator when clean to prevent layout shift", () => {
    const { container } = render(<Toolbar {...makeProps({ dirty: false })} />)
    const unsavedSlot = container.querySelector(".w-1\\.5.h-1\\.5")
    expect(unsavedSlot).toBeInTheDocument()
    expect(unsavedSlot).toHaveClass("invisible")
  })

  it("centers the status dots at the toolbar row-gap level without bottom alignment", () => {
    render(<Toolbar {...makeProps({ dirty: true })} />)
    const dotsContainer = screen.getByTestId("toolbar-status-dots")
    expect(dotsContainer).toBeInTheDocument()
    expect(dotsContainer).not.toHaveClass("self-end")
  })

  function getRowLimitInput(): HTMLInputElement {
    return screen.getByLabelText(/preview rows/i) as HTMLInputElement
  }

  function getChunkInput(): HTMLInputElement {
    return screen.getByLabelText(/chunk rows/i) as HTMLInputElement
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

  it("sizes numeric fields with synchronized width for complete configured values and valid maximum", () => {
    useSettingsStore.setState({ rowLimit: 125_000, streamingChunkSize: 10_000_000 })
    render(<Toolbar {...makeProps()} />)

    expect(getRowLimitInput()).toHaveStyle({ width: "calc(8ch + 16px)" })
    expect(getChunkInput()).toHaveStyle({ width: "calc(8ch + 16px)" })
  })

  it("expands both numeric fields together when either value exceeds 8 digits", () => {
    useSettingsStore.setState({ rowLimit: 125_000_000, streamingChunkSize: 500_000 })
    const { unmount } = render(<Toolbar {...makeProps()} />)

    expect(getRowLimitInput()).toHaveStyle({ width: "calc(9ch + 16px)" })
    expect(getChunkInput()).toHaveStyle({ width: "calc(9ch + 16px)" })
    unmount()

    useSettingsStore.setState({ rowLimit: 1_000, streamingChunkSize: 100_000_000 })
    render(<Toolbar {...makeProps()} />)

    expect(getRowLimitInput()).toHaveStyle({ width: "calc(9ch + 16px)" })
    expect(getChunkInput()).toHaveStyle({ width: "calc(9ch + 16px)" })
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

  it("positions Source text directly after brand width aligned to node toolbar width (x + 1)", () => {
    render(<Toolbar {...makeProps()} />)
    const brand = screen.getByTestId("toolbar-brand")
    expect(brand).toHaveClass("w-[165px]")

    const sourceLabel = screen.getByText("Source")
    const sourceContainer = sourceLabel.parentElement
    expect(sourceContainer).not.toHaveClass("ml-12")
    // Brand container immediately precedes sourceContainer in document order
    expect(brand.nextElementSibling).toBe(sourceContainer)
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
