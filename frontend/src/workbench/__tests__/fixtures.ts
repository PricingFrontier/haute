/**
 * A form for the sheet tests (specs/workbench): policy details (one row) shown by a
 * Collection, equipment (many rows, keyed by item) shown by a Table, and premiums (many
 * rows, keyed by item, an output), in the one section of one sheet, with a second sheet
 * whose one section is empty.
 */
import { vi } from "vitest"
import type { ChartWidget, FieldRef, FormSpec, HeadingWidget, Page, SchemaColumn, SchemaTable, Section, ShowWhen } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { BLANK_SECTION_HEIGHT, SHEET_PADDING } from "../../utils/sheetGeometry"
import {
  BLANK_THEME,
  CHART_OPTIONS,
  COLLECTION_OPTIONS,
  findWidget,
  HEADING_OPTIONS,
  QUOTE_DETAILS_COLUMNS,
  QUOTE_DETAILS_ID,
  showsFields,
  TABLE_OPTIONS,
  type FieldsWidget,
  type Widget,
} from "../../utils/workbenchForm"

const ALWAYS: ShowWhen = { match: "all", conditions: [] }

/** A section holding `widgets`, always shown unless `overrides` says otherwise, as tall as a new sheet's or as they reach. */
export const section = (id: string, widgets: Widget[] = [], overrides: Partial<Section> = {}): Section => ({
  id,
  title: id,
  height: Math.max(BLANK_SECTION_HEIGHT, ...widgets.map((widget) => widget.y + widget.h)),
  show_when: ALWAYS,
  widgets,
  ...overrides,
})

/** The sheets' width: what `stubLayout`'s viewport holds inside its padding, so they fit it at 100%. */
export const SHEET_WIDTH = 1000 - SHEET_PADDING * 2

/**
 * A sheet of one section, `s_` and its id, holding `widgets`, `SHEET_WIDTH` wide or as far
 * as they reach, always shown unless `showWhen` says otherwise.
 */
export const sheet = (id: string, title: string, widgets: Widget[] = [], showWhen: ShowWhen = ALWAYS): Page => ({
  id,
  title,
  width: Math.max(SHEET_WIDTH, ...widgets.map((widget) => widget.x + widget.w)),
  show_when: showWhen,
  sections: [section(`s_${id}`, widgets)],
})

/** Put `widget` in a sheet's section, the first of the first sheet unless said otherwise, which grows, as the sheet widens, to hold it. */
export function addTo(form: FormSpec, widget: Widget, page = 0, at = 0): void {
  const target = form.pages[page].sections[at]
  target.widgets.push(widget)
  target.height = Math.max(target.height, widget.y + widget.h)
  form.pages[page].width = Math.max(form.pages[page].width, widget.x + widget.w)
}

/** The components of a sheet's section, the first of the first sheet unless said otherwise. */
export const widgetsOf = (form: FormSpec, page = 0, at = 0): Widget[] => form.pages[page].sections[at].widgets

export const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})

export const table = (id: string, name: string, overrides: Partial<SchemaTable> = {}): SchemaTable => ({
  id,
  name,
  role: "input",
  rows: "one",
  columns: [],
  ...overrides,
})

/** Haute's quote_details table, as every form holds it, its columns labelled by name in `labels`. */
export const detailsTable = (labels: Record<string, string> = {}): SchemaTable =>
  table(QUOTE_DETAILS_ID, QUOTE_DETAILS_ID, {
    columns: QUOTE_DETAILS_COLUMNS.map(([id, name, type]) => column(id, name, { type, label: labels[name] ?? "" })),
  })

