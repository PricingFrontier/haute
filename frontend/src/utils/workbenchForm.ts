/**
 * Pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, the sheets, their sections and the components in them, what the components show
 * or plot of the schema and when they show, and what would stop the schema being the
 * pipeline's tables or a sheet, a section or a component showing what it should. Each
 * operation returns a new form and leaves its input untouched.
 */
import type { ChartWidget, ColumnLook, FieldRef, FormSchema, FormSpec, HeadingWidget, Page, SchemaColumn, SchemaTable, Section, ShowWhen, Theme } from "../api/types"
import { ASCII_IDENTIFIER_RE, apiInputLabelIssue, apiInputLabelIssueMessage } from "./apiInputPorts"
import { PYTHON_KEYWORDS } from "./globalConstants"
import {
  BLANK_SECTION_HEIGHT,
  BLANK_SHEET_WIDTH,
  NEW_SECTION_HEIGHT,
  fittedSectionHeight,
  freePlace,
  leastSectionHeight,
  leastSheetWidth,
  sectionsHeight,
  type Rect,
  type Size,
} from "./sheetGeometry"
import { IDENTITY_COLUMNS, IDENTITY_NAMES, identityCount, isIdentityColumn, QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, sheetColumns } from "./quoteIdentity"
import { cellProblem, filled, isDay, parseTypedNumber, presetColumn, pricedRows, valuesBasis, type PricedRows, type RowsByTable } from "./sheetValues"
import {
  ALWAYS,
  columnKey,
  conditionFault,
  fieldKey,
  fieldKeys,
  fieldShowWhen,
  isGroup,
  isListCondition,
  isRowCount,
  isRowsCondition,
  isRowTotal,
  leafConditions,
  listColumns,
  neverShown,
  sheetVisibility,
  testLabel,
  TOTALS,
  type ColumnTest,
  type ConditionTest,
  type ListCondition,
  type ShowWhenEntry,
  type SingleCondition,
} from "./sheetVisibility"

/** A section's widget: a Table, a Collection, a Chart or a Heading. */
export type Widget = Section["widgets"][number]
export type WidgetType = Widget["type"]
/** What the palette adds: a component of a kind, or a section. */
export type PaletteType = WidgetType | "section"
/** A component that shows schema columns: a Table, a Collection or a Chart. A Heading shows none. */
export type FieldsWidget = Exclude<Widget, HeadingWidget>
/** A column's type: the Quote Input's column types. */
export type ColumnType = SchemaColumn["type"]
/**
 * How a Table's grid looks, whether its rows are fixed: none inserted or deleted, whether it
 * is transposed: its fields down its side, its rows across, whether its fields' schema
 * groups head their labels, its text, its header, its lines and its frame, and its columns'
 * own looks.
 */
export type TableOptions = Pick<
  Extract<Widget, { type: "tableInput" }>,
  | "row_numbers"
  | "density"
  | "gridlines"
  | "striped"
  | "fixed_rows"
  | "header"
  | "frame"
  | "transposed"
  | "groups"
  | "font_size"
  | "header_font_size"
  | "header_bold"
  | "header_wrap"
  | "header_orientation"
  | "header_color"
  | "header_font_color"
  | "header_align"
  | "grid_color"
  | "grid_weight"
  | "grid_style"
  | "frame_color"
  | "frame_weight"
  | "column_looks"
>
/** How one of a Table's columns, or one of a Collection's fields, looks: a `ColumnLook` without the field it is for. */
export type ColumnLookOptions = Omit<ColumnLook, "table" | "column">
/**
 * How a Collection's boxes look, and the gaps among them: where its labels sit, how dense
 * and whether framed, and, as a Table's are set, its text, its labels, its boxes' borders
 * and widths, its frame and its fields' own looks.
 */
export type CollectionOptions = Pick<
  Extract<Widget, { type: "collection" }>,
  | "labels"
  | "density"
  | "frame"
  | "font_size"
  | "header"
  | "header_font_size"
  | "header_bold"
  | "header_wrap"
  | "header_color"
  | "header_font_color"
  | "header_align"
  | "label_width"
  | "box_style"
  | "box_weight"
  | "box_color"
  | "box_align"
  | "box_width"
  | "frame_color"
  | "frame_weight"
  | "column_looks"
  | "spacers"
>
/** The look a Table and a Collection share: its density, its frame, its text, its labels' and its columns' own looks. */
export type SharedLook = Pick<TableOptions, keyof TableOptions & keyof CollectionOptions>
/** A place in a component's order: one of its fields, or one of a Collection's spacers. */
export type OrderItem = { kind: "field"; field: FieldRef } | { kind: "spacer" }
/** What names a Chart's rows, and how it draws. */
export type ChartOptions = Pick<ChartWidget, "category" | "chart_type" | "orientation" | "stacked" | "legend" | "data_labels">
/** How a Heading's text shows. */
export type HeadingOptions = Pick<HeadingWidget, "size" | "align">

/**
 * A new id: 64 random bits, so two the view makes never meet in practice; the server
 * refuses a form holding any id twice, a block copied by hand or by a merge among them.
 */
/**
 * The most rows a Table starts with, and columns a Collection lays its boxes in, as the
 * server's form takes them (`TABLE_ROWS_MAX` and `COLLECTION_COLUMNS_MAX` in
 * `src/haute/_workbench_form.py`).
 */
export const TABLE_ROWS_MAX = 50
export const COLLECTION_COLUMNS_MAX = 12

export function newId(prefix: string): string {
  const bits = crypto.getRandomValues(new Uint8Array(8))
  return `${prefix}_${Array.from(bits, (byte) => byte.toString(16).padStart(2, "0")).join("")}`
}

/** `base`, or `base_2`, `base_3`… until it is none of `taken`. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const used = new Set(taken)
  if (!used.has(base)) return base
  let n = 2
  while (used.has(`${base}_${n}`)) n += 1
  return `${base}_${n}`
}

/** `sum_insured` as "Sum insured": what a column is called until it has a label. */
export const readableName = (name: string): string =>
  name.replace(/_+/g, " ").trim().replace(/^./, (c) => c.toUpperCase())

export const allSections = (spec: FormSpec): Section[] => spec.pages.flatMap((page) => page.sections)

/** A sheet's components, section by section. */
export const pageWidgets = (page: Page): Widget[] => page.sections.flatMap((section) => section.widgets)

export const allWidgets = (spec: FormSpec): Widget[] => spec.pages.flatMap(pageWidgets)

/** Whether a component shows schema columns: a Table, a Collection or a Chart, not a Heading. */
export const showsFields = (widget: Widget): widget is FieldsWidget => widget.type !== "heading"

const edit = (spec: FormSpec, change: (draft: FormSpec) => void): FormSpec => {
  const draft = structuredClone(spec)
  change(draft)
  return draft
}

/**
 * A layout change, as each step of a drag makes: `spec` with its sheet `pageId` as `change`
 * leaves a copy of it whose sections `sectionIds` are copied too, their lists of components
 * with them; `change` copies a component it changes. Every other sheet, section and
 * component, the schema and the sample stay the objects they were, so what draws them is
 * not drawn again.
 */
function editLayout(spec: FormSpec, pageId: string, sectionIds: readonly string[], change: (page: Page) => void): FormSpec {
  const page = { ...pageOf(spec, pageId) }
  page.sections = page.sections.map((section) => (sectionIds.includes(section.id) ? { ...section, widgets: [...section.widgets] } : section))
  change(page)
  return { ...spec, pages: spec.pages.map((candidate) => (candidate.id === pageId ? page : candidate)) }
}

/** The section `id` of a sheet. */
function sectionIn(page: Page, id: string): Section {
  const section = page.sections.find((candidate) => candidate.id === id)
  if (section === undefined) throw new Error(`Sheet ${page.title} has no section ${id}`)
  return section
}

function schemaTableOf(spec: FormSpec, id: string): SchemaTable {
  const table = spec.schema.tables.find((candidate) => candidate.id === id)
  if (table === undefined) throw new Error(`The schema has no table ${id}`)
  return table
}

function schemaColumnOf(table: SchemaTable, id: string): SchemaColumn {
  const column = table.columns.find((candidate) => candidate.id === id)
  if (column === undefined) throw new Error(`Table ${table.name} has no column ${id}`)
  return column
}

/** A new table, leading with the quote's reference and option, as every table but quote_details does. */
export const createSchemaTable = (spec: FormSpec): SchemaTable => ({
  id: newId("t"),
  name: uniqueName("table", spec.schema.tables.map((table) => table.name)),
  role: "input",
  rows: "one",
  columns: IDENTITY_COLUMNS.map(([id, name, type]) => blankColumn(id, name, type)),
})

/**
 * A new column for a table, to go at `at` among its columns (the end by default): of the
 * type of the column above it, or Text first in the table or under the quote's identity or
 * its index.
 */
export const createSchemaColumn = (spec: FormSpec, tableId: string, at?: number): SchemaColumn => {
  const { columns } = schemaTableOf(spec, tableId)
  const above = columns[(at ?? columns.length) - 1]
  const type = above === undefined || above.index || isIdentityColumn(above) ? "str" : above.type
  return blankColumn(newId("c"), uniqueName("column", columns.map((column) => column.name)), type)
}

/** A column of `type` with nothing of its own set. */
function blankColumn(id: string, name: string, type: SchemaColumn["type"]): SchemaColumn {
  return {
    id,
    name,
    type,
    label: "",
    group: "",
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
    default_value: "",
    number_format: "general",
    decimal_places: null,
    use_grouping: true,
  }
}

/** A table's index: a column holding each row's number, from 1, as the rows stand. */
export const createIndexColumn = (spec: FormSpec, tableId: string): SchemaColumn => ({
  ...createSchemaColumn(spec, tableId),
  name: uniqueName("row_number", schemaTableOf(spec, tableId).columns.map((column) => column.name)),
  type: "int",
  index: true,
})

export const addSchemaTable = (spec: FormSpec, table: SchemaTable): FormSpec =>
  edit(spec, (draft) => {
    draft.schema.tables.push(table)
  })

export const updateSchemaTable = (spec: FormSpec, id: string, patch: Partial<SchemaTable>): FormSpec =>
  edit(spec, (draft) => {
    Object.assign(schemaTableOf(draft, id), patch)
  })

