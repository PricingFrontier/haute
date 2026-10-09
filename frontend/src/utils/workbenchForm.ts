/**
 * Pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, the sheets and the components on them, what the components show of the
 * schema, and what would stop the schema being the pipeline's tables or a component
 * showing what it should. Each operation returns a new form and leaves its input untouched.
 */
import type { FieldRef, FormSpec, Page, SchemaColumn, SchemaTable } from "../api/types"
import { apiInputLabelIssue, apiInputLabelIssueMessage } from "./apiInputPorts"
import type { Rect } from "./sheetGeometry"

/** A sheet's widget: a Table or a Collection. */
export type Widget = Page["widgets"][number]
export type WidgetType = Widget["type"]
/** A column's type: the Quote Input's column types. */
export type ColumnType = SchemaColumn["type"]

export const newId = (prefix: string): string => `${prefix}_${crypto.randomUUID().slice(0, 8)}`

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

export const allWidgets = (spec: FormSpec): Widget[] => spec.pages.flatMap((page) => page.widgets)

const edit = (spec: FormSpec, change: (draft: FormSpec) => void): FormSpec => {
  const draft = structuredClone(spec)
  change(draft)
  return draft
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

export const createSchemaTable = (spec: FormSpec): SchemaTable => ({
  id: newId("t"),
  name: uniqueName("table", spec.schema.tables.map((table) => table.name)),
  role: "input",
  rows: "one",
  columns: [],
})

export const createSchemaColumn = (spec: FormSpec, tableId: string): SchemaColumn => ({
  id: newId("c"),
  name: uniqueName("column", schemaTableOf(spec, tableId).columns.map((column) => column.name)),
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
})

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

/** Take the schema columns `drop` matches out of every widget that shows them. */
const dropFields = (draft: FormSpec, drop: (field: FieldRef) => boolean): void => {
  for (const widget of allWidgets(draft)) widget.fields = widget.fields.filter((field) => !drop(field))
}

/** Removing a table removes its columns from the widgets that show them, and its sample rows. */
export const removeSchemaTable = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    draft.schema.tables = draft.schema.tables.filter((table) => table.id !== id)
    dropFields(draft, (field) => field.table === id)
    delete draft.sample[id]
  })

/** A table with one row per quote has no keys: keys say which of many rows is which. */
export const setSchemaTableRows = (spec: FormSpec, id: string, rows: SchemaTable["rows"]): FormSpec =>
  edit(spec, (draft) => {
    const table = schemaTableOf(draft, id)
    table.rows = rows
    if (rows === "one") for (const column of table.columns) column.key = false
  })

/** Add a column at `index`, or at the end. */
export const addSchemaColumn = (spec: FormSpec, tableId: string, column: SchemaColumn, index?: number): FormSpec =>
  edit(spec, (draft) => {
    const { columns } = schemaTableOf(draft, tableId)
    columns.splice(index ?? columns.length, 0, column)
  })

export const moveSchemaColumn = (spec: FormSpec, tableId: string, columnId: string, to: number): FormSpec =>
  edit(spec, (draft) => {
    const { columns } = schemaTableOf(draft, tableId)
    const from = columns.findIndex((column) => column.id === columnId)
    const [column] = columns.splice(from, 1)
    columns.splice(to, 0, column)
  })

export const updateSchemaColumn = (
  spec: FormSpec,
  tableId: string,
  columnId: string,
  patch: Partial<SchemaColumn>,
): FormSpec =>
  edit(spec, (draft) => {
    Object.assign(schemaColumnOf(schemaTableOf(draft, tableId), columnId), patch)
  })

/** Removing a column removes it from the widgets that show it, and its sample values. */
export const removeSchemaColumn = (spec: FormSpec, tableId: string, columnId: string): FormSpec =>
  edit(spec, (draft) => {
    const table = schemaTableOf(draft, tableId)
    table.columns = table.columns.filter((column) => column.id !== columnId)
    dropFields(draft, (field) => field.table === tableId && field.column === columnId)
    for (const row of draft.sample[tableId] ?? []) delete row[columnId]
  })

/** The widgets showing a schema table's columns, or just one of them. */
export const fieldUses = (spec: FormSpec, tableId: string, columnId?: string): Widget[] =>
  allWidgets(spec).filter((widget) =>
    widget.fields.some((field) => field.table === tableId && (columnId === undefined || field.column === columnId)),
  )