export const sheetForm = (): FormSpec => ({
  version: 1,
  name: "motor",
  theme: { ...BLANK_THEME },
  schema: {
    tables: [
      table("policy", "policy", { columns: [column("policy.state", "state", { options: ["CA", "NY"], required: true }), column("policy.year", "year", { type: "int" })] }),
      table("equipment", "equipment", {
        rows: "many",
        columns: [column("equipment.item", "item", { key: true }), column("equipment.value", "value", { type: "float" })],
      }),
      table("premiums", "premiums", {
        role: "output",
        rows: "many",
        columns: [column("premiums.item", "item", { key: true }), column("premiums.premium", "premium", { type: "float" })],
      }),
    ],
  },
  pages: [
    sheet("p1", "Sheet 1", [
      { id: "w_boxes", type: "collection", x: 16, y: 16, w: 400, h: 120, title: "Policy", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy", column: "policy.state" }], show_when: { match: "all", conditions: [] } },
      {
        id: "w_grid",
        type: "tableInput",
        x: 16,
        y: 160,
        w: 720,
        h: 200,
        title: "Equipment",
        rows: 3,
        ...TABLE_OPTIONS,
        fields: [{ table: "equipment", column: "equipment.item" }, { table: "equipment", column: "equipment.value" }],
        show_when: { match: "all", conditions: [] },
      },
    ]),
    sheet("p2", "Sheet 2"),
  ],
  sample: {},
})

/**
 * `sheetForm` with a coverage table given a row for each of its perils, AOP and EQ, and two
 * Tables of it: its perils' shown only while the policy's year is 1900, so hidden, and its
 * limits' always shown.
 */
export const hiddenPerilsForm = (): FormSpec => {
  const form = sheetForm()
  form.schema.tables.push(
    table("coverage", "coverage", {
      rows: "many",
      columns: [
        column("coverage.peril", "peril", { key: true, options: ["AOP", "EQ"], options_source: { file: "perils.csv", column: "peril" }, row_per_value: true }),
        column("coverage.limit", "limit", { type: "float" }),
      ],
    }),
  )
  const grid = (id: string, shown: string, y: number, showWhen: ShowWhen): Widget => ({
    id,
    type: "tableInput",
    x: 16,
    y,
    w: 400,
    h: 120,
    title: "",
    rows: 3,
    ...TABLE_OPTIONS,
    fields: [{ table: "coverage", column: shown }],
    show_when: showWhen,
  })
  const in1900: ShowWhen = { match: "all", conditions: [{ table: "policy", column: "policy.year", op: "equals", values: ["1900"] }] }
  addTo(form, grid("w_perils", "coverage.peril", 380, in1900))
  addTo(form, grid("w_limits", "coverage.limit", 520, ALWAYS))
  return form
}

/** A Chart plotting `fields`, with a new Chart's options unless `overrides` says otherwise, always shown. */
export const chartWidget = (id: string, fields: FieldRef[], overrides: Partial<ChartWidget> = {}): ChartWidget => ({
  id,
  type: "chart",
  x: 16,
  y: 380,
  w: 480,
  h: 280,
  title: "",
  fields,
  show_when: { match: "all", conditions: [] },
  ...CHART_OPTIONS,
  ...overrides,
})

/** A Heading of `text`, with no description and a new Heading's options unless `overrides` says otherwise, always shown. */
export const headingWidget = (id: string, text: string, overrides: Partial<HeadingWidget> = {}): HeadingWidget => ({
  id,
  type: "heading",
  x: 16,
  y: 380,
  w: 480,
  h: 40,
  text,
  description: "",
  show_when: { match: "all", conditions: [] },
  ...HEADING_OPTIONS,
  ...overrides,
})

/**
 * `sheetForm` with a tick box, cover, among the policy's boxes, and its second sheet,
 * "Cover", holding a Collection of the policy's year and shown only while cover is ticked.
 */
export const coverForm = (): FormSpec => {
  const form = sheetForm()
  form.schema.tables[0].columns.push(column("policy.cover", "cover", { type: "bool" }))
  fieldsWidget(form, "w_boxes").fields.push({ table: "policy", column: "policy.cover" })
  form.pages[1] = sheet(
    "p2",
    "Cover",
    [
      {
        id: "w_year",
        type: "collection",
        x: 16,
        y: 16,
        w: 400,
        h: 120,
        title: "Year",
        fields: [{ table: "policy", column: "policy.year" }],
        show_when: { match: "all", conditions: [] },
        columns: 2, ...COLLECTION_OPTIONS,
      },
    ],
    { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] },
  )
  return form
}

/**
 * `coverForm`'s first sheet in three sections: the policy's boxes, then "Flood", holding
 * the equipment's Table and shown only while cover is ticked, then "Notes", holding a
 * Heading; each section 16 below what it holds.
 */
export const sectionedForm = (): FormSpec => {
  const form = coverForm()
  const [boxes, grid] = form.pages[0].sections[0].widgets
  const note = headingWidget("w_note", "Notes", { y: 16 })
  form.pages[0].sections = [
    section("s_policy", [boxes], { title: "Policy", height: 152 }),
    section("s_flood", [{ ...grid, y: 16 }], {
      title: "Flood",
      height: 232,
      show_when: { match: "all", conditions: [{ table: "policy", column: "policy.cover", op: "ticked", values: [] }] },
    }),
    section("s_notes", [note], { title: "Notes", height: 72 }),
  ]
  return form
}

/** Load `form` into the form store as read at `rev-0`, and reset the view store. */
export function loadForm(form: FormSpec = sheetForm()): void {
  useWorkbenchFormStore.setState({
    form,
    revision: "rev-0",
    status: "ready",
    loadError: null,
    savedForm: JSON.stringify(form),
    dirty: false,
    undoStack: [],
    redoStack: [],
    stale: false,
    saving: false,
  })
  useWorkbenchViewStore.setState({
    section: "sheets",
    underwriterApp: false,
    pageId: null,
    selectedId: null,
    sheetSelected: false,
    sectionId: null,
    zoom: 1,
    fitted: false,
    creating: null,
    movingSection: null,
    widgetPanes: {},
    copied: null,
    sheet: null,
    viewport: null,
  })
}

export const currentForm = (): FormSpec => {
  const { form } = useWorkbenchFormStore.getState()
  if (form === null) throw new Error("no form")
  return form
}

/** The form's component `id`, one that shows fields: a Table, a Collection or a Chart. */
export function fieldsWidget(form: FormSpec, id: string): FieldsWidget {
  const widget = findWidget(form, id)?.widget
  if (widget === undefined || !showsFields(widget)) throw new Error(`The form has no component ${id} that shows fields`)
  return widget
}

class MockResizeObserver {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}

/** jsdom has no ResizeObserver and reports every box as empty; give the sheet a viewport. */
export function stubLayout(rect = { left: 0, top: 0, width: 1000, height: 800 }): void {
  vi.stubGlobal("ResizeObserver", MockResizeObserver)
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    ...rect,
    right: rect.left + rect.width,
    bottom: rect.top + rect.height,
    x: rect.left,
    y: rect.top,
    toJSON: () => ({}),
  } as DOMRect)
  Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => rect.width })
}

/** A pointer drag from (fromX, fromY) on `target` to (toX, toY), released there. */
export function drag(target: Element, from: [number, number], to: [number, number]): void {
  target.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button: 0, clientX: from[0], clientY: from[1] }))
  window.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX: to[0], clientY: to[1] }))
  window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true, clientX: to[0], clientY: to[1] }))
}

/** Release any press a test left unreleased, so its tracker never acts on the next test. */
export function releasePointer(): void {
  window.dispatchEvent(new MouseEvent("pointerup", { bubbles: true }))
}