/**
 * Move the table at `from` in the schema to `to`; Haute's quote_details table, first, stays
 * first: it is not moved, and nothing is moved above it. A move to where it is changes nothing.
 */
export function moveSchemaTable(spec: FormSpec, from: number, to: number): FormSpec {
  const { tables } = spec.schema
  if (tables[from] === undefined || tables[to] === undefined) {
    throw new Error(`Cannot move the table at ${from} to ${to}: the schema has ${tables.length} tables`)
  }
  if (from === to || isQuoteDetails(tables[from]) || (to === 0 && isQuoteDetails(tables[0]))) return spec
  return edit(spec, (draft) => {
    const [table] = draft.schema.tables.splice(from, 1)
    draft.schema.tables.splice(to, 0, table)
  })
}

/**
 * Take the schema columns `drop` matches out of a widget that shows them, a Collection's
 * spacers keeping their places among the fields left, numbering again the rows of a Chart
 * that one of them labelled.
 */
const dropShown = (widget: Widget, drop: (field: FieldRef) => boolean): void => {
  if (!showsFields(widget)) return
  setOrder(widget, orderItems(widget).filter((item) => item.kind === "spacer" || !drop(item.field)))
  if (widget.type === "chart" && widget.category !== null && drop(widget.category)) widget.category = null
}

/** Take the schema columns `drop` matches out of every widget that shows them (`dropShown`). */
const dropFields = (draft: FormSpec, drop: (field: FieldRef) => boolean): void => {
  for (const widget of allWidgets(draft)) dropShown(widget, drop)
}

/**
 * A `show_when` with each condition as `keep` leaves it, those it gives null taken out, a
 * group left with none going too, as it would hold nothing to test.
 */
const withoutConditions = (showWhen: ShowWhen, keep: (condition: SingleCondition) => SingleCondition | null): ShowWhen => {
  const kept = (conditions: readonly SingleCondition[]): SingleCondition[] =>
    conditions.flatMap((condition) => {
      const left = keep(condition)
      return left === null ? [] : [left]
    })
  return {
    match: showWhen.match,
    conditions: showWhen.conditions.flatMap((entry): ShowWhenEntry[] => {
      if (!isGroup(entry)) return kept([entry])
      const left = kept(entry.conditions)
      return left.length === 0 ? [] : [{ ...entry, conditions: left }]
    }),
  }
}

/** Leave every sheet's, section's, widget's and field's conditions as `keep` leaves them (`withoutConditions`). */
const dropConditions = (draft: FormSpec, keep: (condition: SingleCondition) => SingleCondition | null): void => {
  for (const holder of [...draft.pages, ...allSections(draft), ...allWidgets(draft)]) {
    holder.show_when = withoutConditions(holder.show_when, keep)
  }
  for (const widget of allWidgets(draft)) {
    if (widget.type === "tableInput" || widget.type === "collection") widget.column_looks = looksKeeping(widget.column_looks, keep)
  }
}

/** The ids of the columns of its table a condition tests: a one-row table's condition's own, or those a list's tests of a row and its total test. */
const conditionColumns = (condition: SingleCondition): string[] => (isListCondition(condition) ? listColumns(condition) : [condition.column])

/**
 * A condition without what it tests of a column: none for a one-row table's condition on
 * it or a total of it; a list's tests of a row on it taken out, a condition in any or
 * every row left with none going too, as it needs one.
 */
function withoutColumn(condition: SingleCondition, tableId: string, columnId: string): SingleCondition | null {
  if (condition.table !== tableId) return condition
  if (!isListCondition(condition)) return condition.column === columnId ? null : condition
  if (isRowTotal(condition) && condition.column === columnId) return null
  const where = condition.where.filter((test) => test.column !== columnId)
  if (where.length === condition.where.length) return condition
  return isRowsCondition(condition) && where.length === 0 ? null : { ...condition, where }
}

/**
 * Removing a table removes its columns from the widgets that show them, or label a Chart's
 * rows, and the conditions on it, and its sample rows.
 */
export const removeSchemaTable = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    draft.schema.tables = draft.schema.tables.filter((table) => table.id !== id)
    dropFields(draft, (field) => field.table === id)
    dropConditions(draft, (condition) => (condition.table === id ? null : condition))
    delete draft.sample[id]
  })

/**
 * A table with one row per quote has no keys, which say which of many rows is which, no
 * index, which numbers them, and no row for each value: switching to one row drops them,
 * the index column with what shows it and its sample values, as removing a column does. A
 * many-row table's columns take no default: switching to many rows clears them.
 */
export const setSchemaTableRows = (spec: FormSpec, id: string, rows: SchemaTable["rows"]): FormSpec => {
  const index = rows === "one" ? schemaTableOf(spec, id).columns.find((column) => column.index) : undefined
  const without = index === undefined ? spec : removeSchemaColumn(spec, id, index.id)
  return edit(without, (draft) => {
    const table = schemaTableOf(draft, id)
    table.rows = rows
    for (const column of table.columns) {
      if (rows === "one") {
        column.key = false
        column.row_per_value = false
      } else column.default_value = ""
    }
  })
}

/**
 * Give a table a row for each of a column's allowed values, unticking any other column of
 * the table that gave its rows, as only one does; or stop it.
 */
export const setRowPerValue = (spec: FormSpec, tableId: string, columnId: string, on: boolean): FormSpec =>
  edit(spec, (draft) => {
    for (const column of schemaTableOf(draft, tableId).columns) {
      if (column.id === columnId) column.row_per_value = on
      else if (on) column.row_per_value = false
    }
  })

/** Add a column at `index`, or at the end. */
export const addSchemaColumn = (spec: FormSpec, tableId: string, column: SchemaColumn, index?: number): FormSpec =>
  edit(spec, (draft) => {
    const { columns } = schemaTableOf(draft, tableId)
    columns.splice(index ?? columns.length, 0, column)
  })

/**
 * Move a column to `to`. The quote's identity, first in its table, and the index after it
 * stay where they are, and nothing goes above them.
 */
export const moveSchemaColumn = (spec: FormSpec, tableId: string, columnId: string, to: number): FormSpec => {
  const table = schemaTableOf(spec, tableId)
  const { columns } = table
  const from = columns.findIndex((column) => column.id === columnId)
  const head = identityCount(table) + (columns[identityCount(table)]?.index ? 1 : 0)
  if (from === to || columns[from].index || from < head || to < head) return spec
  return edit(spec, (draft) => {
    const moved = schemaTableOf(draft, tableId).columns
    const [column] = moved.splice(from, 1)
    moved.splice(to, 0, column)
  })
}

/** Change a column; the quote's identity is Haute's, and nothing of it changes. */
export const updateSchemaColumn = (
  spec: FormSpec,
  tableId: string,
  columnId: string,
  patch: Partial<SchemaColumn>,
): FormSpec =>
  isIdentityColumn({ id: columnId })
    ? spec
    : edit(spec, (draft) => {
        Object.assign(schemaColumnOf(schemaTableOf(draft, tableId), columnId), patch)
      })

/** Whether a column of `type` can have allowed values: any but a tick box or a date. */
export const takesOptions = (type: SchemaColumn["type"]): boolean => type !== "bool" && type !== "date"

/**
 * Change a column's type, dropping the rules the new type cannot have: the range and the
 * number format of a column no longer a number, and the allowed values of one now a tick
 * box or a date, with the params file they came from and a row for each, which would
 * otherwise still rule its cells; and the default of one that
 * becomes or stops being a tick box, a tick or text no longer its kind of value, or that
 * becomes a date while its default is not a day, which a date's box cannot show. The quote's
 * identity keeps its types.
 */
export const changeSchemaColumnType = (
  spec: FormSpec,
  tableId: string,
  columnId: string,
  type: SchemaColumn["type"],
): FormSpec =>
  isIdentityColumn({ id: columnId })
    ? spec
    : edit(spec, (draft) => {
        const column = schemaColumnOf(schemaTableOf(draft, tableId), columnId)
        const { default_value: value } = column
        if ((type === "bool") !== (column.type === "bool")) column.default_value = ""
        else if (type === "date" && typeof value === "string" && !isDay(value.trim())) column.default_value = ""
        column.type = type
        if (type !== "int" && type !== "float") {
          column.min = null
          column.max = null
          column.number_format = "general"
          column.decimal_places = null
          column.use_grouping = true
        }
        if (!takesOptions(type)) {
          column.options = []
          column.options_source = null
          column.row_per_value = false
        }
      })

/**
 * Removing a column removes it from the widgets that show it, or label a Chart's rows,
 * and what the conditions test of it (`withoutColumn`), and its sample values. The quote's
 * identity stays.
 */
export const removeSchemaColumn = (spec: FormSpec, tableId: string, columnId: string): FormSpec =>
  isIdentityColumn({ id: columnId })
    ? spec
    : edit(spec, (draft) => {
        const table = schemaTableOf(draft, tableId)
        table.columns = table.columns.filter((column) => column.id !== columnId)
        dropFields(draft, (field) => field.table === tableId && field.column === columnId)
        dropConditions(draft, (condition) => withoutColumn(condition, tableId, columnId))
        for (const row of draft.sample[tableId] ?? []) delete row[columnId]
      })

/** The widgets showing a schema table's columns, or just one of them, a Chart labelling its rows by one included. */
export const fieldUses = (spec: FormSpec, tableId: string, columnId?: string): Widget[] =>
  allWidgets(spec).filter(showsFields).filter((widget) =>
    [...widget.fields, ...(widget.type === "chart" && widget.category !== null ? [widget.category] : [])].some(
      (field) => field.table === tableId && (columnId === undefined || field.column === columnId),
    ),
  )

/** The sheets, sections and widgets whose conditions, or whose fields' conditions, are on a schema table, or test just one of its columns. */
export const conditionUses = (spec: FormSpec, tableId: string, columnId?: string): (Page | Section | Widget)[] => {
  const uses = (showWhen: ShowWhen): boolean =>
    leafConditions(showWhen).some((condition) => condition.table === tableId && (columnId === undefined || conditionColumns(condition).includes(columnId)))
  return [...spec.pages, ...allSections(spec), ...allWidgets(spec)].filter(
    (holder) => uses(holder.show_when) || ("column_looks" in holder && holder.column_looks.some((look) => uses(look.show_when))),
  )
}

