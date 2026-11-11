/**
 * Pure operations on the workbench's form (specs/workbench): the schema's tables and
 * columns, the sheets, their sections and the components in them, what the components show
 * or plot of the schema and when they show, and what would stop the schema being the
 * pipeline's tables or a sheet, a section or a component showing what it should. Each
 * operation returns a new form and leaves its input untouched.
 */
import type {
  ChartWidget,
  ColumnLook,
  CsvExport,
  CsvImport,
  ExcelExport,
  ExcelImport,
  ExportWidget,
  FieldRef,
  FormSchema,
  FormSpec,
  HeadingWidget,
  ImportWidget,
  Page,
  SchemaColumn,
  SchemaTable,
  Section,
  ShowWhen,
  SqlExport,
  SqlImport,
  Theme,
} from "../api/types"
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
import {
  blankColumn,
  type Block,
  hasInputs,
  hasOutputs,
  IDENTITY_COLUMNS,
  IDENTITY_NAMES,
  identityCount,
  type IndexKind,
  isIdentityColumn,
  isOutputColumn,
  outputsAlone,
  ownInputs,
  ownOutputs,
  QUOTE_DETAILS_NAME,
  QUOTE_DETAILS_MANAGED,
  sheetColumns,
  tableColumns,
  tableIndex,
  typedInputs,
  withIdentityPlaced,
} from "./quoteIdentity"
import { canonicalJson } from "./canonicalJson"
import { isDay } from "./isoDate"
import {
  cellProblem,
  filled,
  indexTextLimit,
  isTyping,
  newQuoteValues,
  parseTypedNumber,
  pricedRows,
  valuesIndex,
  type PricedRows,
  type RowsByTable,
} from "./sheetValues"
import { keyCollisions } from "./workbenchTables"
import {
  ALWAYS,
  basisOf,
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
  onPrice,
  priceProblem,
  takesNothingTyped,
  testLabel,
  TOTALS,
  type ColumnTest,
  type ConditionTest,
  type ListCondition,
  type ShowWhenEntry,
  type SingleCondition,
} from "./sheetVisibility"

/** A section's widget: a Table, a Collection, a Chart, a Heading, an Import or an Export. */
export type Widget = Section["widgets"][number]
export type WidgetType = Widget["type"]
/** What the palette adds: a component of a kind, or a section. */
export type PaletteType = WidgetType | "section"
/** A button on a sheet: an Import or an Export. Neither shows schema columns nor takes anything typed. */
export type ButtonWidget = ImportWidget | ExportWidget
/** A component that shows schema columns: a Table, a Collection or a Chart. A Heading and a button show none. */
export type FieldsWidget = Exclude<Widget, HeadingWidget | ButtonWidget>
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

/** What a sheet's part's id is made of after its kind. */
const PART_ID_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789"

/** What a sheet's part is, as its id starts: a component's kind, `table` for a Table. */
export type PartKind = "sheet" | "section" | "table" | Exclude<WidgetType, "tableInput">

/** The kind a component of `type`'s id starts with. */
export const partKind = (type: WidgetType): PartKind => (type === "tableInput" ? "table" : type)

/**
 * A sheet's, a section's or a component's id, the editor's own, which no file holds
 * (READ-24): its kind, then `_` and four random lowercase letters or digits, drawn again
 * while `taken`, the form's own ids, holds it.
 */
export function newPartId(kind: PartKind, taken: Iterable<string> = []): string {
  const used = new Set(taken)
  let id: string
  do {
    const bytes = crypto.getRandomValues(new Uint8Array(4))
    id = `${kind}_${Array.from(bytes, (byte) => PART_ID_CHARACTERS[byte % PART_ID_CHARACTERS.length]).join("")}`
  } while (used.has(id))
  return id
}

/** Every sheet's, section's and component's id in `spec`, which a new one must not take. */
export const formPartIds = (spec: FormSpec): string[] => [
  ...spec.pages.map((page) => page.id),
  ...allSections(spec).map((section) => section.id),
  ...allWidgets(spec).map((widget) => widget.id),
]

/** `base`, or `base_2`, `base_3`… until it is none of `taken`, whatever their case. */
export function uniqueName(base: string, taken: Iterable<string>): string {
  const used = new Set(Array.from(taken, folded))
  if (!used.has(folded(base))) return base
  let n = 2
  while (used.has(folded(`${base}_${n}`))) n += 1
  return `${base}_${n}`
}

/** A name as names are told apart: a table's or a column's is its own whatever its case (READ-23). */
const folded = (name: string): string => name.toLowerCase()

/**
 * Whether `name` is another of `names`' whatever its case, `own` aside: a table and a column
 * are known by their names, so a rename onto another's would make the two one (READ-23).
 */
export const nameTaken = (names: readonly string[], name: string, own: string): boolean =>
  names.some((other) => other !== own && folded(other) === folded(name))

/**
 * Whether a sample row of the table `tableName` holds a value under `name`, one kept from a
 * column the schema no longer has: a column renamed to it would take that row's value over
 * its own, so the rename is refused rather than one value lost (READ-23).
 */
export const sampleHolds = (spec: FormSpec, tableName: string, name: string): boolean =>
  (spec.sample[tableName] ?? []).some((row) => name in row)

/** `sum_insured` as "Sum insured": what a column is called until it has a label. */
export const readableName = (name: string): string =>
  name.replace(/_+/g, " ").trim().replace(/^./, (c) => c.toUpperCase())

export const allSections = (spec: FormSpec): Section[] => spec.pages.flatMap((page) => page.sections)

/** A sheet's components, section by section. */
export const pageWidgets = (page: Page): Widget[] => page.sections.flatMap((section) => section.widgets)

export const allWidgets = (spec: FormSpec): Widget[] => spec.pages.flatMap(pageWidgets)

/** Whether a component shows schema columns: a Table, a Collection or a Chart, not a Heading or a button. */
export const showsFields = (widget: Widget): widget is FieldsWidget =>
  widget.type === "tableInput" || widget.type === "collection" || widget.type === "chart"

/** Whether a component is a button: an Import or an Export. */
export const isButton = (widget: Widget): widget is ButtonWidget => widget.type === "import" || widget.type === "export"

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
  const table = spec.schema.tables.find((candidate) => candidate.name === id)
  if (table === undefined) throw new Error(`The schema has no table ${id}`)
  return table
}

function schemaColumnOf(table: SchemaTable, id: string): SchemaColumn {
  const column = tableColumns(table).find((candidate) => candidate.name === id)
  if (column === undefined) throw new Error(`Table ${table.name} has no column ${id}`)
  return column
}

/**
 * A new table, its inputs leading with the quote's reference and option, as every table but
 * quote_details does, and no column of its own, named apart from every other whatever their
 * case (READ-23).
 */
export const createSchemaTable = (spec: FormSpec): SchemaTable => ({
  name: uniqueName("table", spec.schema.tables.map((table) => table.name)),
  rows: "one",
  inputs: IDENTITY_COLUMNS.map(([name, type]) => blankColumn(name, type)),
  outputs: [],
})

/**
 * A new column for a block of a table, to go at `at` in the block (its end by default): of
 * the type of the column above it there, or Text first in the block or under the quote's
 * identity or the index; named apart from every column of the table, in either block.
 */
export const createSchemaColumn = (spec: FormSpec, tableId: string, block: Block, at?: number): SchemaColumn => {
  const table = schemaTableOf(spec, tableId)
  const columns = table[block]
  const above = columns[(at ?? columns.length) - 1]
  const type = above === undefined || above.index || isIdentityColumn(table, above) ? "str" : above.type
  return blankColumn(uniqueName("column", tableColumns(table).map((column) => column.name)), type)
}

/** A many-row table's index as the editor gives it: an Integer Haute numbers 1, 2, 3, `table_index` until renamed. */
export const createIndexColumn = (spec: FormSpec, tableId: string): SchemaColumn => ({
  ...blankColumn(uniqueName("table_index", tableColumns(schemaTableOf(spec, tableId)).map((column) => column.name)), "int"),
  index: "numbered",
})

export const addSchemaTable = (spec: FormSpec, table: SchemaTable): FormSpec =>
  edit(spec, (draft) => {
    draft.schema.tables.push(table)
  })

/** A table renamed, each use of its name with it; neither its rows nor its blocks, which their own operations change. */
export const updateSchemaTable = (spec: FormSpec, id: string, patch: Pick<SchemaTable, "name">): FormSpec =>
  patch.name === id || nameTaken(spec.schema.tables.map((table) => table.name), patch.name, id)
    ? spec
    : edit(spec, (draft) => {
        schemaTableOf(draft, id).name = patch.name
        renameTableUses(draft, id, patch.name)
      })

/**
 * Take what shows or uses the column `columnId` of the table `tableId` out of the form: the
 * widgets that show it, or label a Chart's rows by it, what the conditions test of it
 * (`withoutColumn`), what Imports read of it or give a parameter, and its sample values.
 */
function dropColumnUses(draft: FormSpec, tableId: string, columnId: string): void {
  const gone = (field: FieldRef) => field.table === tableId && field.column === columnId
  dropFields(draft, gone)
  dropConditions(draft, (condition) => withoutColumn(condition, tableId, columnId))
  for (const widget of allWidgets(draft)) dropButtonUses(widget, () => false, gone)
  for (const row of draft.sample[tableId] ?? []) delete row[columnId]
}

/**
 * The table `tableId` given the index it needs, as the server's `_check_keys` holds it:
 * every many-row table holds one index, the one it has under its name, type and kind, its
 * allowed values and their source with it while it gives a row for each, or a new one
 * (`createIndexColumn`), first after the quote's identity in the block it belongs to: its
 * outputs while it is a table of outputs alone (`outputsAlone`), and its inputs otherwise,
 * an empty table's included, so it moves between the blocks as the table gains or loses its
 * typed inputs or its last priced output, a typed index then the pricing's to fill or the
 * underwriter's again. A one-row table loses it with what shows it (`dropColumnUses`).
 * quote_details is left as it is.
 */
