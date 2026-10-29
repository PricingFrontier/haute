/**
 * The workbench's view (specs/workbench): the form read when it first shows, its states,
 * the schema's pane over the sheet, the way out of a refused save, the view's keyboard shortcuts, and the
 * project's panels beside it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { fetchWorkbenchFormTables } from "../../api/workbench"
import useDataEpochStore from "../../stores/useDataEpochStore"
import useGraphStore from "../../stores/useGraphStore"
import useUIStore from "../../stores/useUIStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchPricingStore, { PRICING_DELAY_MS } from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { SCHEMA_FULL_WIDTH } from "../../stores/useWorkbenchViewStore"
import { BLANK_THEME } from "../../utils/workbenchForm"
import WorkbenchView from "../WorkbenchView"
import { sheetForm } from "./fixtures"

vi.mock("../SchemaEditor", () => ({
  default: ({ readOnly }: { readOnly?: boolean }) => (
    <div data-testid="schema-editor" data-read-only={readOnly ? "true" : "false"}>
      <input aria-label="Column name" />
    </div>
  ),
}))
vi.mock("../SheetCanvas", () => ({ default: () => <div data-testid="sheet-canvas" /> }))
vi.mock("../PageTabs", () => ({ default: () => <div data-testid="page-tabs" /> }))
vi.mock("../PropertiesPanel", () => ({ default: () => <div data-testid="properties-panel" /> }))
vi.mock("../../panels/GitPanel", () => ({ default: () => <div data-testid="git-panel" /> }))
vi.mock("../../panels/assistant/AssistantPanel", () => ({ default: () => <div data-testid="assistant-panel" /> }))
vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
}))

/** The pricing store as it is, before a test stands in for its schedule. */
const realPricing = useWorkbenchPricingStore.getState()

const onSave = vi.fn(async () => true)
const onSaveShortcut = vi.fn()
const resolveGraph = vi.fn(() => ({ nodes: [], edges: [] }))

function renderView() {
  return render(
    <WorkbenchView onSave={onSave} onSaveShortcut={onSaveShortcut} isInsideSubmodel={false} readOnly={false} resolveGraph={resolveGraph} />,
  )
}

const key = (target: EventTarget, init: KeyboardEventInit) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }))