/** The schema table and column a field names, or null when it is no longer in the schema: of a form, or of the schema alone. */
export function fieldColumn({ schema }: Pick<FormSpec, "schema">, field: FieldRef): { table: SchemaTable; column: SchemaColumn } | null {
  const table = schema.tables.find((candidate) => candidate.id === field.table)
  const column = table?.columns.find((candidate) => candidate.id === field.column)
  return table && column ? { table, column } : null
}

/** A field a component shows, with the schema column it names, or null when that is gone. */
export interface ShownField {
  key: string
  found: { table: SchemaTable; column: SchemaColumn } | null
}

/** The fields a component shows, each with its column in `schema`, in the component's order; a Heading shows none. */
export const shownFields = (schema: FormSchema, widget: Widget): ShownField[] =>
  showsFields(widget) ? widget.fields.map((field) => ({ key: `${field.table}:${field.column}`, found: fieldColumn({ schema }, field) })) : []

/** The input tables a Table's grid takes rows of, in its order: those of the many-row columns it shows that are typed into. */
export const gridInputTables = (fields: readonly ShownField[]): SchemaTable[] => [
  ...new Map(
    fields.flatMap(({ found }) =>
      found !== null && found.table.role === "input" && found.table.rows === "many" && !found.column.index && !isIdentityColumn(found.column)
        ? [[found.table.id, found.table] as const]
        : [],
    ),
  ).values(),
]

// ─── Sheets, sections and components ─────────────────────────────────────

function pageOf(spec: FormSpec, id: string): Page {
  const page = spec.pages.find((candidate) => candidate.id === id)
  if (page === undefined) throw new Error(`The form has no sheet ${id}`)
  return page
}

/** When a new sheet, section or component shows: always, with no conditions. */
const alwaysShown = (): ShowWhen => ({ match: "all", conditions: [] })

/** A new section of `page`, named in turn on it, `height` tall, always shown and empty. */
export const createSection = (page: Page, height: number = NEW_SECTION_HEIGHT): Section => ({
  id: newId("section"),
  title: uniqueName(`Section ${page.sections.length + 1}`, page.sections.map((section) => section.title)),
  height,
  show_when: alwaysShown(),
  widgets: [],
})

/**
 * A new sheet, named in turn, as wide as the last (`BLANK_SHEET_WIDTH` for a form with
 * none), always shown, holding one section `BLANK_SECTION_HEIGHT` tall: the whole sheet.
 */
export function createPage(spec: FormSpec): Page {
  const page: Page = {
    id: newId("page"),
    title: uniqueName(`Sheet ${spec.pages.length + 1}`, spec.pages.map((candidate) => candidate.title)),
    width: spec.pages.at(-1)?.width ?? BLANK_SHEET_WIDTH,
    show_when: alwaysShown(),
    sections: [],
  }
  page.sections.push(createSection(page, BLANK_SECTION_HEIGHT))
  return page
}

export const addPage = (spec: FormSpec, page: Page): FormSpec =>
  edit(spec, (draft) => {
    draft.pages.push(page)
  })

export const renamePage = (spec: FormSpec, id: string, title: string): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, id).title = title
  })

/** Move the sheet at `from` in the tabs' order to `to`; a move to where it is changes nothing. */
export function movePage(spec: FormSpec, from: number, to: number): FormSpec {
  if (from === to) return spec
  if (spec.pages[from] === undefined || spec.pages[to] === undefined) {
    throw new Error(`Cannot move the sheet at ${from} to ${to}: the form has ${spec.pages.length} sheets`)
  }
  return edit(spec, (draft) => {
    const [page] = draft.pages.splice(from, 1)
    draft.pages.splice(to, 0, page)
  })
}

export const setPageShowWhen = (spec: FormSpec, id: string, showWhen: ShowWhen): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, id).show_when = structuredClone(showWhen)
  })

/**
 * Set a sheet's width in whole pixels, never under what its components reach or
 * `MIN_SHEET_WIDTH`; the form as given while it is that already, so no edit is recorded,
 * and for a width that is not a safe whole number, which the file cannot hold.
 */
export function setPageWidth(spec: FormSpec, id: string, width: number): FormSpec {
  const page = pageOf(spec, id)
  const kept = Math.max(Math.round(width), leastSheetWidth(pageWidgets(page)))
  if (page.width === kept || !Number.isSafeInteger(kept)) return spec
  return edit(spec, (draft) => {
    pageOf(draft, id).width = kept
  })
}

/**
 * Set a sheet's height, its sections' heights summed (`sectionsHeight`), through its last
 * section's (`setSectionHeight`), so never under `leastSheetHeight`.
 */
export function setPageHeight(spec: FormSpec, id: string, height: number): FormSpec {
  const { sections } = pageOf(spec, id)
  const last = sections.at(-1)
  if (last === undefined) throw new Error(`The sheet ${id} has no sections`)
  return setSectionHeight(spec, last.id, height - sectionsHeight(sections) + last.height)
}

/** Remove a sheet with its components; the schema and the sample stay. The last sheet stays. */
export const removePage = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, id)
    if (draft.pages.length === 1) throw new Error("The last sheet cannot be removed")
    draft.pages = draft.pages.filter((page) => page.id !== id)
  })

/** The section with `id`, with its sheet and its place on it, or null. */
export function findSection(spec: FormSpec, id: string): { section: Section; page: Page; index: number } | null {
  for (const page of spec.pages) {
    const index = page.sections.findIndex((section) => section.id === id)
    if (index >= 0) return { section: page.sections[index], page, index }
  }
  return null
}

function sectionOf(spec: FormSpec, id: string): Section {
  const found = findSection(spec, id)
  if (found === null) throw new Error(`No sheet has a section ${id}`)
  return found.section
}

/** Add a section to a sheet at `index`, or at the end. */
export const addSection = (spec: FormSpec, pageId: string, section: Section, index?: number): FormSpec =>
  edit(spec, (draft) => {
    const { sections } = pageOf(draft, pageId)
    sections.splice(index ?? sections.length, 0, section)
  })

/**
 * Start `added` in a section at `at` down it: the components whose top is there or below
 * move into `added`, under the section, keeping their places on the sheet, and the section
 * keeps the height above the line, but never less than what stays in it reaches, which
 * pushes what moved down by as much. `added` takes the rest of the height, but never less
 * than what it holds reaches or `MIN_SECTION_HEIGHT`.
 */
export const splitSection = (spec: FormSpec, id: string, at: number, added: Section): FormSpec =>
  edit(spec, (draft) => {
    const found = findSection(draft, id)
    if (found === null) throw new Error(`No sheet has a section ${id}`)
    const { section, page, index } = found
    const holds = [...added.widgets, ...section.widgets.filter((widget) => widget.y >= at).map((widget) => ({ ...widget, y: widget.y - at }))]
    section.widgets = section.widgets.filter((widget) => widget.y < at)
    const above = Math.max(at, leastSectionHeight(section.widgets))
    page.sections.splice(index + 1, 0, { ...added, widgets: holds, height: Math.max(section.height - at, leastSectionHeight(holds)) })
    section.height = above
  })

export const renameSection = (spec: FormSpec, id: string, title: string): FormSpec =>
  edit(spec, (draft) => {
    sectionOf(draft, id).title = title
  })

export const setSectionShowWhen = (spec: FormSpec, id: string, showWhen: ShowWhen): FormSpec =>
  edit(spec, (draft) => {
    sectionOf(draft, id).show_when = structuredClone(showWhen)
  })

/**
 * A section's height set; the form as given while it is that already, so no edit is
 * recorded, and for a height that is not a safe whole number, which the file cannot hold.
 */
function withSectionHeight(spec: FormSpec, id: string, height: number): FormSpec {
  const found = findSection(spec, id)
  if (found === null) throw new Error(`No sheet has a section ${id}`)
  if (found.section.height === height || !Number.isSafeInteger(height)) return spec
  return editLayout(spec, found.page.id, [id], (page) => {
    sectionIn(page, id).height = height
  })
}

/** Set a section's height in whole pixels, never under what its components reach or `MIN_SECTION_HEIGHT`. */
export const setSectionHeight = (spec: FormSpec, id: string, height: number): FormSpec =>
  withSectionHeight(spec, id, Math.max(Math.round(height), leastSectionHeight(sectionOf(spec, id).widgets)))

/** Fit a section to its components (`fittedSectionHeight`). */
export const fitSection = (spec: FormSpec, id: string): FormSpec => withSectionHeight(spec, id, fittedSectionHeight(sectionOf(spec, id).widgets))

/** Move the section at `from` on a sheet to `to`; a move to where it is changes nothing. */
export function moveSection(spec: FormSpec, pageId: string, from: number, to: number): FormSpec {
  if (from === to) return spec
  const { sections } = pageOf(spec, pageId)
  if (sections[from] === undefined || sections[to] === undefined) {
    throw new Error(`Cannot move the section at ${from} to ${to}: the sheet has ${sections.length} sections`)
  }
  return edit(spec, (draft) => {
    const moved = pageOf(draft, pageId).sections
    const [section] = moved.splice(from, 1)
    moved.splice(to, 0, section)
  })
}

/** Remove a section with its components; the schema and the sample stay. A sheet's last section stays. */
export const removeSection = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    const found = findSection(draft, id)
    if (found === null) throw new Error(`No sheet has a section ${id}`)
    if (found.page.sections.length === 1) throw new Error("A sheet's last section cannot be removed")
    found.page.sections.splice(found.index, 1)
  })

/** The component with `id`, with its sheet, its section and its place in it, or null. */
export function findWidget(spec: FormSpec, id: string): { widget: Widget; page: Page; section: Section; index: number } | null {
  for (const page of spec.pages) {
    for (const section of page.sections) {
      const index = section.widgets.findIndex((widget) => widget.id === id)
      if (index >= 0) return { widget: section.widgets[index], page, section, index }
    }
  }
  return null
}

