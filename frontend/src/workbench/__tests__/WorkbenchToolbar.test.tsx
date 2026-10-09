/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand, the sections,
 * the form's Undo and Redo, the sheet's zoom, and the project's controls with Save saving
 * the form and Commit running the host's milestone flow.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
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

const onCommit = vi.fn()
const renderToolbar = () => render(<WorkbenchToolbar onCommit={onCommit} />)

describe("WorkbenchToolbar", () => {
  const actions = { save: vi.fn(async () => true), undo: vi.fn(), redo: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ status: "ready", saving: false, undoStack: [], redoStack: [], ...actions })
    useWorkbenchViewStore.setState({ section: "sheets", zoom: 1 })
    useWorkbenchPricingStore.setState({ error: null })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("has the brand, the sections, Undo and Redo, the zoom, and the project's controls with Save and Commit", () => {
    renderToolbar()

    expect(screen.getByRole("toolbar", { name: "Workbench toolbar" })).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-brand")).toHaveTextContent("haute")
    expect(screen.getByRole("button", { name: "Sheets" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-redo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-zoom-in")).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-assistant")).toBeEnabled()
    expect(screen.getByTestId("toolbar-help")).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-save")).toBeEnabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeEnabled()
  })

  it("switches between the sheets and the schema, which has no zoom", () => {
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Schema" }))
    expect(useWorkbenchViewStore.getState().section).toBe("schema")
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.queryByTestId("toolbar-zoom-in")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Sheets" }))
    expect(useWorkbenchViewStore.getState().section).toBe("sheets")
  })

  it("zooms the sheet in and out by a step", () => {
    renderToolbar()

    fireEvent.click(screen.getByTestId("toolbar-zoom-in"))
    expect(useWorkbenchViewStore.getState().zoom).toBeCloseTo(1.1)
    fireEvent.click(screen.getByTestId("toolbar-zoom-out"))
    fireEvent.click(screen.getByTestId("toolbar-zoom-out"))
    expect(useWorkbenchViewStore.getState().zoom).toBeCloseTo(0.9)
  })

  it("saves the form, commits through the host, and keeps both off while the form loads or saves", () => {
    const { rerender } = renderToolbar()
    fireEvent.click(screen.getByTestId("toolbar-save"))
    expect(actions.save).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))
    expect(onCommit).toHaveBeenCalledTimes(1)

    useWorkbenchFormStore.setState({ saving: true })
    rerender(<WorkbenchToolbar onCommit={onCommit} />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeDisabled()

    useWorkbenchFormStore.setState({ saving: false, status: "loading" })
    rerender(<WorkbenchToolbar onCommit={onCommit} />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeEnabled()
  })

  it("says why pricing the sample last failed", () => {
    const { rerender } = renderToolbar()
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()

    useWorkbenchPricingStore.setState({ error: "Connect a frame to the Workbench Output's 'pricing_output' table." })
    rerender(<WorkbenchToolbar onCommit={onCommit} />)
    const note = screen.getByTestId("workbench-pricing-error")
    expect(note).toHaveTextContent("Pricing failed: Connect a frame to the Workbench Output's 'pricing_output' table.")
    expect(note).toHaveAttribute("title", "Connect a frame to the Workbench Output's 'pricing_output' table.")
  })

  it("undoes and redoes the form's history", () => {
    useWorkbenchFormStore.setState({ undoStack: [blank], redoStack: [blank] })
    renderToolbar()

    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))

    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(actions.redo).toHaveBeenCalledTimes(1)
  })
})
