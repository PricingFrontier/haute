/**
 * The toolbar while the workbench's view shows (specs/workbench), the same controls in
 * both modes, those a mode has no use for disabled: the brand, Build over Preview, the
 * sections and Display, the form's Undo and Redo, the sheet's zoom, the project's controls,
 * Save and Commit, both the host's as the pipeline toolbar's are, and in Preview why pricing
 * the sample failed, with no Price or Clear: the sample is priced as it is typed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { BLANK_THEME } from "../../utils/workbenchForm"
import WorkbenchToolbar from "../WorkbenchToolbar"

vi.mock("../../components/BranchIndicator", () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="branch-indicator">{children}</div>,
}))

const blank: FormSpec = {
  version: 1,
  name: "motor",
  theme: { ...BLANK_THEME },
  schema: { tables: [] },
  pages: [
    {
      id: "page_1",
      title: "Sheet 1",
      width: 1280,
      show_when: { match: "all", conditions: [] },
      sections: [{ id: "section_1", title: "Section 1", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] }],
    },
  ],
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
        columns: [{ id: "c1", name: "limit", type: "str", label: "", key: false, required: false, min: null, max: null, options: [], options_source: null, row_per_value: false, index: false, help: "", placeholder: "", group: "", default_value: "", number_format: "general", decimal_places: null, use_grouping: true }],
      },
    ],
  },
}

const onSave = vi.fn()
const onCommit = vi.fn()
const renderToolbar = () => render(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)

describe("WorkbenchToolbar", () => {
  const actions = { undo: vi.fn(), redo: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ form, status: "ready", saving: false, undoStack: [], redoStack: [], ...actions })
    useWorkbenchViewStore.setState({ mode: "build", section: "sheets", zoom: 1 })
    useWorkbenchPricingStore.setState({ error: null })
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

  it("switches between the sheets and the schema, where the zoom is disabled", () => {
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Schema" }))
    expect(useWorkbenchViewStore.getState().section).toBe("schema")
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByTestId("toolbar-zoom-in")).toBeDisabled()
    expect(screen.getByTestId("toolbar-zoom-out")).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Sheets" }))
    expect(useWorkbenchViewStore.getState().section).toBe("sheets")
    expect(screen.getByTestId("toolbar-zoom-in")).toBeEnabled()
  })

  it("keeps the same controls in both modes, in the same places", () => {
    const controls = () => within(screen.getByTestId("workbench-toolbar-actions")).getAllByRole("button").map((button) => button.textContent)
    renderToolbar()
    const building = controls()
    expect(building).toEqual(["Build", "Preview", "Sheets", "Schema", "Display", "Undo", "Redo", "Zoom In", "Zoom Out"])

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(controls()).toEqual(building)
  })

  it("switches to Preview on the sheets, from the schema too, its sections disabled, the zoom and the form's Undo and Redo on, no Price or Clear, and back to Build", () => {
    useWorkbenchViewStore.setState({ section: "schema" })
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "preview", section: "sheets" })
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Sheets" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Sheets" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Schema" })).toBeDisabled()
    expect(screen.getByTestId("toolbar-undo")).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-zoom-in")).toBeEnabled()
    // The sample is priced as it is typed, and nothing empties it.
    expect(screen.queryByRole("button", { name: "Price" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Build" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "build", section: "sheets" })
    expect(screen.getByRole("button", { name: "Schema" })).toBeEnabled()
  })

  it("has Display while building, opening the box of the sheets' colours, disabled in Preview", () => {
    renderToolbar()
    fireEvent.click(screen.getByRole("button", { name: "Display" }))
    expect(screen.getByRole("dialog", { name: "Display" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(screen.queryByRole("dialog", { name: "Display" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Display" })).toBeDisabled()
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

  it("says why pricing the sample last failed in Preview, and nothing of it while building, which prices nothing", () => {
    useWorkbenchPricingStore.setState({ error: "Connect a frame to the Workbench Output's 'pricing_output' table." })
    const { rerender } = renderToolbar()
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()

    useWorkbenchViewStore.setState({ mode: "preview" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    const note = screen.getByTestId("workbench-pricing-error")
    expect(note).toHaveTextContent("Pricing failed: Connect a frame to the Workbench Output's 'pricing_output' table.")
    expect(note).toHaveAttribute("title", "Connect a frame to the Workbench Output's 'pricing_output' table.")

    useWorkbenchPricingStore.setState({ error: null })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()
  })

  it("undoes and redoes the form's history, building or in Preview, where the sample is typed", () => {
    useWorkbenchFormStore.setState({ undoStack: [blank], redoStack: [blank] })
    const { rerender } = renderToolbar()

    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))
    useWorkbenchViewStore.setState({ mode: "preview" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} />)
    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))

    expect(actions.undo).toHaveBeenCalledTimes(2)
    expect(actions.redo).toHaveBeenCalledTimes(2)
  })
})