/** Grow a section and its sheet, in a copy, to hold one of its components: as tall and as wide as the component reaches, at least. */
const holdWidget = (page: Page, section: Section, widget: Widget): void => {
  section.height = Math.max(section.height, widget.y + widget.h)
  page.width = Math.max(page.width, widget.x + widget.w)
}

function widgetOf(spec: FormSpec, id: string): Widget {
  const found = findWidget(spec, id)
  if (found === null) throw new Error(`No sheet has a component ${id}`)
  return found.widget
}

/** The component with `id`, one that shows fields: a Heading shows none. */
function fieldsWidgetOf(spec: FormSpec, id: string): FieldsWidget {
  const widget = widgetOf(spec, id)
  if (!showsFields(widget)) throw new Error("A Heading shows no fields")
  return widget
}

/**
 * A new Table's options: numbered, compact, lined between rows, striped, not fixed, its
 * header as written, framed, not transposed and without groups; 12px text, its header's
 * the same size, bold, level, unwrapped, placed as each column's look says and in the
 * theme's colours, thin solid gridlines and a thin frame in the theme's colours, and no
 * column with a look of its own.
 */
export const TABLE_OPTIONS: Readonly<TableOptions> = {
  row_numbers: true,
  density: "compact",
  gridlines: "rows",
  striped: true,
  fixed_rows: false,
  header: "plain",
  frame: true,
  transposed: false,
  groups: false,
  font_size: 12,
  header_font_size: null,
  header_bold: true,
  header_wrap: false,
  header_orientation: "horizontal",
  header_color: null,
  header_font_color: null,
  header_align: "auto",
  grid_color: null,
  grid_weight: "thin",
  grid_style: "solid",
  frame_color: null,
  frame_weight: "thin",
  column_looks: [],
}

/**
 * A Table column's or a Collection field's look before any is given: the component's own
 * colours, bold off, its label and values placed as the component puts them and as wide as
 * the grid, or the Collection, makes it, and shown while its component is.
 */
export const PLAIN_COLUMN_LOOK: Readonly<ColumnLookOptions> = {
  header_color: null,
  header_font_color: null,
  header_align: "auto",
  cell_color: null,
  font_color: null,
  bold: false,
  align: "auto",
  width: null,
  show_when: ALWAYS,
}

/**
 * How narrow and how wide a Table's column, or a Collection's boxes, can be set, in pixels,
 * as the server's form takes them (`COLUMN_WIDTH_MIN` and `COLUMN_WIDTH_MAX` in
 * `src/haute/_workbench_form.py`).
 */
export const COLUMN_WIDTH_MIN = 24
export const COLUMN_WIDTH_MAX = 1000

/**
 * How little and how much of its field a Collection's label beside its box can take, in
 * percent, as the server's form takes them (`LABEL_WIDTH_MIN` and `LABEL_WIDTH_MAX` there).
 */
export const LABEL_WIDTH_MIN = 1
export const LABEL_WIDTH_MAX = 99

/** The look of a Table's column or a Collection's field: its own, or the plain one while it has none. */
export const columnLook = (widget: Pick<SharedLook, "column_looks">, field: FieldRef): ColumnLookOptions => {
  const look = widget.column_looks.find((at) => at.table === field.table && at.column === field.column)
  if (look === undefined) return PLAIN_COLUMN_LOOK
  const { table: _table, column: _column, ...options } = look
  return options
}

/** Whether a column look sets anything but when its column shows: a fill, a font, a place, a width or bold. */
export const hasOwnLook = (look: ColumnLookOptions): boolean =>
  (Object.keys(PLAIN_COLUMN_LOOK) as (keyof ColumnLookOptions)[]).some((key) => key !== "show_when" && look[key] !== PLAIN_COLUMN_LOOK[key])

/** Whether a column look changes nothing, so no Table or Collection holds it. */
const plainLook = (look: ColumnLookOptions): boolean => !hasOwnLook(look) && look.show_when.conditions.length === 0

/**
 * A component's looks with each `show_when` as `withoutConditions` leaves it for `keep`, one
 * left with no conditions always shown and one left changing nothing taken away.
 */
const looksKeeping = (looks: readonly ColumnLook[], keep: (condition: SingleCondition) => SingleCondition | null): ColumnLook[] =>
  looks.flatMap((look) => {
    const showWhen = withoutConditions(look.show_when, keep)
    const kept = { ...look, show_when: showWhen.conditions.length === 0 ? alwaysShown() : showWhen }
    return plainLook(kept) ? [] : [kept]
  })

/**
 * The form with a Table's column or a Collection's field, one it shows, looking as `patch`
 * says over its look as it was, a `show_when` left with no conditions always shown; a look
 * that comes to change nothing is taken away, and `clear` puts back all but its `show_when`.
 * A new look goes after the others, in the file's order.
 */
export const setColumnLook = (spec: FormSpec, widgetId: string, field: FieldRef, patch: Partial<ColumnLookOptions> | "clear"): FormSpec =>
  edit(spec, (draft) => {
    const widget = widgetOf(draft, widgetId)
    if (widget.type !== "tableInput" && widget.type !== "collection") throw new Error("Only a Table or a Collection has column looks")
    if (!widget.fields.some((shown) => shown.table === field.table && shown.column === field.column)) {
      throw new Error(`The ${widget.type === "collection" ? "Collection" : "Table"} does not show ${field.table}.${field.column}`)
    }
    const at = widget.column_looks.findIndex((look) => look.table === field.table && look.column === field.column)
    const was = columnLook(widget, field)
    const next = patch === "clear" ? { ...PLAIN_COLUMN_LOOK, show_when: was.show_when } : { ...was, ...patch }
    if (plainLook(next)) {
      if (at >= 0) widget.column_looks.splice(at, 1)
      return
    }
    const showWhen = next.show_when.conditions.length === 0 ? alwaysShown() : next.show_when
    const look: ColumnLook = { table: field.table, column: field.column, ...next, show_when: showWhen }
    if (at >= 0) widget.column_looks[at] = look
    else widget.column_looks.push(look)
  })

/**
 * A new Collection's options: its labels above its boxes, comfortable and framed; 12px
 * text, its labels the same size, as written, neither bold nor wrapped, in the theme's
 * colours, at their fields' start and two fifths of a field wide beside their boxes; its
 * boxes' borders thin, solid and in the theme's colour, their values placed as their types
 * put them, each box as wide as its label leaves; a thin frame in the theme's colour; and
 * no field with a look of its own and no spacers among them.
 */
export const COLLECTION_OPTIONS: Readonly<CollectionOptions> = {
  labels: "above",
  density: "comfortable",
  frame: true,
  font_size: 12,
  header: "plain",
  header_font_size: null,
  header_bold: false,
  header_wrap: false,
  header_color: null,
  header_font_color: null,
  header_align: "auto",
  label_width: null,
  box_style: "solid",
  box_weight: "thin",
  box_color: null,
  box_align: "auto",
  box_width: null,
  frame_color: null,
  frame_weight: "thin",
  column_looks: [],
  spacers: [],
}

/** A new Chart's options: its rows numbered, columns, upright, unstacked, its legend at the bottom and no data labels. */
export const CHART_OPTIONS: Readonly<ChartOptions> = {
  category: null,
  chart_type: "column",
  orientation: "vertical",
  stacked: false,
  legend: "bottom",
  data_labels: false,
}

/** A new Heading's options: medium, a section's heading, aligned left. */
export const HEADING_OPTIONS: Readonly<HeadingOptions> = { size: "medium", align: "left" }

/** A new component of `type` at `rect`, showing nothing yet, and always. */
export function createWidget<T extends WidgetType>(type: T, rect: Rect): Extract<Widget, { type: T }>
export function createWidget(type: WidgetType, rect: Rect): Widget {
  // In the file's order, so a component the view made compares equal to it saved.
  const { x, y, w, h } = rect
  const placed = { id: newId("w"), x, y, w, h }
  const showing = { title: "", fields: [] as FieldRef[], show_when: alwaysShown() }
  switch (type) {
    case "collection":
      // Column looks and spacers of its own, not the options' one lists.
      return { ...placed, type, ...showing, columns: 3, ...COLLECTION_OPTIONS, column_looks: [], spacers: [] }
    case "chart":
      return { ...placed, type, ...showing, ...CHART_OPTIONS }
    case "tableInput":
      // Column looks of its own, not the options' one list.
      return { ...placed, type, ...showing, rows: 3, ...TABLE_OPTIONS, column_looks: [] }
    case "heading":
      return { ...placed, type, text: "", description: "", show_when: alwaysShown(), ...HEADING_OPTIONS }
  }
}

/** One of the sheets' colours: a key of the form's `theme`. */
export type ThemeColour = keyof Theme

/** The sheets' colours before any is chosen: the editor's own throughout, in the file's order. */
export const BLANK_THEME: Readonly<Theme> = {
  sheet_color: null,
  background_color: null,
  border_color: null,
  font_color: null,
  input_cell_color: null,
  output_cell_color: null,
}

/** The form with one of the sheets' colours set, or the editor's own again for null. */
export const setThemeColour = (spec: FormSpec, key: ThemeColour, colour: string | null): FormSpec =>
  edit(spec, (draft) => {
    draft.theme[key] = colour
  })

/**
 * Add a component to a section, kept inside it and its sheet's width (`keptInside`); only
 * one taller than the section grows it, and only one wider than the sheet widens it.
 */
export const addWidget = (spec: FormSpec, sectionId: string, widget: Widget): FormSpec =>
  edit(spec, (draft) => {
    const found = findSection(draft, sectionId)
    if (found === null) throw new Error(`No sheet has a section ${sectionId}`)
    const { section, page } = found
    const kept = { ...widget, x: keptInside(widget.x, widget.w, page.width), y: keptInside(widget.y, widget.h, section.height) }
    section.widgets.push(kept)
    holdWidget(page, section, kept)
  })

/**
 * Add a new component of `type`, `size` but no wider than its sheet, to a section, at the
 * first place free in it (`freePlace`), the section growing to hold it where that is below
 * its foot, as adding one from the palette's keys does; with its id.
 */
