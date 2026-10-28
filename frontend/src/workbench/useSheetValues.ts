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
 * sheet are not drawn again.
 */
import { createContext, useContext, useMemo, useState } from "react"
import type { FormSchema, FormSpec, Page } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { previewBasis, usePricingContext, type SamplePrice } from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore, { activePage } from "../stores/useWorkbenchViewStore"
import { newQuoteValues, quoteProblems, valuesBasis, withPresetRows, type RowsByTable } from "../utils/sheetValues"
import { quoteEligible, quotedSchema, sheetVisibility, type SheetVisibility } from "../utils/sheetVisibility"
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
  /** The cells that break their column's rules, by `cellKey`; none while nothing is typed. */
  problems: Readonly<Record<string, string>>
  /** The sheets and components these values show, and the columns they hide. */
  visibility: SheetVisibility
  /** The schema as these values' quote reads it: without the columns the sheets hide. */
  quoted: FormSchema
  /** The values may not change: the cells take no typing and rows are not added or deleted. */
  readOnly: boolean
  /** The columns, by `columnKey`, whose cells take no typing: the underwriter app sets them. */
  locked: ReadonlySet<string>
}

export const SheetValuesContext = createContext<SheetValues | null>(null)

/** The values of the sheet a component is on. */
export function useSheetValues(): SheetValues {
  const values = useContext(SheetValuesContext)
  if (values === null) throw new Error("The sheet's values are not provided")
  return values
}

const NO_PROBLEMS: Readonly<Record<string, string>> = {}
const NO_ROWS: RowsByTable = {}
// Before the form is read nothing draws a sheet; these only fill the shape.
const NO_VISIBILITY: SheetVisibility = { pages: new Set(), sections: new Set(), widgets: new Set(), fields: new Set(), hidden: new Set() }
const NO_SCHEMA: FormSchema = { tables: [] }
const NOTHING_LOCKED: ReadonlySet<string> = new Set()
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
  const shown = useMemo(() => (built === null ? NO_VISIBILITY : sheetVisibility(built, rows)), [built, rows])
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
    }),
    [rows, visibility, quoted],
  )
}

/**
 * The form's sample in Preview: typed there, each edit undone like any other, priced live,
 * whatever its cells hold, and checked against the columns' rules once anything is typed,
 * the marks never stopping its pricing.
 */
export function useSampleValues(active = true): SheetValues {
  const form = useWorkbenchFormStore((s) => (active ? s.form : null))
  const change = useWorkbenchFormStore((s) => s.change)
  const price = useWorkbenchPricingStore((s) => s.price)
  // A price made in another context (the pipeline, its data or source moved) shows stale.
  const context = usePricingContext()
  const schema = form?.schema ?? null
  const sample = form?.sample ?? null
  const rows = useMemo(() => (schema === null || sample === null ? NO_ROWS : withPresetRows(schema, sample)), [schema, sample])
  const shown = useMemo(() => (form === null ? NO_VISIBILITY : sheetVisibility(form, form.sample)), [form])
  const visibility = useSteadyVisibility(shown)
  const quoted = useQuoted(schema, visibility)
  const eligible = useMemo(() => form !== null && quoteEligible(form, form.sample), [form])
  const problems = useMemo(() => (eligible ? quoteProblems(quoted, rows) : NO_PROBLEMS), [eligible, quoted, rows])
  const basis = useMemo(() => (form === null ? "" : previewBasis(form, context)), [form, context])
  return useMemo<SheetValues>(
    () => ({
      rows,
      setCell: (tableId, row, columnId, value) => change((spec) => withSampleCell(spec, tableId, row, columnId, value)),
      setRows: (rowsByTable) => change((spec) => withSampleRows(spec, rowsByTable)),
      price,
      basis,
      problems,
      visibility,
      quoted,
      readOnly: false,
      locked: NOTHING_LOCKED,
    }),
    [rows, change, price, basis, problems, visibility, quoted],
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
  const schema = form?.schema ?? null
  const rows = useMemo(() => (schema === null ? quote : withPresetRows(schema, quote)), [schema, quote])
  const shown = useMemo(() => (form === null ? NO_VISIBILITY : sheetVisibility(form, quote)), [form, quote])
  const visibility = useSteadyVisibility(shown)
  const quoted = useQuoted(schema, visibility)
  // As the server counts typing: what the sheets show, the details the app sets and the defaults aside.
  const typed = useMemo(() => !readOnly && form !== null && quoteEligible(form, quote, locked), [readOnly, form, quote, locked])
  const problems = useMemo(() => (typed ? quoteProblems(quoted, rows) : NO_PROBLEMS), [typed, quoted, rows])
  const basis = useMemo(() => (schema === null ? "" : valuesBasis(schema, visibility.hidden, quote)), [schema, visibility, quote])
  return useMemo<SheetValues>(
    () => ({ rows, setCell, setRows, price, basis, problems, visibility, quoted, readOnly, locked }),
    [rows, setCell, setRows, price, basis, problems, visibility, quoted, readOnly, locked],
  )
}

/**
 * Which sheets and components show, for what draws outside the sheet (the tabs): a new
 * quote's while building; in Preview, the sample's, or the quote's in the underwriter app.
 */
export function useSheetVisibility(): SheetVisibility {
  const form = useWorkbenchFormStore((s) => s.form)
  const previewing = useWorkbenchViewStore((s) => s.mode === "preview")
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const underwriterApp = useWorkbenchViewStore((s) => s.underwriterApp)
  if (form === null) return NO_VISIBILITY
  return sheetVisibility(form, !previewing ? newQuoteValues(form.schema) : underwriterApp ? quote : form.sample)
}