describe("WorkbenchView", () => {
  const actions = { load: vi.fn(async () => {}), reload: vi.fn(async () => {}), undo: vi.fn(), redo: vi.fn() }

  const pricing = { setPricer: vi.fn(), schedule: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ status: "ready", loadError: null, stale: false, form: null, ...actions })
    useWorkbenchPricingStore.setState({ ...pricing, error: null, price: null })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, selectedId: null })
    useWorkbenchStore.setState({ enabled: true, activeView: "workbench", formPath: "rating/workbench" })
    useUIStore.setState({ gitOpen: false, assistantOpen: false, paletteOpen: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.useRealTimers()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline", formPath: null })
  })

  it("reads the form when it first shows, and shows the sheets with the palette once it is read", () => {
    renderView()

    expect(actions.load).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("page-tabs")).toBeInTheDocument()
    expect(screen.getByTestId("sheet-canvas")).toBeInTheDocument()
    expect(screen.queryByTestId("schema-editor")).not.toBeInTheDocument()
    expect(screen.getByTestId("palette-item-tableInput")).toBeInTheDocument()
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Workbench" })).toHaveAttribute("aria-pressed", "true")
  })

  it("opens the schema's pane over the sheet under its tabs, the sheet and the properties panel still showing, and closes it", () => {
    renderView()
    act(() => {
      useWorkbenchViewStore.getState().setSchemaOpen(true)
    })
    const pane = screen.getByTestId("schema-pane")
    expect(within(pane).getByTestId("schema-editor")).toHaveAttribute("data-read-only", "false")
    expect(within(pane).getByText("Schema")).toBeInTheDocument()
    expect(screen.getByTestId("sheet-canvas")).toBeInTheDocument()
    expect(screen.getByTestId("properties-panel")).toBeInTheDocument()
    // Laid over the sheet, in the box it shares with the canvas, the tabs above it.
    expect(pane.parentElement).toContainElement(screen.getByTestId("sheet-canvas"))
    expect(pane.parentElement).not.toContainElement(screen.getByTestId("page-tabs"))
    expect(pane).toHaveClass("absolute", "left-0")
    expect(useWorkbenchViewStore.getState().schemaPane).toBe(pane)

    fireEvent.click(within(pane).getByTitle("Close"))
    expect(screen.queryByTestId("schema-pane")).not.toBeInTheDocument()
    expect(useWorkbenchViewStore.getState()).toMatchObject({ schemaOpen: false, schemaPane: null })
  })

  it("opens the schema's pane with its fields at full stretch, a width a drag reaches on a narrow window too", () => {
    const innerWidth = window.innerWidth
    try {
      // Half of it is under the full stretch.
      Object.defineProperty(window, "innerWidth", { configurable: true, value: 1000 })
      renderView()
      act(() => {
        useWorkbenchViewStore.getState().setSchemaOpen(true)
      })
      const pane = screen.getByTestId("schema-pane")
      expect(SCHEMA_FULL_WIDTH).toBe(640 + 10 + 4)
      expect(pane.firstElementChild).toHaveStyle({ width: `${SCHEMA_FULL_WIDTH}px` })

      const handle = within(pane).getByTestId("panel-resize-handle")
      fireEvent.mouseDown(handle, { clientX: 600 })
      fireEvent.mouseMove(window, { clientX: 400, buttons: 1 })
      fireEvent.mouseUp(window)
      expect(useWorkbenchViewStore.getState().schemaWidth).toBe(SCHEMA_FULL_WIDTH - 200)
      fireEvent.mouseDown(handle, { clientX: 200 })
      fireEvent.mouseMove(window, { clientX: 900, buttons: 1 })
      fireEvent.mouseUp(window)
      expect(useWorkbenchViewStore.getState().schemaWidth).toBe(SCHEMA_FULL_WIDTH)
    } finally {
      Object.defineProperty(window, "innerWidth", { configurable: true, value: innerWidth })
      useWorkbenchViewStore.setState({ schemaWidth: SCHEMA_FULL_WIDTH })
    }
  })

  it("lays Preview out as building, the schema's pane kept open and read only, the palette's components disabled and no properties panel", () => {
    renderView()
    expect(screen.getByTestId("properties-panel")).toBeInTheDocument()

    act(() => {
      useWorkbenchViewStore.getState().setSchemaOpen(true)
      useWorkbenchViewStore.getState().showMode("preview")
    })
    expect(screen.getByTestId("page-tabs")).toBeInTheDocument()
    expect(screen.getByTestId("sheet-canvas")).toBeInTheDocument()
    expect(screen.getByTestId("schema-editor")).toHaveAttribute("data-read-only", "true")
    expect(screen.getByTestId("palette-item-tableInput")).toHaveAttribute("aria-disabled", "true")
    expect(screen.queryByTestId("properties-panel")).not.toBeInTheDocument()

    act(() => {
      useWorkbenchViewStore.getState().showMode("build")
    })
    expect(screen.getByTestId("schema-editor")).toHaveAttribute("data-read-only", "false")
    expect(screen.getByTestId("palette-item-tableInput")).not.toHaveAttribute("aria-disabled")
    expect(screen.getByTestId("properties-panel")).toBeInTheDocument()
  })

  it("says the workbench is loading, or why it could not be read with a way to try again", () => {
    useWorkbenchFormStore.setState({ status: "loading" })
    const { rerender } = renderView()
    expect(screen.getByRole("status")).toHaveTextContent("Loading the workbench…")

    act(() => {
      useWorkbenchFormStore.setState({ status: "failed", loadError: "rating/workbench/form.json is not JSON: bad" })
    })
    rerender(
      <WorkbenchView onSave={onSave} onSaveShortcut={onSaveShortcut} isInsideSubmodel={false} readOnly={false} resolveGraph={resolveGraph} />,
    )
    expect(screen.getByRole("alert")).toHaveTextContent("Could not read the workbench: rating/workbench/form.json is not JSON: bad")
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(actions.reload).toHaveBeenCalledTimes(1)
  })

  it("reports a save refused because the file changed on disk, with a reload", () => {
    useWorkbenchFormStore.setState({ stale: true })
    renderView()

    expect(screen.getByRole("alert")).toHaveTextContent(
      "rating/workbench changed on disk since the workbench read it. Reload it to go on; unsaved edits will be lost.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    expect(actions.reload).toHaveBeenCalledTimes(1)
  })

  it("saves the project through the host on Ctrl+S, with a focused field's edit included", async () => {
    vi.useFakeTimers()
    try {
      useWorkbenchViewStore.setState({ schemaOpen: true })
      renderView()
      const field = screen.getByRole("textbox", { name: "Column name" })
      field.focus()

      key(field, { key: "s", ctrlKey: true })
      expect(document.activeElement).not.toBe(field)
      expect(onSaveShortcut).not.toHaveBeenCalled()
      await vi.runAllTimersAsync()
      expect(onSaveShortcut).toHaveBeenCalledTimes(1)

      key(window, { key: "s", metaKey: true })
      expect(onSaveShortcut).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("undoes and redoes with Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y, not while typing or in a dialog", () => {
    useWorkbenchViewStore.setState({ schemaOpen: true })
    renderView()
    key(window, { key: "z", ctrlKey: true })
    key(window, { key: "Z", ctrlKey: true, shiftKey: true })
    key(window, { key: "y", ctrlKey: true })
    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(actions.redo).toHaveBeenCalledTimes(2)

    key(screen.getByRole("textbox", { name: "Column name" }), { key: "z", ctrlKey: true })
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    dialog.setAttribute("aria-modal", "true")
    document.body.appendChild(dialog)
    key(dialog, { key: "z", ctrlKey: true })
    key(dialog, { key: "s", ctrlKey: true })
    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(onSaveShortcut).not.toHaveBeenCalled()
  })

  it("gives the pricing store a pricer while it shows, and prices the sample in Preview as it shows and when its basis changes, never while building", () => {
    const form = {
      version: 1 as const,
      name: "motor",
      theme: { ...BLANK_THEME },
      schema: { tables: [] },
      pages: [
        {
          id: "p",
          title: "Sheet 1",
          width: 1280,
          show_when: { match: "all" as const, conditions: [] },
          sections: [{ id: "section_1", title: "Section 1", height: 560, show_when: { match: "all" as const, conditions: [] }, widgets: [] }],
        },
      ],
      sample: {},
    }
    useWorkbenchFormStore.setState({ form })
    const { unmount } = renderView()

    expect(pricing.setPricer).toHaveBeenCalledTimes(1)
    expect(pricing.setPricer.mock.calls[0][0]).toEqual(expect.any(Function))
    // Building, the sheets are a new quote's: nothing is priced, with the schema's pane open neither.
    expect(pricing.schedule).not.toHaveBeenCalled()
    act(() => {
      useWorkbenchFormStore.setState({ form: { ...form, sample: { t: [{ c: "1" }] } } })
      useWorkbenchViewStore.getState().setSchemaOpen(true)
      useWorkbenchFormStore.setState({ form: { ...form, sample: { t: [{ c: "2" }] } } })
    })
    expect(pricing.schedule).not.toHaveBeenCalled()

    // Preview prices the sample as it shows, as the pipeline may have changed meanwhile.
    act(() => useWorkbenchViewStore.getState().showMode("preview"))
    expect(pricing.schedule).toHaveBeenCalledTimes(1)
    // The same basis prices nothing again; a typed sample does.
    act(() => {
      useWorkbenchFormStore.setState({ form: { ...form, sample: { t: [{ c: "2" }] }, pages: [{ ...form.pages[0], title: "Renamed" }] } })
    })
    expect(pricing.schedule).toHaveBeenCalledTimes(1)
    act(() => {
      useWorkbenchFormStore.setState({ form: { ...form, sample: { t: [{ c: "3" }] } } })
    })
    expect(pricing.schedule).toHaveBeenCalledTimes(2)
    // What the sample prices on moved while Preview shows: the pipeline, then its data.
    act(() => useGraphStore.setState({ structuralFingerprint: "edited by the Assistant" }))
    expect(pricing.schedule).toHaveBeenCalledTimes(3)
    act(() => useDataEpochStore.getState().moved())
    expect(pricing.schedule).toHaveBeenCalledTimes(4)

    unmount()
    expect(pricing.setPricer).toHaveBeenLastCalledWith(null)
  })

  it("drops the sample's pricing waiting for the pause when Preview is left, so nothing is priced while building", async () => {
    vi.useFakeTimers()
    // The pricing store's own timer, not a stand-in for it.
    useWorkbenchPricingStore.setState({ setPricer: realPricing.setPricer, schedule: realPricing.schedule, cancel: realPricing.cancel })
    vi.mocked(fetchWorkbenchFormTables).mockRejectedValue(new Error("offline"))
    useWorkbenchFormStore.setState({ form: { ...sheetForm(), sample: { policy: [{ "policy.state": "CA" }] } } })
    renderView()

    act(() => useWorkbenchViewStore.getState().showMode("preview"))
    await act(() => vi.advanceTimersByTimeAsync(PRICING_DELAY_MS - 100))
    act(() => useWorkbenchViewStore.getState().showMode("build"))
    await act(() => vi.advanceTimersByTimeAsync(PRICING_DELAY_MS))
    expect(fetchWorkbenchFormTables).not.toHaveBeenCalled()

    // Left in Preview, the sample is priced once the pause has passed.
    act(() => useWorkbenchViewStore.getState().showMode("preview"))
    await act(() => vi.advanceTimersByTimeAsync(PRICING_DELAY_MS))
    expect(fetchWorkbenchFormTables).toHaveBeenCalledTimes(1)
  })

  it("opens the Git and Assistant panels beside the view, in the properties panel's place", async () => {
    useUIStore.setState({ gitOpen: true })
    renderView()
    // The panels are lazy chunks, mounted once their import settles.
    expect(await within(screen.getByRole("complementary", { name: "Version control" })).findByTestId("git-panel")).toBeInTheDocument()
    expect(screen.queryByTestId("properties-panel")).not.toBeInTheDocument()

    act(() => {
      useUIStore.setState({ gitOpen: false, assistantOpen: true })
    })
    expect(await within(screen.getByRole("complementary", { name: "Assistant" })).findByTestId("assistant-panel")).toBeInTheDocument()
  })
})