export function addWidgetInFreePlace(spec: FormSpec, sectionId: string, type: WidgetType, size: Size): { spec: FormSpec; id: string } {
  const found = findSection(spec, sectionId)
  if (found === null) throw new Error(`No sheet has a section ${sectionId}`)
  const fitted = { w: Math.min(size.w, found.page.width), h: size.h }
  const widget = createWidget(type, { ...freePlace(found.section.widgets, fitted, found.page.width), ...fitted })
  const next = edit(spec, (draft) => {
    const { page, section } = findSection(draft, sectionId)!
    section.widgets.push(widget)
    holdWidget(page, section, widget)
  })
  return { spec: next, id: widget.id }
}

/**
 * What any component can be given; `rows` and the Table's options are a Table's alone,
 * `columns` and the Collection's options a Collection's, but for the look the two share
 * (`SharedLook`), the Chart's options a Chart's, and `text`, `description` and the
 * Heading's options a Heading's, which has no `title` or `fields`.
 */
export type WidgetPatch = Partial<Rect> & {
  title?: string
  fields?: FieldRef[]
  show_when?: ShowWhen
  rows?: number
  columns?: number
  text?: string
  description?: string
} & Partial<TableOptions> &
  Partial<CollectionOptions> &
  Partial<ChartOptions> &
  Partial<HeadingOptions>

/** What a Table and a Collection both have: the keys both kinds' options hold (`SharedLook`). */
const TABLE_OR_COLLECTION = Object.keys(TABLE_OPTIONS).filter((key) => key in COLLECTION_OPTIONS)
const TABLE_ONLY = ["rows", ...Object.keys(TABLE_OPTIONS).filter((key) => !TABLE_OR_COLLECTION.includes(key))]
const COLLECTION_ONLY = ["columns", ...Object.keys(COLLECTION_OPTIONS).filter((key) => !TABLE_OR_COLLECTION.includes(key))]
const CHART_ONLY = Object.keys(CHART_OPTIONS)
const HEADING_ONLY = ["text", "description", ...Object.keys(HEADING_OPTIONS)]
/** What every component but a Heading has. */
const NOT_HEADING = ["title", "fields"]

/**
 * Change a component as `patch` says; grown past its section's bottom, as a resize grows it,
 * the section grows to hold it, pushing the sections under it down, and grown past the
 * sheet's right edge, the sheet widens to hold it. Moves go through `placeWidget` and
 * `moveWidget`, which keep a component inside its section and its sheet's width.
 */
export function updateWidget(spec: FormSpec, id: string, patch: WidgetPatch): FormSpec {
  const found = findWidget(spec, id)
  if (found === null) throw new Error(`No sheet has a component ${id}`)
  const { widget } = found
  const tableOnly = TABLE_ONLY.find((key) => key in patch)
  if (tableOnly !== undefined && widget.type !== "tableInput") throw new Error(`Only a Table has ${tableOnly}`)
  const collectionOnly = COLLECTION_ONLY.find((key) => key in patch)
  if (collectionOnly !== undefined && widget.type !== "collection") throw new Error(`Only a Collection has ${collectionOnly}`)
  const shared = TABLE_OR_COLLECTION.find((key) => key in patch)
  if (shared !== undefined && widget.type !== "tableInput" && widget.type !== "collection") throw new Error(`Only a Table or a Collection has ${shared}`)
  const chartOnly = CHART_ONLY.find((key) => key in patch)
  if (chartOnly !== undefined && widget.type !== "chart") throw new Error(`Only a Chart has ${chartOnly}`)
  const headingOnly = HEADING_ONLY.find((key) => key in patch)
  if (headingOnly !== undefined && widget.type !== "heading") throw new Error(`Only a Heading has ${headingOnly}`)
  const notHeading = NOT_HEADING.find((key) => key in patch)
  if (notHeading !== undefined && widget.type === "heading") throw new Error(`A Heading has no ${notHeading}`)
  return editLayout(spec, found.page.id, [found.section.id], (page) => {
    const section = sectionIn(page, found.section.id)
    const changed = Object.assign({ ...widget }, patch)
    section.widgets[found.index] = changed
    holdWidget(page, section, changed)
  })
}

/**
 * Where a component `h` tall may sit in a section `height` tall, as near `y` as it can:
 * inside it, never across its top or its bottom; and, across, where one `w` wide may sit
 * on a sheet `width` wide. One taller than the section sits at its top, and one wider than
 * the sheet at its left edge, and the section or the sheet grows to hold it (`holdWidget`).
 */
export const keptInside = (y: number, h: number, height: number): number => Math.max(0, Math.min(y, height - h))

/**
 * Move a component to `rect` in a section, its own or another, kept inside it and its
 * sheet's width (`keptInside`); into another it goes last, so it is drawn over the
 * components there. Only a component taller than the section it lands in grows it, or one
 * wider than the sheet widens it; the section it left keeps its height.
 */
export function placeWidget(spec: FormSpec, id: string, sectionId: string, rect: Rect): FormSpec {
  const found = findWidget(spec, id)
  if (found === null) throw new Error(`No sheet has a component ${id}`)
  const target = findSection(spec, sectionId)
  if (target === null) throw new Error(`No sheet has a section ${sectionId}`)
  if (target.page !== found.page) throw new Error(`Component ${id} moves only between the sections of its own sheet`)
  return editLayout(spec, found.page.id, [found.section.id, sectionId], (page) => {
    const from = sectionIn(page, found.section.id)
    const section = sectionIn(page, sectionId)
    const { x, y, w, h } = rect
    const placed = { ...found.widget, x: keptInside(x, w, page.width), y: keptInside(y, h, section.height), w, h }
    if (section === from) {
      from.widgets[found.index] = placed
    } else {
      from.widgets.splice(found.index, 1)
      section.widgets.push(placed)
    }
    holdWidget(page, section, placed)
  })
}

/** Move a component within its section, kept inside it and its sheet's width (`keptInside`): a nudge never crosses its section's top or bottom or the sheet's right edge. */
export function moveWidget(spec: FormSpec, id: string, to: { x: number; y: number }): FormSpec {
  const found = findWidget(spec, id)
  if (found === null) throw new Error(`No sheet has a component ${id}`)
  const { section, widget } = found
  return placeWidget(spec, id, section.id, { x: Math.max(0, to.x), y: to.y, w: widget.w, h: widget.h })
}

export const removeWidget = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    for (const section of allSections(draft)) section.widgets = section.widgets.filter((widget) => widget.id !== id)
  })

/** How far below and to the right of a component its copy sits. */
export const COPY_OFFSET = 16

/** A copy of a component just below and to the right of it, in the same section, kept inside it and its sheet's width (`keptInside`). */
export function duplicateWidget(spec: FormSpec, id: string): { spec: FormSpec; id: string } {
  const copyId = newId("w")
  const next = edit(spec, (draft) => {
    const found = findWidget(draft, id)
    if (found === null) throw new Error(`No sheet has a component ${id}`)
    const { x, y, w, h } = found.widget
    const copy = {
      ...structuredClone(found.widget),
      id: copyId,
      x: keptInside(x + COPY_OFFSET, w, found.page.width),
      y: keptInside(y + COPY_OFFSET, h, found.section.height),
    }
    found.section.widgets.splice(found.index + 1, 0, copy)
  })
  return { spec: next, id: copyId }
}

/**
 * Paste a copy of `widget`, a component copied before, into a section: where it sat, moved
 * `COPY_OFFSET` down and to the right while a component in the section sits at exactly that
 * place, kept inside the section and its sheet's width (`addWidget`), with a new id. What it
 * shows or tests of a column the schema has lost since is left out, as removing the column
 * took it out of every component then.
 */
export function pasteWidget(spec: FormSpec, widget: Widget, sectionId: string): { spec: FormSpec; id: string } {
  const found = findSection(spec, sectionId)
  if (found === null) throw new Error(`No sheet has a section ${sectionId}`)
  const { section, page } = found
  const place = (x: number, y: number) => ({ x: keptInside(x, widget.w, page.width), y: keptInside(y, widget.h, section.height) })
  let at = place(widget.x, widget.y)
  while (section.widgets.some((other) => other.x === at.x && other.y === at.y)) {
    const next = place(at.x + COPY_OFFSET, at.y + COPY_OFFSET)
    // Held at the section's bottom right corner, it sits over the last one there.
    if (next.x === at.x && next.y === at.y) break
    at = next
  }
  const id = newId("w")
  const copy: Widget = { ...structuredClone(widget), id, ...at }
  const gone = (field: FieldRef) => fieldColumn(spec, field) === null
  dropShown(copy, gone)
  const keep = (condition: SingleCondition): SingleCondition | null => {
    if (!spec.schema.tables.some((table) => table.id === condition.table)) return null
    let kept: SingleCondition | null = condition
    for (const column of conditionColumns(condition)) {
      if (kept !== null && gone({ table: condition.table, column })) kept = withoutColumn(kept, condition.table, column)
    }
    return kept
  }
  copy.show_when = withoutConditions(copy.show_when, keep)
  if (copy.type === "tableInput" || copy.type === "collection") copy.column_looks = looksKeeping(copy.column_looks, keep)
  return { spec: addWidget(spec, sectionId, copy), id }
}

/** Whose columns a Table or a Collection shows: a Table, many-row tables'; a Collection, one-row tables'. A Chart plots either. */
export const rowsShown = (widget: { type: "tableInput" | "collection" }): SchemaTable["rows"] =>
  widget.type === "collection" ? "one" : "many"

/**
 * Which rows a table's columns fill: one per quote, or many, lined up by the names of
 * their key columns. Columns can share a grid only when this matches.
 */
export const tableGrain = (table: SchemaTable): string =>
  table.rows === "one"
    ? "one"
    : `many:${
        table.columns
          .filter((column) => column.key)
          .map((column) => column.name)
          .sort()
          .join(",") || table.id
      }`

/**
 * Which rows a Chart's columns fill (`tableGrain`): its first field's table's, else that of
 * the column labelling its rows; null while it has neither. The rest line up with it.
 */
export function chartGrain(schema: FormSchema, widget: ChartWidget): string | null {
  const refs = widget.category === null ? widget.fields : [...widget.fields, widget.category]
  for (const field of refs) {
    const found = fieldColumn({ schema }, field)
    if (found !== null) return tableGrain(found.table)
  }
  return null
}

