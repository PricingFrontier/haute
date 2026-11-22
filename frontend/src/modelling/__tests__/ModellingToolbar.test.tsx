/**
 * The toolbar while the Modelling view shows (specs/frontend-modelling-optimiser-ui): the
 * brand, Model Setup over Training Split and the project's controls, with no training,
 * undo, redo, source, pipeline
 * or canvas action.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import ModellingToolbar from "../ModellingToolbar"
import useModellingViewStore, { type TrainAction } from "../../stores/useModellingViewStore"
import useUIStore from "../../stores/useUIStore"

vi.mock("../../components/BranchIndicator", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="branch-indicator">{children}</div>,
}))

const handlers = { onSave: vi.fn(), onCommit: vi.fn() }

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
    useModellingViewStore.setState({ modelId: null, panel: "model", trainAction: null })
    useUIStore.setState({ gitOpen: false, assistantOpen: false, filesPlace: null })
  })

  it("keeps the project's controls and drops undo, redo, the source, the pipeline and the canvas actions", () => {
    renderToolbar()
    const toolbar = screen.getByRole("toolbar", { name: "Modelling toolbar" })

    expect(within(toolbar).getByTestId("toolbar-brand")).toBeInTheDocument()
    for (const gone of ["toolbar-undo", "toolbar-redo", "source-selector", "toolbar-pipeline-settings", "toolbar-zoom-in", "toolbar-layout"]) {
      expect(within(toolbar).queryByTestId(gone)).toBeNull()
    }
    fireEvent.click(within(toolbar).getByRole("button", { name: /^save$/i }))
    fireEvent.click(within(toolbar).getByRole("button", { name: /commit/i }))
    expect(handlers.onSave).toHaveBeenCalledTimes(1)
    expect(handlers.onCommit).toHaveBeenCalledTimes(1)
  })

  it("stacks setup above split and leaves training actions in the Training pane", () => {
    renderToolbar()
    const setup = screen.getByRole("group", { name: "Setup" })
    expect(within(setup).getAllByRole("button").map((button) => button.textContent)).toEqual(["Model Setup", "Training Split"])
    expect(setup).toHaveClass("toolbar-column")
    expect(screen.getByRole("button", { name: "Model Setup" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Training Split" })).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Train" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument()
  })

  it("returns to Main Menu from the toolbar even while read-only", () => {
    useModellingViewStore.setState({ modelId: "frequency", chosenModel: "m1", factor: "age" })
    renderToolbar({ editingDisabled: true })
    const mainMenu = screen.getByRole("button", { name: "Main Menu" })
    expect(mainMenu).toBeEnabled()
    fireEvent.click(mainMenu)
    expect(useModellingViewStore.getState().modelId).toBeNull()
    expect(useModellingViewStore.getState().chosenModel).toBeNull()
    expect(mainMenu).toBeDisabled()
  })

  it("opens Model Setup and Training Split in the right-hand column, one at a time, a toolbar panel giving way", () => {
    useModellingViewStore.setState({ modelId: "frequency" })
    useUIStore.setState({ gitOpen: true })
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Model Setup" }))
    expect(useModellingViewStore.getState().panel).toBe("setup")
    expect(useUIStore.getState().gitOpen).toBe(false)
    expect(screen.getByRole("button", { name: "Model Setup" })).toHaveAttribute("aria-pressed", "true")

    fireEvent.click(screen.getByRole("button", { name: "Training Split" }))
    expect(useModellingViewStore.getState().panel).toBe("split")
    // Asking again goes back to the chosen model's settings.
    fireEvent.click(screen.getByRole("button", { name: "Training Split" }))
    expect(useModellingViewStore.getState().panel).toBe("model")
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
