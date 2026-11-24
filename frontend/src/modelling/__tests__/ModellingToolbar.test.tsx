/**
 * The toolbar while the Modelling view shows (specs/frontend-modelling-optimiser-ui): the
 * brand, Menu over Pipeline, Model Setup over Data Split, Features over Parameters, Training, and the
 * project's controls, with no training,
 * undo, redo, source or canvas action.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import ModellingToolbar from "../ModellingToolbar"
import useModellingViewStore, { type TrainAction } from "../../stores/useModellingViewStore"
import useUIStore from "../../stores/useUIStore"

vi.mock("../../components/BranchIndicator", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="branch-indicator">{children}</div>,
}))

const handlers = { onSave: vi.fn(), onCommit: vi.fn(), wsStatus: "connected" as const }

const renderToolbar = (overrides: Partial<Parameters<typeof ModellingToolbar>[0]> = {}) =>
  render(<ModellingToolbar {...handlers} {...overrides} />)

const action = (overrides: Partial<TrainAction> = {}): TrainAction => ({
  training: false,
  busy: false,
  issues: [],
  error: null,
  train: vi.fn(),
  cancel: vi.fn(),
  ...overrides,
})

describe("ModellingToolbar", () => {
  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    useModellingViewStore.setState({ modelId: null, panel: "features", trainAction: null })
    useUIStore.setState({ gitOpen: false, assistantOpen: false, filesPlace: null })
  })

  it("keeps the project's controls and drops undo, redo, the source, the pipeline and the canvas actions", () => {
    renderToolbar()
    const toolbar = screen.getByRole("toolbar", { name: "Modelling toolbar" })

    expect(within(toolbar).getByTestId("toolbar-brand")).toBeInTheDocument()
    for (const gone of ["toolbar-undo", "toolbar-redo", "source-selector", "toolbar-zoom-in", "toolbar-layout"]) {
      expect(within(toolbar).queryByTestId(gone)).toBeNull()
    }
    fireEvent.click(within(toolbar).getByRole("button", { name: /^save$/i }))
    fireEvent.click(within(toolbar).getByRole("button", { name: /commit/i }))
    expect(handlers.onSave).toHaveBeenCalledTimes(1)
    expect(handlers.onCommit).toHaveBeenCalledTimes(1)
  })

  it("stacks setup above split, features above parameters, and leaves training actions in the Training pane", () => {
    renderToolbar()
    const setup = screen.getByRole("group", { name: "Setup" })
    expect(within(setup).getAllByRole("button").map((button) => button.textContent)).toEqual(["Model Setup", "Data Split"])
    expect(setup).toHaveClass("toolbar-column")
    const model = screen.getByRole("group", { name: "Model" })
    expect(within(model).getAllByRole("button").map((button) => button.textContent)).toEqual(["Features", "Parameters"])
    for (const name of ["Model Setup", "Data Split", "Features", "Parameters", "Training"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled()
    }
    expect(screen.queryByRole("button", { name: "Train" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument()
  })

  it("puts the pipeline toolbar's Pipeline button under Menu, Offline while the server is away", () => {
    const { rerender } = renderToolbar()
    const mainMenu = screen.getByRole("button", { name: "Menu" })
    const pipeline = screen.getByTestId("toolbar-pipeline-settings")
    expect(mainMenu.parentElement).toContainElement(pipeline)
    // Straight after the brand, as the pipeline toolbar's Source over Pipeline column is.
    const toolbar = screen.getByRole("toolbar", { name: "Modelling toolbar" })
    expect(mainMenu.parentElement?.parentElement).toBe(toolbar)
    expect(mainMenu.parentElement?.previousElementSibling).toBe(within(toolbar).getByTestId("toolbar-brand"))
    expect(mainMenu.compareDocumentPosition(pipeline) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(pipeline).toHaveTextContent("Pipeline")
    expect(pipeline).toHaveAttribute("aria-haspopup", "dialog")
    rerender(<ModellingToolbar {...handlers} wsStatus="disconnected" />)
    expect(screen.getByTestId("toolbar-pipeline-settings")).toHaveTextContent("Offline")
  })

  it("returns to Menu from the toolbar even while read-only", () => {
    useModellingViewStore.setState({ modelId: "frequency", chosenModel: "m1", factor: "age" })
    renderToolbar({ editingDisabled: true })
    const mainMenu = screen.getByRole("button", { name: "Menu" })
    expect(mainMenu).toBeEnabled()
    fireEvent.click(mainMenu)
    expect(useModellingViewStore.getState().modelId).toBeNull()
    expect(useModellingViewStore.getState().chosenModel).toBeNull()
    expect(mainMenu).toBeDisabled()
  })

  it("picks the right-hand column's panel, one at a time, a toolbar panel giving way", () => {
    useModellingViewStore.setState({ modelId: "frequency" })
    useUIStore.setState({ gitOpen: true })
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Model Setup" }))
    expect(useModellingViewStore.getState().panel).toBe("setup")
    expect(useUIStore.getState().gitOpen).toBe(false)
    expect(screen.getByRole("button", { name: "Model Setup" })).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(screen.getByRole("button", { name: "Data Split" }))
    expect(useModellingViewStore.getState().panel).toBe("split")
    // Asking again closes it, as the pipeline's panel buttons do, and once more opens it.
    fireEvent.click(screen.getByRole("button", { name: "Data Split" }))
    expect(useModellingViewStore.getState().panel).toBeNull()
    expect(screen.getByRole("button", { name: "Data Split" })).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(screen.getByRole("button", { name: "Data Split" }))
    expect(useModellingViewStore.getState().panel).toBe("split")
    // Under the Assistant it is not showing, so asking brings it back rather than closing it.
    act(() => useUIStore.setState({ assistantOpen: true }))
    expect(screen.getByRole("button", { name: "Data Split" })).toHaveAttribute("aria-pressed", "false")
    fireEvent.click(screen.getByRole("button", { name: "Data Split" }))
    expect(useModellingViewStore.getState().panel).toBe("split")
    expect(useUIStore.getState().assistantOpen).toBe(false)
    for (const [name, panel] of [["Features", "features"], ["Parameters", "params"], ["Training", "train"]] as const) {
      fireEvent.click(screen.getByRole("button", { name }))
      expect(useModellingViewStore.getState().panel).toBe(panel)
      expect(screen.getByRole("button", { name })).toHaveAttribute("aria-pressed", "true")
    }
    expect(screen.getByRole("button", { name: "Model Setup" })).toHaveAttribute("aria-pressed", "false")
  })

  it("keeps training controls out of the toolbar while a stage runs", () => {
    useModellingViewStore.setState({ modelId: "frequency", trainAction: action() })
    renderToolbar()
    expect(screen.queryByRole("button", { name: "Train" })).not.toBeInTheDocument()
    act(() => useModellingViewStore.setState({ trainAction: action({ training: true, busy: true }) }))
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument()
  })

  it("trains and saves nothing while the pipeline cannot be edited", () => {
    useModellingViewStore.setState({ modelId: "frequency", trainAction: action() })
    renderToolbar({ editingDisabled: true })

    expect(screen.queryByRole("button", { name: "Train" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: /^save$/i })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Model Setup" })).toBeEnabled()
  })
})
