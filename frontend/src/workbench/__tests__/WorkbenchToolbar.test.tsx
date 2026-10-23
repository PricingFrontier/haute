/**
 * The toolbar while the workbench's view shows (specs/workbench), the same controls in
 * both modes, those a mode has no use for disabled: the brand, the pipeline toolbar's Source
 * over Pipeline, the form's Undo and Redo and the sheet's zoom where the pipeline toolbar has
 * its own, Build over Preview, Schema over Display, the project's controls,
 * Save and Commit, both the host's as the pipeline toolbar's are, and in Preview why pricing
 * the sample failed, with no Price or Clear: the sample is priced as it is typed.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import type { FormSpec } from "../../api/types"
import Toolbar from "../../components/Toolbar"
import useSettingsStore from "../../stores/useSettingsStore"
import useUIStore from "../../stores/useUIStore"
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
const renderToolbar = () => render(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" />)

/** The pipeline toolbar's props, nothing selected or running: what the workbench's is held to. */
const pipelineToolbarProps = {
  nodeCount: 5,
  canUndo: false,
  canRedo: false,
  onUndo: vi.fn(),
  onRedo: vi.fn(),
  onZoomIn: vi.fn(),
  onZoomOut: vi.fn(),
  onOpenUtility: vi.fn(),
  onOpenConstants: vi.fn(),
  submodelAction: "create" as const,
  canRunSubmodelAction: false,
  onSubmodelAction: vi.fn(),
  canCreateInstance: false,
  onCreateInstance: vi.fn(),
  onCentre: vi.fn(),
  onAutoLayout: vi.fn(),
  isAutoLayouting: false,
  onSave,
  onSaveCommit: onCommit,
  wsStatus: "connected" as const,
}

/**
 * The toolbar's leading controls in order: the column after the brand, the group after it,
 * and that group's first two columns.
 */
function leadingControls() {
  const column = screen.getByTestId("toolbar-brand").nextElementSibling as HTMLElement
  const actions = column.nextElementSibling as HTMLElement
  const [undoRedo, zoom] = Array.from(actions.children) as HTMLElement[]
  return {
    column: column.dataset.testid,
    source: within(column).getByTestId("source-selector").textContent,
    pipeline: within(column).getByTestId("toolbar-pipeline-settings").textContent,
    actions: actions.className,
    undoRedo: [within(undoRedo).getByTestId("toolbar-undo").textContent, within(undoRedo).getByTestId("toolbar-redo").textContent],
    zoom: [within(zoom).getByTestId("toolbar-zoom-in").textContent, within(zoom).getByTestId("toolbar-zoom-out").textContent],
  }
}

