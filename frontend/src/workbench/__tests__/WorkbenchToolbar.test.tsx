/**
 * The toolbar while the workbench's view shows (specs/workbench): the brand, Build over
 * Preview, the sections, the form's Undo and Redo, the sheet's zoom, the project's
 * controls, Save and Commit, both the host's as the pipeline toolbar's are, and in
 * Preview Price and Clear for the quote.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
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

/** The blank form with one input table, `t1`, holding one column, `c1`: what a quote can be typed into. */
const form: FormSpec = {
  ...blank,
  schema: {
    tables: [
      {
        id: "t1",
        name: "policy",
        role: "input",
        rows: "one",
        columns: [{ id: "c1", name: "limit", type: "str", label: "", key: false, required: false, min: null, max: null, options: [], index: false }],
      },
    ],
  },
}

const onSave = vi.fn()
const onCommit = vi.fn()
const renderToolbar = () => render(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)

describe("WorkbenchToolbar", () => {
  const actions = { undo: vi.fn(), redo: vi.fn() }
  const quote = { priceQuote: vi.fn(async () => {}), clear: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ form, status: "ready", saving: false, undoStack: [], redoStack: [], ...actions })
    useWorkbenchViewStore.setState({ section: "sheets", zoom: 1 })
    useWorkbenchPricingStore.setState({ error: null })
    useWorkbenchPreviewStore.setState({ quote: {}, error: null, pricing: false, ...quote })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("has the brand, Build pressed over Preview, the sections, Undo and Redo, the zoom, and the project's controls with Save and Commit", () => {
    renderToolbar()

    expect(screen.getByRole("toolbar", { name: "Workbench toolbar" })).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-brand")).toHaveTextContent("haute")
    expect(screen.getByRole("button", { name: "Build" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByRole("button", { name: "Sheets" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-redo")).toBeDisabled()
    expect(screen.getByTestId("toolbar-zoom-in")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Price" })).not.toBeInTheDocument()
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

  it("switches to Preview, which has the zoom and the quote's Price and Clear in place of the sections and the history, and back to Build", () => {
    useWorkbenchViewStore.setState({ section: "schema" })
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(useWorkbenchViewStore.getState().section).toBe("preview")
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.queryByRole("button", { name: "Sheets" })).not.toBeInTheDocument()
    expect(screen.queryByTestId("toolbar-undo")).not.toBeInTheDocument()
    expect(screen.getByTestId("toolbar-zoom-in")).toBeInTheDocument()

    // Nothing typed: nothing to price, as a deployed request never holds a blank quote,
    // and nothing to clear. Typed: Price prices it, and Clear asks, then starts over.
    expect(screen.getByRole("button", { name: "Price" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Price" })).toHaveAttribute("title", "Type the quote first")
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled()
    // A value in a column the schema no longer has, or in a second row of a table now
    // with one row per quote, counts for nothing: the server leaves it out.
    act(() => {
      useWorkbenchPreviewStore.setState({ quote: { t1: [{ gone: "x" }, { c1: "z" }], gone: [{ c1: "y" }] } })
    })
    expect(screen.getByRole("button", { name: "Price" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Clear" })).toBeDisabled()
    act(() => {
      useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "x" }] } })
    })
    fireEvent.click(screen.getByRole("button", { name: "Price" }))
    expect(quote.priceQuote).toHaveBeenCalledTimes(1)
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
    fireEvent.click(screen.getByRole("button", { name: "Clear" }))
    expect(confirm).toHaveBeenCalledWith("Clear the quote? Every cell will be emptied.")
    expect(quote.clear).not.toHaveBeenCalled()
    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole("button", { name: "Clear" }))
    expect(quote.clear).toHaveBeenCalledTimes(1)

    act(() => {
      useWorkbenchPreviewStore.setState({ pricing: true })
    })
    expect(screen.getByRole("button", { name: "Price" })).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Build" }))
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

  it("saves and commits through the host, the pipeline toolbar's controls, off while editing is disabled and on whatever the form's own state", () => {
    const { rerender } = renderToolbar()
    fireEvent.click(screen.getByTestId("toolbar-save"))
    expect(onSave).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByTestId("toolbar-save-commit"))
    expect(onCommit).toHaveBeenCalledTimes(1)

    // The pipeline document cannot be edited: Save and Commit are off, and the Assistant
    // while no turn runs, as they are in the pipeline toolbar.
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} editingDisabled />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeDisabled()

    // The form loading or saving is the form's state, not the project's: Save stays the
    // project's, and only the form's own controls wait for it.
    useWorkbenchFormStore.setState({ saving: true, status: "loading" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    expect(screen.getByTestId("toolbar-save")).toBeEnabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeEnabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeEnabled()
    expect(screen.getByTestId("toolbar-undo")).toBeDisabled()
  })

  it("says why pricing the sample last failed while building, and why the quote has no price in Preview", () => {
    const { rerender } = renderToolbar()
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()

    useWorkbenchPricingStore.setState({ error: "Connect a frame to the Workbench Output's 'pricing_output' table." })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    const note = screen.getByTestId("workbench-pricing-error")
    expect(note).toHaveTextContent("Pricing failed: Connect a frame to the Workbench Output's 'pricing_output' table.")
    expect(note).toHaveAttribute("title", "Connect a frame to the Workbench Output's 'pricing_output' table.")

    useWorkbenchViewStore.setState({ section: "preview" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()
    useWorkbenchPreviewStore.setState({ error: "2 cells need attention" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    expect(screen.getByTestId("workbench-pricing-error")).toHaveTextContent("2 cells need attention")
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
