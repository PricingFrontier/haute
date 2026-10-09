/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand, the form's
 * Undo and Redo, and the project's controls with Save saving the form and no Commit.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import WorkbenchToolbar from "../WorkbenchToolbar"

vi.mock("../../components/BranchIndicator", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="branch-indicator">{children}</div>,
}))

const blank: FormSpec = {
  version: 1,
  name: "motor",
  schema: { tables: [] },
  pages: [{ id: "page_1", title: "Sheet 1", widgets: [] }],
  sample: {},
}

describe("WorkbenchToolbar", () => {
  const actions = { save: vi.fn(async () => true), undo: vi.fn(), redo: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ status: "ready", saving: false, undoStack: [], redoStack: [], ...actions })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("has the brand, Undo and Redo, and the project's controls with Save but no Commit", () => {
    render(<WorkbenchToolbar />)

    expect(screen.getByRole("toolbar", { name: "Workbench toolbar" })).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-brand")).toHaveTextContent("haute")
    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-redo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeEnabled()
    expect(screen.getByTestId("toolbar-help")).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-save")).toBeEnabled()
    expect(screen.queryByTestId("toolbar-save-commit")).not.toBeInTheDocument()
  })

  it("saves the form, and keeps Save off while the form loads or saves", () => {
    const { rerender } = render(<WorkbenchToolbar />)
    fireEvent.click(screen.getByTestId("toolbar-save"))
    expect(actions.save).toHaveBeenCalledTimes(1)

    useWorkbenchFormStore.setState({ saving: true })
    rerender(<WorkbenchToolbar />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()

    useWorkbenchFormStore.setState({ saving: false, status: "loading" })
    rerender(<WorkbenchToolbar />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeEnabled()
  })

  it("undoes and redoes the form's history", () => {
    useWorkbenchFormStore.setState({ undoStack: [blank], redoStack: [blank] })
    render(<WorkbenchToolbar />)

    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))

    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(actions.redo).toHaveBeenCalledTimes(1)
  })
})
