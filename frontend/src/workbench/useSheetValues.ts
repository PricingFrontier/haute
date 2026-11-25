/**
 * What a sheet's cells hold and show (specs/workbench), given to the components on it
 * through a context: a new quote while building, its defaults alone and nothing priced; the
 * form's sample in Preview; and the underwriter app's quote in the app. Each gives the
 * rows by table, how a cell or a grid's rows change, what the last pricing of those values
 * gave with the basis it was priced for, the cells that break their column's rules, which
 * sheets and components the values show, and the schema as the values' quote reads it,
 * without the columns the sheets hide. Each reads a table a column gives its rows as those
 * rows (`withPresetRows`), as the server prices them. Each is made only while `active`, as
 * the sheet uses one at a time, and stays the same object through a change that leaves
 * what it holds and shows alone, as dragging a component makes, so the components on the
 * sheet are not drawn again. Preview and the app lock the same cells, the quote_details
 * columns the app sets (`APP_SET_COLUMNS`), and in both the validation_passed cell is
 * worked out, whatever the values hold for it: whether they and their price pass every
 * check, as Validation says it (`withValidationPassed`). Preview shows the identity its
 * sample is priced under (`withStandIn`).
 */
import { createContext, useContext, useMemo, useState } from "react"
import type { FormSchema, FormSpec, Page } from "../api/types"
import { exportWorkbenchFile, type CellsExporter } from "../api/workbench"
import { importWorkbenchRows, type RowsImporter } from "../api/workbenchImport"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore, { NOTHING_LOCKED } from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { previewBasis, usePricingContext, type SamplePrice } from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { quoteChecks } from "../utils/quoteChecks"
import { QUOTE_DETAILS, QUOTE_DETAILS_NAME } from "../utils/quoteIdentity"
import { indexProblemCells, newQuoteValues, quoteProblems, withPresetRows, type RowsByTable } from "../utils/sheetValues"
import { APP_SET_COLUMNS, basisOf, columnKey, quoteEligible, quoteIndexProblems, quotedSchema, sheetVisibility, withPrice, type SheetVisibility } from "../utils/sheetVisibility"
import { withSampleCell, withSampleRows, type SampleRow, type SampleValue } from "../utils/workbenchForm"

export interface SheetValues {
  rows: RowsByTable
  /** Set one cell, adding empty rows up to it. */
  setCell: (tableId: string, row: number, columnId: string, value: SampleValue) => void
  /** Replace tables' rows, as a grid adds and deletes a row in every table it shows. */
  setRows: (rowsByTable: Record<string, SampleRow[]>) => void
  /** What the last pricing of these values gave; null before the first, and after a failure. */
  price: SamplePrice | null
  /** What pricing the values depends on now; a price for another basis is out of date. */
  basis: string
  /** The cells that break their column's rules, or a typed index's, by `cellKey`; none while nothing is typed. */
  problems: Readonly<Record<string, string>>
  /** The sheets and components these values show, and the columns they hide. */
  visibility: SheetVisibility
  /** The schema as these values' quote reads it: without the columns the sheets hide. */
  quoted: FormSchema
  /** The values may not change: the cells take no typing and rows are not added or deleted. */
  readOnly: boolean
  /** The columns, by `columnKey`, whose cells take no typing: the underwriter app sets them. */
  locked: ReadonlySet<string>
  /**
   * What runs an Export to an Excel workbook of cells on these values: in Preview on the
   * sample's price while it is the price of the sample as it stands, in the app on the
   * quote while it is final; null while none runs, and while building.
   */
  exportCells: CellsExporter | null
  /**
   * What runs an Import on these values: in Preview on the sample, in the app on the quote
   * while it may change; null while building and while the values may not change.
   */
  importRows: RowsImporter | null
}

export const SheetValuesContext = createContext<SheetValues | null>(null)