function placeIndex(draft: FormSpec, tableId: string): void {
  const table = schemaTableOf(draft, tableId)
  if (isQuoteDetails(table)) return
  const index = tableIndex(table)
  if (table.rows === "many") {
    const held = index ?? createIndexColumn(draft, tableId)
    const block: Block = outputsAlone(table) ? "outputs" : "inputs"
    table.inputs = table.inputs.filter((column) => column !== held)
    table.outputs = table.outputs.filter((column) => column !== held)
    // First after the identity heading the block, as the block stands without it.
    const own = block === "inputs" ? ownInputs(table) : ownOutputs(table)
    table[block].splice(table[block].length - own.length, 0, held)
  } else if (index !== null) {
    table.inputs = table.inputs.filter((column) => column !== index)
    table.outputs = table.outputs.filter((column) => column !== index)
    dropColumnUses(draft, tableId, index.name)
  }
}

/** The table `tableId` with the quote's identity at the head of its first block (`withIdentityPlaced`). */
function placeIdentity(draft: FormSpec, tableId: string): void {
  draft.schema.tables = draft.schema.tables.map((table) => (table.name === tableId ? withIdentityPlaced(table) : table))
}

/**
 * The table `tableId` holding its index as the server holds it (`placeIndex`): what every
 * operation changing a table's rows or blocks ends with, before the identity is placed
 * (`withIdentityPlaced`).
 */
export const withIndexAsNeeded = (spec: FormSpec, tableId: string): FormSpec => edit(spec, (draft) => placeIndex(draft, tableId))

/**
 * Within an edit: the index placed, then the identity, as Control flow 18 orders
 * them; and a table left with no inputs, its last moved to the outputs or removed, gives up
 * its rows in the sample, which holds input tables' alone, as the form folder's save does.
 */
const settleBlocks = (draft: FormSpec, tableId: string): void => {
  placeIndex(draft, tableId)
  placeIdentity(draft, tableId)
  if (!hasInputs(schemaTableOf(draft, tableId))) delete draft.sample[tableId]
}

/**
 * Each use of the table `from` made a use of `to`, its new name (READ-23): its columns shown,
 * labelling a Chart's rows and given a look, the conditions on it, what Imports and Exports
 * use of it, and its sample rows.
 */
function renameTableUses(draft: FormSpec, from: string, to: string): void {
  const renamed = (name: string): string => (name === from ? to : name)
  const field = (ref: FieldRef): FieldRef => ({ ...ref, table: renamed(ref.table) })
  for (const widget of allWidgets(draft)) {
    if (showsFields(widget)) widget.fields = widget.fields.map(field)
    if (widget.type === "chart" && widget.category !== null) widget.category = field(widget.category)
    if (widget.type === "tableInput" || widget.type === "collection") {
      widget.column_looks = widget.column_looks.map((look) => ({ ...look, table: renamed(look.table) }))
    }
    if (widget.type === "import") {
      if (widget.table !== null) widget.table = renamed(widget.table)
      if (widget.source.format === "sql") widget.source.parameters = widget.source.parameters.map((given) => ({ ...given, table: renamed(given.table) }))
    } else if (widget.type === "export") {
      widget.tables = widget.tables.map(renamed)
      if (widget.target.format === "sql") widget.target.table_names = widget.target.table_names.map((named) => ({ ...named, table: renamed(named.table) }))
    }
  }
  dropConditions(draft, (condition): SingleCondition =>
    isListCondition(condition)
      ? { ...condition, table: renamed(condition.table) }
      : { ...condition, table: renamed(condition.table), field: condition.field === null ? null : field(condition.field) },
  )
  if (from in draft.sample) {
    draft.sample[to] = draft.sample[from]
    delete draft.sample[from]
  }
}

/**
 * Each use of the column `from` of the table `tableName` made a use of `to`, its new name
 * (READ-23): where it is shown, labels a Chart's rows or has a look, what the conditions
 * test of it, what Imports read of it or give a parameter, and its sample values.
 */
function renameColumnUses(draft: FormSpec, tableName: string, from: string, to: string): void {
  const own = (name: string): string => (name === from ? to : name)
  const field = <T extends FieldRef>(ref: T): T => (ref.table === tableName && ref.column === from ? { ...ref, column: to } : ref)
  for (const widget of allWidgets(draft)) {
    if (showsFields(widget)) widget.fields = widget.fields.map(field)
    if (widget.type === "chart" && widget.category !== null) widget.category = field(widget.category)
    if (widget.type === "tableInput" || widget.type === "collection") widget.column_looks = widget.column_looks.map(field)
    if (widget.type === "import") {
      if (widget.table === tableName) widget.column_names = widget.column_names.map((named) => ({ ...named, column: own(named.column) }))
      if (widget.source.format === "sql") widget.source.parameters = widget.source.parameters.map(field)
    }
  }
  dropConditions(draft, (condition): SingleCondition => {
    if (!isListCondition(condition)) return { ...field(condition), field: condition.field === null ? null : field(condition.field) }
    if (condition.table !== tableName) return condition
    const where = condition.where.map((test) => ({ ...test, column: own(test.column), field: test.field === null ? null : own(test.field) }))
    return isRowTotal(condition) ? { ...condition, where, column: own(condition.column) } : { ...condition, where }
  })
  for (const row of draft.sample[tableName] ?? []) {
    if (from in row) {
      row[to] = row[from]
      delete row[from]
    }
  }
}

/** The settings only an input column takes, each as a column without it holds it. */
const INPUT_ONLY_RULES: Pick<SchemaColumn, "min" | "max" | "options" | "options_source" | "default_value"> = {
  min: null,
  max: null,
  options: [],
  options_source: null,
  default_value: "",
}

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
 * Take what an Import or an Export uses of the tables `table` matches and the columns
 * `column` matches out of it: an Import filling such a table fills none, its column names
 * going with it; a column's name and the parameters given such a column go; and an Export
 * stops exporting such a table, with the name it was written to.
 */