/** The schema table and column a field names, or null when it is no longer in the schema. */
export function fieldColumn(spec: FormSpec, field: FieldRef): { table: SchemaTable; column: SchemaColumn } | null {
  const table = spec.schema.tables.find((candidate) => candidate.id === field.table)
  const column = table?.columns.find((candidate) => candidate.id === field.column)
  return table && column ? { table, column } : null
}

/** A field a component shows, with the schema column it names, or null when that is gone. */
export interface ShownField {
  key: string
  found: { table: SchemaTable; column: SchemaColumn } | null
}

/** The fields a component shows, each with its column, in the component's order. */
export const shownFields = (spec: FormSpec, widget: Widget): ShownField[] =>
  widget.fields.map((field) => ({ key: `${field.table}:${field.column}`, found: fieldColumn(spec, field) }))

// ─── Sheets and components ───────────────────────────────────────────────

function pageOf(spec: FormSpec, id: string): Page {
  const page = spec.pages.find((candidate) => candidate.id === id)
  if (page === undefined) throw new Error(`The form has no sheet ${id}`)
  return page
}

export const createPage = (spec: FormSpec): Page => ({
  id: newId("page"),
  title: uniqueName(`Sheet ${spec.pages.length + 1}`, spec.pages.map((page) => page.title)),
  widgets: [],
})

export const addPage = (spec: FormSpec, page: Page): FormSpec =>
  edit(spec, (draft) => {
    draft.pages.push(page)
  })

export const renamePage = (spec: FormSpec, id: string, title: string): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, id).title = title
  })

/** Remove a sheet with its components; the schema and the sample stay. The last sheet stays. */
export const removePage = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, id)
    if (draft.pages.length === 1) throw new Error("The last sheet cannot be removed")
    draft.pages = draft.pages.filter((page) => page.id !== id)
  })

/** The component with `id`, with its sheet and its place on it, or null. */
export function findWidget(spec: FormSpec, id: string): { widget: Widget; page: Page; index: number } | null {
  for (const page of spec.pages) {
    const index = page.widgets.findIndex((widget) => widget.id === id)
    if (index >= 0) return { widget: page.widgets[index], page, index }
  }
  return null
}

function widgetOf(spec: FormSpec, id: string): Widget {
  const found = findWidget(spec, id)
  if (found === null) throw new Error(`No sheet has a component ${id}`)
  return found.widget
}

/** A new component of `type` at `rect`, showing nothing yet. */
export function createWidget(type: WidgetType, rect: Rect): Widget {
  const placed = { id: newId("w"), ...rect, title: "", fields: [] as FieldRef[] }
  return type === "collection" ? { ...placed, type, columns: 3 } : { ...placed, type, rows: 3 }
}

export const addWidget = (spec: FormSpec, pageId: string, widget: Widget): FormSpec =>
  edit(spec, (draft) => {
    pageOf(draft, pageId).widgets.push(widget)
  })

/** What any component can be given; `rows` is a Table's alone and `columns` a Collection's. */
export type WidgetPatch = Partial<Rect> & { title?: string; fields?: FieldRef[]; rows?: number; columns?: number }

export const updateWidget = (spec: FormSpec, id: string, patch: WidgetPatch): FormSpec =>
  edit(spec, (draft) => {
    const widget = widgetOf(draft, id)
    if ("rows" in patch && widget.type !== "tableInput") throw new Error("Only a Table has rows")
    if ("columns" in patch && widget.type !== "collection") throw new Error("Only a Collection has columns")
    Object.assign(widget, patch)
  })

export const removeWidget = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    for (const page of draft.pages) page.widgets = page.widgets.filter((widget) => widget.id !== id)
  })

/** A copy of a component just below and to the right of it, on the same sheet. */
export function duplicateWidget(spec: FormSpec, id: string): { spec: FormSpec; id: string } {
  const copyId = newId("w")
  const next = edit(spec, (draft) => {
    const found = findWidget(draft, id)
    if (found === null) throw new Error(`No sheet has a component ${id}`)
    const copy = { ...structuredClone(found.widget), id: copyId, x: found.widget.x + 16, y: found.widget.y + 16 }
    found.page.widgets.splice(found.index + 1, 0, copy)
  })
  return { spec: next, id: copyId }
}

/** Whose columns a component shows: a Table, many-row tables'; a Collection, one-row tables'. */
export const rowsShown = (widget: Pick<Widget, "type">): SchemaTable["rows"] =>
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