/** The values of the sheet a component is on. */
export function useSheetValues(): SheetValues {
  const values = useContext(SheetValuesContext)
  if (values === null) throw new Error("The sheet's values are not provided")
  return values
}

const NO_PROBLEMS: Readonly<Record<string, string>> = {}

/**
 * The cells the values' quote marks, by `cellKey`: those breaking their column's rules
 * (`quoteProblems`), and a typed index's blank, repeated or too long (`quoteIndexProblems`),
 * which stops the quote's pricing and its save.
 */
const cellProblems = (form: FormSpec, quoted: FormSchema, rows: RowsByTable): Readonly<Record<string, string>> => ({
  ...quoteProblems(quoted, rows),
  ...indexProblemCells(quoteIndexProblems(form, rows)),
})
const NO_ROWS: RowsByTable = {}
/**
 * Nothing shown and nothing hidden: the visibility of values not read yet, and of what draws
 * fields apart from any sheet, the New quote window.
 */
export const NO_VISIBILITY: SheetVisibility = { pages: new Set(), sections: new Set(), widgets: new Set(), fields: new Set(), hidden: new Set() }
// Before the form is read nothing draws a sheet; this only fills the shape.
const NO_SCHEMA: FormSchema = { tables: [] }
const TYPE_NOTHING = () => {}

const sameMembers = (a: ReadonlySet<string>, b: ReadonlySet<string>): boolean => a.size === b.size && [...a].every((id) => b.has(id))

/**
 * The visibility last given while `next` shows and hides the same sheets, sections,
 * components, fields and columns, else `next`: a change that shows and hides nothing new
 * keeps the values the same object.
 */
function useSteadyVisibility(next: SheetVisibility): SheetVisibility {
  const [kept, setKept] = useState(next)
  if (
    kept === next
    || (sameMembers(kept.pages, next.pages)
      && sameMembers(kept.sections, next.sections)
      && sameMembers(kept.widgets, next.widgets)
      && sameMembers(kept.fields, next.fields)
      && sameMembers(kept.hidden, next.hidden))
  ) {
    return kept
  }
  setKept(next)
  return next
}

/** The identity the server prices a sample under, whatever it holds (`STAND_IN_REFERENCE` and `STAND_IN_OPTION` in `src/haute/_workbench_input.py`). */
const STAND_IN = { [QUOTE_DETAILS.quote_reference]: "PREVIEW", [QUOTE_DETAILS.quote_option]: "1" }

/** `rows` with quote_details' identity as a sample is priced under it. */
function withStandIn(rows: RowsByTable): RowsByTable {
  const [details = {}, ...rest] = rows[QUOTE_DETAILS_NAME] ?? []
  return { ...rows, [QUOTE_DETAILS_NAME]: [{ ...details, ...STAND_IN }, ...rest] }
}

/**
 * `rows` with quote_details' validation_passed as the checks on `values` and the price they
 * were given come to (`quoteChecks`, which Validation reads too): ticked while every one
 * passes, whatever the cell held.
 */
function withValidationPassed(form: FormSpec, values: RowsByTable, rows: RowsByTable, price: SamplePrice | null): RowsByTable {
  const passed = quoteChecks(form, values, price?.tables ?? null).every((check) => check.passed)
  const [details = {}, ...rest] = rows[QUOTE_DETAILS_NAME] ?? []
  return { ...rows, [QUOTE_DETAILS_NAME]: [{ ...details, [QUOTE_DETAILS.validation_passed]: passed }, ...rest] }
}

/** The schema as a quote with `visibility` reads it, made again only when either changes. */
const useQuoted = (schema: FormSchema | null, visibility: SheetVisibility): FormSchema =>
  useMemo(() => (schema === null ? NO_SCHEMA : quotedSchema(schema, visibility.hidden)), [schema, visibility])