const dropButtonUses = (widget: Widget, table: (id: string) => boolean, column: (field: FieldRef) => boolean): void => {
  if (widget.type === "import") {
    const filled = widget.table
    if (filled !== null && table(filled)) {
      widget.table = null
      widget.column_names = []
    } else if (filled !== null) {
      widget.column_names = widget.column_names.filter((named) => !column({ table: filled, column: named.column }))
    }
    if (widget.source.format === "sql") widget.source.parameters = widget.source.parameters.filter((given) => !table(given.table) && !column(given))
  } else if (widget.type === "export") {
    widget.tables = widget.tables.filter((id) => !table(id))
    if (widget.target.format === "sql") widget.target.table_names = widget.target.table_names.filter((named) => !table(named.table))
  }
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

/** The columns a condition tests, by table and column name: a one-row table's condition's own and the field it is tested against, or those of its table a list's tests of a row and its total test. */
const conditionColumns = (condition: SingleCondition): FieldRef[] =>
  isListCondition(condition)
    ? listColumns(condition).map((column) => ({ table: condition.table, column }))
    : [{ table: condition.table, column: condition.column }, ...(condition.field === null ? [] : [condition.field])]

/** The ids of the tables a condition tests: its own, and the table of the field it is tested against. */
const conditionTables = (condition: SingleCondition): string[] => [condition.table, ...conditionColumns(condition).map((field) => field.table)]

/**
 * A condition without what it tests of a column: none for a one-row table's condition on
 * it or a total of it; a list's tests of a row on it taken out, a condition in any or
 * every row left with none going too, as it needs one.
 */
function withoutColumn(condition: SingleCondition, tableId: string, columnId: string): SingleCondition | null {
  if (!isListCondition(condition)) {
    const { field } = condition
    const tests = condition.table === tableId && condition.column === columnId
    const against = field !== null && field.table === tableId && field.column === columnId
    return tests || against ? null : condition
  }
  if (condition.table !== tableId) return condition
  if (isRowTotal(condition) && condition.column === columnId) return null
  const where = condition.where.filter((test) => test.column !== columnId && test.field !== columnId)
  if (where.length === condition.where.length) return condition
  return isRowsCondition(condition) && where.length === 0 ? null : { ...condition, where }
}

/**
 * Removing a table removes its columns from the widgets that show them, or label a Chart's
 * rows, the conditions on it, what Imports and Exports use of it, and its sample rows.
 */
export const removeSchemaTable = (spec: FormSpec, id: string): FormSpec =>
  edit(spec, (draft) => {
    draft.schema.tables = draft.schema.tables.filter((table) => table.name !== id)
    dropFields(draft, (field) => field.table === id)
    dropConditions(draft, (condition) => (conditionTables(condition).includes(id) ? null : condition))
    for (const widget of allWidgets(draft)) dropButtonUses(widget, (table) => table === id, (field) => field.table === id)
    delete draft.sample[id]
  })

/**
 * A table with one row per quote has no index, which keys many rows: switching to one row
 * drops it with what shows it, as removing a column does. A many-row table's columns take
 * no default: switching to many rows clears them, and the table gains its index, numbered,
 * an empty table's too (`withIndexAsNeeded`).
 */
export const setSchemaTableRows = (spec: FormSpec, id: string, rows: SchemaTable["rows"]): FormSpec =>
  edit(spec, (draft) => {
    const table = schemaTableOf(draft, id)
    table.rows = rows
    if (rows === "many") for (const column of tableColumns(table)) column.default_value = ""
    settleBlocks(draft, id)
  })

/**
 * How the index of the table `tableId` gets its values (`IndexKind`): numbered by Haute,
 * typed, or a row for each of its allowed values, which are read from a params file as any
 * column's are (`AllowedField`). Leaving typed clears its placeholder, which only a typed
 * index shows, and leaving values its allowed values and their source, which only an index
 * of values holds. The form as given for a table without an index, or one of that kind.
 */
export const setIndexKind = (spec: FormSpec, tableId: string, kind: IndexKind): FormSpec => {
  const index = tableIndex(schemaTableOf(spec, tableId))
  if (index === null || index.index === kind) return spec
  return edit(spec, (draft) => {
    const held = tableIndex(schemaTableOf(draft, tableId)) as SchemaColumn
    if (held.index === "typed") held.placeholder = ""
    if (held.index === "values") {
      held.options = []
      held.options_source = null
    }
    held.index = kind
  })
}

/**
 * Add a column to a block of a table, at `at` in the block or at its end; a many-row table's
 * index goes to the head of the block it now belongs to (`withIndexAsNeeded`), and the
 * quote's identity heads the table's first block (`withIdentityPlaced`).
 */
export const addSchemaColumn = (spec: FormSpec, tableId: string, block: Block, column: SchemaColumn, at?: number): FormSpec =>
  edit(spec, (draft) => {
    const columns = schemaTableOf(draft, tableId)[block]
    columns.splice(at ?? columns.length, 0, column)
    settleBlocks(draft, tableId)
  })

/** How many columns head a block and stay there: the quote's identity at its head, and after it the index, in whichever block holds it. */
const fixedHead = (table: SchemaTable, block: Block): number => {
  const identity = table[block].length - (block === "inputs" ? ownInputs(table) : ownOutputs(table)).length
  return identity + (table[block][identity]?.index ? 1 : 0)
}

/**
 * Move a column to `at` in a block of its table, its own or the other. The quote's identity
 * and the index after it stay where they are, nothing goes above them, and the index is
 * never moved by this, only with the blocks (`withIndexAsNeeded`). A column reaching the
 * outputs loses what only an input takes (`INPUT_ONLY_RULES`: its rules but Required and
 * its default) and its values in the sample, which only what is typed takes. Then the table
 * holds its index as the server holds it (`withIndexAsNeeded`), and the identity heads its first block
 * (`withIdentityPlaced`), so a table whose last typed input moves to its outputs has no
 * inputs left, the identity and then its index heading its outputs: Control flow 18.
 */
export const moveSchemaColumn = (spec: FormSpec, tableId: string, columnId: string, block: Block, at: number): FormSpec => {
  const table = schemaTableOf(spec, tableId)
  const own: Block = isOutputColumn(table, columnId) ? "outputs" : "inputs"
  const from = table[own].findIndex((column) => column.name === columnId)
  if (from === -1 || table[own][from].index || from < fixedHead(table, own) || at < fixedHead(table, block)) return spec
  if (own === block && (from === at || at >= table[own].length)) return spec
  if (own !== block && at > table[block].length) return spec
  return edit(spec, (draft) => {
    const held = schemaTableOf(draft, tableId)
    const [column] = held[own].splice(from, 1)
    if (own !== block && block === "outputs") {
      Object.assign(column, INPUT_ONLY_RULES)
      for (const row of draft.sample[tableId] ?? []) delete row[columnId]
    }
    held[block].splice(at, 0, column)
    settleBlocks(draft, tableId)
  })
}

/** Whether the column `columnId` of `table` is its index, which is renamed and retyped between Integer and Text, but never moved or removed. */
const isIndex = (table: SchemaTable, columnId: string): boolean => tableColumns(table).some((column) => column.name === columnId && column.index !== null)

/** The types an index takes: Integer and Text, a key every store holds. */
export const INDEX_TYPES: ReadonlySet<SchemaColumn["type"]> = new Set(["int", "str"])

/**
 * Whether the index `column` may take `patch`, as the server's `_check_index` holds it: its
 * kind changes through `setIndexKind` alone and its type is an Integer or Text; always
 * filled, it takes no Required, range or default; it shows a placeholder only while typed,
 * and holds allowed values, with their source, only while it gives a row for each.
 */
const indexTakes = (column: SchemaColumn, patch: Partial<SchemaColumn>): boolean =>
  (patch.index === undefined || patch.index === column.index) &&
  (patch.type === undefined || INDEX_TYPES.has(patch.type)) &&
  patch.required !== true &&
  (patch.min ?? null) === null &&
  (patch.max ?? null) === null &&
  (patch.default_value === undefined || patch.default_value === "" || patch.default_value === false) &&
  (column.index === "typed" || !patch.placeholder) &&
  (column.index === "values" || ((patch.options ?? []).length === 0 && (patch.options_source ?? null) === null))

/**
 * Change a column; the quote's identity is Haute's, and nothing of it changes, and the index
 * holds only what the server lets it (`indexTakes`).
 */
export const updateSchemaColumn = (
  spec: FormSpec,
  tableId: string,
  columnId: string,
  patch: Partial<SchemaColumn>,
): FormSpec =>
  isIdentityColumn(schemaTableOf(spec, tableId), { name: columnId }) ||
  (isIndex(schemaTableOf(spec, tableId), columnId) && !indexTakes(schemaColumnOf(schemaTableOf(spec, tableId), columnId), patch)) ||
  (patch.name !== undefined &&
    patch.name !== columnId &&
    (nameTaken(tableColumns(schemaTableOf(spec, tableId)).map((column) => column.name), patch.name, columnId) || sampleHolds(spec, tableId, patch.name)))
    ? spec
    : edit(spec, (draft) => {
        Object.assign(schemaColumnOf(schemaTableOf(draft, tableId), columnId), patch)
        if (patch.name !== undefined && patch.name !== columnId) renameColumnUses(draft, tableId, columnId, patch.name)
      })

/** Whether a column of `type` can have allowed values: any but a tick box or a date. */
export const takesOptions = (type: SchemaColumn["type"]): boolean => type !== "bool" && type !== "date"

/**
 * Change a column's type, dropping the rules the new type cannot have: the range and the
 * number format of a column no longer a number, and the allowed values of one now a tick
 * box or a date, with the params file they came from, which would
 * otherwise still rule its cells; and the default of one that
 * becomes or stops being a tick box, a tick or text no longer its kind of value, or that
 * becomes a date while its default is not a day, which a date's box cannot show. The quote's
 * identity keeps its types, and the index is an Integer or Text alone (`INDEX_TYPES`), an
 * index of values keeping its allowed values, which give its rows.
 */
export const changeSchemaColumnType = (
  spec: FormSpec,
  tableId: string,
  columnId: string,
  type: SchemaColumn["type"],
): FormSpec =>
  isIdentityColumn(schemaTableOf(spec, tableId), { name: columnId }) || (isIndex(schemaTableOf(spec, tableId), columnId) && !INDEX_TYPES.has(type))
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
        }
      })

/**
 * Removing a column removes it from the widgets that show it, or label a Chart's rows,
 * and what the conditions test of it (`withoutColumn`), and its sample values; a many-row
 * table's index, which is never removed, goes to the head of the block it now belongs to
 * (`withIndexAsNeeded`), and the quote's identity, which stays, heads its first block
 * (`withIdentityPlaced`).
 */
export const removeSchemaColumn = (spec: FormSpec, tableId: string, columnId: string): FormSpec =>
  isIdentityColumn(schemaTableOf(spec, tableId), { name: columnId }) || isIndex(schemaTableOf(spec, tableId), columnId)
    ? spec
    : edit(spec, (draft) => {
        const table = schemaTableOf(draft, tableId)
        table.inputs = table.inputs.filter((column) => column.name !== columnId)
        table.outputs = table.outputs.filter((column) => column.name !== columnId)
        dropColumnUses(draft, tableId, columnId)
        settleBlocks(draft, tableId)
      })

/** Whether a component shows a schema table's columns, or just one of them, a Chart labelling its rows by one included. */
const showsColumn = (widget: Widget, tableId: string, columnId: string | undefined): boolean =>
  showsFields(widget) &&
  [...widget.fields, ...(widget.type === "chart" && widget.category !== null ? [widget.category] : [])].some(
    (field) => field.table === tableId && (columnId === undefined || field.column === columnId),
  )

/** The widgets showing a schema table's columns, or just one of them, a Chart labelling its rows by one included. */
export const fieldUses = (spec: FormSpec, tableId: string, columnId?: string): Widget[] =>
  allWidgets(spec).filter((widget) => showsColumn(widget, tableId, columnId))

/** The names of the columns an Import of a schema table reads by their own names. */
const importRead = (spec: FormSpec, tableId: string): ReadonlySet<string> => {
  const table = spec.schema.tables.find((candidate) => candidate.name === tableId)
  return new Set(table === undefined ? [] : importColumns(table).map((column) => column.name))
}

/** How an Import or an Export uses a schema table or one of its columns. */
type ButtonWay = "imports" | "parameter" | "exports"

/**
 * How a button uses a schema table: filling it, giving a parameter one of its columns or
 * exporting it; or, given one of its columns, reading it, by its own name (`read`) or
 * another, giving a parameter it or exporting it with its table.
 */
const buttonWays = (widget: ButtonWidget, tableId: string, columnId: string | undefined, read: ReadonlySet<string>): ButtonWay[] => {
  if (widget.type === "export") return widget.tables.includes(tableId) ? ["exports"] : []
  const reads =
    widget.table === tableId && (columnId === undefined || read.has(columnId) || widget.column_names.some((named) => named.column === columnId))
  const gives =
    widget.source.format === "sql" && widget.source.parameters.some((given) => given.table === tableId && (columnId === undefined || given.column === columnId))
  return [...(reads ? (["imports"] as const) : []), ...(gives ? (["parameter"] as const) : [])]
}

/** The Imports and Exports using a schema table, or one of its columns (`buttonWays`). */
export const buttonUses = (spec: FormSpec, tableId: string, columnId?: string): ButtonWidget[] => {
  const read = importRead(spec, tableId)
  return allWidgets(spec)
    .filter(isButton)
    .filter((widget) => buttonWays(widget, tableId, columnId, read).length > 0)
}

/** Whether a sheet's, a section's or a widget's conditions, or its fields', are on a schema table, or test just one of its columns. */
const testsColumn = (holder: Page | Section | Widget, tableId: string, columnId: string | undefined): boolean => {
  const uses = (showWhen: ShowWhen): boolean =>
    leafConditions(showWhen).some((condition) =>
      columnId === undefined ? conditionTables(condition).includes(tableId) : conditionColumns(condition).some((field) => field.table === tableId && field.column === columnId),
    )
  return uses(holder.show_when) || ("column_looks" in holder && holder.column_looks.some((look) => uses(look.show_when)))
}