/** Whether a Chart can plot a column: a number column, other than an index, which numbers the rows, or the quote's option. */
export const plottable = (column: SchemaColumn): boolean =>
  (column.type === "int" || column.type === "float") && !column.index && !isIdentityColumn(column)

/**
 * The columns that can label a Chart's rows: those of the many-row tables of its grain,
 * other than an index ("Row number" numbers the rows as an index does) and the quote's
 * identity, which the sheets never show, by table.
 */
export function labelColumns(spec: FormSpec, widget: ChartWidget): { table: SchemaTable; columns: SchemaColumn[] }[] {
  const grain = chartGrain(spec.schema, widget)
  if (grain === null || grain === "one") return []
  return spec.schema.tables
    .filter((table) => table.rows === "many" && tableGrain(table) === grain)
    .map((table) => ({ table, columns: sheetColumns(table).filter((column) => !column.index) }))
    .filter(({ columns }) => columns.length > 0)
}

/** A component's order: its fields, with a Collection's spacers among them where they sit. */
export function orderItems(widget: FieldsWidget): OrderItem[] {
  const spacers = widget.type === "collection" ? widget.spacers : []
  const items: OrderItem[] = []
  let next = 0
  const spacersUpTo = (before: number) => {
    while (next < spacers.length && spacers[next] <= before) {
      items.push({ kind: "spacer" })
      next += 1
    }
  }
  widget.fields.forEach((field, index) => {
    spacersUpTo(index)
    items.push({ kind: "field", field })
  })
  spacersUpTo(Infinity)
  return items
}

/** Write a component's order back to it, in a draft: its fields, and a Collection's spacers, none while it shows no fields. */
function setOrder(widget: FieldsWidget, items: readonly OrderItem[]): void {
  const fields: FieldRef[] = []
  const spacers: number[] = []
  for (const item of items) {
    if (item.kind === "field") fields.push(item.field)
    else spacers.push(fields.length)
  }
  widget.fields = fields
  if (widget.type === "collection") widget.spacers = fields.length === 0 ? [] : spacers
  // A column the Table or the Collection stops showing loses its look.
  if (widget.type === "tableInput" || widget.type === "collection") {
    widget.column_looks = widget.column_looks.filter((look) => fields.some((field) => field.table === look.table && field.column === look.column))
  }
}

/** The Collection with `id`: only a Collection has spacers. */
function collectionOf(spec: FormSpec, id: string): Extract<Widget, { type: "collection" }> {
  const widget = widgetOf(spec, id)
  if (widget.type !== "collection") throw new Error("Only a Collection has spacers")
  return widget
}

/** Move a field, or a Collection's spacer, from one place in its order (`orderItems`) to another. */
export const moveInOrder = (spec: FormSpec, widgetId: string, from: number, to: number): FormSpec =>
  edit(spec, (draft) => {
    const widget = fieldsWidgetOf(draft, widgetId)
    const items = orderItems(widget)
    const [item] = items.splice(from, 1)
    if (item === undefined) throw new Error(`Nothing is at ${from} in its order`)
    items.splice(to, 0, item)
    setOrder(widget, items)
  })

/**
 * Show the field in the component if it does not, at the end of its order, after any spacer
 * there; or stop showing it if it does, the spacers after it keeping their places among the
 * fields.
 */
export const toggleField = (spec: FormSpec, widgetId: string, field: FieldRef): FormSpec =>
  edit(spec, (draft) => {
    const widget = fieldsWidgetOf(draft, widgetId)
    const items = orderItems(widget)
    const at = items.findIndex((item) => item.kind === "field" && item.field.table === field.table && item.field.column === field.column)
    if (at >= 0) items.splice(at, 1)
    else items.push({ kind: "field", field })
    setOrder(widget, items)
  })

/** Add a spacer to a Collection, after its last field. */
export const addSpacer = (spec: FormSpec, widgetId: string): FormSpec =>
  edit(spec, (draft) => {
    const widget = collectionOf(draft, widgetId)
    widget.spacers.push(widget.fields.length)
  })

/** Remove the spacer at a place in a Collection's order (`orderItems`). */
export const removeSpacer = (spec: FormSpec, widgetId: string, at: number): FormSpec =>
  edit(spec, (draft) => {
    const widget = collectionOf(draft, widgetId)
    const items = orderItems(widget)
    if (items[at]?.kind !== "spacer") throw new Error(`No spacer is at ${at}`)
    items.splice(at, 1)
    setOrder(widget, items)
  })

// ─── The sample ──────────────────────────────────────────────────────────

/** A value typed into the sample: text, or a tick. The server types it by its column. */
export type SampleValue = string | boolean
/** A row of the sample, keyed by column id. */
export type SampleRow = Record<string, SampleValue>

/** `rows` with one cell set, padded with empty rows up to it. */
export function withCell(rows: readonly SampleRow[], row: number, columnId: string, value: SampleValue): SampleRow[] {
  const next = [...rows, ...Array.from({ length: Math.max(0, row + 1 - rows.length) }, (): SampleRow => ({}))]
  next[row] = { ...next[row], [columnId]: value }
  return next
}

/** The sample with one cell of a table's row set, empty rows added up to it. */
export const withSampleCell = (spec: FormSpec, tableId: string, row: number, columnId: string, value: SampleValue): FormSpec =>
  edit(spec, (draft) => {
    draft.sample[tableId] = withCell(draft.sample[tableId] ?? [], row, columnId, value)
  })

/**
 * The sample with several tables' rows replaced, as one edit: a grid adds and deletes a row
 * in every table it shows at once.
 */
export const withSampleRows = (spec: FormSpec, rowsByTable: Record<string, SampleRow[]>): FormSpec =>
  edit(spec, (draft) => {
    for (const [tableId, rows] of Object.entries(rowsByTable)) draft.sample[tableId] = rows
  })

/** `rows` with empty rows added to make `length`. */
export const padRows = (rows: readonly SampleRow[], length: number): SampleRow[] => [
  ...rows,
  ...Array.from({ length: Math.max(0, length - rows.length) }, (): SampleRow => ({})),
]

/** How many of `rows` there are up to the last with a value typed in any column. */
const typedRows = (rows: readonly SampleRow[]): number => rows.reduce((last, row, index) => (Object.values(row).some(filled) ? index + 1 : last), 0)

/**
 * Set the rows a Table's grid starts with, and start its grid in the sample again at them,
 * so the change shows while building: the input tables it shows keep their rows up to the
 * last with a value typed in any of them, empty rows making up the rest of its rows; with
 * nothing typed they hold no rows, so the grid shows its start. No value typed is dropped.
 */
export const setTableRows = (spec: FormSpec, widgetId: string, rows: number): FormSpec => {
  const next = updateWidget(spec, widgetId, { rows })
  const tables = gridInputTables(shownFields(next.schema, widgetOf(next, widgetId))).map((table) => table.id)
  const kept = Math.max(0, ...tables.map((id) => typedRows(next.sample[id] ?? [])))
  return edit(next, (draft) => {
    for (const id of tables) {
      if (kept === 0) delete draft.sample[id]
      else draft.sample[id] = padRows((draft.sample[id] ?? []).slice(0, kept), Math.max(kept, rows))
    }
  })
}

const bases = new WeakMap<FormSpec, string>()

/**
 * What pricing the sample depends on, as one string to compare (`valuesBasis`): the schema,
 * the columns the sheets hide as the sample sets them, and the sample. A sheet laid out
 * differently prices the same, while a condition that hides or shows a column does not.
 */
export function pricingBasis(spec: FormSpec): string {
  let basis = bases.get(spec)
  if (basis === undefined) {
    basis = valuesBasis(spec.schema, sheetVisibility(spec, spec.sample).hidden, spec.sample)
    bases.set(spec, basis)
  }
  return basis
}

/** What a pricing gave: the values it priced, as the server typed them, and its output tables by name. */
interface Priced {
  readonly sample: Readonly<Record<string, unknown>>
  readonly tables: Readonly<Record<string, PricedRows>>
}

/**
 * Each many-row output table of `outputs` lined up with a grid's `count` rows, through the
 * first of `inputs` (the many-row input tables shown, in order) keyed alike: on each grid
 * row, the output row `pricedRows` finds for it, the input table read as the quote reads it
 * (`quoted`), so its filled rows are the typed rows the pricing answers with. An output
 * table no input table lines up with is left out, and every one while there is no price.
 */
export function linedUpOutputs(
  inputs: readonly SchemaTable[],
  outputs: readonly SchemaTable[],
  held: RowsByTable,
  count: number,
  price: Priced | null,
  quoted: FormSchema,
): Map<string, (Record<string, unknown> | undefined)[]> {
  const lined = new Map<string, (Record<string, unknown> | undefined)[]>()
  if (price === null) return lined
  for (const output of outputs) {
    const input = inputs.find((table) => tableGrain(table) === tableGrain(output))
    const read = quoted.tables.find((table) => table.id === input?.id)
    if (input === undefined || read === undefined || lined.has(output.id)) continue
    const typed = price.sample[input.name]
    const grid = Array.from({ length: count }, (_, row) => held[input.id]?.[row])
    lined.set(output.id, pricedRows(read, grid, Array.isArray(typed) ? (typed as PricedRows) : [], price.tables[output.name] ?? []))
  }
  return lined
}

/** A row's value under `name`, there and not null: a value a key can match by. */
const keyedBy = (row: Record<string, unknown> | undefined, name: string): boolean => row?.[name] !== undefined && row[name] !== null

/**
 * Output tables shown without an input table, lined up with the first one's answer: its
 * rows as pricing gave them, and each other table's row keyed like each of them by the
 * first's key columns (none without keys), as a Chart and a Table of output columns alone
 * show them. Nothing while there is no price.
 */