/** `form` with its sheet `page` always shown: the sheet's conditions set aside, the rest as they are. */
function withSheetShown(form: FormSpec, page: Page): FormSpec {
  if (page.show_when.conditions.length === 0) return form
  return { ...form, pages: form.pages.map((sheet) => (sheet === page ? { ...page, show_when: { ...page.show_when, conditions: [] } } : sheet)) }
}

/**
 * A new quote while building: the sheets as an underwriter's new quote opens them, holding
 * its columns' defaults and nothing priced, the sheet being built decided as if it showed,
 * so its own conditions fade nothing on it: its tab marks it hidden. Its cells are drawn as
 * a new quote's, not as a read-only quote's, and take no typing: the sheet holds them inert
 * while building.
 */
export function useNewQuoteValues(active = true): SheetValues {
  const form = useWorkbenchFormStore((s) => (active ? s.form : null))
  const pageId = useWorkbenchViewStore((s) => s.pageId)
  const schema = form?.schema ?? null
  // A new quote holds the schema's defaults alone.
  const rows = useMemo(() => (schema === null ? NO_ROWS : withPresetRows(schema, newQuoteValues(schema))), [schema])
  const built = useMemo(() => (form === null ? null : withSheetShown(form, activePage(form, pageId))), [form, pageId])
  // Nothing is priced while building: every output is empty.
  const shown = useMemo(() => (built === null ? NO_VISIBILITY : withPrice(built, rows, sheetVisibility(built, rows), null)), [built, rows])
  const visibility = useSteadyVisibility(shown)
  const quoted = useQuoted(schema, visibility)
  return useMemo<SheetValues>(
    () => ({
      rows,
      setCell: TYPE_NOTHING,
      setRows: TYPE_NOTHING,
      price: null,
      basis: "",
      problems: NO_PROBLEMS,
      visibility,
      quoted,
      readOnly: false,
      locked: NOTHING_LOCKED,
      exportCells: null,
      importRows: null,
    }),
    [rows, visibility, quoted],
  )
}

/**
 * The form's sample in Preview: typed there, each edit undone like any other, priced live,
 * and checked against the columns' rules once anything is typed. Ordinary field problems
 * are marked; a typed index's problems also stop pricing.
 */
export function useSampleValues(active = true): SheetValues {
  const form = useWorkbenchFormStore((s) => (active ? s.form : null))
  const change = useWorkbenchFormStore((s) => s.change)
  const price = useWorkbenchPricingStore((s) => s.price)
  // A price made in another context (the pipeline, its data or source moved) shows stale.
  const context = usePricingContext()
  const schema = form?.schema ?? null
  const sample = form?.sample ?? null
  const rows = useMemo(
    () => (form === null || schema === null || sample === null ? NO_ROWS : withStandIn(withValidationPassed(form, sample, withPresetRows(schema, sample), price))),
    [form, schema, sample, price],
  )
  const shown = useMemo(() => (form === null ? NO_VISIBILITY : withPrice(form, form.sample, sheetVisibility(form, form.sample), price)), [form, price])
  const visibility = useSteadyVisibility(shown)
  const quoted = useQuoted(schema, visibility)
  const eligible = useMemo(() => form !== null && quoteEligible(form, form.sample, APP_SET_COLUMNS), [form])
  const problems = useMemo(() => (eligible && form !== null ? cellProblems(form, quoted, rows) : NO_PROBLEMS), [eligible, form, quoted, rows])
  const basis = useMemo(() => (form === null ? "" : previewBasis(form, context)), [form, context])
  // The workbook holds the sample as last priced, so it runs while that price is current.
  const exportCells = useMemo<CellsExporter | null>(
    () => (form === null || price === null || price.basis !== basis ? null : (componentId) => exportWorkbenchFile(form, componentId, price)),
    [form, price, basis],
  )
  const importRows = useMemo<RowsImporter | null>(
    () => (form === null ? null : (componentId, file) => importWorkbenchRows(form, componentId, form.sample, file)),
    [form],
  )
  return useMemo<SheetValues>(
    () => ({
      rows,
      setCell: (tableId, row, columnId, value) => {
        // A column the app sets takes no typing, as in the app.
        if (!APP_SET_COLUMNS.has(columnKey(tableId, columnId))) change((spec) => withSampleCell(spec, tableId, row, columnId, value))
      },
      setRows: (rowsByTable) => change((spec) => withSampleRows(spec, rowsByTable)),
      price,
      basis,
      problems,
      visibility,
      quoted,
      readOnly: false,
      locked: APP_SET_COLUMNS,
      exportCells,
      importRows,
    }),
    [rows, change, price, basis, problems, visibility, quoted, exportCells, importRows],
  )
}