/** The sheets, sections and widgets whose conditions, or whose fields' conditions, are on a schema table, or test just one of its columns. */
export const conditionUses = (spec: FormSpec, tableId: string, columnId?: string): (Page | Section | Widget)[] =>
  [...spec.pages, ...allSections(spec), ...allWidgets(spec)].filter((holder) => testsColumn(holder, tableId, columnId))

/** A way a component uses a schema column, in the order its uses list them. */
export type ColumnWay = "shows" | "showWhen" | ButtonWay

/** A component using a schema column, and the ways it does. */
export interface ComponentUse {
  widget: Widget
  ways: ColumnWay[]
}

/** A sheet using a schema column: whether its own conditions test it, its sections whose own conditions do, and its components using it. */
export interface SheetUse {
  page: Page
  tested: boolean
  sections: Section[]
  components: ComponentUse[]
}

/**
 * The sheets using a schema column, in their order, each with what on it does, its
 * components section by section in their drawing order: the holders `fieldUses`,
 * `conditionUses` and `buttonUses` give, sheet by sheet, each component once.
 */
export const columnUses = (spec: FormSpec, tableId: string, columnId: string): SheetUse[] => {
  const read = importRead(spec, tableId)
  const ways = (widget: Widget): ColumnWay[] => [
    ...(showsColumn(widget, tableId, columnId) ? (["shows"] as const) : []),
    ...(testsColumn(widget, tableId, columnId) ? (["showWhen"] as const) : []),
    ...(isButton(widget) ? buttonWays(widget, tableId, columnId, read) : []),
  ]
  return spec.pages.flatMap((page) => {
    const tested = testsColumn(page, tableId, columnId)
    const sections = page.sections.filter((section) => testsColumn(section, tableId, columnId))
    const components = pageWidgets(page)
      .map((widget) => ({ widget, ways: ways(widget) }))
      .filter((use) => use.ways.length > 0)
    return tested || sections.length > 0 || components.length > 0 ? [{ page, tested, sections, components }] : []
  })
}

/** The schema table and column a field names, or null when it is no longer in the schema: of a form, or of the schema alone. */
export function fieldColumn({ schema }: Pick<FormSpec, "schema">, field: FieldRef): { table: SchemaTable; column: SchemaColumn } | null {
  const table = schema.tables.find((candidate) => candidate.name === field.table)
  const column = table === undefined ? undefined : tableColumns(table).find((candidate) => candidate.name === field.column)
  return table && column ? { table, column } : null
}

/** A field a component shows, with the schema column it names, or null when that is gone. */
export interface ShownField {
  key: string
  found: { table: SchemaTable; column: SchemaColumn } | null
}

/** The fields a component shows, each with its column in `schema`, in the component's order; a Heading shows none. */
export const shownFields = (schema: FormSchema, widget: Widget): ShownField[] =>
  showsFields(widget) ? widget.fields.map((field) => ({ key: columnKey(field.table, field.column), found: fieldColumn({ schema }, field) })) : []

/**
 * The input tables a Table's grid takes rows of, in its order: those of the many-row inputs
 * it shows that hold a row's own value, a typed index and an index of values among them; a
 * numbered index numbers the grid's rows, whichever table's they are.
 */