export function answeredOutputs(
  outputs: readonly SchemaTable[],
  price: Priced | null,
): Map<string, (Record<string, unknown> | undefined)[]> {
  const lined = new Map<string, (Record<string, unknown> | undefined)[]>()
  const [first] = outputs
  if (first === undefined || price === null) return lined
  const answer = price.tables[first.name] ?? []
  const keys = first.columns.filter((column) => column.key).map((column) => column.name)
  for (const table of outputs) {
    if (lined.has(table.id)) continue
    const own = price.tables[table.name] ?? []
    const matched = (row: Record<string, unknown>) =>
      keys.length === 0
        ? undefined
        : own.find((candidate) => keys.every((name) => keyedBy(row, name) && keyedBy(candidate, name) && String(candidate[name]) === String(row[name])))
    lined.set(table.id, table.id === first.id ? [...answer] : answer.map(matched))
  }
  return lined
}

/** What stops a component showing what it should, by the component it is about. */
export interface WidgetProblem {
  widgetId: string
  message: string
}

const KIND_NAMES: Record<WidgetType, string> = { tableInput: "Table", collection: "Collection", chart: "Chart", heading: "Heading" }

/** `Table "Layers"`, a Heading by its text, `Heading "Cover"`, or `A Table` while it has no title. */
export const widgetName = (widget: Widget): string => {
  const name = widget.type === "heading" ? widget.text.trim() : widget.title
  return name ? `${KIND_NAMES[widget.type]} "${name}"` : `A ${KIND_NAMES[widget.type]}`
}

/**
 * What stops a component showing what it should: no fields, a column no longer in the
 * schema, a table of the other kind (as when a table is switched between one row and
 * many after its columns were chosen), tables whose rows do not line up, or what stops
 * a Chart plotting what it should (`chartProblems`); a Heading's blank text; and what
 * stops its conditions doing what they say (`conditionProblems`).
 */
export function widgetProblems(spec: FormSpec): WidgetProblem[] {
  const problems: WidgetProblem[] = []
  const never = neverShown(spec)
  for (const widget of allWidgets(spec)) {
    const name = widgetName(widget)
    const problem = (message: string) => problems.push({ widgetId: widget.id, message: `${name} ${message}` })
    if (widget.type === "heading") {
      if (widget.text.trim() === "") problem("has no text")
    } else {
      if (widget.fields.length === 0) problem("shows no fields")
      const found = widget.fields.map((field) => fieldColumn(spec, field))
      if (found.includes(null)) problem("shows a column that is no longer in the schema")
      if (widget.type === "chart") {
        for (const message of chartProblems(spec, widget)) problem(message)
      } else {
        const rows = rowsShown(widget)
        if (found.some((shown) => shown !== null && shown.table.rows !== rows)) {
          problem(rows === "many" ? "shows a one-row table; a Collection shows those" : "shows a many-row table; a Table shows those")
        }
        const grains = new Set(found.flatMap((shown) => (shown !== null && shown.table.rows === rows ? [tableGrain(shown.table)] : [])))
        // A Table's many-row input tables sit side by side row by row, so a column giving one
        // its rows must give each of them the same, in the same order.
        const given = new Set(
          found.flatMap((shown) =>
            widget.type === "tableInput" && shown !== null && shown.table.role === "input" && shown.table.rows === "many" && !shown.column.index
              ? [JSON.stringify(presetColumn(shown.table)?.options ?? null)]
              : [],
          ),
        )
        if (grains.size > 1 || given.size > 1) problem("shows tables whose rows do not line up")
      }
    }
    for (const message of conditionProblems(spec, widget, never)) problem(message)
    if (widget.type !== "tableInput" && widget.type !== "collection") continue
    for (const field of widget.fields) {
      const holder = { id: fieldKey(widget.id, field.table, field.column), show_when: fieldShowWhen(widget, field) }
      const label = columnLabel(fieldColumn(spec, field)?.column)
      for (const message of conditionProblems(spec, holder, never, "fields")) {
        problems.push({ widgetId: widget.id, message: `${name}'s field "${label}" ${message}` })
      }
    }
  }
  return problems
}

/**
 * What stops a Chart plotting what it should: a field it cannot plot (not a number, or an
 * index), the column labelling its rows gone, tables whose rows do not line up (its fields'
 * and that column's), or a pie of many rows given more than one field, of which it plots
 * the first.
 */
function chartProblems(spec: FormSpec, widget: ChartWidget): string[] {
  const problems: string[] = []
  const found = widget.fields.map((field) => fieldColumn(spec, field))
  for (const shown of found) {
    if (shown === null || plottable(shown.column)) continue
    const label = shown.column.label || readableName(shown.column.name)
    problems.push(`plots ${label}, which ${shown.column.index ? "numbers the rows" : "is not a number"}`)
  }
  const category = widget.category === null ? undefined : fieldColumn(spec, widget.category)
  if (category === null) problems.push("labels its rows by a column that is no longer in the schema")
  const lined = [...found, category ?? null].flatMap((shown) => (shown === null ? [] : [tableGrain(shown.table)]))
  if (new Set(lined).size > 1) problems.push("shows tables whose rows do not line up")
  const grain = chartGrain(spec.schema, widget)
  if (widget.chart_type === "pie" && grain !== null && grain !== "one" && widget.fields.length > 1) {
    problems.push("is a pie of many rows, so it plots only its first field")
  }
  return problems
}

/** What stops a sheet's conditions doing what they say, by the sheet it is about. */
export interface PageProblem {
  pageId: string
  message: string
}

/** `Sheet "Business interruption"`. */
export const pageName = (page: Page): string => `Sheet "${page.title}"`

/** What stops a sheet's conditions doing what they say (`conditionProblems`). */
export function pageProblems(spec: FormSpec): PageProblem[] {
  const never = neverShown(spec)
  return spec.pages.flatMap((page) =>
    conditionProblems(spec, page, never).map((message) => ({ pageId: page.id, message: `${pageName(page)} ${message}` })),
  )
}

/** What stops a section's conditions doing what they say, by the section it is about. */
export interface SectionProblem {
  sectionId: string
  message: string
}

/** `Section "Flood"`. */
export const sectionName = (section: Section): string => `Section "${section.title}"`

/** What stops a section's conditions doing what they say (`conditionProblems`). */
export function sectionProblems(spec: FormSpec): SectionProblem[] {
  const never = neverShown(spec)
  return allSections(spec).flatMap((section) =>
    conditionProblems(spec, section, never).map((message) => ({ sectionId: section.id, message: `${sectionName(section)} ${message}` })),
  )
}

/** The tests that compare a value with the condition's own, which allowed values should be. */
const EQUALITY_TESTS: ReadonlySet<ConditionTest> = new Set(["equals", "not_equals", "one_of", "not_one_of"])

/** Whether `value` is one of `column`'s allowed values, a number's read as numbers. */
function allowed(column: SchemaColumn, value: string): boolean {
  if (column.type === "int" || column.type === "float") {
    const number = parseTypedNumber(value)
    return column.options.some((option) => number !== null && parseTypedNumber(option) === number)
  }
  return column.options.some((option) => option.trim() === value.trim())
}

/**
 * What stops a sheet's, a section's or a component's conditions doing what they say: a
 * condition that cannot hold as written (`conditionFault`); a value tested for equality that
 * is not one of its column's allowed values, which no value chosen from them can match; a
 * number `match` asking for more conditions than its `show_when` or group holds; and,
 * while neither, nothing able to show it again once hidden (`neverShown`), its `id` a
 * field's `fieldKey` for a field, whose columns waited on hide with other `fields`.
 */
function conditionProblems(
  spec: FormSpec,
  holder: { id: string; show_when: ShowWhen },
  never: ReadonlyMap<string, SingleCondition>,
  hidingWith: "components" | "fields" = "components",
): string[] {
  const problems: string[] = []
  const { match, conditions } = holder.show_when
  // With no conditions it always shows, whatever the match asks for.
  if (typeof match === "number" && conditions.length > 0 && match > conditions.length) {
    problems.push(`needs at least ${match} conditions to hold but has only ${conditions.length}, so it never shows`)
  }
  for (const entry of conditions) {
    if (isGroup(entry) && typeof entry.match === "number" && entry.match > entry.conditions.length) {
      problems.push(`has a group that needs at least ${entry.match} conditions to hold but holds only ${entry.conditions.length}`)
    }
  }
  for (const condition of leafConditions(holder.show_when)) {
    const fault = conditionFault(spec, condition)
    if (fault !== null) {
      problems.push(fault)
      continue
    }
    // A condition that fits names a table, and the columns of it it tests, the schema has.
    const table = schemaTableOf(spec, condition.table)
    const tests: ColumnTest[] = isListCondition(condition) ? condition.where : [condition]
    for (const test of tests) {
      const column = table.columns.find((candidate) => candidate.id === test.column) as SchemaColumn
      if (!column.options.length || !EQUALITY_TESTS.has(test.op)) continue
      for (const value of test.values) {
        if (!allowed(column, value)) problems.push(`tests ${column.name} against "${value}", which isn't one of its allowed values`)
      }
    }
  }
  const waiting = never.get(holder.id)
  if (waiting !== undefined) {
    // Typed into a Table or a Collection: a Chart only plots and a Heading shows none, so neither counts.
    const entered = (keys: string[]) => allWidgets(spec).some((widget) => fieldKeys(widget).some((key) => keys.includes(key)))
    if (isListCondition(waiting)) {
      // A condition on rows that never shows again fits, so its table and the columns it tests are in the schema.
      const table = schemaTableOf(spec, waiting.table)
      const tested = listColumns(waiting)
      const missing = tested.find((id) => !entered([columnKey(table.id, id)]))
      const keys = sheetColumns(table)
        .filter((column) => !column.index)
        .map((column) => columnKey(table.id, column.id))
      if (missing !== undefined) {
        problems.push(`waits on ${(table.columns.find((column) => column.id === missing) as SchemaColumn).name}, which is on no Table`)
      } else {
        problems.push(
          tested.length > 0 || entered(keys)
            ? `waits on the rows of ${table.name}, which are only on ${hidingWith} that hide with it, so it could not show again once hidden`
            : `waits on the rows of ${table.name}, which are on no Table`,
        )
      }
    } else {
      const name = fieldColumn(spec, waiting)?.column.name ?? waiting.column
      problems.push(
        entered([columnKey(waiting.table, waiting.column)])
          ? `waits on ${name}, which is only on ${hidingWith} that hide with it, so it could not show again once hidden`
          : `waits on ${name}, which is on no Table or Collection`,
      )
    }
  }
  return problems
}