describe("WorkbenchToolbar", () => {
  const actions = { undo: vi.fn(), redo: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ form, status: "ready", saving: false, undoStack: [], redoStack: [], ...actions })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, zoom: 1 })
    useWorkbenchPricingStore.setState({ error: null })
    useSettingsStore.setState({ sources: ["live"], activeSource: "live" })
    useUIStore.setState({ calculationMode: "automatic" })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("has the brand, Source over Pipeline, Undo and Redo, the zoom, Build pressed over Preview, Schema over Display, and the project's controls with Save and Commit", () => {
    renderToolbar()

    expect(screen.getByRole("toolbar", { name: "Workbench toolbar" })).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-brand")).toHaveTextContent("haute")
    expect(screen.getByTestId("source-selector")).toHaveTextContent("live")
    expect(screen.getByTestId("toolbar-pipeline-settings")).toHaveTextContent(/^Pipeline$/)
    expect(screen.getByRole("button", { name: "Build" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.queryByRole("button", { name: "Sheets" })).not.toBeInTheDocument()
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

  it("opens and closes the schema's pane, pressed while it is open, the zoom on either way", () => {
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Schema" }))
    expect(useWorkbenchViewStore.getState().schemaOpen).toBe(true)
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByTestId("toolbar-zoom-in")).toBeEnabled()
    expect(screen.getByTestId("toolbar-zoom-out")).toBeEnabled()

    fireEvent.click(screen.getByRole("button", { name: "Schema" }))
    expect(useWorkbenchViewStore.getState().schemaOpen).toBe(false)
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "false")
  })

  it("stacks Schema over Display in one column", () => {
    renderToolbar()
    const column = screen.getByRole("button", { name: "Schema" }).parentElement!
    expect(column).toHaveClass("toolbar-column")
    expect(column).toContainElement(screen.getByRole("button", { name: "Display" }))
  })

  it("has Schema off until the form is read", () => {
    useWorkbenchFormStore.setState({ status: "loading" })
    renderToolbar()
    expect(screen.getByRole("button", { name: "Schema" })).toBeDisabled()
  })

  it("keeps the same controls in both modes, in the same places", () => {
    const controls = () => within(screen.getByTestId("workbench-toolbar-actions")).getAllByRole("button").map((button) => button.textContent)
    renderToolbar()
    const building = controls()
    expect(building).toEqual(["Undo", "Redo", "Zoom In", "Zoom Out", "Build", "Preview", "Schema", "Display"])

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(controls()).toEqual(building)
  })

  it("puts Source over Pipeline after the brand, then Undo and Redo and the zoom, where the pipeline toolbar has them", () => {
    render(<Toolbar {...pipelineToolbarProps} />)
    const pipeline = leadingControls()
    expect(pipeline).toMatchObject({ column: "toolbar-source-pipeline", source: "live", pipeline: "Pipeline" })
    cleanup()

    renderToolbar()
    expect(leadingControls()).toEqual(pipeline)
  })

  it("closes the Source dropdown and the Help menu on a sheet component's press, which cancels the mouse events after it", () => {
    renderToolbar()
    // A component on the sheet: its press is all there is, stopped where it lands.
    const component = document.body.appendChild(document.createElement("div"))
    component.addEventListener("pointerdown", (event) => {
      event.preventDefault()
      event.stopPropagation()
    })
    try {
      fireEvent.click(screen.getByTestId("source-selector"))
      expect(screen.getByRole("button", { name: "Add source" })).toBeInTheDocument()
      fireEvent.pointerDown(component)
      expect(screen.queryByRole("button", { name: "Add source" })).not.toBeInTheDocument()

      fireEvent.click(screen.getByTestId("toolbar-help"))
      expect(screen.getByRole("menu", { name: "Help" })).toBeInTheDocument()
      fireEvent.pointerDown(component)
      expect(screen.queryByRole("menu", { name: "Help" })).not.toBeInTheDocument()
    } finally {
      component.remove()
    }
  })

  it("reports the server Offline on its Pipeline button, as the pipeline toolbar does", () => {
    render(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="reconnecting" />)
    expect(screen.getByTestId("toolbar-pipeline-settings")).toHaveTextContent(/^Offline$/)
  })

  it("switches to Preview with the schema's pane kept open, Schema still on, the zoom and the form's Undo and Redo on, no Price or Clear, and back to Build", () => {
    useWorkbenchViewStore.setState({ schemaOpen: true })
    renderToolbar()

    fireEvent.click(screen.getByRole("button", { name: "Preview" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "preview", schemaOpen: true })
    expect(screen.getByRole("button", { name: "Preview" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Schema" })).toBeEnabled()
    fireEvent.click(screen.getByRole("button", { name: "Schema" }))
    expect(useWorkbenchViewStore.getState().schemaOpen).toBe(false)
    expect(screen.getByTestId("toolbar-undo")).toBeInTheDocument()
    expect(screen.getByTestId("toolbar-zoom-in")).toBeEnabled()
    // The sample is priced as it is typed, and nothing empties it.
    expect(screen.queryByRole("button", { name: "Price" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("button", { name: "Build" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "build", schemaOpen: false })
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
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" editingDisabled />)
    expect(screen.getByTestId("toolbar-save")).toBeDisabled()
    expect(screen.getByTestId("toolbar-save-commit")).toBeDisabled()
    expect(screen.getByTestId("toolbar-assistant")).toBeDisabled()

    // The form loading or saving is the form's state, not the project's: Save stays the
    // project's, and only the form's own controls wait for it.
    useWorkbenchFormStore.setState({ saving: true, status: "loading" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" />)
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
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" />)
    const note = screen.getByTestId("workbench-pricing-error")
    expect(note).toHaveTextContent("Pricing failed: Connect a frame to the Workbench Output's 'pricing_output' table.")
    expect(note).toHaveAttribute("title", "Connect a frame to the Workbench Output's 'pricing_output' table.")

    useWorkbenchPricingStore.setState({ error: null })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" />)
    expect(screen.queryByTestId("workbench-pricing-error")).not.toBeInTheDocument()
  })

  it("undoes and redoes the form's history, building or in Preview, where the sample is typed", () => {
    useWorkbenchFormStore.setState({ undoStack: [blank], redoStack: [blank] })
    const { rerender } = renderToolbar()

    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))
    useWorkbenchViewStore.setState({ mode: "preview" })
    rerender(<WorkbenchToolbar onSave={onSave} onCommit={onCommit} wsStatus="connected" />)
    fireEvent.click(screen.getByTestId("toolbar-undo"))
    fireEvent.click(screen.getByTestId("toolbar-redo"))

    expect(actions.undo).toHaveBeenCalledTimes(2)
    expect(actions.redo).toHaveBeenCalledTimes(2)
  })
})