/**
 * The underwriter app's quote: priced as typing pauses, whatever its cells hold, checked
 * against the columns' rules once anything beyond its defaults is typed while it may
 * change, and read as the server makes the quote, without the values the sheets hide.
 */
export function useQuoteValues(active = true): SheetValues {
  const form = useWorkbenchFormStore((s) => (active ? s.form : null))
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const price = useWorkbenchPreviewStore((s) => s.price)
  const setCell = useWorkbenchPreviewStore((s) => s.setCell)
  const setRows = useWorkbenchPreviewStore((s) => s.setRows)
  const readOnly = useWorkbenchPreviewStore((s) => s.readOnly)
  const locked = useWorkbenchPreviewStore((s) => s.locked)
  const exportCells = useWorkbenchPreviewStore((s) => s.exporter)
  const importer = useWorkbenchPreviewStore((s) => s.importer)
  const schema = form?.schema ?? null
  const rows = useMemo(
    () => (form === null || schema === null ? quote : withValidationPassed(form, quote, withPresetRows(schema, quote), price)),
    [form, schema, quote, price],
  )
  const shown = useMemo(() => (form === null ? NO_VISIBILITY : withPrice(form, quote, sheetVisibility(form, quote), price)), [form, quote, price])
  const visibility = useSteadyVisibility(shown)
  const quoted = useQuoted(schema, visibility)
  // As the server counts typing: what the sheets show, the details the app sets and the defaults aside.
  const typed = useMemo(() => !readOnly && form !== null && quoteEligible(form, quote, locked), [readOnly, form, quote, locked])
  const problems = useMemo(() => (typed && form !== null ? cellProblems(form, quoted, rows) : NO_PROBLEMS), [typed, form, quoted, rows])
  const basis = useMemo(() => (form === null ? "" : basisOf(form, quote, visibility.hidden)), [form, visibility, quote])
  const importRows = useMemo<RowsImporter | null>(
    () => (importer === null || readOnly ? null : (componentId, file) => importer(componentId, quote, file)),
    [importer, readOnly, quote],
  )
  return useMemo<SheetValues>(
    () => ({ rows, setCell, setRows, price, basis, problems, visibility, quoted, readOnly, locked, exportCells, importRows }),
    [rows, setCell, setRows, price, basis, problems, visibility, quoted, readOnly, locked, exportCells, importRows],
  )
}

/**
 * Which sheets and components show, for what draws outside the sheet (the tabs): a new
 * quote's while building, nothing priced; in Preview, the sample's, or the quote's in the
 * underwriter app, each with the price it shows.
 */
export function useSheetVisibility(): SheetVisibility {
  const form = useWorkbenchFormStore((s) => s.form)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const quotePrice = useWorkbenchPreviewStore((s) => s.price)
  const samplePrice = useWorkbenchPricingStore((s) => s.price)
  const underwriterApp = useWorkbenchViewStore((s) => s.underwriterApp)
  const values = form === null ? null : !previewing ? newQuoteValues(form.schema) : underwriterApp ? quote : form.sample
  const price = !previewing ? null : underwriterApp ? quotePrice : samplePrice
  return useMemo(() => (form === null || values === null ? NO_VISIBILITY : withPrice(form, values, sheetVisibility(form, values), price)), [form, values, price])
}
