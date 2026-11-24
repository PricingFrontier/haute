/**
 * The properties panel (specs/workbench): the selected component's title, Show when and
 * layout, a Table's fixed rows and look, a Collection's look, a Chart's chart, the order of its fields, and the
 * fields ticked from the schema tables of its kind (a Chart's from every table, the columns
 * it cannot plot disabled), with tables whose rows do not line up greyed out, and searched; a Heading's
 * text, description, size, alignment and Show when; the showing sheet's own panel, its
 * title, its Show when, its width and its deletion, asked first in the editor's own dialog;
 * and a section's panel, its title, its Show when, its
 * height and Fit, a section added below it, and its deletion.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { createWidget, findSection, findWidget, removeSchemaColumn, removeSchemaTable, setExportCellColumn, updateWidget } from "../../utils/workbenchForm"
import { answerQuestion, asked } from "../../test-utils/question"
import PropertiesPanel from "../PropertiesPanel"
import type { ExcelCellsExport, ExportWidget, FieldRef, FormSpec, HttpImport, ImportWidget, Section } from "../../api/types"
import { COMPONENT_COLOR } from "../widgetKinds"
import { addTo, chartWidget, column, coverForm, currentForm, fieldsWidget, headingWidget, loadForm, sectionedForm, sheetForm, table, widgetsOf } from "./fixtures"

const grid = () => fieldsWidget(currentForm(), "w_grid")
const select = (id: string | null) => useWorkbenchViewStore.setState({ selectedId: id })
const chart = () => fieldsWidget(currentForm(), "w_chart")
/** Open a section's panel, as pressing its header does. */
const openSection = (id: string) => act(() => useWorkbenchViewStore.getState().selectSection(id))
/** Show a Table's or a Collection's pane, as pressing its tab does. */
const showPane = (name: "Config" | "Display") => fireEvent.click(screen.getByRole("tab", { name }))
const sectionIds = () => currentForm().pages[0].sections.map((candidate) => candidate.id)
/** The names a `ColumnSelector` offers once `search` is typed in its menu, which closes again. */
function offered(label: string, search = ""): string[] {
  fireEvent.click(screen.getByRole("button", { name: label }))
  const box = screen.getByRole("combobox", { name: `Search ${label}` })
  if (search) fireEvent.change(box, { target: { value: search } })
  const names = within(screen.getByRole("listbox", { name: `${label} options` })).queryAllByRole("option").map((option) => option.firstElementChild?.textContent ?? "")
  fireEvent.keyDown(box, { key: "Escape" })
  return names
}

/** Choose `name` in a `ColumnSelector`, searched for in its menu. */
function pick(label: string, name: string) {
  fireEvent.click(screen.getByRole("button", { name: label }))
  fireEvent.change(screen.getByRole("combobox", { name: `Search ${label}` }), { target: { value: name } })
  const option = within(screen.getByRole("listbox", { name: `${label} options` })).queryAllByRole("option").find((held) => held.firstElementChild?.textContent === name)
  if (option === undefined) throw new Error(`${label} offers no ${name}`)
  fireEvent.click(option)
}

/** A select's choices, each its value and its label. */
const choices = (select: HTMLElement) => [...select.querySelectorAll("option")].map((option) => [option.value, option.textContent])

/** The form's section `id`, as it is now. */
function sectionOf(id: string): Section {
  const found = findSection(currentForm(), id)
  if (found === null) throw new Error(`The form has no section ${id}`)
  return found.section
}

/** `sheetForm`, its equipment numbered by its index, with a Chart, w_chart, plotting `fields`. */
function chartForm(fields: FieldRef[]): FormSpec {
  const form = sheetForm()
  addTo(form, chartWidget("w_chart", fields))
  return form
}

