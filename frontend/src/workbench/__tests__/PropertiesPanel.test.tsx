/**
 * The properties panel (specs/workbench): the selected component's title, Show when and
 * layout, a Table's fixed rows and look, a Collection's look, a Chart's chart, the order of its fields, and the
 * fields ticked from the schema tables of its kind (a Chart's from every table, the columns
 * it cannot plot disabled), with tables whose rows do not line up greyed out; a Heading's
 * text, description, size, alignment and Show when; the showing sheet's own panel, its
 * title, its Show when, its width and its deletion, asked first in the editor's own dialog;
 * and a section's panel, its title, its Show when, its
 * height and Fit, a section added below it, and its deletion.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { findSection, findWidget, updateWidget } from "../../utils/workbenchForm"
import { answerQuestion, asked } from "../../test-utils/question"
import PropertiesPanel from "../PropertiesPanel"
import type { FieldRef, FormSpec, Section } from "../../api/types"
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
/** A select's choices, each its value and its label. */
const choices = (select: HTMLElement) => [...select.querySelectorAll("option")].map((option) => [option.value, option.textContent])

/** The form's section `id`, as it is now. */
function sectionOf(id: string): Section {
  const found = findSection(currentForm(), id)
  if (found === null) throw new Error(`The form has no section ${id}`)
  return found.section
}