/**
 * A condition read by its columns' labels and its table's name: `Cover BI is ticked`,
 * `Class of business is one of Motor, Fleet`, `Age is less than 25 in any row of drivers`,
 * `Number of rows in perils where Include coverage is ticked is at least 2`, `Sum of Limit in
 * perils is greater than 1000000`.
 */
export function conditionText(spec: Pick<FormSpec, "schema">, condition: SingleCondition): string {
  if (isListCondition(condition)) return listText(spec, condition)
  return testText(fieldColumn(spec, condition)?.column ?? null, condition)
}

/** A column's label, or what stands for one gone. */
const columnLabel = (column: SchemaColumn | null | undefined): string =>
  column === null || column === undefined ? "A missing column" : column.label || readableName(column.name)

/** A test of a column read by its label: `Age is less than 25`. */
function testText(column: SchemaColumn | null | undefined, test: ColumnTest): string {
  const words = testLabel(test.op, column?.type ?? "str")
  const text = `${columnLabel(column)} ${words}`
  return test.values.length === 0 ? text : `${text} ${test.values.join(", ")}`
}

/** A condition on a list's rows read as `conditionText` says. */
function listText(spec: Pick<FormSpec, "schema">, condition: ListCondition): string {
  const table = spec.schema.tables.find((candidate) => candidate.id === condition.table)
  const name = table?.name ?? "a missing table"
  const columnOf = (id: string) => table?.columns.find((column) => column.id === id)
  const tests = condition.where.map((test) => testText(columnOf(test.column), test)).join(" and ")
  if (isRowsCondition(condition)) return `${tests} in ${condition.rows} row of ${name}`
  const where = condition.where.length === 0 ? "" : ` where ${tests}`
  if (isRowCount(condition)) return `Number of rows in ${name}${where} ${testLabel(condition.op, "int")} ${condition.count}`
  const kind = TOTALS.find((total) => total.value === condition.total)?.label ?? condition.total
  return `${kind} of ${columnLabel(columnOf(condition.column))} in ${name}${where} ${testLabel(condition.op, "float")} ${condition.value}`
}

/** A match as the sentences say it: `all`, `any` or `at least 2`. */
const matchText = (match: ShowWhen["match"]): string => (typeof match === "number" ? `at least ${match}` : match)

/**
 * When a sheet, a section or a component shows, as its eye's title says it: a group of two or
 * more as `any of (…, …)`, and one asking for at least a number however many it holds.
 */
export function showWhenText(spec: Pick<FormSpec, "schema">, showWhen: ShowWhen): string {
  const parts = showWhen.conditions.map((entry) => {
    if (!isGroup(entry)) return conditionText(spec, entry)
    const texts = entry.conditions.map((condition) => conditionText(spec, condition))
    return texts.length === 1 && typeof entry.match !== "number" ? texts[0] : `${matchText(entry.match)} of (${texts.join(", ")})`
  })
  if (parts.length === 0) return "Always shows"
  if (parts.length === 1 && typeof showWhen.match !== "number") return `Shows when ${parts[0]}`
  return `Shows when ${matchText(showWhen.match)} of these are true: ${parts.join("; ")}`
}

// ─── Problems ────────────────────────────────────────────────────────────

/** What stops the schema being the pipeline's tables, by the table it is about. */
export interface SchemaProblem {
  tableId: string
  message: string
}

/** Why `name` cannot name a frame's column, as the server holds it (`is_frame_label`), or null. */
export function columnNameProblem(name: string): string | null {
  if (!ASCII_IDENTIFIER_RE.test(name)) return "needs a name of letters, digits and _"
  if (PYTHON_KEYWORDS.has(name)) return "cannot be named with a Python keyword"
  return null
}

/** Whether `text` is a number, a whole one when `whole`, as a cell and the server read one. */
const isNumber = (text: string, whole: boolean): boolean => {
  const n = parseTypedNumber(text)
  return n !== null && (!whole || Number.isInteger(n))
}

/**
 * What would stop the schema being the pipeline's tables. A table's name is a port's
 * name, so it is held to the Quote Input's label rules (`apiInputLabelIssue`, with the
 * document's reserved labels); a column's name is an identifier, unique in its table; a
 * many-row table needs a key, and one column at most gives it a row for each value; an
 * input column's rules must make sense (a range that is not inverted, allowed values of its
 * type, read from a params file rather than typed in), and a one-row input column's
 * default must be a value they take. A table without the quote's identity whose own column
 * takes either of its names is named as the server names it (`identity_clashes`).
 */
export function schemaProblems(spec: FormSpec, reservedLabels: ReadonlySet<string>): SchemaProblem[] {
  const problems: SchemaProblem[] = []
  const { tables } = spec.schema
  tables.forEach((table, index) => {
    const name = `Table "${table.name}"`
    // Haute's own table is the one with its id; the server refuses any other by its name,
    // in these words.
    if (table.name === QUOTE_DETAILS_ID && table.id !== QUOTE_DETAILS_ID) {
      problems.push({ tableId: table.id, message: QUOTE_DETAILS_REFUSAL })
      return
    }
    const issue = apiInputLabelIssueMessage(
      apiInputLabelIssue(
        table.name,
        tables.filter((_, other) => other !== index).map((other) => other.name),
        reservedLabels,
      ),
    )
    if (issue !== null) problems.push({ tableId: table.id, message: `${name}: ${issue}` })
    if (!isQuoteDetails(table) && !table.columns.some(isIdentityColumn)) {
      for (const column of table.columns) {
        if (IDENTITY_NAMES.has(column.name)) problems.push({ tableId: table.id, message: identityClash(table, column) })
      }
    }
    if (table.rows === "many" && !table.columns.some((column) => column.key)) {
      problems.push({ tableId: table.id, message: `${name} has many rows, so it needs a key column` })
    }
    const given = presetColumn(table)
    for (const column of table.columns) {
      if (given !== null && column !== given && column.row_per_value && !column.index && column.options.length > 0) {
        problems.push({ tableId: table.id, message: `${name} has a row for each value of ${given.name}, so ${column.name} gives it none` })
      }
    }
    const columnNames = new Set<string>()
    for (const column of table.columns) {
      const columnName = `Column "${column.name}" in ${name}`
      const nameProblem = columnNameProblem(column.name)
      if (nameProblem !== null) {
        problems.push({ tableId: table.id, message: `${columnName} ${nameProblem}` })
      } else if (columnNames.has(column.name)) {
        problems.push({ tableId: table.id, message: `${columnName} repeats the name ${column.name}` })
      }
      columnNames.add(column.name)
      if (table.role !== "input" || column.index) continue
      if (!takesOptions(column.type) && column.options.length > 0) {
        const kind = column.type === "bool" ? "a tick box" : "a date"
        problems.push({ tableId: table.id, message: `${columnName} allows values, which ${kind} cannot` })
      } else if (column.options.length > 0 && column.options_source === null) {
        problems.push({ tableId: table.id, message: `${columnName} has allowed values typed in, not read from a params file` })
      }
      if (column.type === "int" || column.type === "float") {
        if (column.min !== null && column.max !== null && column.min > column.max) {
          problems.push({ tableId: table.id, message: `${columnName} has a minimum above its maximum` })
        }
        const bad = column.options.find((option) => !isNumber(option, column.type === "int"))
        const kind = column.type === "int" ? "a whole number" : "a number"
        if (bad !== undefined) {
          problems.push({ tableId: table.id, message: `${columnName} allows "${bad}", which isn't ${kind}` })
        }
      }
      const refused = table.rows === "one" ? defaultProblem(column) : null
      if (refused !== null) problems.push({ tableId: table.id, message: `${columnName} has a default its rules refuse: ${refused}` })
    }
  })
  return problems
}

/**
 * What its rules refuse in a one-row input column's default, or null: a tick box starts
 * ticked or not and any other column with text, which must pass its rules as a cell typed
 * into must (`cellProblem`).
 */
function defaultProblem(column: SchemaColumn): string | null {
  const value = column.default_value
  if (!filled(value)) return null
  if ((column.type === "bool") !== (value === true)) return column.type === "bool" ? "Not a tick" : "Not text"
  return cellProblem(column, value)
}

// ── Haute's quote_details table ──────────────────────────────────────────

export { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED }
/** Why another table under its name is refused, in the server's words (`QUOTE_DETAILS_REFUSAL` in `src/haute/_workbench_form.py`). */
export const QUOTE_DETAILS_REFUSAL = "quote_details is Haute's own table of a quote's details: name yours otherwise"

/** Its columns, in order, as id, name and type (`QUOTE_DETAILS_COLUMNS`), a quote's administration; their structure is fixed. */
export const QUOTE_DETAILS_COLUMNS: ReadonlyArray<readonly [id: string, name: string, type: SchemaColumn["type"]]> = [
  ["qd_reference", "quote_reference", "str"],
  ["qd_option", "quote_option", "int"],
  ["qd_quote_name", "quote_name", "str"],
  ["qd_insured", "insured", "str"],
  ["qd_broker", "broker", "str"],
  ["qd_inception_date", "inception_date", "date"],
  ["qd_expiry_date", "expiry_date", "date"],
  ["qd_is_renewal", "is_renewal", "bool"],
  ["qd_stage", "stage", "str"],
  ["qd_assignee", "assignee", "str"],
  ["qd_archived", "archived", "bool"],
  ["qd_created_date", "created_date", "date"],
  ["qd_created_by", "created_by", "str"],
  ["qd_modified_date", "modified_date", "date"],
  ["qd_modified_by", "modified_by", "str"],
  ["qd_haute_version", "haute_version", "str"],
]

/** Why a table's own column keeps the quote's identity from it, in the server's words (`identity_clashes`). */
const identityClash = (table: SchemaTable, column: SchemaColumn): string =>
  `${table.name}.${column.name} has a name Haute keeps for the quote's identity in every table: rename it, and the table takes quote_reference and quote_option at the next save`

/** Whether `table` is Haute's quote_details table, whose structure the editor does not change. */
export const isQuoteDetails = (table: Pick<SchemaTable, "id">): boolean => table.id === QUOTE_DETAILS_ID