/** Move a component's field from one place in its order to another. */
export const moveField = (spec: FormSpec, widgetId: string, from: number, to: number): FormSpec =>
  edit(spec, (draft) => {
    const { fields } = widgetOf(draft, widgetId)
    const [field] = fields.splice(from, 1)
    fields.splice(to, 0, field)
  })

/** Show the field in the component if it does not, or stop showing it if it does. */
export const toggleField = (spec: FormSpec, widgetId: string, field: FieldRef): FormSpec =>
  edit(spec, (draft) => {
    const widget = widgetOf(draft, widgetId)
    const at = widget.fields.findIndex((shown) => shown.table === field.table && shown.column === field.column)
    if (at >= 0) widget.fields.splice(at, 1)
    else widget.fields.push(field)
  })

/** What stops a component showing what it should, by the component it is about. */
export interface WidgetProblem {
  widgetId: string
  message: string
}

const KIND_NAMES: Record<WidgetType, string> = { tableInput: "Table", collection: "Collection" }

/** `Table "Layers"`, or `A Table` while it has no title. */
export const widgetName = (widget: Widget): string =>
  widget.title ? `${KIND_NAMES[widget.type]} "${widget.title}"` : `A ${KIND_NAMES[widget.type]}`

/**
 * What stops a component showing what it should: no fields, a column no longer in the
 * schema, a table of the other kind (as when a table is switched between one row and
 * many after its columns were chosen), or tables whose rows do not line up.
 */
export function widgetProblems(spec: FormSpec): WidgetProblem[] {
  const problems: WidgetProblem[] = []
  for (const widget of allWidgets(spec)) {
    const name = widgetName(widget)
    const problem = (message: string) => problems.push({ widgetId: widget.id, message: `${name} ${message}` })
    if (widget.fields.length === 0) problem("shows no fields")
    const found = widget.fields.map((field) => fieldColumn(spec, field))
    if (found.includes(null)) problem("shows a column that is no longer in the schema")
    const rows = rowsShown(widget)
    if (found.some((shown) => shown !== null && shown.table.rows !== rows)) {
      problem(rows === "many" ? "shows a one-row table; a Collection shows those" : "shows a many-row table; a Table shows those")
    }
    const grains = new Set(found.flatMap((shown) => (shown !== null && shown.table.rows === rows ? [tableGrain(shown.table)] : [])))
    if (grains.size > 1) problem("shows tables whose rows do not line up")
  }
  return problems
}

// ─── Problems ────────────────────────────────────────────────────────────

/** What stops the schema being the pipeline's tables, by the table it is about. */
export interface SchemaProblem {
  tableId: string
  message: string
}

const COLUMN_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/

const isNumber = (text: string, whole: boolean): boolean => {
  const n = Number(text)
  return text.trim() !== "" && Number.isFinite(n) && (!whole || Number.isInteger(n))
}

/**
 * What would stop the schema being the pipeline's tables. A table's name is a port's
 * name, so it is held to the Quote Input's label rules (`apiInputLabelIssue`, with the
 * document's reserved labels); a column's name is an identifier, unique in its table; a
 * many-row table needs a key; an input column's rules must make sense (a range that is
 * not inverted, allowed values of its type).
 */
export function schemaProblems(spec: FormSpec, reservedLabels: ReadonlySet<string>): SchemaProblem[] {
  const problems: SchemaProblem[] = []
  const { tables } = spec.schema
  tables.forEach((table, index) => {
    const name = `Table "${table.name}"`
    const issue = apiInputLabelIssueMessage(
      apiInputLabelIssue(
        table.name,
        tables.filter((_, other) => other !== index).map((other) => other.name),
        reservedLabels,
      ),
    )
    if (issue !== null) problems.push({ tableId: table.id, message: `${name}: ${issue}` })
    if (table.rows === "many" && !table.columns.some((column) => column.key)) {
      problems.push({ tableId: table.id, message: `${name} has many rows, so it needs a key column` })
    }
    const columnNames = new Set<string>()
    for (const column of table.columns) {
      const columnName = `Column "${column.name}" in ${name}`
      if (!COLUMN_NAME.test(column.name)) {
        problems.push({ tableId: table.id, message: `${columnName} needs a name of letters, digits and _` })
      } else if (columnNames.has(column.name)) {
        problems.push({ tableId: table.id, message: `${columnName} repeats the name ${column.name}` })
      }
      columnNames.add(column.name)
      if (table.role !== "input" || column.index) continue
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
    }
  })
  return problems
}