/** `sheetForm` with a row-number index on equipment and a Chart, w_chart, plotting `fields`. */
function chartForm(fields: FieldRef[]): FormSpec {
  const form = sheetForm()
  form.schema.tables[1].columns.unshift(column("equipment.row", "row", { type: "int", index: true }))
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

    // Config first, in the component's colour: its order and its fields.
    expect(within(screen.getByRole("tablist", { name: "Table panes" })).getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Config", "Display"])
    const config = screen.getByRole("tab", { name: "Config" })
    expect(config).toHaveAttribute("aria-selected", "true")
    expect(config).toHaveStyle({ color: COMPONENT_COLOR })
    expect(pane()).toHaveAttribute("aria-labelledby", "component-config-tab")
    expect(regions()).toEqual(["Order", "Fields"])
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

  it("starts a Table's grid in the sample again at the rows it is given, keeping every value typed, each one undo step", () => {
    const form = sheetForm()
    form.sample = { equipment: [{}, { "equipment.item": "Crane" }, {}, {}, {}] }
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
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }])
    // More: empty rows make them up.
    setRows("4")
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }, {}, {}])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(2)
    act(() => useWorkbenchFormStore.getState().undo())
    expect(grid()).toMatchObject({ rows: 1 })
    expect(currentForm().sample.equipment).toEqual([{}, { "equipment.item": "Crane" }])
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

    // A label's width is set only beside its box, in whole pixels from 24 to 1000.
    const labels = region("Labels")
    expect(screen.queryByTestId("label-width")).not.toBeInTheDocument()
    fireEvent.change(within(labels).getByRole("combobox", { name: "Position" }), { target: { value: "beside" } })
    expect(screen.getByTestId("label-width")).toHaveAttribute("placeholder", "Auto")
    type("label-width", "10")
    expect(screen.getByTestId("label-width-error")).toHaveTextContent("Enter a whole number from 24 to 1000.")
    type("label-width", "160")
    fireEvent.change(within(labels).getByRole("combobox", { name: "Case" }), { target: { value: "caps" } })
    fireEvent.change(within(labels).getByRole("combobox", { name: "Size" }), { target: { value: "11" } })
    fireEvent.click(within(labels).getByRole("button", { name: "Colour #4B5563 for the labels' fill" }))
    fireEvent.click(within(labels).getByRole("button", { name: "Colour #FFFFFF for the labels' font" }))
    fireEvent.click(within(labels).getByRole("checkbox", { name: "Bold" }))
    fireEvent.click(within(labels).getByRole("checkbox", { name: "Wrap" }))

    // Its boxes' borders and their width.
    const boxRegion = region("Boxes")
    fireEvent.change(within(boxRegion).getByRole("combobox", { name: "Style" }), { target: { value: "dotted" } })
    fireEvent.change(within(boxRegion).getByRole("combobox", { name: "Weight" }), { target: { value: "medium" } })
    fireEvent.click(within(boxRegion).getByRole("button", { name: "Colour #000000 for the boxes" }))
    type("box-width", "96")

    const frame = region("Frame")
    fireEvent.change(within(frame).getByRole("combobox", { name: "Weight" }), { target: { value: "thick" } })
    fireEvent.click(within(frame).getByRole("button", { name: "Colour #000000 for the frame" }))
    expect(boxes()).toMatchObject({
      font_size: 14,
      labels: "beside",
      label_width: 160,
      header: "caps",
      header_font_size: 11,
      header_color: "#4B5563",
      header_font_color: "#FFFFFF",
      header_bold: true,
      header_wrap: true,
      box_style: "dotted",
      box_weight: "medium",
      box_color: "#000000",
      box_width: 96,
      frame_weight: "thick",
      frame_color: "#000000",
    })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(15)

    // A field's own look: its label's and its box's fills and fonts, its place, its box's width and bold.
    const fields = region("Fields")
    fireEvent.click(within(fields).getByRole("button", { name: "State" }))
    const state = within(fields).getByRole("group", { name: "State's look" })
    fireEvent.click(within(state).getByRole("button", { name: "Colour #2563EB for State's label fill" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #FFFFFF for State's label font" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #D1D5DB for State's fill" }))
    fireEvent.click(within(state).getByRole("button", { name: "Colour #000000 for State's font" }))
    fireEvent.change(within(state).getByRole("combobox", { name: "Align" }), { target: { value: "right" } })
    type("column-width-policy:policy.state", "64")
    fireEvent.click(within(state).getByRole("checkbox", { name: "Bold" }))
    expect(boxes().column_looks).toEqual([
      {
        table: "policy",
        column: "policy.state",
        header_color: "#2563EB",
        header_font_color: "#FFFFFF",
        cell_color: "#D1D5DB",
        font_color: "#000000",
        bold: true,
        align: "right",
        width: 64,
      },
    ])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(22)
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

    // The header's size is its text's until one is chosen; its labels turn three ways.
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
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(12)

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
    const align = within(item).getByRole("combobox", { name: "Align" })
    expect(choices(align)).toEqual([
      ["auto", "Auto"],
      ["left", "Left"],
      ["center", "Centre"],
      ["right", "Right"],
    ])
    fireEvent.change(align, { target: { value: "center" } })
    fireEvent.click(within(item).getByRole("checkbox", { name: "Bold" }))

    // A width is a whole number of pixels from 24 to 1000, blank for as wide as the grid makes it.
    const width = screen.getByTestId("column-width-equipment:equipment.item")
    expect(width).toHaveAttribute("placeholder", "Auto")
    fireEvent.change(width, { target: { value: "10" } })
    fireEvent.blur(width)
    expect(screen.getByTestId("column-width-equipment:equipment.item-error")).toHaveTextContent("Enter a whole number from 24 to 1000.")
    fireEvent.change(width, { target: { value: "120" } })
    fireEvent.blur(width)
    expect(looks()).toEqual([
      {
        table: "equipment",
        column: "equipment.item",
        header_color: "#2563EB",
        header_font_color: "#FFFFFF",
        cell_color: "#D1D5DB",
        font_color: "#000000",
        bold: true,
        align: "center",
        width: 120,
      },
    ])
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(7)
    // Marked in the list as having a look of its own.
    expect(columnButton("Item, has a look of its own")).toBeInTheDocument()
    expect(columnButton("Value")).toBeInTheDocument()

    fireEvent.change(width, { target: { value: "" } })
    fireEvent.blur(width)
    expect(looks()[0].width).toBeNull()

    // Transposed, a column has no width.
    act(() => useWorkbenchFormStore.getState().change((spec) => updateWidget(spec, "w_grid", { transposed: true })))
    rerender(<PropertiesPanel />)
    expect(screen.queryByTestId("column-width-equipment:equipment.item")).not.toBeInTheDocument()

    fireEvent.click(within(item).getByRole("button", { name: "Clear" }))
    expect(looks()).toEqual([])
    expect(columnButton("Item")).toBeInTheDocument()
    expect(within(item).queryByRole("button", { name: "Clear" })).not.toBeInTheDocument()
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

    loadForm(chartForm([{ table: "equipment", column: "equipment.value" }]))
    select("w_chart")
    rerender(<PropertiesPanel />)
    expect(screen.queryByRole("region", { name: "Layout" })).not.toBeInTheDocument()
    const settings = screen.getByRole("region", { name: "Chart" })
    const undoSteps = () => useWorkbenchFormStore.getState().undoStack.length

    // Rows: numbered, or labelled by a column of a table lined up with it, other than an index.
    const rows = within(settings).getByRole("combobox", { name: "Label rows by" })
    expect(rows).toHaveValue("")
    expect(within(rows).getAllByRole("group").map((group) => group.getAttribute("label"))).toEqual(["equipment", "premiums"])
    expect(within(rows).getAllByRole("option").map((option) => option.textContent)).toEqual(["Row number", "Item", "Value", "Item", "Premium"])
    fireEvent.change(rows, { target: { value: JSON.stringify(["equipment", "equipment.item"]) } })
    expect(chart()).toMatchObject({ category: { table: "equipment", column: "equipment.item" } })
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
    loadForm(chartForm([{ table: "policy", column: "policy.year" }]))
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
    const index = within(fields).getByRole("checkbox", { name: "Show equipment row" })
    expect(index).toBeDisabled()
    expect(index.closest("label")).toHaveAttribute("title", cannotPlot)
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" })).toBeEnabled()
    expect(within(fields).getByRole("checkbox", { name: "Show premiums premium" }).closest("label")).not.toHaveAttribute("title")

    // Once it plots a one-row column, the many-row tables do not line up with it.
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show policy year" }))
    expect(chart().fields).toEqual([{ table: "policy", column: "policy.year" }])
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
    expect(within(fields).getByText("2/2")).toBeInTheDocument()
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show premiums premium" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.item", "equipment.value", "premiums.premium"])
    fireEvent.click(within(fields).getByRole("checkbox", { name: "Show equipment item" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.value", "premiums.premium"])
    expect(screen.getByText("2 shown")).toBeInTheDocument()
  })

  it("greys out a table whose rows do not line up with the fields already chosen", () => {
    const form = sheetForm()
    form.schema.tables.push(table("claims", "claims", { rows: "many", columns: [column("claims.id", "id", { key: true })] }))
    loadForm(form)
    select("w_grid")
    render(<PropertiesPanel />)

    const claims = screen.getByTestId("fields-table-claims")
    expect(claims).toHaveStyle({ opacity: "0.5" })
    expect(claims).toHaveAttribute("title", "Its rows don't line up with the fields already chosen")
    expect(within(claims).getByRole("checkbox", { name: "Show claims id" })).toBeDisabled()
    expect(screen.getByTestId("fields-table-premiums")).toHaveStyle({ opacity: "1" })
  })

  it("orders the fields: Alt+Down moves one, and a cross stops showing it", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    const order = screen.getByRole("region", { name: "Order" })

    const rows = within(order).getAllByRole("listitem")
    expect(rows.map((row) => row.getAttribute("aria-label"))).toEqual(["1. Item", "2. Value"])
    fireEvent.keyDown(rows[0], { key: "ArrowDown", altKey: true })
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.value", "equipment.item"])

    fireEvent.click(within(order).getByRole("button", { name: "Stop showing Value" }))
    expect(grid().fields.map((field) => field.column)).toEqual(["equipment.item"])
    // A Table has no spacers.
    expect(screen.queryByRole("button", { name: "Add spacer" })).not.toBeInTheDocument()
  })

  it("puts spacers among a Collection's fields: added after the last, moved by the keys with the focus or dragged, and removed, each one undo step", () => {
    // The policy's boxes showing its state, cover and year.
    const form = coverForm()
    fieldsWidget(form, "w_boxes").fields.push({ table: "policy", column: "policy.year" })
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

  it("points at the schema when it has no tables of the component's kind", () => {
    const form = sheetForm()
    form.schema.tables = form.schema.tables.filter((candidate) => candidate.rows !== "one")
    loadForm(form)
    select("w_boxes")
    render(<PropertiesPanel />)

    expect(screen.getByText(/The schema has no one-row tables yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Open the schema" }))
    expect(useWorkbenchViewStore.getState().section).toBe("schema")
  })

  it("sets when a component shows, each change one undo step", () => {
    select("w_grid")
    render(<PropertiesPanel />)
    showPane("Display")
    const showWhen = screen.getByRole("region", { name: "Show when" })

    expect(within(showWhen).getByText("Always")).toBeInTheDocument()
    fireEvent.click(within(showWhen).getByRole("button", { name: "Add condition" }))
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 value" }), { target: { value: "NY" } })

    expect(grid().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] })
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
    expect(heading().show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.state", op: "equals", values: ["NY"] }] })
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
    expect(sheet().show_when.conditions).toEqual([{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }])

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
    expect(within(showWhen).getByRole("combobox", { name: "Condition 1 column" })).toHaveValue("policy:policy.cover")
    fireEvent.change(within(showWhen).getByRole("combobox", { name: "Condition 1 test" }), { target: { value: "not_ticked" } })
    expect(sectionOf("s_flood").show_when).toEqual({ match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "not_ticked", values: [] }] })
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