export const gridInputTables = (fields: readonly ShownField[]): SchemaTable[] => [
  ...new Map(
    fields.flatMap(({ found }) =>
      found !== null &&
      found.table.rows === "many" &&
      !isOutputColumn(found.table, found.column) &&
      found.column.index !== "numbered" &&
      !isIdentityColumn(found.table, found.column)
        ? [[found.table.name, found.table] as const]
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

/**
 * A new section of `page`, named in turn on it, `height` tall, always shown and empty, its
 * id none of `taken`, the form's own (`formPartIds`).
 */
export const createSection = (page: Page, height: number = NEW_SECTION_HEIGHT, taken: Iterable<string> = []): Section => ({
  id: newPartId("section", taken),
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
  const taken = formPartIds(spec)
  const page: Page = {
    id: newPartId("sheet", taken),
    title: uniqueName(`Sheet ${spec.pages.length + 1}`, spec.pages.map((candidate) => candidate.title)),
    width: spec.pages.at(-1)?.width ?? BLANK_SHEET_WIDTH,
    show_when: alwaysShown(),
    sections: [],
  }
  page.sections.push(createSection(page, BLANK_SECTION_HEIGHT, [...taken, page.id]))
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

/**
 * A new component of `type` at `rect` for `spec`, showing nothing yet, and always: named by
 * its kind (`nextWidgetName`), its id none of the form's own (`formPartIds`).
 */
export function createWidget<T extends WidgetType>(type: T, rect: Rect, spec: FormSpec): Extract<Widget, { type: T }>
export function createWidget(type: WidgetType, rect: Rect, spec: FormSpec): Widget {
  // In the file's order, so a component the view made compares equal to it saved.
  const { x, y, w, h } = rect
  const placed = { id: newPartId(partKind(type), formPartIds(spec)), name: nextWidgetName(spec, type), x, y, w, h }
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
    case "import":
      return { ...placed, type, label: "Import", show_when: alwaysShown(), source: structuredClone(IMPORT_SOURCES.csv), table: null, mode: "replace", column_names: [] }
    case "export":
      return { ...placed, type, label: "Export", show_when: alwaysShown(), target: structuredClone(EXPORT_TARGETS.csv), tables: [] }
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

/** The word a component of `type`'s name starts with until the builder names it: its kind, `table` for a Table. */
const NAME_WORDS: Record<WidgetType, string> = {
  tableInput: "table",
  collection: "collection",
  chart: "chart",
  heading: "heading",
  import: "import",
  export: "export",
}

/** Every component's name in `spec`, whatever its case, which a new one must not take. */
const componentNames = (spec: FormSpec): Set<string> => new Set(allWidgets(spec).map((widget) => folded(widget.name)))

/** `stem`, `_` and the first number from `from` that makes a name none of `spec`'s components has. */
function firstFreeName(spec: FormSpec, stem: string, from: number): string {
  const taken = componentNames(spec)
  let n = from
  while (taken.has(folded(`${stem}_${n}`))) n += 1
  return `${stem}_${n}`
}

/**
 * A new component's name: its kind's word and the first number from 1 that makes a name no
 * component has, whatever its case (`table_1`, then `table_2`). It names the component's file.
 */
export const nextWidgetName = (spec: FormSpec, type: WidgetType): string => firstFreeName(spec, NAME_WORDS[type], 1)

/**
 * A copy's name, duplicated or pasted: its original's without a final `_` and digits, then
 * `_` and the first number from 2 that makes a free name, so "layers" gives "layers_2" and
 * "table_1" gives "table_2".
 */
export const copyName = (spec: FormSpec, name: string): string => firstFreeName(spec, name.replace(/_\d+$/, ""), 2)

/** Names Windows keeps for devices, which no file can take, whatever their case. */
const WINDOWS_DEVICE_RE = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i

/**
 * Why `name` cannot be the component `id`'s, or null: a name is an identifier, as a column's
 * is, names the component's file, so not one Windows keeps, and is no other component's
 * whatever its case.
 */
export function componentNameProblem(spec: FormSpec, id: string, name: string): string | null {
  if (!ASCII_IDENTIFIER_RE.test(name)) return "Use letters, digits and _, not starting with a digit"
  if (WINDOWS_DEVICE_RE.test(name)) return `${name} is a name Windows keeps for itself`
  const other = allWidgets(spec).find((widget) => widget.id !== id && folded(widget.name) === folded(name))
  return other === undefined ? null : `Another component is named ${other.name}`
}

/** Give a component a name, refusing one `componentNameProblem` refuses. */
export function renameWidget(spec: FormSpec, id: string, name: string): FormSpec {
  const problem = componentNameProblem(spec, id, name)
  if (problem !== null) throw new Error(problem)
  return edit(spec, (draft) => {
    const found = findWidget(draft, id)
    if (found === null) throw new Error(`No sheet has a component ${id}`)
    found.widget.name = name
  })
}

/**
 * Add a new component to a section under the name it holds, kept inside it and its sheet's
 * width (`keptInside`); only one taller than the section grows it, and only one wider than
 * the sheet widens it.
 */
export const addWidget = (spec: FormSpec, sectionId: string, widget: Widget): FormSpec =>
  edit(spec, (draft) => {
    const found = findSection(draft, sectionId)
    if (found === null) throw new Error(`No sheet has a section ${sectionId}`)
    const { section, page } = found
    const kept = {
      ...widget,
      x: keptInside(widget.x, widget.w, page.width),
      y: keptInside(widget.y, widget.h, section.height),
    }
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
  const widget = createWidget(type, { ...freePlace(found.section.widgets, fitted, found.page.width), ...fitted }, spec)
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
 * (`SharedLook`), the Chart's options a Chart's, `text`, `description` and the
 * Heading's options a Heading's, and `label` a button's; only a Table, a Collection and a
 * Chart have a `title` and `fields`. What an Import or an Export does changes through its
 * own operations (`setImportFormat` and those after it).
 */
export type WidgetPatch = Partial<Rect> & {
  title?: string
  fields?: FieldRef[]
  show_when?: ShowWhen
  rows?: number
  columns?: number
  text?: string
  description?: string
  label?: string
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
const BUTTON_ONLY = ["label"]
/** What a Table, a Collection and a Chart have, and neither a Heading nor a button. */
const FIELDS_ONLY = ["title", "fields"]

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
  const buttonOnly = BUTTON_ONLY.find((key) => key in patch)
  if (buttonOnly !== undefined && !isButton(widget)) throw new Error(`Only an Import or an Export has ${buttonOnly}`)
  const fieldsOnly = FIELDS_ONLY.find((key) => key in patch)
  if (fieldsOnly !== undefined && !showsFields(widget)) throw new Error(`${kindArticle(widget.type)} ${KIND_NAMES[widget.type]} has no ${fieldsOnly}`)
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
 * components there, keeping its name. Only a component taller than the section it lands in grows it, or one
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

/** A copy of a component just below and to the right of it, in the same section, kept inside it and its sheet's width (`keptInside`), named as a copy is (`copyName`). */
export function duplicateWidget(spec: FormSpec, id: string): { spec: FormSpec; id: string } {
  const original = findWidget(spec, id)
  if (original === null) throw new Error(`No sheet has a component ${id}`)
  const copyId = newPartId(partKind(original.widget.type), formPartIds(spec))
  const name = copyName(spec, original.widget.name)
  const next = edit(spec, (draft) => {
    const found = findWidget(draft, id)
    if (found === null) throw new Error(`No sheet has a component ${id}`)
    const { x, y, w, h } = found.widget
    const copy = {
      ...structuredClone(found.widget),
      id: copyId,
      name,
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
 * place, kept inside the section and its sheet's width (`addWidget`), with a new id and named
 * as a copy is (`copyName`). What it shows or tests of a column the schema has lost since is
 * left out, as removing the column took it out of every component then.
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
  const id = newPartId(partKind(widget.type), formPartIds(spec))
  const copy: Widget = { ...structuredClone(widget), id, name: copyName(spec, widget.name), ...at }
  const gone = (field: FieldRef) => fieldColumn(spec, field) === null
  dropShown(copy, gone)
  dropButtonUses(copy, (table) => !spec.schema.tables.some((candidate) => candidate.name === table), gone)
  const keep = (condition: SingleCondition): SingleCondition | null => {
    if (!conditionTables(condition).every((tableId) => spec.schema.tables.some((table) => table.name === tableId))) return null
    let kept: SingleCondition | null = condition
    for (const field of conditionColumns(condition)) {
      if (kept !== null && gone(field)) kept = withoutColumn(kept, field.table, field.column)
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
 * Which rows a table's columns fill: `one` per quote, or a many-row table's by its index,
 * so a table's inputs and outputs always line up: a numbered index's by the table itself
 * (`numbered:<table>`), lining up with no other; a typed one's by its name and type
 * (`typed:<name>=<type>`); and an index of values' by the params file's column its values
 * come from and its type (`values:<file>/<column>=<type>`), whatever each index is named.
 * One whose values were typed in, as an older form holds them, lines up with no other.
 * Columns can share a grid only when this matches.
 */
export function tableGrain(table: SchemaTable): string {
  if (table.rows === "one") return "one"
  const index = tableIndex(table)
  if (index === null || index.index === "numbered") return `numbered:${table.name}`
  if (index.index === "typed") return `typed:${index.name}=${index.type}`
  const source = index.options_source
  return source === null ? `values:${table.name}` : `values:${source.file}/${source.column}=${index.type}`
}

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

/** Whether a Chart can plot a column: a number column, other than a numbered index, which numbers the rows, or the quote's option. */
export const plottable = (table: SchemaTable, column: SchemaColumn): boolean =>
  (column.type === "int" || column.type === "float") && column.index !== "numbered" && !isIdentityColumn(table, column)

/**
 * The columns that can label a Chart's rows: those of the many-row tables of its grain,
 * other than a numbered index ("Row number" numbers the rows as it does) and the quote's
 * identity, which the sheets never show, by table.
 */
export function labelColumns(spec: FormSpec, widget: ChartWidget): { table: SchemaTable; columns: SchemaColumn[] }[] {
  const grain = chartGrain(spec.schema, widget)
  if (grain === null || grain === "one") return []
  return spec.schema.tables
    .filter((table) => table.rows === "many" && tableGrain(table) === grain)
    .map((table) => ({ table, columns: sheetColumns(table).filter((column) => column.index !== "numbered") }))
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

// ─── Imports and Exports ─────────────────────────────────────────────────

/** Where an Import reads from: a CSV, an Excel workbook or a database. */
export type ImportFormat = ImportWidget["source"]["format"]
/** Where an Export writes to: a CSV, an Excel workbook or a database. */
export type ExportFormat = ExportWidget["target"]["format"]

/**
 * The furthest down a workbook's sheet an Import's header row can be, as the server's form
 * takes it (`HEADER_ROW_MAX` in `src/haute/_workbench_form.py`).
 */
export const HEADER_ROW_MAX = 100

/** What a new Export names its file: the quote's reference and option. */
export const EXPORT_FILE_NAME = "{quote_reference}-{quote_option}"

/** Each format's settings as a new Import starts them, in the file's order: a CSV's delimiter found from the file, a workbook's first sheet under its first row, no connection or query. */
export const IMPORT_SOURCES: { readonly [F in ImportFormat]: Readonly<Extract<ImportWidget["source"], { format: F }>> } = {
  csv: { format: "csv", delimiter: "auto" },
  excel: { format: "excel", sheet: "", header_row: 1 },
  sql: { format: "sql", connection: "", query: "", parameters: [] },
}

/** Each format's settings as a new Export starts them, in the file's order: a file named by the quote's reference and option, headed by names, its values raw, a CSV's split by commas; no connection. */
export const EXPORT_TARGETS: { readonly [F in ExportFormat]: Readonly<Extract<ExportWidget["target"], { format: F }>> } = {
  csv: { file_name: EXPORT_FILE_NAME, headers: "names", values: "raw", format: "csv", delimiter: "comma" },
  excel: { file_name: EXPORT_FILE_NAME, headers: "names", values: "raw", format: "excel" },
  sql: { format: "sql", connection: "", table_names: [] },
}

function importOf(spec: FormSpec, id: string): ImportWidget {
  const widget = widgetOf(spec, id)
  if (widget.type !== "import") throw new Error(`Component ${id} is not an Import`)
  return widget
}

function exportOf(spec: FormSpec, id: string): ExportWidget {
  const widget = widgetOf(spec, id)
  if (widget.type !== "export") throw new Error(`Component ${id} is not an Export`)
  return widget
}

/** An Import reading another format, its settings for it as a new Import's (`IMPORT_SOURCES`), its table and column names kept. */
export const setImportFormat = (spec: FormSpec, id: string, format: ImportFormat): FormSpec =>
  importOf(spec, id).source.format === format
    ? spec
    : edit(spec, (draft) => {
        importOf(draft, id).source = structuredClone(IMPORT_SOURCES[format])
      })

/** The settings a format of an Import has of its own, beside the query and its parameters. */
export type ImportSourcePatch = Partial<Pick<CsvImport, "delimiter"> & Pick<ExcelImport, "sheet" | "header_row"> & Pick<SqlImport, "connection">>

const SOURCE_KEYS: Record<ImportFormat, readonly string[]> = { csv: ["delimiter"], excel: ["sheet", "header_row"], sql: ["connection"] }

/** Change the settings of an Import's format: a CSV's delimiter, a workbook's sheet or header row, a database's connection. */
export function updateImportSource(spec: FormSpec, id: string, patch: ImportSourcePatch): FormSpec {
  const { source } = importOf(spec, id)
  const foreign = Object.keys(patch).find((key) => !SOURCE_KEYS[source.format].includes(key))
  if (foreign !== undefined) throw new Error(`An Import from ${source.format} has no ${foreign}`)
  const held: Record<string, unknown> = { ...source }
  if (Object.entries(patch).every(([key, value]) => held[key] === value)) return spec
  return edit(spec, (draft) => {
    Object.assign(importOf(draft, id).source, patch)
  })
}

/** An Import filling another input table, or none: the column names given for the last dropped. */
export const setImportTable = (spec: FormSpec, id: string, table: string | null): FormSpec =>
  importOf(spec, id).table === table
    ? spec
    : edit(spec, (draft) => {
        const widget = importOf(draft, id)
        widget.table = table
        widget.column_names = []
      })

/** How an Import fills a many-row table: its rows replaced, added to, or updated by its keys. */
export const setImportMode = (spec: FormSpec, id: string, mode: ImportWidget["mode"]): FormSpec =>
  importOf(spec, id).mode === mode
    ? spec
    : edit(spec, (draft) => {
        importOf(draft, id).mode = mode
      })

/** The name an Import reads a column of its table from; blank takes it away, the column then read from the column of its own name or label. */
export function setColumnName(spec: FormSpec, id: string, column: string, name: string): FormSpec {
  const named = importOf(spec, id).column_names.find((entry) => entry.column === column)
  const blank = name.trim() === ""
  if (blank ? named === undefined : named?.name === name) return spec
  return edit(spec, (draft) => {
    const widget = importOf(draft, id)
    if (blank) widget.column_names = widget.column_names.filter((entry) => entry.column !== column)
    else if (named !== undefined) widget.column_names = widget.column_names.map((entry) => (entry.column === column ? { column, name } : entry))
    else widget.column_names.push({ column, name })
  })
}

/** A query's quoted text, `'…'` and `"…"`, and its comments, `--` to the line's end and `/* … *\/`, where a `:name` is no parameter. */
const NOT_PARAMETERS = /'(?:[^']|'')*'|"(?:[^"]|"")*"|--[^\n]*|\/\*[\s\S]*?\*\//g

/** A `:name` that is no `::` cast and no part of a word. */
const PARAMETER = /(?<![:\w]):([A-Za-z_]\w*)/g

/** The `:name`s a query holds, in the order it first holds them, once each, outside its quoted text and comments. */
export const queryParameters = (query: string): string[] => [...new Set(Array.from(query.replace(NOT_PARAMETERS, " ").matchAll(PARAMETER), (found) => found[1]))]

function sqlImportOf(spec: FormSpec, id: string): SqlImport {
  const { source } = importOf(spec, id)
  if (source.format !== "sql") throw new Error(`Component ${id} imports from ${source.format}, which has no query`)
  return source
}

/** An Import's query, each parameter it no longer holds losing its column. */
export const setImportQuery = (spec: FormSpec, id: string, query: string): FormSpec =>
  sqlImportOf(spec, id).query === query
    ? spec
    : edit(spec, (draft) => {
        const source = sqlImportOf(draft, id)
        const held = new Set(queryParameters(query))
        source.query = query
        source.parameters = source.parameters.filter((given) => held.has(given.name))
      })

/** Give a parameter of an Import's query the value of a column, or none. */
export function bindParameter(spec: FormSpec, id: string, name: string, field: FieldRef | null): FormSpec {
  const given = sqlImportOf(spec, id).parameters.find((parameter) => parameter.name === name)
  if (field === null ? given === undefined : given?.table === field.table && given.column === field.column) return spec
  return edit(spec, (draft) => {
    const source = sqlImportOf(draft, id)
    if (field === null) source.parameters = source.parameters.filter((parameter) => parameter.name !== name)
    else if (given !== undefined) source.parameters = source.parameters.map((parameter) => (parameter.name === name ? { name, ...field } : parameter))
    else source.parameters.push({ name, ...field })
  })
}

/**
 * The columns of a table an Import fills: the inputs an underwriter types, a typed index
 * among them, neither the quote's identity, a numbered index nor an index of values, whose
 * values the sheets give, nor an output, nor in quote_details a column the app sets.
 */
export const importColumns = (table: SchemaTable): SchemaColumn[] =>
  ownInputs(table).filter((column) => isTyping(column) && !(table.name === QUOTE_DETAILS_NAME && QUOTE_DETAILS_MANAGED.has(column.name)))

/** The columns a query's parameter can be given: a one-row table's inputs. */
export const parameterColumns = (table: SchemaTable): SchemaColumn[] => (table.rows === "one" ? typedInputs(table) : [])

/** Whether an Export writes a file: a CSV or an Excel workbook. */
const isFile = (target: ExportWidget["target"]): target is CsvExport | ExcelExport => target.format !== "sql"

/** An Export writing another format, its settings for it as a new Export's (`EXPORT_TARGETS`), its file name, headers and values kept between a CSV and a workbook. */
export function setExportFormat(spec: FormSpec, id: string, format: ExportFormat): FormSpec {
  const { target } = exportOf(spec, id)
  if (target.format === format) return spec
  return edit(spec, (draft) => {
    const next = structuredClone(EXPORT_TARGETS[format])
    if (isFile(next) && isFile(target)) Object.assign(next, { file_name: target.file_name, headers: target.headers, values: target.values })
    exportOf(draft, id).target = next
  })
}

/** The settings a format of an Export has of its own, beside the names of the tables it writes to. */
export type ExportTargetPatch = Partial<Pick<CsvExport, "file_name" | "headers" | "values" | "delimiter"> & Pick<SqlExport, "connection">>

const TARGET_KEYS: Record<ExportFormat, readonly string[]> = {
  csv: ["file_name", "headers", "values", "delimiter"],
  excel: ["file_name", "headers", "values"],
  sql: ["connection"],
}

/** Change the settings of an Export's format: a file's name, headers and values, a CSV's delimiter, a database's connection. */
export function updateExportTarget(spec: FormSpec, id: string, patch: ExportTargetPatch): FormSpec {
  const { target } = exportOf(spec, id)
  const foreign = Object.keys(patch).find((key) => !TARGET_KEYS[target.format].includes(key))
  if (foreign !== undefined) throw new Error(`An Export to ${target.format} has no ${foreign}`)
  const held: Record<string, unknown> = { ...target }
  if (Object.entries(patch).every(([key, value]) => held[key] === value)) return spec
  return edit(spec, (draft) => {
    Object.assign(exportOf(draft, id).target, patch)
  })
}

/** Export a table, after those it exports, or stop exporting it, the name it was written to going too. */
export const toggleExportTable = (spec: FormSpec, id: string, table: string): FormSpec =>
  edit(spec, (draft) => {
    const widget = exportOf(draft, id)
    if (!widget.tables.includes(table)) {
      widget.tables.push(table)
      return
    }
    widget.tables = widget.tables.filter((exported) => exported !== table)
    if (widget.target.format === "sql") widget.target.table_names = widget.target.table_names.filter((named) => named.table !== table)
  })

function sqlExportOf(spec: FormSpec, id: string): SqlExport {
  const { target } = exportOf(spec, id)
  if (target.format !== "sql") throw new Error(`Component ${id} exports to ${target.format}, which names no tables`)
  return target
}

/** The name of the database table an Export writes one of its tables to; blank takes it away, the table then written to the table of its own name. */
export function setTableName(spec: FormSpec, id: string, table: string, name: string): FormSpec {
  if (!exportOf(spec, id).tables.includes(table)) throw new Error(`Component ${id} does not export ${table}`)
  const named = sqlExportOf(spec, id).table_names.find((entry) => entry.table === table)
  const blank = name.trim() === ""
  if (blank ? named === undefined : named?.name === name) return spec
  return edit(spec, (draft) => {
    const target = sqlExportOf(draft, id)
    if (blank) target.table_names = target.table_names.filter((entry) => entry.table !== table)
    else if (named !== undefined) target.table_names = target.table_names.map((entry) => (entry.table === table ? { table, name } : entry))
    else target.table_names.push({ table, name })
  })
}

/** The `{name}`s a file name holds, in order. */
export const fileNameTokens = (fileName: string): string[] => Array.from(fileName.matchAll(/\{([^{}]*)\}/g), (found) => found[1])

// ─── The sample ──────────────────────────────────────────────────────────

/** A value typed into the sample: text, or a tick. The server types it by its column. */
export type SampleValue = string | boolean
/** A row of the sample, keyed by column name. */
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

/**
 * Whether the sample holds a new quote's values and nothing else (`newQuoteValues`): a row
 * left empty in a grid shows, so it counts.
 */
export const isNewQuoteSample = (spec: FormSpec): boolean => canonicalJson(spec.sample) === canonicalJson(newQuoteValues(spec.schema))

/**
 * The sample started again as a new quote, its one-row tables' defaults alone, as Preview's
 * Reset makes it; the form itself while the sample is one already.
 */
export const newQuoteSample = (spec: FormSpec): FormSpec =>
  isNewQuoteSample(spec) ? spec : { ...spec, sample: structuredClone(newQuoteValues(spec.schema)) as Record<string, SampleRow[]> }

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
  const tables = gridInputTables(shownFields(next.schema, widgetOf(next, widgetId))).map((table) => table.name)
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
 * What pricing the sample depends on, as one string to compare (`basisOf`): the schema,
 * the columns the sheets hide as the sample sets them, and the sample. A sheet laid out
 * differently prices the same, while a condition that hides or shows a column does not.
 */
export function pricingBasis(spec: FormSpec): string {
  let basis = bases.get(spec)
  if (basis === undefined) {
    basis = basisOf(spec, spec.sample)
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
 * Each many-row table of `outputs` (the tables whose outputs are shown) lined up with a
 * grid's `count` rows by index, never by place: a table whose inputs the grid shows through
 * its own typed rows, each answer row taken by its index's value; any other through the
 * first of `inputs` (the many-row input tables shown, in order) whose index lines up with
 * its own (`tableGrain`) and which has a record on that display row, the two indexes perhaps named apart. On each grid row, the answer
 * row `pricedRows` finds for it, the input table read as the quote reads it (`quoted`), so
 * its filled rows are the typed rows the pricing answers with. A table no input table lines
 * up with is left out, and every one while there is no price.
 */
export function linedUpOutputs(
  inputs: readonly SchemaTable[],
  outputs: readonly SchemaTable[],
  held: RowsByTable,
  count: number,
  price: Priced | null,
  quoted: FormSchema,
  rowOrder?: ReadonlyMap<string, readonly (number | undefined)[]>,
): Map<string, (Record<string, unknown> | undefined)[]> {
  const lined = new Map<string, (Record<string, unknown> | undefined)[]>()
  if (price === null) return lined
  for (const output of outputs) {
    if (lined.has(output.name)) continue
    const own = inputs.find((table) => table.name === output.name)
    const candidates = own === undefined ? inputs.filter((table) => tableGrain(table) === tableGrain(output)) : [own]
    const result: (Record<string, unknown> | undefined)[] = Array.from({ length: count })
    const assigned = new Set<number>()
    for (const input of candidates) {
      const read = quoted.tables.find((table) => table.name === input.name)
      if (read === undefined) continue
      const typed = price.sample[input.name]
      const grid = Array.from({ length: Math.max(count, held[input.name]?.length ?? 0) }, (_, row) => held[input.name]?.[row])
      const [typedIndex, answerIndex] = [tableIndex(read), tableIndex(output)]
      const keys = typedIndex === null || answerIndex === null ? null : { typed: typedIndex.name, answer: answerIndex.name }
      const matched = pricedRows(read, keys, grid, Array.isArray(typed) ? (typed as PricedRows) : [], price.tables[output.name] ?? [])
      const order = rowOrder?.get(input.name)
      for (let row = 0; row < count; row++) {
        const source = order === undefined ? row : order[row]
        if (source === undefined || assigned.has(row)) continue
        result[row] = matched[source]
        assigned.add(row)
      }
      lined.set(output.name, result)
    }
  }
  return lined
}

/** A row's value under `name`, there and not null: a value an index can match by. */
const keyedBy = (row: Record<string, unknown> | undefined, name: string): boolean => row?.[name] !== undefined && row[name] !== null

/**
 * Tables whose outputs are shown without an input table, lined up with the first one's
 * answer: its rows as pricing gave them, and each other table whose index lines up with the
 * first's (`tableGrain`) its row whose index holds the same value, as text, each index under
 * its own name (an input table's carried in its answer); none for a table that does not line
 * up, as a Chart and a Table of output columns alone show them. Nothing while there is no
 * price.
 */
export function answeredOutputs(
  outputs: readonly SchemaTable[],
  price: Priced | null,
): Map<string, (Record<string, unknown> | undefined)[]> {
  const lined = new Map<string, (Record<string, unknown> | undefined)[]>()
  const [first] = outputs
  if (first === undefined || price === null) return lined
  const answer = price.tables[first.name] ?? []
  const key = tableIndex(first)?.name
  for (const table of outputs) {
    if (lined.has(table.name)) continue
    const own = price.tables[table.name] ?? []
    const name = tableIndex(table)?.name
    const matched = (row: Record<string, unknown>) =>
      key === undefined || name === undefined || tableGrain(table) !== tableGrain(first)
        ? undefined
        : own.find((candidate) => keyedBy(row, key) && keyedBy(candidate, name) && String(candidate[name]) === String(row[key]))
    lined.set(table.name, table.name === first.name ? [...answer] : answer.map(matched))
  }
  return lined
}

/** What stops a component showing what it should, by the component it is about. */
export interface WidgetProblem {
  widgetId: string
  message: string
}

const KIND_NAMES: Record<WidgetType, string> = {
  tableInput: "Table",
  collection: "Collection",
  chart: "Chart",
  heading: "Heading",
  import: "Import",
  export: "Export",
}

/** "A" or "An", as a kind of component's name takes it. */
const kindArticle = (type: WidgetType): string => (/^[AEIOU]/.test(KIND_NAMES[type]) ? "An" : "A")

/** `Table "Layers"`, a Heading by its text, `Heading "Cover"`, a button by its label, `Import "Schedule"`, or `A Table` and `An Import` while it has none. */
export const widgetName = (widget: Widget): string => {
  const name = widget.type === "heading" ? widget.text.trim() : isButton(widget) ? widget.label.trim() : widget.title
  return name ? `${KIND_NAMES[widget.type]} "${name}"` : `${kindArticle(widget.type)} ${KIND_NAMES[widget.type]}`
}

/**
 * What stops a component showing what it should: no fields, a column no longer in the
 * schema, a table of the other kind (as when a table is switched between one row and
 * many after its columns were chosen), tables whose rows do not line up, or what stops
 * a Chart plotting what it should (`chartProblems`); a Heading's blank text; a button's
 * blank label and what stops an Import or an Export doing what its settings say
 * (`importProblems`, `exportProblems`); and what stops its conditions doing what they say
 * (`conditionProblems`).
 */
export function widgetProblems(spec: FormSpec): WidgetProblem[] {
  const problems: WidgetProblem[] = []
  const never = neverShown(spec)
  for (const widget of allWidgets(spec)) {
    const name = widgetName(widget)
    const problem = (message: string) => problems.push({ widgetId: widget.id, message: `${name} ${message}` })
    if (widget.type === "heading") {
      if (widget.text.trim() === "") problem("has no text")
    } else if (isButton(widget)) {
      if (widget.label.trim() === "") problem("has no label")
      for (const message of widget.type === "import" ? importProblems(spec, widget) : exportProblems(spec, widget)) problem(message)
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
        // A Table's many-row input tables sit side by side row by row, so tables whose index
        // gives a row for each value must each give the same values, in the same order.
        const given = new Set(
          found.flatMap((shown) =>
            widget.type === "tableInput" &&
            shown !== null &&
            !isOutputColumn(shown.table, shown.column) &&
            shown.table.rows === "many" &&
            shown.column.index !== "numbered"
              ? [JSON.stringify(valuesIndex(shown.table)?.options ?? null)]
              : [],
          ),
        )
        if (grains.size > 1 || given.size > 1) problem("shows tables whose rows do not line up")
      }
    }
    for (const message of conditionProblems(spec, widget, never, takesNothingTyped(spec, widget))) problem(message)
    if (widget.type !== "tableInput" && widget.type !== "collection") continue
    for (const field of widget.fields) {
      const holder = { id: fieldKey(widget.id, field.table, field.column), show_when: fieldShowWhen(widget, field) }
      const label = columnLabel(fieldColumn(spec, field)?.column)
      for (const message of conditionProblems(spec, holder, never, takesNothingTyped(spec, field), "fields")) {
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
    if (shown === null || plottable(shown.table, shown.column)) continue
    const label = shown.column.label || readableName(shown.column.name)
    problems.push(`plots ${label}, which ${shown.column.index === "numbered" ? "numbers the rows" : "is not a number"}`)
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

/**
 * What stops an Import doing what its settings say: no table, one gone or a table with no
 * inputs, which pricing fills; a column named that is gone or that an underwriter does not
 * type, an output among them; replacing or adding to the rows of a table with a row for each
 * allowed value; and, from a database, no connection or query, a parameter its query holds
 * given no column, or a column gone or not an input of a one-row table, or a column given a
 * parameter its query does not hold.
 */
function importProblems(spec: FormSpec, widget: ImportWidget): string[] {
  const problems: string[] = []
  const table = widget.table === null ? undefined : spec.schema.tables.find((candidate) => candidate.name === widget.table)
  if (widget.table === null) problems.push("fills no table")
  else if (table === undefined) problems.push("fills a table that is no longer in the schema")
  else if (!hasInputs(table)) problems.push("fills a table with no inputs, which pricing fills")
  else {
    const typed = new Set(importColumns(table).map((column) => column.name))
    const named = widget.column_names.map((entry) => tableColumns(table).find((column) => column.name === entry.column))
    if (named.includes(undefined)) problems.push("reads into a column that is no longer in the schema")
    if (named.some((column) => column !== undefined && !typed.has(column.name))) problems.push("reads into a column an underwriter does not type")
    if (widget.mode !== "update" && valuesIndex(table) !== null) {
      problems.push(`${widget.mode === "replace" ? "replaces" : "adds to"} the rows of ${table.name}, which has a row for each allowed value; only Update by key keeps them`)
    }
  }
  if (widget.source.format === "sql") {
    const { connection, query, parameters } = widget.source
    if (connection.trim() === "") problems.push("names no connection")
    if (query.trim() === "") problems.push("has no query")
    const held = queryParameters(query)
    for (const name of held) {
      const given = parameters.find((parameter) => parameter.name === name)
      const found = given === undefined ? undefined : fieldColumn(spec, given)
      if (found === undefined) problems.push(`gives :${name} no column`)
      else if (found === null) problems.push(`gives :${name} a column that is no longer in the schema`)
      else if (isOutputColumn(found.table, found.column)) problems.push(`gives :${name} an output, which pricing fills`)
      else if (found.table.rows === "many") problems.push(`gives :${name} a column of a many-row table`)
    }
    for (const given of parameters) {
      if (!held.includes(given.name)) problems.push(`gives a column to :${given.name}, which its query does not hold`)
    }
  }
  return problems
}

/**
 * What stops an Export doing what its settings say: no tables, one gone, more than one to
 * a CSV, a file with no name or naming a column quote_details does not have, and, to a
 * database, no connection.
 */
function exportProblems(spec: FormSpec, widget: ExportWidget): string[] {
  const problems: string[] = []
  const { target, tables } = widget
  if (tables.length === 0) problems.push("exports no tables")
  if (tables.some((id) => !spec.schema.tables.some((table) => table.name === id))) problems.push("exports a table that is no longer in the schema")
  if (target.format === "sql") {
    if (target.connection.trim() === "") problems.push("names no connection")
    return problems
  }
  if (target.format === "csv" && tables.length > 1) problems.push("exports more than one table to a CSV, which holds one")
  if (target.file_name.trim() === "") problems.push("has no file name")
  const details = spec.schema.tables.find((table) => table.name === QUOTE_DETAILS_NAME)
  for (const token of new Set(fileNameTokens(target.file_name))) {
    if (details === undefined || !tableColumns(details).some((column) => column.name === token)) problems.push(`names its file by {${token}}, which is not a column of quote_details`)
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
    conditionProblems(spec, page, never, takesNothingTyped(spec, page)).map((message) => ({ pageId: page.id, message: `${pageName(page)} ${message}` })),
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
    conditionProblems(spec, section, never, takesNothingTyped(spec, section)).map((message) => ({ sectionId: section.id, message: `${sectionName(section)} ${message}` })),
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
  // What it is on takes nothing typed, so a condition on the price applies to it.
  priced: boolean,
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
    const fault = conditionFault(spec, condition) ?? (!priced && onPrice(spec, condition) ? priceProblem(spec, condition) : null)
    if (fault !== null) {
      problems.push(fault)
      continue
    }
    // A condition that fits names a table, and the columns of it it tests, the schema has.
    const table = schemaTableOf(spec, condition.table)
    const tests: ColumnTest[] = isListCondition(condition) ? condition.where : [condition]
    for (const test of tests) {
      const column = tableColumns(table).find((candidate) => candidate.name === test.column) as SchemaColumn
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
      const missing = tested.find((id) => !entered([columnKey(table.name, id)]))
      const keys = sheetColumns(table)
        .filter((column) => column.index !== "numbered")
        .map((column) => columnKey(table.name, column.name))
      if (missing !== undefined) {
        problems.push(`waits on ${(tableColumns(table).find((column) => column.name === missing) as SchemaColumn).name}, which is on no Table`)
      } else {
        problems.push(
          tested.length > 0 || entered(keys)
            ? `waits on the rows of ${table.name}, which are only on ${hidingWith} that hide with it, so it could not show again once hidden`
            : `waits on the rows of ${table.name}, which are on no Table`,
        )
      }
    } else {
      // It waits on its column, and on the field it is tested against while it has one: either may be the one that cannot show again.
      const named = (field: FieldRef): string => fieldColumn(spec, field)?.column.name ?? field.column
      const waitedOn = waiting.field === null ? [waiting] : [waiting, waiting.field]
      const absent = waitedOn.find((field) => !entered([columnKey(field.table, field.column)]))
      problems.push(
        absent === undefined
          ? `waits on ${waitedOn.map(named).join(" or ")}, which ${waitedOn.length > 1 ? "are" : "is"} only on ${hidingWith} that hide with it, so it could not show again once hidden`
          : `waits on ${named(absent)}, which is on no Table or Collection`,
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
  const other = condition.field === null ? null : (fieldColumn(spec, condition.field)?.column ?? undefined)
  return testText(fieldColumn(spec, condition)?.column ?? null, condition, other)
}

/** A column's label, or what stands for one gone. */
const columnLabel = (column: SchemaColumn | null | undefined): string =>
  column === null || column === undefined ? "A missing column" : column.label || readableName(column.name)

/** Days added to a Date, as a sentence says them: ` + 365 days`, ` - 1 day`, nothing for none. */
const daysText = (days: number): string => (days === 0 ? "" : ` ${days > 0 ? "+" : "-"} ${Math.abs(days)} ${Math.abs(days) === 1 ? "day" : "days"}`)

/**
 * A test of a column read by its label: `Age is less than 25`, or, tested against another
 * field (`other`, null while it names none and undefined while that is gone), by its label
 * and its days: `Completion date is after Start date + 365 days`.
 */
function testText(column: SchemaColumn | null | undefined, test: ColumnTest, other: SchemaColumn | null | undefined): string {
  const words = testLabel(test.op, column?.type ?? "str")
  const text = `${columnLabel(column)} ${words}`
  if (other !== null) return `${text} ${columnLabel(other)}${daysText(test.days)}`
  return test.values.length === 0 ? text : `${text} ${test.values.join(", ")}`
}

/** A condition on a list's rows read as `conditionText` says. */
function listText(spec: Pick<FormSpec, "schema">, condition: ListCondition): string {
  const table = spec.schema.tables.find((candidate) => candidate.name === condition.table)
  const name = table?.name ?? "a missing table"
  const columnOf = (id: string) => (table === undefined ? undefined : tableColumns(table).find((column) => column.name === id))
  const tests = condition.where.map((test) => testText(columnOf(test.column), test, test.field === null ? null : columnOf(test.field))).join(" and ")
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

/** The longest table or column name the quote store keeps whole (`STORE_NAME_LENGTH`). */
export const STORE_NAME_LENGTH = 63
/** What starts a name the quote store keeps for its own tables and columns. */
const STORE_PREFIX = "haute_"

/** What keeps a table's own name from being a table of the quote store's, as the server holds it (`store_name_problems`). */
function storeTableProblems(table: SchemaTable): string[] {
  const problems: string[] = []
  if (table.name.length > STORE_NAME_LENGTH) {
    problems.push(`has a name of ${table.name.length} characters: a name is at most ${STORE_NAME_LENGTH}, the longest every database keeps whole`)
  }
  if (table.name.startsWith(STORE_PREFIX)) problems.push(`starts ${STORE_PREFIX}, which the quote store keeps for its own names`)
  if (table.name.startsWith("sqlite_")) problems.push("starts sqlite_, which SQLite keeps for its own tables")
  return problems
}

/**
 * What keeps a column's name from being a column of the quote store's table, as the server
 * holds it (`store_name_problems`): `folded` holds the names before it by their lower case.
 */
function storeColumnProblems(table: SchemaTable, column: SchemaColumn, folded: Map<string, string>): string[] {
  const problems: string[] = []
  const lower = column.name.toLowerCase()
  const earlier = folded.get(lower)
  if (earlier === undefined) folded.set(lower, column.name)
  else if (earlier !== column.name) problems.push(`differs from "${earlier}" only in case, so a database cannot hold both`)
  if (column.name.length > STORE_NAME_LENGTH) {
    problems.push(`has a name of ${column.name.length} characters: a name is at most ${STORE_NAME_LENGTH}, the longest every database keeps whole`)
  }
  // Haute's own quote_details.haute_version is the one name of its own starting so.
  if (column.name.startsWith(STORE_PREFIX) && !(isQuoteDetails(table) && column.name === "haute_version")) {
    problems.push(`starts ${STORE_PREFIX}, which the quote store keeps for its own names`)
  }
  return problems
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

/** A table's ports, as the pipeline's tables name them: `<name>_input` while it has inputs and `<name>_output` while it has outputs. */
export const tablePorts = (table: SchemaTable): string[] => [
  ...(hasInputs(table) ? [`${table.name}_input`] : []),
  ...(hasOutputs(table) ? [`${table.name}_output`] : []),
]

/** How many characters of an index of values' value too long to key a row its problem quotes, as the server's does. */
const INDEX_VALUE_SHOWN = 40

/**
 * What keeps a table's index of values from giving its rows (`_check_index`, in the server's
 * words where it has them): allowed values read from a params file, each of the index's
 * type, a Text one's within `indexTextLimit`. The operations hold what else
 * an index may hold (`updateSchemaColumn`, `setIndexKind`).
 */
function indexProblems(table: SchemaTable): string[] {
  const index = tableIndex(table)
  if (index === null || index.index !== "values") return []
  const where = `${table.name}.${index.name}`
  if (index.options_source === null || index.options.length === 0) return [`${where} gives a row for each value, so it needs allowed values from a params file`]
  const problems: string[] = []
  const bad = index.type === "int" ? index.options.find((option) => !isNumber(option, true)) : undefined
  if (bad !== undefined) problems.push(`Column "${index.name}" in Table "${table.name}" allows "${bad}", which isn't a whole number`)
  const long = index.type === "str" ? index.options.find((option) => indexTextLimit(option) !== null) : undefined
  if (long !== undefined) {
    const shown = [...long].slice(0, INDEX_VALUE_SHOWN).join("")
    problems.push(`${where}'s allowed value "${shown}" is longer than ${indexTextLimit(long)}, the longest a Text index holds: correct the params file`)
  }
  return problems
}

/**
 * What would stop the schema being the pipeline's tables. A table's name is an
 * identifier, unique whatever its case, and its ports (`tablePorts`) are held to the Quote
 * Input's label rules (`apiInputLabelIssue`, with the document's reserved labels); a
 * column's name is an identifier, unique in its table across its blocks; an index of
 * values needs allowed values from a params file, of its type, a Text one's at most 255
 * characters (`indexProblems`), that stay distinct in its type (`keyCollisions`); an input's rules
 * must make sense (a range that is not inverted, allowed values of its type, read from a
 * params file rather than typed in), and a one-row table's input's default must be a value
 * they take. A table without the quote's identity whose own column takes either of its
 * names is named as the server names it (`identity_clashes`).
 */
export function schemaProblems(spec: FormSpec, reservedLabels: ReadonlySet<string>): SchemaProblem[] {
  const problems: SchemaProblem[] = []
  const { tables } = spec.schema
  const ports = tables.map(tablePorts)
  tables.forEach((table, index) => {
    const name = `Table "${table.name}"`
    const others = tables.filter((_, other) => other !== index)
    const issue = apiInputLabelIssueMessage(
      apiInputLabelIssue(
        table.name,
        others.map((other) => other.name),
        new Set(),
      ),
    )
    if (issue !== null) problems.push({ tableId: table.name, message: `${name}: ${issue}` })
    else {
      const otherPorts = ports.filter((_, other) => other !== index).flat()
      for (const port of ports[index]) {
        const portIssue = apiInputLabelIssueMessage(apiInputLabelIssue(port, otherPorts, reservedLabels))
        if (portIssue !== null) problems.push({ tableId: table.name, message: `${name}'s port ${port}: ${portIssue}` })
      }
    }
    for (const problem of storeTableProblems(table)) problems.push({ tableId: table.name, message: `${name} ${problem}` })
    const columns = tableColumns(table)
    if (!isQuoteDetails(table) && identityCount(table) === 0) {
      for (const column of columns) {
        if (IDENTITY_NAMES.has(column.name)) problems.push({ tableId: table.name, message: identityClash(table, column) })
      }
    }
    for (const problem of indexProblems(table)) problems.push({ tableId: table.name, message: problem })
    for (const collision of keyCollisions(table)) problems.push({ tableId: table.name, message: collision })
    const columnNames = new Set<string>()
    const folded = new Map<string, string>()
    for (const column of columns) {
      const columnName = `Column "${column.name}" in ${name}`
      const nameProblem = columnNameProblem(column.name)
      if (nameProblem !== null) {
        problems.push({ tableId: table.name, message: `${columnName} ${nameProblem}` })
      } else if (columnNames.has(column.name)) {
        problems.push({ tableId: table.name, message: `${columnName} repeats the name ${column.name}` })
      }
      columnNames.add(column.name)
      for (const problem of storeColumnProblems(table, column, folded)) problems.push({ tableId: table.name, message: `${columnName} ${problem}` })
      if (isOutputColumn(table, column) || column.index) continue
      if (!takesOptions(column.type) && column.options.length > 0) {
        const kind = column.type === "bool" ? "a tick box" : "a date"
        problems.push({ tableId: table.name, message: `${columnName} allows values, which ${kind} cannot` })
      } else if (column.options.length > 0 && column.options_source === null) {
        problems.push({ tableId: table.name, message: `${columnName} has allowed values typed in, not read from a params file` })
      }
      if (column.type === "int" || column.type === "float") {
        if (column.min !== null && column.max !== null && column.min > column.max) {
          problems.push({ tableId: table.name, message: `${columnName} has a minimum above its maximum` })
        }
        const bad = column.options.find((option) => !isNumber(option, column.type === "int"))
        const kind = column.type === "int" ? "a whole number" : "a number"
        if (bad !== undefined) {
          problems.push({ tableId: table.name, message: `${columnName} allows "${bad}", which isn't ${kind}` })
        }
      }
      const refused = table.rows === "one" ? defaultProblem(column) : null
      if (refused !== null) problems.push({ tableId: table.name, message: `${columnName} has a default its rules refuse: ${refused}` })
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

export { QUOTE_DETAILS_NAME, QUOTE_DETAILS_MANAGED }
export { QUOTE_DETAILS, QUOTE_DETAILS_COLUMNS, type QuoteDetailName } from "./quoteIdentity"

/** Why a table's own column keeps the quote's identity from it, in the server's words (`identity_clashes`). */
const identityClash = (table: SchemaTable, column: SchemaColumn): string =>
  `${table.name}.${column.name} has a name Haute keeps for the quote's identity in every table: rename it, and the table takes quote_reference and quote_option at the next save`

/** Whether `table` is Haute's quote_details table, whose structure the editor does not change. */
export const isQuoteDetails = (table: Pick<SchemaTable, "name">): boolean => table.name === QUOTE_DETAILS_NAME