describe("PropertiesPanel", () => {
  beforeEach(() => loadForm())
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("shows nothing until a component is selected, then its kind, title and layout", () => {
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()

    select("w_grid")
    rerender(<PropertiesPanel />)
    expect(screen.getByText("Table")).toBeInTheDocument()
    showPane("Display")
    const title = screen.getByRole("textbox", { name: "Title" })
    expect(title).toHaveValue("Equipment")
    fireEvent.change(title, { target: { value: "Schedule" } })
    fireEvent.blur(title)
    expect(grid().title).toBe("Schedule")

    const rows = screen.getByTestId("widget-rows")
    fireEvent.change(rows, { target: { value: "60" } })
    fireEvent.blur(rows)
    expect(screen.getByTestId("widget-rows-error")).toHaveTextContent("Enter a whole number from 1 to 50.")
    expect(grid()).toMatchObject({ rows: 3 })
    fireEvent.change(rows, { target: { value: "5" } })
    fireEvent.blur(rows)
    expect(grid()).toMatchObject({ rows: 5 })

    fireEvent.click(screen.getByTitle("Close"))
    expect(useWorkbenchViewStore.getState().selectedId).toBeNull()
  })

  it("shows the next component's own values, never a value refused for the one before", () => {
    const form = sheetForm()
    // The same Rows as the first, so only the key tells the two fields apart.
    const copy = { ...structuredClone(fieldsWidget(form, "w_grid")), id: "w_grid_2" }
    form.pages[0].sections[0].widgets.push(copy)
    loadForm(form)
    // Both open on Display, so the field stays drawn from one to the other.
    useWorkbenchViewStore.getState().setWidgetPane("w_grid", "display")
    useWorkbenchViewStore.getState().setWidgetPane("w_grid_2", "display")
    select("w_grid")
    render(<PropertiesPanel />)
    const rows = () => screen.getByTestId("widget-rows") as HTMLInputElement

    // Past the 50 a Table may start with: refused, the draft kept on screen.
    fireEvent.change(rows(), { target: { value: "99" } })
    fireEvent.blur(rows())
    expect(rows().value).toBe("99")

    act(() => select("w_grid_2"))
    // Its own Rows, 3, never the 99 refused for the first.
    expect(rows().value).toBe("3")
  })

  it("puts a Table's and a Collection's settings in two panes, Config with the data it shows and Display with how, keeping each component's pane", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)
    const pane = () => screen.getByRole("tabpanel")
    const regions = () => within(pane()).getAllByRole("region").map((region) => region.getAttribute("aria-label"))

    // Config first, in the component's colour: its name, its order and its fields.
    expect(within(screen.getByRole("tablist", { name: "Table panes" })).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Config", "Display"])
    const config = screen.getByRole("tab", { name: "Config" })
    expect(config).toHaveAttribute("aria-selected", "true")
    expect(config).toHaveStyle({ color: COMPONENT_COLOR })
    expect(pane()).toHaveAttribute("aria-labelledby", "component-config-tab")
    expect(regions()).toEqual(["Component name", "Order", "Fields"])
    // Display: its title, when it shows, its layout and its look: its header, its lines,
    // its frame and its columns' own looks.
    showPane("Display")
    expect(regions()).toEqual(["Title", "Show when", "Layout", "Look", "Header", "Lines", "Frame", "Columns"])

    // Each component keeps its own: a Collection opens on Config.
    select("w_boxes")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("tablist", { name: "Collection panes" })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "Config" })).toHaveAttribute("aria-selected", "true")
    showPane("Display")
    expect(regions()).toEqual(["Title", "Show when", "Layout", "Look", "Labels", "Boxes", "Frame", "Fields"])
    select("w_grid")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("tab", { name: "Display" })).toHaveAttribute("aria-selected", "true")
    // The pane is the view's, not the form's: nothing to undo.
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])
  })

  it("starts every component's panel with its Component name, at the top of its Config pane or its one list", () => {
    const form = sheetForm()
    addTo(form, chartWidget("w_chart", []))
    addTo(form, headingWidget("w_head", "Cover"))
    addTo(form, { ...createWidget("import", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_in" })
    addTo(form, { ...createWidget("export", { x: 192, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    loadForm(form)
    const { rerender } = render(<PropertiesPanel />)
    const firstRegion = () => within(screen.getByTestId("workbench-properties")).getAllByRole("region")[0].getAttribute("aria-label")
    for (const [id, name] of [["w_grid", "w_grid"], ["w_boxes", "w_boxes"], ["w_chart", "w_chart"], ["w_head", "w_head"], ["w_in", "import_1"], ["w_out", "export_1"]]) {
      select(id)
      rerender(<PropertiesPanel />)
      expect(firstRegion()).toBe("Component name")
      expect(screen.getByTestId("component-name")).toHaveValue(name)
    }
  })

  it("renames a component on Enter or blur as one undo step, and refuses a name that is no identifier or another's", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const field = () => screen.getByTestId("component-name")
    const name = () => findWidget(currentForm(), "w_grid")!.widget.name
    const undo = () => useWorkbenchFormStore.getState().undoStack

    fireEvent.change(field(), { target: { value: "equipment" } })
    fireEvent.keyDown(field(), { key: "Enter" })
    expect(name()).toBe("equipment")
    expect(undo()).toHaveLength(1)
    fireEvent.change(field(), { target: { value: "Equipment_rows" } })
    fireEvent.blur(field())
    expect(name()).toBe("Equipment_rows")
    expect(undo()).toHaveLength(2)

    // Refused at the field, saying why: the component keeps its name and nothing is recorded.
    fireEvent.change(field(), { target: { value: "2nd" } })
    fireEvent.blur(field())
    expect(screen.getByTestId("component-name-error")).toHaveTextContent("Use letters, digits and _, not starting with a digit")
    fireEvent.change(field(), { target: { value: "W_BOXES" } })
    fireEvent.keyDown(field(), { key: "Enter" })
    expect(screen.getByTestId("component-name-error")).toHaveTextContent("Another component is named w_boxes")
    expect(name()).toBe("Equipment_rows")
    expect(undo()).toHaveLength(2)
  })

  it("starts a Table's grid in the sample again at the rows it is given, keeping every value typed, each one undo step", () => {
    const form = sheetForm()
    form.sample = { equipment: [{}, { item: "Crane" }, {}, {}, {}] }
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const setRows = (text: string) => {
      const rows = screen.getByTestId("widget-rows")
      fireEvent.change(rows, { target: { value: text } })
      fireEvent.blur(rows)
    }

    // Fewer: the empty rows after the last typed go, down to it.
    setRows("1")
    expect(grid()).toMatchObject({ rows: 1 })
    expect(currentForm().sample.equipment).toEqual([{}, { item: "Crane" }])
    // More: empty rows make them up.
    setRows("4")
    expect(currentForm().sample.equipment).toEqual([{}, { item: "Crane" }, {}, {}])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(grid()).toMatchObject({ rows: 1 })
    expect(currentForm().sample.equipment).toEqual([{}, { item: "Crane" }])
  })

  it("sets a Table's fixed rows, transposed, and its look, its groups too, and a Collection's look, each with its own", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)
    showPane("Display")

    fireEvent.click(screen.getByRole("checkbox", { name: "Fixed rows" }))
    fireEvent.click(within(screen.getByRole("region", { name: "Layout" })).getByRole("checkbox", { name: "Transposed" }))
    const look = screen.getByRole("region", { name: "Look" })
    fireEvent.change(within(look).getByRole("combobox", { name: "Density" }), { target: { value: "comfortable" } })
    fireEvent.change(within(screen.getByRole("region", { name: "Lines" })).getByRole("combobox", { name: "Gridlines" }), { target: { value: "none" } })
    const header = within(screen.getByRole("region", { name: "Header" })).getByRole("combobox", { name: "Case" })
    expect(choices(header)).toEqual([
      ["plain", "As written"],
      ["caps", "Capitals"],
    ])
    fireEvent.change(header, { target: { value: "caps" } })
    fireEvent.click(within(look).getByRole("checkbox", { name: "Row numbers" }))
    fireEvent.click(within(look).getByRole("checkbox", { name: "Groups" }))
    fireEvent.click(within(look).getByRole("checkbox", { name: "Striped rows" }))
    fireEvent.click(within(screen.getByRole("region", { name: "Frame" })).getByRole("checkbox", { name: "Frame" }))
    expect(grid()).toMatchObject({
      fixed_rows: true,
      transposed: true,
      density: "comfortable",
      gridlines: "none",
      header: "caps",
      row_numbers: false,
      groups: true,
      striped: false,
      frame: false,
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(9)

    // A Collection's look has none of a Table's own: no rows, no grid and no turned labels.
    select("w_boxes")
    rerender(<PropertiesPanel />)
    showPane("Display")
    expect(screen.getByTestId("widget-columns")).toHaveValue("2")
    expect(screen.queryByRole("checkbox", { name: "Fixed rows" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Transposed" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Gridlines" })).not.toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Direction" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Row numbers" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Groups" })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "Striped rows" })).not.toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Header" })).not.toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Columns" })).not.toBeInTheDocument()
    const labels = within(screen.getByRole("region", { name: "Labels" })).getByRole("combobox", { name: "Position" })
    expect(choices(labels)).toEqual([
      ["above", "Above"],
      ["beside", "Beside"],
    ])
    expect(labels).toHaveValue("above")
    const boxesLook = screen.getByRole("region", { name: "Look" })
    expect(within(boxesLook).getByRole("combobox", { name: "Density" })).toHaveValue("comfortable")
    fireEvent.change(labels, { target: { value: "beside" } })
    fireEvent.change(within(boxesLook).getByRole("combobox", { name: "Density" }), { target: { value: "compact" } })
    fireEvent.click(within(screen.getByRole("region", { name: "Frame" })).getByRole("checkbox", { name: "Frame" }))
    expect(fieldsWidget(currentForm(), "w_boxes")).toMatchObject({ labels: "beside", density: "compact", frame: false })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(12)
  })

  it("sets a Collection's text, its labels, its boxes, its frame and a field's own look as a Table's are set, each one undo step", () => {
    select("w_boxes")
    render(<PropertiesPanel />)
    showPane("Display")
    const region = (name: string) => screen.getByRole("region", { name })
    const boxes = () => fieldsWidget(currentForm(), "w_boxes") as Extract<ReturnType<typeof grid>, { type: "collection" }>
    const type = (testId: string, text: string) => {
      fireEvent.change(screen.getByTestId(testId), { target: { value: text } })
      fireEvent.blur(screen.getByTestId(testId))
    }

    fireEvent.change(within(region("Look")).getByRole("combobox", { name: "Text size" }), { target: { value: "14" } })

    // A label's width is set only beside its box, a whole percentage of its field from 1 to 99.
    const labels = region("Labels")
    expect(screen.queryByTestId("label-width")).not.toBeInTheDocument()
    fireEvent.change(within(labels).getByRole("combobox", { name: "Position" }), { target: { value: "beside" } })
    expect(screen.getByTestId("label-width")).toHaveAttribute("placeholder", "Auto")
    expect(within(labels).getByText("%")).toBeInTheDocument()
    type("label-width", "100")
    expect(screen.getByTestId("label-width-error")).toHaveTextContent("Enter a whole number from 1 to 99.")
    type("label-width", "60")
    fireEvent.change(within(labels).getByRole("combobox", { name: "Case" }), { target: { value: "caps" } })
    fireEvent.change(within(labels).getByRole("combobox", { name: "Size" }), { target: { value: "11" } })
    fireEvent.change(within(labels).getByRole("combobox", { name: "Align" }), { target: { value: "left" } })
    fireEvent.click(within(labels).getByRole("button", { name: "Colour #4B5563 for the labels' fill" }))
    fireEvent.click(within(labels).getByRole("button", { name: "Colour #FFFFFF for the labels' font" }))
    fireEvent.click(within(labels).getByRole("checkbox", { name: "Bold" }))
    fireEvent.click(within(labels).getByRole("checkbox", { name: "Wrap" }))

    // Its boxes' borders, where their values sit, apart from their labels, and their width.
    const boxRegion = region("Boxes")
    fireEvent.change(within(boxRegion).getByRole("combobox", { name: "Style" }), { target: { value: "dotted" } })
    fireEvent.change(within(boxRegion).getByRole("combobox", { name: "Weight" }), { target: { value: "medium" } })
    fireEvent.click(within(boxRegion).getByRole("button", { name: "Colour #000000 for the boxes" }))
    fireEvent.change(within(boxRegion).getByRole("combobox", { name: "Align" }), { target: { value: "center" } })
    type("box-width", "96")

    const frame = region("Frame")
    fireEvent.change(within(frame).getByRole("combobox", { name: "Weight" }), { target: { value: "thick" } })
    fireEvent.click(within(frame).getByRole("button", { name: "Colour #000000 for the frame" }))
    expect(boxes()).toMatchObject({
      font_size: 14,
      labels: "beside",
      label_width: 60,
      header: "caps",
      header_font_size: 11,
      header_color: "#4B5563",
      header_font_color: "#FFFFFF",
      header_bold: true,
      header_wrap: true,
      header_align: "left",
      box_style: "dotted",
      box_weight: "medium",
      box_color: "#000000",
      box_align: "center",
      box_width: 96,
      frame_weight: "thick",
      frame_color: "#000000",
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(17)

    // A field's own look: its label's and its box's fills, fonts and places, its box's width and bold.
    const fields = region("Fields")
    fireEvent.click(within(fields).getByRole("button", { name: "State" }))
    const state = within(fields).getByRole("group", { name: "State's look" })
    fireEvent.click(within(state).getByRole("button", { name: "Colour #2563EB for State's label fill" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #FFFFFF for State's label font" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #D1D5DB for State's fill" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #000000 for State's font" }))
    fireEvent.change(within(state).getByRole("combobox", { name: "State's label align" }), { target: { value: "center" } })
    fireEvent.change(within(state).getByRole("combobox", { name: "State's align" }), { target: { value: "right" } })
    type("column-width-policy:state", "64")
    fireEvent.click(within(state).getByRole("checkbox", { name: "Bold" }))
    expect(boxes().column_looks).toEqual([
      {
        table: "policy",
        column: "state",
        header_color: "#2563EB",
        header_font_color: "#FFFFFF",
        header_align: "center",
        cell_color: "#D1D5DB",
        font_color: "#000000",
        bold: true,
        align: "right",
        width: 64,
        show_when: { match: "all", conditions: [] },
      },
    ])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(25)
    expect(within(fields).getByRole("button", { name: "State, has a look of its own" })).toBeInTheDocument()
    fireEvent.click(within(state).getByRole("button", { name: "Clear" }))
    expect(boxes().column_looks).toEqual([])

    // Blank again, a width is Auto; above its box, a label has no width to set.
    type("box-width", "")
    expect(boxes().box_width).toBeNull()
    fireEvent.change(within(labels).getByRole("combobox", { name: "Position" }), { target: { value: "above" } })
    expect(screen.queryByTestId("label-width")).not.toBeInTheDocument()
  })

  it("sets a Table's text, its header, its lines and its frame as a spreadsheet's are set, each one undo step", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const region = (name: string) => screen.getByRole("region", { name })

    const size = within(region("Look")).getByRole("combobox", { name: "Text size" })
    expect(choices(size).map(([value]) => value)).toEqual(["10", "11", "12", "13", "14", "16", "18"])
    expect(size).toHaveValue("12")
    fireEvent.change(size, { target: { value: "14" } })

    // The header's size is its text's until one is chosen; its labels turn three ways and sit where chosen.
    const header = region("Header")
    const headerSize = within(header).getByRole("combobox", { name: "Size" })
    expect(headerSize).toHaveValue("auto")
    fireEvent.change(headerSize, { target: { value: "16" } })
    const direction = within(header).getByRole("combobox", { name: "Direction" })
    expect(choices(direction)).toEqual([
      ["horizontal", "Horizontal"],
      ["up", "Rotated up"],
      ["down", "Rotated down"],
      ["stacked", "Stacked"],
    ])
    fireEvent.change(direction, { target: { value: "up" } })
    fireEvent.change(within(header).getByRole("combobox", { name: "Align" }), { target: { value: "center" } })
    fireEvent.click(within(header).getByRole("button", { name: "Colour #4B5563 for the header's fill" }))
    fireEvent.click(within(header).getByRole("button", { name: "Colour #FFFFFF for the header's font" }))
    fireEvent.click(within(header).getByRole("checkbox", { name: "Bold" }))
    fireEvent.click(within(header).getByRole("checkbox", { name: "Wrap" }))

    const lines = region("Lines")
    expect(choices(within(lines).getByRole("combobox", { name: "Style" }))).toEqual([
      ["solid", "Solid"],
      ["dashed", "Dashed"],
      ["dotted", "Dotted"],
    ])
    fireEvent.change(within(lines).getByRole("combobox", { name: "Style" }), { target: { value: "dashed" } })
    fireEvent.change(within(lines).getByRole("combobox", { name: "Weight" }), { target: { value: "medium" } })
    fireEvent.click(within(lines).getByRole("button", { name: "Colour #000000 for the lines" }))

    // The frame's weight and colour while it is framed.
    const frame = region("Frame")
    fireEvent.change(within(frame).getByRole("combobox", { name: "Weight" }), { target: { value: "thick" } })
    fireEvent.click(within(frame).getByRole("button", { name: "Colour #000000 for the frame" }))
    expect(grid()).toMatchObject({
      font_size: 14,
      header_font_size: 16,
      header_orientation: "up",
      header_align: "center",
      header_color: "#4B5563",
      header_font_color: "#FFFFFF",
      header_bold: false,
      header_wrap: true,
      grid_style: "dashed",
      grid_weight: "medium",
      grid_color: "#000000",
      frame_weight: "thick",
      frame_color: "#000000",
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(13)

    fireEvent.change(headerSize, { target: { value: "auto" } })
    expect(grid()).toMatchObject({ header_font_size: null })
    fireEvent.click(within(lines).getByRole("button", { name: "Automatic colour for the lines" }))
    expect(grid()).toMatchObject({ grid_color: null })
    fireEvent.click(within(frame).getByRole("checkbox", { name: "Frame" }))
    expect(within(frame).queryByRole("combobox", { name: "Weight" })).not.toBeInTheDocument()
    expect(within(frame).queryByRole("button", { name: "Automatic colour for the frame" })).not.toBeInTheDocument()
  })

  it("shows a colour as it is picked, one undo step as the picker is left, and commits nothing again for a pick an undo finished first", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const picker = screen.getByLabelText("Custom colour for the header's fill")
    const store = useWorkbenchFormStore.getState

    // Shown at each step of the pick, and one undo step once the picker is left.
    fireEvent.change(picker, { target: { value: "#112233" } })
    fireEvent.change(picker, { target: { value: "#445566" } })
    expect(grid()).toMatchObject({ header_color: "#445566" })
    expect(store().undoStack).toHaveLength(0)
    fireEvent.blur(picker)
    expect(grid()).toMatchObject({ header_color: "#445566" })
    expect(store().undoStack).toHaveLength(1)

    // An undo while the picker still has the focus finishes the pick and takes it back;
    // leaving the picker then commits nothing, and the pick can be redone.
    fireEvent.change(picker, { target: { value: "#778899" } })
    act(() => store().undo())
    expect(grid()).toMatchObject({ header_color: "#445566" })
    fireEvent.blur(picker)
    expect(grid()).toMatchObject({ header_color: "#445566" })
    expect(store().redoStack).toHaveLength(1)
    act(() => store().redo())
    expect(grid()).toMatchObject({ header_color: "#778899" })

    // The next pick, and a swatch after it, commit as ever.
    fireEvent.change(picker, { target: { value: "#AABBCC" } })
    fireEvent.blur(picker)
    expect(grid()).toMatchObject({ header_color: "#AABBCC" })
    fireEvent.click(screen.getByRole("button", { name: "Automatic colour for the header's fill" }))
    expect(grid()).toMatchObject({ header_color: null })
  })

  it("gives a Table's column a look of its own from its Columns, each one undo step, and clears it", () => {
    select("w_grid")
    const { rerender } = render(<PropertiesPanel />)
    showPane("Display")
    const columns = screen.getByRole("region", { name: "Columns" })
    const columnButton = (name: string | RegExp) => within(columns).getByRole("button", { name })
    const looks = () => (grid() as Extract<ReturnType<typeof grid>, { type: "tableInput" }>).column_looks

    // Its columns in its order, each closed.
    expect(within(columns).getAllByRole("button", { expanded: false }).map((button) => button.textContent)).toEqual(["Item", "Value"])
    fireEvent.click(columnButton("Item"))
    const item = within(columns).getByRole("group", { name: "Item's look" })
    expect(columnButton("Item")).toHaveAttribute("aria-expanded", "true")
    expect(within(item).queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()

    fireEvent.click(within(item).getByRole("button", { name: "Colour #2563EB for Item's header fill" }))
    fireEvent.click(within(item).getByRole("button", { name: "Colour #FFFFFF for Item's header font" }))
    fireEvent.click(within(item).getByRole("button", { name: "Colour #D1D5DB for Item's fill" }))
    fireEvent.click(within(item).getByRole("button", { name: "Colour #000000 for Item's font" }))
    // Its header and its cells each placed apart.
    const align = within(item).getByRole("combobox", { name: "Item's align" })
    expect(choices(align)).toEqual([
      ["auto", "Auto"],
      ["left", "Left"],
      ["center", "Centre"],
      ["right", "Right"],
    ])
    fireEvent.change(within(item).getByRole("combobox", { name: "Item's header align" }), { target: { value: "left" } })
    fireEvent.change(align, { target: { value: "center" } })
    fireEvent.click(within(item).getByRole("checkbox", { name: "Bold" }))

    // A width is a whole number of pixels from 24 to 1000, blank for as wide as the grid makes it.
    const width = screen.getByTestId("column-width-equipment:item")
    expect(width).toHaveAttribute("placeholder", "Auto")
    fireEvent.change(width, { target: { value: "10" } })
    fireEvent.blur(width)
    expect(screen.getByTestId("column-width-equipment:item-error")).toHaveTextContent("Enter a whole number from 24 to 1000.")
    fireEvent.change(width, { target: { value: "120" } })
    fireEvent.blur(width)
    expect(looks()).toEqual([
      {
        table: "equipment",
        column: "item",
        header_color: "#2563EB",
        header_font_color: "#FFFFFF",
        header_align: "left",
        cell_color: "#D1D5DB",
        font_color: "#000000",
        bold: true,
        align: "center",
        width: 120,
        show_when: { match: "all", conditions: [] },
      },
    ])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(8)
    // Marked in the list as having a look of its own.
    expect(columnButton("Item, has a look of its own")).toBeInTheDocument()
    expect(columnButton("Value")).toBeInTheDocument()

    fireEvent.change(width, { target: { value: "" } })
    fireEvent.blur(width)
    expect(looks()[0].width).toBeNull()

    // Transposed, a column has no width.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_grid", { transposed: true })))
    rerender(<PropertiesPanel />)
    expect(screen.queryByTestId("column-width-equipment:item")).not.toBeInTheDocument()

    fireEvent.click(within(item).getByRole("button", { name: "Clear" }))
    expect(looks()).toEqual([])
    expect(columnButton("Item")).toBeInTheDocument()
    expect(within(item).queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()
  })

  it("gives a Table's column a Show when of its own in its look, which Clear keeps, each one undo step", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const columns = screen.getByRole("region", { name: "Columns" })
    const looks = () => (grid() as Extract<ReturnType<typeof grid>, { type: "tableInput" }>).column_looks
    fireEvent.click(within(columns).getByRole("button", { name: "Item" }))
    const item = within(columns).getByRole("group", { name: "Item's look" })
    expect(within(item).getByText("Always")).toBeInTheDocument()
    const undone = useWorkbenchFormStore.getState().undoStack.length

    fireEvent.click(within(item).getByRole("button", { name: "Add condition" }))
    expect(looks()).toHaveLength(1)
    expect(looks()[0]).toMatchObject({ column: "item", bold: false, show_when: { match: "all" } })
    expect(looks()[0].show_when.conditions).toHaveLength(1)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(undone + 1)
    // Marked in the list with an eye naming its conditions, and with no dot: its look is the Table's.
    expect(within(columns).getByRole("img", { name: /^Shows when / })).toBeInTheDocument()
    expect(within(columns).queryByText(", has a look of its own")).not.toBeInTheDocument()
    expect(within(item).queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()

    // Clear takes its look away and keeps its Show when.
    fireEvent.click(within(item).getByRole("checkbox", { name: "Bold" }))
    fireEvent.click(within(item).getByRole("button", { name: "Clear" }))
    expect(looks()).toHaveLength(1)
    expect(looks()[0]).toMatchObject({ bold: false })
    expect(looks()[0].show_when.conditions).toHaveLength(1)

    // Its last condition removed, it always shows, so it has no look left.
    fireEvent.click(within(item).getByRole("button", { name: "Remove condition 1" }))
    expect(looks()).toEqual([])
    expect(within(item).getByText("Always")).toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(undone + 4)
  })

  it("sets a Chart's type, what labels its rows, its legend, stacked, horizontal and data labels, each one undo step", () => {
    loadForm(chartForm([]))
    select("w_chart")
    const { rerender } = render(<PropertiesPanel />)
    expect(screen.getByText("Chart")).toBeInTheDocument()
    // A Chart's settings are one list, with no panes.
    expect(screen.queryByRole("tablist")).not.toBeInTheDocument()
    expect(screen.queryByRole("region", { name: "Layout" })).not.toBeInTheDocument()
    // Nothing to chart: no settings until it plots a field.
    expect(screen.queryByRole("region", { name: "Chart" })).not.toBeInTheDocument()

    loadForm(chartForm([{ table: "equipment", column: "value" }]))
    select("w_chart")
    rerender(<PropertiesPanel />)
    expect(screen.queryByRole("region", { name: "Layout" })).not.toBeInTheDocument()
    const settings = screen.getByRole("region", { name: "Chart" })
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    // Numbered rows belong to equipment alone; its index is not a label choice.
    const rows = within(settings).getByRole("combobox", { name: "Label rows by" })
    expect(rows).toHaveValue("")
    expect(within(rows).getAllByRole("group").map((group) => group.getAttribute("label"))).toEqual(["equipment"])
    expect(within(rows).getAllByRole("option").map((option) => option.textContent)).toEqual(["Row number", "Item", "Value"])
    fireEvent.change(rows, { target: { value: JSON.stringify(["equipment", "item"]) } })
    expect(chart()).toMatchObject({ category: { table: "equipment", column: "item" } })
    expect(undoSteps()).toBe(1)
    fireEvent.change(rows, { target: { value: "" } })
    expect(chart()).toMatchObject({ category: null })
    expect(undoSteps()).toBe(2)

    fireEvent.change(within(settings).getByRole("combobox", { name: "Legend" }), { target: { value: "right" } })
    expect(chart()).toMatchObject({ legend: "right" })
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Stacked" }))
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Horizontal" }))
    fireEvent.click(within(settings).getByRole("checkbox", { name: "Data labels" }))
    expect(chart()).toMatchObject({ stacked: true, orientation: "horizontal", data_labels: true })
    expect(undoSteps()).toBe(6)

    // A pie is neither stacked nor horizontal; it still takes data labels.
    fireEvent.change(within(settings).getByRole("combobox", { name: "Chart type" }), { target: { value: "pie" } })
    expect(chart()).toMatchObject({ chart_type: "pie" })
    expect(undoSteps()).toBe(7)
    expect(within(settings).getByRole("checkbox", { name: "Stacked" })).toBeDisabled()
    expect(within(settings).getByRole("checkbox", { name: "Horizontal" })).toBeDisabled()
    expect(within(settings).getByRole("checkbox", { name: "Data labels" })).toBeEnabled()

    // Plotting a one-row table, its one row is not labelled.
    loadForm(chartForm([{ table: "policy", column: "year" }]))
    select("w_chart")
    rerender(<PropertiesPanel />)
    expect(screen.getByRole("combobox", { name: "Chart type" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Label rows by" })).not.toBeInTheDocument()
  })

  it("ticks a Chart's fields from every table, the columns it cannot plot disabled and the tables out of line greyed out", () => {
    loadForm(chartForm([]))
    select("w_chart")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })
    const cannotPlot = "A Chart plots number columns other than an index"

    for (const id of ["policy", "equipment", "premiums"]) expect(within(fields).getByTestId(`fields-table-${id}`)).toHaveStyle({ opacity: "1" })
    const state = within(fields).getByRole("checkbox", { name: "Show policy state" })
    expect(state).toBeDisabled()
    expect(state.closest("label")).toHaveAttribute("title", cannotPlot)
    const index = within(fields).getByRole("checkbox", { name: "Show equipment row_number" })
    expect(index).toBeDisabled()
    expect(index.closest("label")).toHaveAttribute("title", cannotPlot)
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" })).toBeEnabled()
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" }).closest("label")).not.toHaveAttribute("title")

    // Once it plots a one-row column, the many-row tables do not line up with it.
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show policy year" }))
    expect(chart().fields).toEqual([{ table: "policy", column: "year" }])
    expect(screen.getByTestId("fields-table-policy")).toHaveStyle({ opacity: "1" })
    const equipment = screen.getByTestId("fields-table-equipment")
    expect(equipment).toHaveStyle({ opacity: "0.5" })
    expect(equipment).toHaveAttribute("title", "Its rows don't line up with the fields already chosen")
    expect(within(equipment).getByRole("checkbox", { name: "Show equipment value" })).toBeDisabled()
    expect(screen.getByTestId("fields-table-premiums")).toHaveStyle({ opacity: "0.5" })
  })

  it("ticks the fields from the schema tables of the component's kind, in the order ticked", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })

    // A Table shows many-row tables: equipment and premiums, not policy.
    expect(within(fields).queryByTestId("fields-table-policy")).not.toBeInTheDocument()
    expect(within(fields).getByText("2/3")).toBeInTheDocument()
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show premiums premium" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["item", "value", "premium"])
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show equipment item" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["value", "premium"])
    expect(screen.getByText("2 shown")).toBeInTheDocument()
  })

  it("never offers the quote's reference and option among a component's fields", () => {
    const form = sheetForm()
    const [equipment] = form.schema.tables.filter((candidate) => candidate.name === "equipment")
    equipment.inputs.unshift(column("quote_reference"), column("quote_option", { type: "int" }))
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })

    expect(within(fields).queryByRole("checkbox", { name: "Show equipment quote_reference" })).toBeNull()
    expect(within(fields).queryByRole("checkbox", { name: "Show equipment quote_option" })).toBeNull()
    // Counted out of what can be shown, and not found by a search.
    expect(within(screen.getByTestId("fields-table-equipment")).getByText("2/3")).toBeInTheDocument()
    fireEvent.change(within(fields).getByRole("textbox", { name: "Search fields" }), { target: { value: "option" } })
    expect(within(fields).getByText("No fields match")).toBeInTheDocument()
  })

  it("greys out a table whose rows do not line up with the fields already chosen", () => {
    const form = sheetForm()
    // Outputs alone keyed by id: not by equipment's row_number, so out of line.
    form.schema.tables.push(table("claims", { rows: "many", outputs: [column("id", { index: "typed" }), column("paid", { type: "float" })] }))
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)

    const claims = screen.getByTestId("fields-table-claims")
    expect(claims).toHaveStyle({ opacity: "0.5" })
    expect(claims).toHaveAttribute("title", "Its rows don't line up with the fields already chosen")
    expect(within(claims).getByRole("checkbox", { name: "Show claims id" })).toBeDisabled()
    expect(screen.getByTestId("fields-table-premiums")).toHaveStyle({ opacity: "0.5" })
    expect(within(screen.getByTestId("fields-table-premiums")).getByRole("checkbox", { name: "Show premiums premium" })).toBeDisabled()
  })

  it("searches the fields as the schema is searched: only the columns matching, by name, label or readable name, each table open, kept as the panes change", () => {
    const form = sheetForm()
    form.schema.tables[1].inputs.push(column("sum_insured", { type: "float", label: "" }))
    form.schema.tables[2].outputs[1].label = "Annual cost"
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)
    const fields = screen.getByRole("region", { name: "Fields" })
    const search = within(fields).getByRole("textbox", { name: "Search fields" })
    const checkboxes = () => within(fields).queryAllByRole("checkbox").map((box) => box.getAttribute("aria-label"))
    fireEvent.click(within(fields).getByRole("button", { name: "Collapse equipment" }))
    expect(within(fields).queryByRole("checkbox", { name: "Show equipment item" })).not.toBeInTheDocument()

    // A name, whatever its case: both tables hold a row_number, the collapsed one shown open.
    fireEvent.change(search, { target: { value: "ROW_NUMBER" } })
    expect(checkboxes()).toEqual(["Show equipment row_number", "Show premiums row_number"])
    const toggle = within(screen.getByTestId("fields-table-equipment")).getByRole("button", { name: "Collapse equipment" })
    expect(toggle).toBeDisabled()
    expect(toggle).toHaveAttribute("aria-expanded", "true")
    // A label, and a name made readable as a column with no label is listed.
    fireEvent.change(search, { target: { value: "annual" } })
    expect(checkboxes()).toEqual(["Show premiums premium"])
    expect(within(fields).queryByTestId("fields-table-equipment")).not.toBeInTheDocument()
    fireEvent.change(search, { target: { value: "sum insured" } })
    expect(checkboxes()).toEqual(["Show equipment sum_insured"])
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show equipment sum_insured" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["item", "value", "sum_insured"])
    fireEvent.change(search, { target: { value: "zzz" } })
    expect(within(fields).getByText("No fields match")).toBeInTheDocument()
    expect(checkboxes()).toEqual([])

    // The panel's own search outlasts a turn to Display and back; Escape empties it, the table collapsed again.
    fireEvent.change(search, { target: { value: "value" } })
    showPane("Display")
    showPane("Config")
    const again = within(screen.getByRole("region", { name: "Fields" })).getByRole("textbox", { name: "Search fields" })
    expect(again).toHaveValue("value")
    expect(within(screen.getByRole("region", { name: "Fields" })).queryAllByRole("checkbox").map((box) => box.getAttribute("aria-label"))).toEqual(["Show equipment value"])
    fireEvent.keyDown(again, { key: "Escape" })
    expect(again).toHaveValue("")
    expect(within(screen.getByRole("region", { name: "Fields" })).getByRole("button", { name: "Expand equipment" })).toBeEnabled()
    expect(within(screen.getByRole("region", { name: "Fields" })).queryAllByRole("checkbox").map((box) => box.getAttribute("aria-label"))).toEqual(["Show premiums row_number", "Show premiums premium"])
  })

  it("orders the fields: Alt+Down moves one, and a cross stops showing it", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const order = screen.getByRole("region", { name: "Order" })

    const rows = within(order).getAllByRole("listitem")
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(["1. Item", "2. Value"])
    fireEvent.keyDown(rows[0], { key: "ArrowDown", altKey: true })
    expect(grid().fields.map((field) => field.column)).toEqual(["value", "item"])

    fireEvent.click(within(order).getByRole("button", { name: "Stop showing Value" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["item"])
    // A Table has no spacers.
    expect(screen.queryByRole("button", { name: "Add spacer" })).not.toBeInTheDocument()
  })

  it("puts spacers among a Collection's fields: added after the last, moved by the keys with the focus or dragged, and removed, each one undo step", () => {
    // The policy's boxes showing its state, cover and year.
    const form = coverForm()
    fieldsWidget(form, "w_boxes").fields.push({ table: "policy", column: "year" })
    loadForm(form)
    select("w_boxes")
    render(<PropertiesPanel />)
    const order = screen.getByRole("region", { name: "Order" })
    const rows = () => within(order).getAllByRole("listitem")
    const labels = () => rows().map((row) => row.getAttribute("aria-label"))
    const spacers = () => {
      const boxes = fieldsWidget(currentForm(), "w_boxes")
      return boxes.type === "collection" ? boxes.spacers : null
    }

    fireEvent.click(within(order).getByRole("button", { name: "Add spacer" }))
    expect(labels()).toEqual(["1. State", "2. Cover", "3. Year", "Spacer"])
    expect(spacers()).toEqual([3])

    // Up twice from the keyboard, between State and Cover, the focus going with it, then down
    // again. Moving an element in the page drops its focus, which jsdom does not, so the row
    // is seen given it where it lands.
    act(() => rows()[3].focus())
    const focus = vi.spyOn(HTMLElement.prototype, "focus")
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp", altKey: true })
    fireEvent.keyDown(document.activeElement!, { key: "ArrowUp", altKey: true })
    expect(labels()).toEqual(["1. State", "Spacer", "2. Cover", "3. Year"])
    expect(focus.mock.contexts.at(-1)).toBe(rows()[1])
    fireEvent.keyDown(document.activeElement!, { key: "ArrowDown", altKey: true })
    expect(labels()).toEqual(["1. State", "2. Cover", "Spacer", "3. Year"])
    expect(focus.mock.contexts.at(-1)).toBe(rows()[2])
    expect(document.activeElement).toBe(rows()[2])
    expect(spacers()).toEqual([2])

    // Dragged onto State, it goes before it.
    fireEvent.dragStart(rows()[2])
    fireEvent.dragOver(rows()[0])
    fireEvent.drop(rows()[0])
    expect(labels()).toEqual(["Spacer", "1. State", "2. Cover", "3. Year"])
    expect(spacers()).toEqual([0])

    // Its cross removes it.
    fireEvent.click(within(order).getByRole("button", { name: "Remove spacer" }))
    expect(labels()).toEqual(["1. State", "2. Cover", "3. Year"])
    expect(spacers()).toEqual([])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(6)
  })

  it("points at the schema when it has no tables of the component's kind, opening its pane with the component still selected", () => {
    const form = sheetForm()
    form.schema.tables = form.schema.tables.filter((candidate) => candidate.rows !== "one")
    loadForm(form)
    select("w_boxes")
    render(<PropertiesPanel />)

    expect(screen.getByText(/The schema has no one-row tables yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Open the schema" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ schemaOpen: true, selectedId: "w_boxes" })
  })

  it("sets when a component shows, each change one undo step", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const showWhen = screen.getByRole("region", { name: "Show when" })

    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })

    expect(grid().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "state", op: "equals", values: ["NY"], field: null, days: 0 }] })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("edits a Heading's text and description as typed, its size, alignment and when it shows, each one undo step, with no title, layout or fields", () => {
    const form = sheetForm()
    addTo(form, headingWidget("w_head", "Cover"))
    loadForm(form)
    select("w_head")
    render(<PropertiesPanel />)
    const heading = () => widgetsOf(currentForm())[2]
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    expect(screen.getByTestId("workbench-properties")).toHaveTextContent("Heading")
    expect(screen.queryByRole("textbox", { name: "Title" })).not.toBeInTheDocument()
    for (const section of ["Layout", "Order", "Fields"]) expect(screen.queryByRole("region", { name: section })).not.toBeInTheDocument()

    // Its text as typed, spaces and all, and cleared when emptied: a blank Heading is a problem, not refused.
    const text = screen.getByRole("textbox", { name: "Heading text" })
    expect(text).toHaveValue("Cover")
    expect(text).toHaveAttribute("placeholder", "Name a section of the sheet")
    fireEvent.change(text, { target: { value: " Equipment schedule " } })
    fireEvent.keyDown(text, { key: "Enter" })
    expect(heading()).toMatchObject({ text: " Equipment schedule ", size: "medium" })
    fireEvent.change(text, { target: { value: "" } })
    fireEvent.blur(text)
    expect(heading()).toMatchObject({ text: "" })
    expect(text).toHaveValue("")
    expect(undoSteps()).toBe(2)

    // Its description, as typed, under it: none until one is typed.
    const description = within(screen.getByRole("region", { name: "Description" })).getByRole("textbox", { name: "Heading description" })
    expect(description).toHaveValue("")
    expect(description).toHaveAttribute("placeholder", "None")
    fireEvent.change(description, { target: { value: " Each item insured " } })
    fireEvent.keyDown(description, { key: "Enter" })
    expect(heading()).toMatchObject({ description: " Each item insured " })
    expect(undoSteps()).toBe(3)

    // Large, Medium or Small, each the size it names.
    const look = screen.getByRole("region", { name: "Look" })
    const size = within(look).getByRole("combobox", { name: "Size" })
    expect([...size.querySelectorAll("option")].map((option) => [option.value, option.textContent])).toEqual([
      ["large", "Large"],
      ["medium", "Medium"],
      ["small", "Small"],
    ])
    expect(size).toHaveValue("medium")
    fireEvent.change(size, { target: { value: "large" } })
    expect(heading()).toMatchObject({ size: "large" })
    expect(size).toHaveValue("large")
    expect(undoSteps()).toBe(4)

    // Left, Centre or Right, one chosen, as a radio group; choosing it again changes nothing.
    const align = within(look).getByRole("radiogroup", { name: "Align" })
    const chosen = () => within(align).getAllByRole("radio").filter((radio) => radio.getAttribute("aria-checked") === "true").map((radio) => radio.textContent)
    expect(within(align).getAllByRole("radio").map((radio) => radio.textContent)).toEqual(["Left", "Centre", "Right"])
    expect(chosen()).toEqual(["Left"])
    fireEvent.click(within(align).getByRole("radio", { name: "Centre" }))
    expect(heading()).toMatchObject({ align: "center" })
    expect(chosen()).toEqual(["Centre"])
    fireEvent.click(within(align).getByRole("radio", { name: "Centre" }))
    // The arrow keys choose the next, the key kept from the sheet's own keys, which would move the Heading.
    expect(fireEvent.keyDown(within(align).getByRole("radio", { name: "Centre" }), { key: "ArrowRight" })).toBe(false)
    expect(heading()).toMatchObject({ align: "right", x: 16 })
    expect(chosen()).toEqual(["Right"])
    expect(undoSteps()).toBe(6)

    // When it shows, so a section's Heading can hide with the section.
    const showWhen = screen.getByRole("region", { name: "Show when" })
    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })
    expect(heading().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "state", op: "equals", values: ["NY"], field: null, days: 0 }] })
    expect(undoSteps()).toBe(8)
  })

  it("is the showing sheet's own panel while the sheet is selected: its title and when it shows", () => {
    loadForm(coverForm())
    useWorkbenchViewStore.setState({ pageId: "p2", sheetSelected: true })
    render(<PropertiesPanel />)
    const sheet = () => currentForm().pages[1]

    expect(screen.getByTestId("workbench-properties")).toHaveTextContent("Sheet")
    const title = screen.getByRole("textbox", { name: "Sheet title" })
    expect(title).toHaveValue("Cover")
    fireEvent.change(title, { target: { value: "  " } })
    fireEvent.blur(title)
    expect(sheet().title).toBe("Cover")
    fireEvent.change(title, { target: { value: " Business interruption " } })
    fireEvent.blur(title)
    expect(sheet().title).toBe("Business interruption")

    const showWhen = screen.getByRole("region", { name: "Show when" })
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_ticked" } })
    expect(sheet().show_when.conditions).toEqual([{ table: "policy", column: "cover", op: "not_ticked", values: [], field: null, days: 0 }])

    act(() => useWorkbenchViewStore.getState().select("w_year"))
    showPane("Display")
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Year")
    expect(useWorkbenchViewStore.getState().sheetSelected).toBe(false)
  })

  it("sets the showing sheet's width in its panel, in whole pixels, never under what its components reach, as one undo step", () => {
    loadForm(sheetForm())
    useWorkbenchViewStore.setState({ pageId: "p1", sheetSelected: true })
    render(<PropertiesPanel />)
    const width = within(screen.getByRole("region", { name: "Layout" })).getByTestId("sheet-width")
    expect(width).toHaveValue("936")

    // Its Table, 16 across and 720 wide, reaches 736; 400 nines are Infinity as a number.
    for (const typed of ["700", "800.5", "wide", "9".repeat(400)]) {
      fireEvent.change(width, { target: { value: typed } })
      fireEvent.blur(width)
      expect(screen.getByTestId("sheet-width-error")).toHaveTextContent("Enter a whole number of pixels, 736 or more.")
      expect(currentForm().pages[0].width).toBe(936)
    }
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
    fireEvent.change(width, { target: { value: "1600" } })
    fireEvent.blur(width)
    expect(currentForm().pages[0].width).toBe(1600)
    expect(screen.queryByTestId("sheet-width-error")).not.toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("sets the showing sheet's height in its panel, its sections' heights summed, through its last section's, never under what that one's components reach, as one undo step", () => {
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ pageId: "p1", sheetSelected: true })
    render(<PropertiesPanel />)
    const height = within(screen.getByRole("region", { name: "Layout" })).getByTestId("sheet-height")
    const heights = () => currentForm().pages[0].sections.map((section) => section.height)
    // 152 + 232 + 72: the bars drawn while building are no part of it.
    expect(height).toHaveValue("456")

    // Notes' Heading, 16 down and 40 tall, reaches 56, so the sheet is no shorter than 152 + 232 + 56.
    for (const typed of ["439", "500.5", "tall"]) {
      fireEvent.change(height, { target: { value: typed } })
      fireEvent.blur(height)
      expect(screen.getByTestId("sheet-height-error")).toHaveTextContent("Enter a whole number of pixels, 440 or more.")
      expect(heights()).toEqual([152, 232, 72])
    }
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
    fireEvent.change(height, { target: { value: "440" } })
    fireEvent.blur(height)
    expect(heights()).toEqual([152, 232, 56])
    fireEvent.change(height, { target: { value: "800" } })
    fireEvent.blur(height)
    expect(heights()).toEqual([152, 232, 416])
    expect(height).toHaveValue("800")
    expect(screen.queryByTestId("sheet-height-error")).not.toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
  })

  it("deletes the showing sheet from the foot of its panel once asked in the page's own question, as one undo step", async () => {
    loadForm(sectionedForm())
    useWorkbenchViewStore.setState({ pageId: "p1", sheetSelected: true })
    render(<PropertiesPanel />)
    const deleteSheet = () => screen.getByRole("button", { name: "Delete sheet" })

    // Asked first, naming the sheet and counting the components in every section; no keeps it.
    fireEvent.click(deleteSheet())
    expect(asked()).toMatchObject({ title: 'Delete "Sheet 1"?', text: "The 3 components on it go with it. The schema and the sample stay.", confirm: "Delete", danger: true })
    await answerQuestion(null)
    expect(currentForm().pages).toHaveLength(2)

    // Delete deletes it with its components, the schema kept; the first sheet left shows, its panel closed.
    fireEvent.click(deleteSheet())
    await answerQuestion("confirm")
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p2"])
    expect(currentForm().schema.tables).toHaveLength(3)
    expect(useWorkbenchViewStore.getState()).toMatchObject({ pageId: "p2", sheetSelected: false })
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("says what goes with a sheet as it asks, one component or nothing, and never deletes the last sheet", () => {
    // Cover holds the Year boxes alone.
    loadForm(coverForm())
    useWorkbenchViewStore.setState({ pageId: "p2", sheetSelected: true })
    const { unmount } = render(<PropertiesPanel />)
    fireEvent.click(screen.getByRole("button", { name: "Delete sheet" }))
    expect(asked()?.text).toBe("The component on it goes with it. The schema and the sample stay.")
    unmount()

    loadForm(sheetForm())
    useWorkbenchViewStore.setState({ pageId: "p2", sheetSelected: true })
    const empty = render(<PropertiesPanel />)
    fireEvent.click(screen.getByRole("button", { name: "Delete sheet" }))
    expect(asked()?.text).toBe("Nothing is on it. The schema and the sample stay.")
    empty.unmount()

    // The only sheet's Delete sheet is disabled.
    const single = sheetForm()
    single.pages = [single.pages[0]]
    loadForm(single)
    useWorkbenchViewStore.setState({ pageId: "p1", sheetSelected: true })
    render(<PropertiesPanel />)
    expect(screen.getByRole("button", { name: "Delete sheet" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Delete sheet" })).toHaveAttribute("title", "The last sheet cannot be deleted")
  })

  it("is a section's own panel while the section is selected: its title, a blank one kept out, and when it shows, each one undo step", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    expect(screen.getByText("Section")).toBeInTheDocument()
    const title = screen.getByRole("textbox", { name: "Section title" })
    expect(title).toHaveValue("Flood")
    fireEvent.change(title, { target: { value: "  " } })
    fireEvent.blur(title)
    expect(sectionOf("s_flood").title).toBe("Flood")
    expect(title).toHaveValue("Flood")
    fireEvent.change(title, { target: { value: " Flood cover " } })
    fireEvent.keyDown(title, { key: "Enter" })
    expect(sectionOf("s_flood").title).toBe("Flood cover")
    expect(undoSteps()).toBe(1)

    // Its own conditions, apart from its sheet's and its components'.
    const showWhen = screen.getByRole("region", { name: "Show when" })
    expect(within(showWhen).getByRole("combobox", { name: "Condition 1 column" })).toHaveValue("policy:cover")
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_ticked" } })
    expect(sectionOf("s_flood").show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "cover", op: "not_ticked", values: [], field: null, days: 0 }] })
    expect(currentForm().pages[0].show_when.conditions).toEqual([])
    expect(grid().show_when.conditions).toEqual([])
    expect(undoSteps()).toBe(2)

    fireEvent.click(screen.getByTitle("Close"))
    expect(useWorkbenchViewStore.getState().sectionId).toBeNull()
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()
  })

  it("sets a section's height in whole pixels, refusing less than its components reach, and fits it to them", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    const height = within(screen.getByRole("region", { name: "Layout" })).getByTestId("section-height")
    expect(height).toHaveValue("232")

    // Its Table, 16 from its top and 200 tall, reaches 216 down it.
    for (const typed of ["200", "240.5", "tall"]) {
      fireEvent.change(height, { target: { value: typed } })
      fireEvent.blur(height)
      expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 216 or more.")
      expect(sectionOf("s_flood").height).toBe(232)
    }
    fireEvent.change(height, { target: { value: "216" } })
    fireEvent.blur(height)
    expect(sectionOf("s_flood").height).toBe(216)
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()

    // Fit leaves 16 under its lowest component.
    fireEvent.click(screen.getByRole("button", { name: "Fit" }))
    expect(sectionOf("s_flood").height).toBe(232)
    expect(height).toHaveValue("232")
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    expect(currentForm().pages[0].sections.map((section) => section.height)).toEqual([152, 232, 72])
  })

  it("refuses a section's height of digits alone past the safe whole numbers, which no form could save, and takes the least its components reach", () => {
    loadForm(sectionedForm())
    openSection("s_notes")
    render(<PropertiesPanel />)
    const height = within(screen.getByRole("region", { name: "Layout" })).getByTestId("section-height")
    expect(height).toHaveValue("72")

    // 400 nines are Infinity as a number, sent as null; 2^53 is the first whole number past
    // the safe ones.
    for (const typed of ["9".repeat(400), "9007199254740992"]) {
      fireEvent.change(height, { target: { value: typed } })
      fireEvent.blur(height)
      expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 56 or more.")
      expect(sectionOf("s_notes").height).toBe(72)
    }
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)

    // Its Heading, 16 from its top and 40 tall, reaches 56 down it: the least it can be.
    fireEvent.change(height, { target: { value: "56" } })
    fireEvent.blur(height)
    expect(sectionOf("s_notes").height).toBe(56)
    expect(height).toHaveValue("56")
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("shows a section its own height, never a draft refused for another section as tall", () => {
    const form = sectionedForm()
    // "Notes" as tall as "Flood", though its Heading reaches only 56 down it.
    form.pages[0].sections[2].height = 232
    loadForm(form)
    openSection("s_flood")
    render(<PropertiesPanel />)
    const height = () => screen.getByTestId("section-height")

    fireEvent.change(height(), { target: { value: "200" } })
    fireEvent.blur(height())
    expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 216 or more.")

    // On to "Notes": its panel shows its own height, and what was typed for "Flood" never
    // reaches it, though 200 would do for it.
    openSection("s_notes")
    expect(height()).toHaveValue("232")
    expect(screen.queryByTestId("section-height-error")).not.toBeInTheDocument()
    fireEvent.focus(height())
    fireEvent.blur(height())
    expect(sectionOf("s_notes").height).toBe(232)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(0)
  })

  it("adds a section below it, 160 tall and named in turn, and selects the new one", () => {
    loadForm(sectionedForm())
    openSection("s_policy")
    render(<PropertiesPanel />)

    fireEvent.click(screen.getByRole("button", { name: "Add section below" }))
    const added = currentForm().pages[0].sections[1]
    expect(sectionIds()).toEqual(["s_policy", added.id, "s_flood", "s_notes"])
    expect(added).toMatchObject({ title: "Section 4", height: 160, show_when: { match: "all", conditions: [] }, widgets: [] })
    expect(useWorkbenchViewStore.getState()).toMatchObject({ sectionId: added.id, selectedId: null, sheetSelected: false })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)

    // Its panel shows now: empty, it can be as little as 48 tall, which Fit makes it.
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Section 4")
    const height = screen.getByTestId("section-height")
    expect(height).toHaveValue("160")
    fireEvent.change(height, { target: { value: "40" } })
    fireEvent.blur(height)
    expect(screen.getByTestId("section-height-error")).toHaveTextContent("Enter a whole number of pixels, 48 or more.")
    fireEvent.click(screen.getByRole("button", { name: "Fit" }))
    expect(sectionOf(added.id).height).toBe(48)
  })

  it("deletes a section with its components, asking first while it holds any, and closes its panel", async () => {
    const form = sectionedForm()
    form.pages[0].sections.push({ id: "s_empty", title: "Empty", height: 48, show_when: { match: "all", conditions: [] }, widgets: [] })
    loadForm(form)
    openSection("s_flood")
    render(<PropertiesPanel />)

    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    expect(asked()).toMatchObject({ title: 'Delete "Flood" and the 1 component in it?', text: "The schema and the sample stay." })
    await answerQuestion(null)
    expect(sectionIds()).toEqual(["s_policy", "s_flood", "s_notes", "s_empty"])
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Flood")

    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    await answerQuestion("confirm")
    expect(sectionIds()).toEqual(["s_policy", "s_notes", "s_empty"])
    expect(findWidget(currentForm(), "w_grid")).toBeNull()
    // The schema stays.
    expect(currentForm().schema.tables).toHaveLength(3)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(useWorkbenchViewStore.getState().sectionId).toBeNull()
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()

    // An empty section goes without asking.
    openSection("s_empty")
    fireEvent.click(screen.getByRole("button", { name: "Delete section" }))
    expect(asked()).toBeNull()
    expect(sectionIds()).toEqual(["s_policy", "s_notes"])
    expect(screen.queryByTestId("workbench-properties")).not.toBeInTheDocument()
  })

  it("never deletes a sheet's only section: its Delete section is disabled", () => {
    openSection("s_p1")
    render(<PropertiesPanel />)

    const remove = screen.getByRole("button", { name: "Delete section" })
    expect(remove).toBeDisabled()
    expect(remove).toHaveAttribute("title", "A sheet keeps at least one section")
    fireEvent.click(remove)
    expect(asked()).toBeNull()
    expect(sectionIds()).toEqual(["s_p1"])
    expect(widgetsOf(currentForm())).toHaveLength(2)
    expect(useWorkbenchViewStore.getState().sectionId).toBe("s_p1")
  })

  it("sets an Import's format and its settings, the table it fills and where its columns are read from, and its label in Display", () => {
    const form = sheetForm()
    addTo(form, { ...createWidget("import", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_in" })
    loadForm(form)
    select("w_in")
    render(<PropertiesPanel />)
    const imported = () => findWidget(currentForm(), "w_in")!.widget as ImportWidget
    const commit = (name: string, value: string) => {
      const field = screen.getByRole("textbox", { name })
      fireEvent.change(field, { target: { value } })
      fireEvent.blur(field)
    }

    expect(screen.getByText("Import")).toBeInTheDocument()
    expect(screen.getByRole("radio", { name: "CSV" })).toHaveAttribute("aria-checked", "true")
    fireEvent.change(screen.getByRole("combobox", { name: "Delimiter" }), { target: { value: "semicolon" } })
    expect(imported().source).toEqual({ format: "csv", delimiter: "semicolon" })
    // No table yet: no rows or columns to set.
    expect(choices(screen.getByRole("combobox", { name: "Into" }))).toEqual([
      ["", "None"],
      ["policy", "policy"],
      ["equipment", "equipment"],
    ])
    expect(screen.queryByRole("combobox", { name: "Rows" })).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole("combobox", { name: "Into" }), { target: { value: "equipment" } })
    expect(choices(screen.getByRole("combobox", { name: "Rows" }))).toEqual([
      ["replace", "Replace"],
      ["append", "Append"],
      ["update", "Update by key"],
    ])
    fireEvent.change(screen.getByRole("combobox", { name: "Rows" }), { target: { value: "update" } })
    expect(screen.getByRole("textbox", { name: "Value is read from" })).toHaveAttribute("placeholder", "value")
    commit("Value is read from", "Sum insured")
    expect(imported()).toMatchObject({ table: "equipment", mode: "update", column_names: [{ column: "value", name: "Sum insured" }] })
    commit("Value is read from", "")
    expect(imported().column_names).toEqual([])
    // A one-row table takes its first row: no Rows.
    fireEvent.change(screen.getByRole("combobox", { name: "Into" }), { target: { value: "policy" } })
    expect(screen.queryByRole("combobox", { name: "Rows" })).not.toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Year is read from" })).toBeInTheDocument()

    fireEvent.click(screen.getByRole("radio", { name: "Excel" }))
    commit("Sheet", "Schedule")
    const header = screen.getByTestId("import-header-row")
    fireEvent.change(header, { target: { value: "101" } })
    fireEvent.blur(header)
    expect(imported().source).toEqual({ format: "excel", sheet: "Schedule", header_row: 1 })
    fireEvent.change(header, { target: { value: "2" } })
    fireEvent.blur(header)
    expect(imported().source).toEqual({ format: "excel", sheet: "Schedule", header_row: 2 })

    fireEvent.click(screen.getByRole("radio", { name: "SQL" }))
    commit("Connection", "policy_db")
    commit("Query", "select value from items where state = :state")
    fireEvent.change(screen.getByRole("combobox", { name: ":state column" }), { target: { value: "policy:state" } })
    expect(imported().source).toEqual({
      format: "sql",
      connection: "policy_db",
      query: "select value from items where state = :state",
      parameters: [{ name: "state", table: "policy", column: "state" }],
    })
    // Only a one-row input table's columns are offered.
    expect(choices(screen.getByRole("combobox", { name: ":state column" })).map(([value]) => value)).toEqual(["", "policy:state", "policy:year"])
    // Offered beside a field one of its parameters reads.
    expect(choices(screen.getByRole("combobox", { name: "Lookup beside" }))).toEqual([
      ["", "None"],
      ["policy:state", "State"],
    ])
    fireEvent.change(screen.getByRole("combobox", { name: "Lookup beside" }), { target: { value: "policy:state" } })
    expect(imported().lookup).toEqual({ table: "policy", column: "state" })

    fireEvent.click(screen.getByRole("radio", { name: "API" }))
    commit("Connection", "crm")
    commit("Path", "/policies/{state}")
    commit("Rows", "data.items")
    fireEvent.change(screen.getByRole("combobox", { name: ":state column" }), { target: { value: "policy:state" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Method" }), { target: { value: "POST" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Send a field" }), { target: { value: "policy.year" } })
    expect(imported().source).toEqual({
      format: "http",
      connection: "crm",
      method: "POST",
      path: "/policies/{state}",
      rows: "data.items",
      parameters: [
        { name: "state", table: "policy", column: "state" },
        { name: "year", table: "policy", column: "year" },
      ],
    })
    expect(screen.getByRole("combobox", { name: ":year column" })).toHaveValue("policy:year")
    // A field sent beside the path's is taken away by its cross; the path's own is not.
    expect(screen.queryByRole("button", { name: "Remove :state" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Remove :year" }))
    expect((imported().source as HttpImport).parameters.map((given) => given.name)).toEqual(["state"])

    showPane("Display")
    commit("Label", "Look up the policy")
    expect(imported().label).toBe("Look up the policy")
    expect(screen.getByRole("region", { name: "Show when" })).toBeInTheDocument()
  })

  it("sets an Export's format, its file's settings or a database's, and the tables it exports", () => {
    const form = sheetForm()
    addTo(form, { ...createWidget("export", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    loadForm(form)
    select("w_out")
    render(<PropertiesPanel />)
    const exported = () => findWidget(currentForm(), "w_out")!.widget as ExportWidget

    const name = screen.getByRole("textbox", { name: "File name" })
    expect(name).toHaveValue("{quote_reference}-{quote_option}")
    fireEvent.change(screen.getByRole("combobox", { name: "Delimiter" }), { target: { value: "tab" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Headers" }), { target: { value: "labels" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Values" }), { target: { value: "formatted" } })
    fireEvent.click(screen.getByRole("checkbox", { name: "premiums" }))
    fireEvent.click(screen.getByRole("checkbox", { name: "policy" }))
    expect(exported()).toMatchObject({ tables: ["premiums", "policy"], target: { format: "csv", delimiter: "tab", headers: "labels", values: "formatted" } })

    fireEvent.click(screen.getByRole("radio", { name: "Excel" }))
    expect(screen.queryByRole("combobox", { name: "Delimiter" })).not.toBeInTheDocument()
    expect(exported().target).toEqual({ file_name: "{quote_reference}-{quote_option}", headers: "labels", values: "formatted", format: "excel" })

    // An Export writes a file: a saved quote is in the store's database already.
    expect(screen.queryByRole("radio", { name: "SQL" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("checkbox", { name: "premiums" }))
    expect(exported()).toMatchObject({ tables: ["policy"] })
  })

  it("lays an Export's Excel out as cells under sheets added and renamed, a row for each that opens for its field, address and label, moved within and between sheets, one added under its sheet's last and one taken away once confirmed", async () => {
    const form = sheetForm()
    addTo(form, { ...createWidget("export", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    loadForm(form)
    select("w_out")
    render(<PropertiesPanel />)
    const exported = () => findWidget(currentForm(), "w_out")!.widget as ExportWidget
    const cells = () => (exported().target as ExcelCellsExport).cells
    const placed = () => cells().map((cell) => `${cell.column}@${cell.sheet}!${cell.cell}`)
    const commit = (name: string, value: string) => {
      const field = screen.getByRole("textbox", { name })
      fireEvent.change(field, { target: { value } })
      fireEvent.blur(field)
    }
    const sheet = (name: string) => screen.getByRole("group", { name: `Sheet ${name}` })
    const rows = (name: string) => within(sheet(name)).queryAllByRole("listitem").map((held) => held.getAttribute("aria-label"))
    const row = (name: string) => screen.getByRole("listitem", { name })
    const settings = (number: number) => screen.getByRole("button", { name: `Cell ${number} settings` })
    const toasts = () => useToastStore.getState().toasts.map((toast) => toast.text)

    fireEvent.click(screen.getByRole("checkbox", { name: "policy" }))
    fireEvent.click(screen.getByRole("radio", { name: "Excel" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Layout" }), { target: { value: "cells" } })
    expect(exported()).toMatchObject({ tables: [], target: { format: "excel_cells", file_name: "{quote_reference}-{quote_option}", cells: [] } })
    // The Format still shows Excel; a workbook of cells has no headers, values or tables.
    expect(exported()).toMatchObject({ target: { blocks: [] } })
    expect(screen.getByRole("radio", { name: "Excel" })).toHaveAttribute("aria-checked", "true")
    for (const gone of ["Headers", "Values"]) expect(screen.queryByRole("combobox", { name: gone })).not.toBeInTheDocument()
    expect(screen.queryByRole("checkbox", { name: "policy" })).not.toBeInTheDocument()
    expect(screen.queryByRole("group", { name: /^Sheet / })).not.toBeInTheDocument()

    // Add a sheet: Sheet1, its name focused, holding no cells, which is saved as nothing.
    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))
    expect(screen.getByRole("textbox", { name: "Sheet Sheet1 name" })).toHaveFocus()
    expect(sheet("Sheet1")).toHaveTextContent("0 cells")
    expect(cells()).toEqual([])

    // Its Add a cell searches the one-row tables' columns, each as table.column.
    expect(offered("Add a cell to Sheet1")).toEqual(["policy.state", "policy.year"])
    expect(offered("Add a cell to Sheet1", "yea")).toEqual(["policy.year"])
    pick("Add a cell to Sheet1", "policy.state")
    pick("Add a cell to Sheet1", "policy.year")
    expect(cells()).toEqual([
      { table: "policy", column: "state", sheet: "Sheet1", cell: "A1", label_cell: "", true_false: null },
      { table: "policy", column: "year", sheet: "Sheet1", cell: "A2", label_cell: "", true_false: null },
    ])
    expect(screen.getByText("2 written")).toBeInTheDocument()
    expect(sheet("Sheet1")).toHaveTextContent("2 cells")
    expect(screen.queryByRole("button", { name: "Remove sheet Sheet1" })).not.toBeInTheDocument()
    expect(rows("Sheet1")).toEqual(["Cell 1 State", "Cell 2 Year"])
    expect(screen.getByTestId("export-cell-1-summary")).toHaveTextContent(/^A1$/)

    // A cell's settings open under it, one cell's at a time, with no Sheet: its sheet is the one it is under.
    fireEvent.click(settings(2))
    expect(settings(2)).toHaveAttribute("aria-expanded", "true")
    expect(screen.queryByRole("textbox", { name: "Cell 2 sheet" })).not.toBeInTheDocument()

    // Two cells meeting are named at the pane's foot, their sheet marked, until they part.
    expect(screen.queryByRole("region", { name: "Clashes" })).not.toBeInTheDocument()
    commit("Cell 2 address", "a1")
    const clashes = screen.getByRole("region", { name: "Clashes" })
    expect(clashes).toHaveTextContent("1 clash")
    expect(within(clashes).getAllByRole("listitem").map((item) => item.textContent)).toEqual(["Sheet1!A1 is written by cell 1 (State) and cell 2 (Year)"])
    expect(screen.getByRole("img", { name: "Clashes on Sheet1" })).toBeInTheDocument()
    commit("Cell 2 address", "A2")
    expect(screen.queryByRole("region", { name: "Clashes" })).not.toBeInTheDocument()
    expect(screen.queryByRole("img", { name: "Clashes on Sheet1" })).not.toBeInTheDocument()

    // Label: None to start, a position past the sheet's edge disabled, Label cell only for Custom.
    const label = (number: number) => screen.getByRole("combobox", { name: `Cell ${number} label` })
    const disabled = (select: HTMLElement) => [...select.querySelectorAll("option")].filter((option) => option.disabled).map((option) => option.value)
    expect(label(2)).toHaveValue("none")
    expect(choices(label(2))).toEqual([["none", "None"], ["above", "Above"], ["under", "Under"], ["left", "Left"], ["right", "Right"], ["custom", "Custom"]])
    expect(disabled(label(2))).toEqual(["left"])
    expect(screen.queryByRole("textbox", { name: "Cell 2 label address" })).not.toBeInTheDocument()
    // A position puts the label in that cell, and goes with the cell to a new address.
    fireEvent.change(label(2), { target: { value: "right" } })
    expect(cells()[1].label_cell).toBe("B2")
    commit("Cell 2 address", " b7 ")
    expect(cells()[1]).toMatchObject({ cell: "B7", label_cell: "C7" })
    expect(label(2)).toHaveValue("right")
    expect(disabled(label(2))).toEqual([])
    fireEvent.change(label(2), { target: { value: "none" } })
    expect(cells()[1].label_cell).toBe("")
    // Custom shows Label cell, stays chosen for an address next to the cell, and leaves the label as the cell moves.
    fireEvent.change(label(2), { target: { value: "custom" } })
    commit("Cell 2 label address", " a7 ")
    expect(label(2)).toHaveValue("custom")
    commit("Cell 2 address", "C9")
    commit("Cell 2 address", "B7")
    expect(screen.getByRole("textbox", { name: "Cell 2 address" })).toHaveValue("B7")
    expect(screen.getByRole("textbox", { name: "Cell 2 label address" })).toHaveValue("A7")
    expect(screen.getByTestId("export-cell-2-summary")).toHaveTextContent(/^B7 · label A7$/)
    fireEvent.click(settings(1))
    expect(settings(2)).toHaveAttribute("aria-expanded", "false")
    expect(screen.queryByRole("textbox", { name: "Cell 2 address" })).not.toBeInTheDocument()
    expect(disabled(label(1))).toEqual(["above", "left"])
    // Opened again, a label next to its cell reads as its position.
    fireEvent.click(settings(2))
    expect(label(2)).toHaveValue("left")
    fireEvent.click(settings(1))

    // Alt+Down on a row moves it within its sheet, its settings open with it and the focus on it; not in a field.
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Cell 1 address" }), { key: "ArrowDown", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Sheet1!B7"])
    fireEvent.keyDown(row("Cell 1 State"), { key: "ArrowDown", altKey: true })
    expect(placed()).toEqual(["year@Sheet1!B7", "state@Sheet1!A1"])
    expect(rows("Sheet1")).toEqual(["Cell 1 Year", "Cell 2 State"])
    expect(row("Cell 2 State")).toHaveFocus()
    expect(settings(2)).toHaveAttribute("aria-expanded", "true")
    expect(screen.getByRole("textbox", { name: "Cell 2 address" })).toHaveValue("A1")
    fireEvent.keyDown(row("Cell 2 State"), { key: "ArrowUp", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Sheet1!B7"])
    expect(settings(1)).toHaveAttribute("aria-expanded", "true")
    // Past the last sheet's last row it stays.
    fireEvent.keyDown(row("Cell 2 Year"), { key: "ArrowDown", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Sheet1!B7"])

    // Another sheet, Sheet2, renamed in its header; a name Excel refuses, or another sheet holds, is refused.
    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))
    expect(sheet("Sheet2")).toHaveTextContent("0 cells")
    commit("Sheet Sheet2 name", "sheet1")
    expect(toasts()).toContain("Another sheet is named sheet1")
    commit("Sheet Sheet2 name", "a/b")
    expect(toasts()).toContain('The sheet name "a/b" holds /')
    expect(screen.getByRole("textbox", { name: "Sheet Sheet2 name" })).toHaveValue("Sheet2")
    commit("Sheet Sheet2 name", "Slip")
    expect(screen.queryByRole("group", { name: "Sheet Sheet2" })).not.toBeInTheDocument()
    expect(sheet("Slip")).toHaveTextContent("0 cells")

    // Past its sheet's last row a cell goes into the sheet after, and past its first back.
    fireEvent.keyDown(row("Cell 2 Year"), { key: "ArrowDown", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Slip!B7"])
    expect(rows("Slip")).toEqual(["Cell 2 Year"])
    expect(row("Cell 2 Year")).toHaveFocus()
    fireEvent.keyDown(row("Cell 2 Year"), { key: "ArrowUp", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Sheet1!B7"])
    // Slip, emptied, stays until it is taken away or a cell comes back.
    expect(sheet("Slip")).toHaveTextContent("0 cells")
    fireEvent.keyDown(row("Cell 2 Year"), { key: "ArrowDown", altKey: true })
    expect(placed()).toEqual(["state@Sheet1!A1", "year@Slip!B7"])

    pick("Cell 1 field", "policy.year")
    expect(cells()).toEqual([
      { table: "policy", column: "year", sheet: "Sheet1", cell: "A1", label_cell: "", true_false: null },
      { table: "policy", column: "year", sheet: "Slip", cell: "B7", label_cell: "A7", true_false: null },
    ])

    // A sheet's next cell goes under its last, its label under that cell's label.
    pick("Add a cell to Slip", "policy.state")
    expect(cells()[2]).toEqual({ table: "policy", column: "state", sheet: "Slip", cell: "B8", label_cell: "A8", true_false: null })
    // A cell is taken away from its settings, once Remove is confirmed.
    expect(screen.queryByRole("button", { name: "Remove cell 3" })).not.toBeInTheDocument()
    fireEvent.click(settings(3))
    fireEvent.click(screen.getByRole("button", { name: "Remove cell 3" }))
    expect(asked()).toMatchObject({ title: "Remove cell 3, State?", text: "It stops writing to Slip!B8, and its label to A8.", confirm: "Remove", danger: true })
    await answerQuestion(null)
    expect(cells()).toHaveLength(3)
    fireEvent.click(screen.getByRole("button", { name: "Remove cell 3" }))
    await answerQuestion("confirm")
    expect(cells()).toHaveLength(2)

    // Sheet1's last cell taken away, Sheet1 stays, empty, until its cross takes it away.
    fireEvent.click(settings(1))
    fireEvent.click(screen.getByRole("button", { name: "Remove cell 1" }))
    expect(asked()?.text).toBe("It stops writing to Sheet1!A1.")
    await answerQuestion("confirm")
    expect(cells()).toEqual([{ table: "policy", column: "year", sheet: "Slip", cell: "B7", label_cell: "A7", true_false: null }])
    expect(sheet("Sheet1")).toHaveTextContent("0 cells")
    fireEvent.click(screen.getByRole("button", { name: "Remove sheet Sheet1" }))
    expect(screen.queryByRole("group", { name: "Sheet Sheet1" })).not.toBeInTheDocument()

    // A sheet holding cells renamed renames them, the focus kept; closed, it shows none of its rows.
    const slipName = screen.getByRole("textbox", { name: "Sheet Slip name" })
    slipName.focus()
    fireEvent.change(slipName, { target: { value: "Summary" } })
    fireEvent.keyDown(slipName, { key: "Enter" })
    expect(cells()[0].sheet).toBe("Summary")
    expect(screen.getByRole("textbox", { name: "Sheet Summary name" })).toHaveFocus()
    fireEvent.click(screen.getByRole("button", { name: "Collapse sheet Summary" }))
    expect(rows("Summary")).toEqual([])
    expect(screen.queryByRole("button", { name: "Add a cell to Summary" })).not.toBeInTheDocument()
    // Closed, and a sheet added and empty, both outlast the Display pane.
    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))
    fireEvent.click(screen.getByRole("tab", { name: "Display" }))
    fireEvent.click(screen.getByRole("tab", { name: "Config" }))
    expect(rows("Summary")).toEqual([])
    expect(sheet("Sheet1")).toHaveTextContent("0 cells")
    fireEvent.click(screen.getByRole("button", { name: "Remove sheet Sheet1" }))
    fireEvent.click(screen.getByRole("button", { name: "Expand sheet Summary" }))
    expect(rows("Summary")).toEqual(["Cell 1 Year"])

    // A cell whose column is gone, or of a many-row table, shows it, never offered.
    act(() => useWorkbenchFormStore.getState().change((spec) => setExportCellColumn(spec, "w_out", 0, { table: "policy", column: "gone" })))
    expect(rows("Summary")).toEqual(["Cell 1 Missing column"])
    fireEvent.click(settings(1))
    expect(screen.getByRole("button", { name: "Cell 1 field" })).toHaveTextContent("policy.gone")
    expect(offered("Cell 1 field")).toEqual(["policy.state", "policy.year"])
    act(() => useWorkbenchFormStore.getState().change((spec) => setExportCellColumn(spec, "w_out", 0, { table: "equipment", column: "value" })))
    expect(rows("Summary")).toEqual(["Cell 1 Value"])
    // A change from outside the list (here the store's, as an undo's) starts its rows afresh, closed.
    expect(settings(1)).toHaveAttribute("aria-expanded", "false")
    fireEvent.click(settings(1))
    expect(screen.getByRole("button", { name: "Cell 1 field" })).toHaveTextContent("equipment.value")
    expect(offered("Cell 1 field")).toEqual(["policy.state", "policy.year"])

    fireEvent.change(screen.getByRole("combobox", { name: "Layout" }), { target: { value: "tables" } })
    expect(exported().target).toEqual({ file_name: "{quote_reference}-{quote_option}", headers: "names", values: "raw", format: "excel" })
  })

  it("shows a workbook of cells whose cells interleave sheets grouped, numbered as shown, and writes them grouped, under one spelling, at the next change", () => {
    const form = sheetForm()
    addTo(form, { ...createWidget("export", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    const widget = findWidget(form, "w_out")!.widget as ExportWidget
    const cell = (column: string, sheet: string, address: string) => ({ table: "policy", column, sheet, cell: address, label_cell: "", true_false: null })
    widget.target = { format: "excel_cells", file_name: "slip", cells: [cell("state", "Slip", "B1"), cell("year", "Other", "B1"), cell("year", "SLIP", "B2")], blocks: [] }
    form.schema.tables.find((held) => held.name === "policy")!.inputs.find((held) => held.name === "state")!.label = "Risk state"
    loadForm(form)
    select("w_out")
    render(<PropertiesPanel />)
    const placed = () => ((findWidget(currentForm(), "w_out")!.widget as ExportWidget).target as ExcelCellsExport).cells.map((held) => `${held.column}@${held.sheet}!${held.cell}`)
    const rows = (name: string) => within(screen.getByRole("group", { name: `Sheet ${name}` })).getAllByRole("listitem").map((held) => held.getAttribute("aria-label"))

    // Slip, as first spelled, first, both its cells together; then Other.
    expect(screen.getAllByRole("group", { name: /^Sheet / }).map((group) => group.getAttribute("aria-label"))).toEqual(["Sheet Slip", "Sheet Other"])
    expect(rows("Slip")).toEqual(["Cell 1 Risk state", "Cell 2 Year"])
    expect(rows("Other")).toEqual(["Cell 3 Year"])
    expect(placed()).toEqual(["state@Slip!B1", "year@Other!B1", "year@SLIP!B2"])
    // A field found by its label as well as its name.
    expect(offered("Add a cell to Other", "risk")).toEqual(["policy.state"])
    fireEvent.click(screen.getByRole("button", { name: "Cell 3 settings" }))
    const address = screen.getByRole("textbox", { name: "Cell 3 address" })
    fireEvent.change(address, { target: { value: "C1" } })
    fireEvent.blur(address)
    expect(placed()).toEqual(["state@Slip!B1", "year@Slip!B2", "year@Other!C1"])
  })

  it("lays an Export's tables out as blocks, each with its sheet, its top-left cell, its header and its columns in an order of its own", () => {
    const form = sheetForm()
    addTo(form, { ...createWidget("export", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    loadForm(form)
    select("w_out")
    render(<PropertiesPanel />)
    const blocks = () => ((findWidget(currentForm(), "w_out")!.widget as ExportWidget).target as ExcelCellsExport).blocks
    const commit = (name: string, value: string) => {
      const field = screen.getByRole("textbox", { name })
      fireEvent.change(field, { target: { value } })
      fireEvent.blur(field)
    }
    const change = (edit: (spec: FormSpec) => FormSpec) => act(() => useWorkbenchFormStore.getState().change(edit))

    fireEvent.click(screen.getByRole("radio", { name: "Excel" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Layout" }), { target: { value: "cells" } })

    // Add a table offers each table a sheet shows columns of, and shows itself again.
    const add = screen.getByRole("combobox", { name: "Add a table" })
    expect(choices(add)).toEqual([["", "Add a table"], ["policy", "policy"], ["equipment", "equipment"], ["premiums", "premiums"]])
    fireEvent.change(add, { target: { value: "equipment" } })
    expect(screen.getByRole("combobox", { name: "Add a table" })).toHaveValue("")
    expect(blocks()).toEqual([{ table: "equipment", columns: [], sheet: "equipment", cell: "A1", header: "labels", true_false: null }])
    expect(screen.getByText("1 written")).toBeInTheDocument()

    commit("Table 1 sheet", "Slip")
    commit("Table 1 address", " a12 ")
    fireEvent.change(screen.getByRole("combobox", { name: "Table 1 header" }), { target: { value: "names" } })
    expect(blocks()[0]).toMatchObject({ sheet: "Slip", cell: "A12", header: "names" })
    expect(screen.getByRole("textbox", { name: "Table 1 address" })).toHaveValue("A12")
    expect(choices(screen.getByRole("combobox", { name: "Table 1 header" }))).toEqual([["labels", "Labels"], ["names", "Names"], ["none", "None"]])

    // Add a column offers the table's columns as a sheet shows them, the index among them,
    // that the block does not hold yet, each by its label.
    const addColumn = () => screen.getByRole("combobox", { name: "Table 1 add a column" })
    expect(choices(addColumn())).toEqual([["", "Add a column"], ["row_number", "Row number"], ["item", "Item"], ["value", "Value"]])
    fireEvent.change(addColumn(), { target: { value: "value" } })
    fireEvent.change(addColumn(), { target: { value: "item" } })
    expect(addColumn()).toHaveValue("")
    expect(choices(addColumn())).toEqual([["", "Add a column"], ["row_number", "Row number"]])
    expect(blocks()[0].columns).toEqual(["value", "item"])
    const rows = () => within(screen.getByRole("list", { name: "Table 1 columns" })).getAllByRole("listitem")
    expect(rows().map((row) => row.getAttribute("aria-label"))).toEqual(["Value", "Item"])

    // Alt+Up moves a column up the order, which is the order left to right; its cross takes it out.
    fireEvent.keyDown(rows()[1], { key: "ArrowUp", altKey: true })
    expect(blocks()[0].columns).toEqual(["item", "value"])
    expect(rows().map((row) => row.getAttribute("aria-label"))).toEqual(["Item", "Value"])
    fireEvent.click(screen.getByRole("button", { name: "Remove Value from table 1" }))
    expect(blocks()[0].columns).toEqual(["item"])

    // A column, then a table, gone from the schema stays, shown as missing.
    change((spec) => removeSchemaColumn(spec, "equipment", "item"))
    expect(rows().map((row) => row.getAttribute("aria-label"))).toEqual(["Missing column"])
    change((spec) => removeSchemaTable(spec, "equipment"))
    expect(screen.getByText("Missing table")).toBeInTheDocument()
    expect(choices(addColumn())).toEqual([["", "Add a column"]])
    fireEvent.click(screen.getByRole("button", { name: "Remove table 1" }))
    expect(blocks()).toEqual([])
    expect(screen.queryByRole("combobox", { name: "Table 1 header" })).not.toBeInTheDocument()
  })

  it("writes a tick's True/False text, a preset or its own, under a True/false cell and under a block writing a tick", () => {
    const form = sheetForm()
    form.schema.tables[0].inputs.push(column("sprinklered", { type: "bool" }))
    form.schema.tables[1].inputs.push(column("insured", { type: "bool" }))
    addTo(form, { ...createWidget("export", { x: 16, y: 380, w: 160, h: 40 }, form), id: "w_out" })
    loadForm(form)
    select("w_out")
    render(<PropertiesPanel />)
    const target = () => (findWidget(currentForm(), "w_out")!.widget as ExportWidget).target as ExcelCellsExport
    const commit = (name: string, value: string) => {
      const field = screen.getByRole("textbox", { name })
      fireEvent.change(field, { target: { value } })
      fireEvent.blur(field)
    }

    fireEvent.click(screen.getByRole("radio", { name: "Excel" }))
    fireEvent.change(screen.getByRole("combobox", { name: "Layout" }), { target: { value: "cells" } })
    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))
    pick("Add a cell to Sheet1", "policy.state")
    pick("Add a cell to Sheet1", "policy.sprinklered")
    // Only a tick's cell has a True/False, Excel's own TRUE and FALSE to start.
    fireEvent.click(screen.getByRole("button", { name: "Cell 1 settings" }))
    expect(screen.getByRole("textbox", { name: "Cell 1 address" })).toBeInTheDocument()
    expect(screen.queryByRole("combobox", { name: "Cell 1 True/False" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Cell 2 settings" }))
    const preset = () => screen.getByRole("combobox", { name: "Cell 2 True/False" })
    expect(preset()).toHaveValue("excel")
    expect(choices(preset())).toEqual([
      ["excel", "TRUE / FALSE"],
      ["x", "X / blank"],
      ["blank_x", "blank / X"],
      ["yes_no", "Yes / No"],
      ["custom", "Custom"],
    ])
    fireEvent.change(preset(), { target: { value: "blank_x" } })
    expect(target().cells[1].true_false).toEqual({ true: "", false: "X" })
    expect(screen.getByTestId("export-cell-2-summary")).toHaveTextContent(/^A2 · blank \/ X$/)
    expect(screen.queryByRole("textbox", { name: "Cell 2 True text" })).not.toBeInTheDocument()

    // Custom shows the two texts, each committed on blur.
    fireEvent.change(preset(), { target: { value: "custom" } })
    expect(preset()).toHaveValue("custom")
    commit("Cell 2 True text", "Covered")
    commit("Cell 2 False text", "Not covered")
    expect(target().cells[1].true_false).toEqual({ true: "Covered", false: "Not covered" })
    expect(screen.getByTestId("export-cell-2-summary")).toHaveTextContent(/^A2 · Covered \/ Not covered$/)
    fireEvent.change(preset(), { target: { value: "excel" } })
    expect(target().cells[1].true_false).toBeNull()

    // A field that is not a tick takes the text away, and its True/False with it.
    fireEvent.change(preset(), { target: { value: "yes_no" } })
    pick("Cell 2 field", "policy.year")
    expect(target().cells[1].true_false).toBeNull()
    expect(screen.queryByRole("combobox", { name: "Cell 2 True/False" })).not.toBeInTheDocument()

    // A block has one once it writes a tick, for every tick it writes.
    fireEvent.change(screen.getByRole("combobox", { name: "Add a table" }), { target: { value: "equipment" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Table 1 add a column" }), { target: { value: "item" } })
    expect(screen.queryByRole("combobox", { name: "Table 1 True/False" })).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("combobox", { name: "Table 1 add a column" }), { target: { value: "insured" } })
    fireEvent.change(screen.getByRole("combobox", { name: "Table 1 True/False" }), { target: { value: "x" } })
    expect(target().blocks[0].true_false).toEqual({ true: "X", false: "" })
    // Text a block keeps once it writes no tick still shows, so it can be taken away.
    fireEvent.click(screen.getByRole("button", { name: "Remove Insured from table 1" }))
    expect(screen.getByRole("combobox", { name: "Table 1 True/False" })).toHaveValue("x")
  })

  it("shows another section's panel as it is selected, and closes as a component is", () => {
    loadForm(sectionedForm())
    openSection("s_flood")
    render(<PropertiesPanel />)
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Flood")

    openSection("s_notes")
    expect(screen.getByRole("textbox", { name: "Section title" })).toHaveValue("Notes")
    expect(screen.getByTestId("section-height")).toHaveValue("72")

    act(() => useWorkbenchViewStore.getState().select("w_grid"))
    expect(screen.queryByRole("textbox", { name: "Section title" })).not.toBeInTheDocument()
    showPane("Display")
    expect(screen.getByRole("textbox", { name: "Title" })).toHaveValue("Equipment")
    expect(useWorkbenchViewStore.getState()).toMatchObject({ selectedId: "w_grid", sectionId: null })
  })
})
