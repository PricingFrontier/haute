/**
 * What a sheet's cells hold and show (specs/workbench), given to the components on it
 * through a context: a new quote while building, its defaults alone and nothing priced; the
 * form's sample in Preview; and the underwriter app's quote in the app. Each gives the
 * rows by table, how a cell or a grid's rows change, what the last pricing of those values
 * gave with the basis it was priced for, the cells that break their column's rules, which
 * sheets and components the values show, and the schema as the values' quote reads it,
 * without the columns the sheets hide. Each reads a table a column gives its rows as those
 * rows (`withPresetRows`), as the server prices them.
 */
import { createContext, useContext, useMemo } from "react"
import type { FormSchema } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type SamplePrice } from "../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../stores/useWorkbenchViewStore"
import { newQuoteValues, quoteFilled, quoteProblems, quoteTyped, valuesBasis, withPresetRows, type RowsByTable } from "../utils/sheetValues"
import { quotedSchema, sheetVisibility, type SheetVisibility } from "../utils/sheetVisibility"
import { pricingBasis, withSampleCell, withSampleRows, type SampleRow, type SampleValue } from "../utils/workbenchForm"

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
const NO_VISIBILITY: SheetVisibility = { pages: new Set(), sections: new Set(), widgets: new Set(), hidden: new Set() }
const NO_SCHEMA: FormSchema = { tables: [] }
const NOTHING_LOCKED: ReadonlySet<string> = new Set()
const TYPE_NOTHING = () => {}

/**
 * A new quote while building: the sheets as an underwriter's new quote opens them, holding
 * its columns' defaults and nothing priced. Its cells are drawn as a new quote's, not as a
 * read-only quote's, and take no typing: the sheet holds them inert while building.
 */
export function useNewQuoteValues(): SheetValues {
  const form = useWorkbenchFormStore((s) => s.form)
  return useMemo<SheetValues>(() => {
    const rows = form === null ? NO_ROWS : withPresetRows(form.schema, newQuoteValues(form.schema))
    const visibility = form === null ? NO_VISIBILITY : sheetVisibility(form, rows)
    return {
      rows,
      setCell: TYPE_NOTHING,
      setRows: TYPE_NOTHING,
      price: null,
      basis: "",
      problems: NO_PROBLEMS,
      visibility,
      quoted: form === null ? NO_SCHEMA : quotedSchema(form.schema, visibility.hidden),
      readOnly: false,
      locked: NOTHING_LOCKED,
    }
  }, [form])
}

/**
 * The form's sample in Preview: typed there, each edit undone like any other, priced live,
 * whatever its cells hold, and checked against the columns' rules once anything is typed,
 * the marks never stopping its pricing.
 */
export function useSampleValues(): SheetValues {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const price = useWorkbenchPricingStore((s) => s.price)
  return useMemo<SheetValues>(() => {
    const visibility = form === null ? NO_VISIBILITY : sheetVisibility(form, form.sample)
    const quoted = form === null ? NO_SCHEMA : quotedSchema(form.schema, visibility.hidden)
    const rows = form === null ? NO_ROWS : withPresetRows(form.schema, form.sample)
    return {
      rows,
      setCell: (tableId, row, columnId, value) => change((spec) => withSampleCell(spec, tableId, row, columnId, value)),
      setRows: (rowsByTable) => change((spec) => withSampleRows(spec, rowsByTable)),
      price,
      basis: form === null ? "" : pricingBasis(form),
      problems: quoteFilled(quoted, rows) ? quoteProblems(quoted, rows) : NO_PROBLEMS,
      visibility,
      quoted,
      readOnly: false,
      locked: NOTHING_LOCKED,
    }
  }, [form, change, price])
}

/**
 * The underwriter app's quote: priced as typing pauses, whatever its cells hold, checked
 * against the columns' rules once anything beyond its defaults is typed while it may
 * change, and read as the server makes the quote, without the values the sheets hide.
 */
export function useQuoteValues(): SheetValues {
  const form = useWorkbenchFormStore((s) => s.form)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const price = useWorkbenchPreviewStore((s) => s.price)
  const setCell = useWorkbenchPreviewStore((s) => s.setCell)
  const setRows = useWorkbenchPreviewStore((s) => s.setRows)
  const readOnly = useWorkbenchPreviewStore((s) => s.readOnly)
  const locked = useWorkbenchPreviewStore((s) => s.locked)
  return useMemo<SheetValues>(() => {
    const visibility = form === null ? NO_VISIBILITY : sheetVisibility(form, quote)
    const quoted = form === null ? NO_SCHEMA : quotedSchema(form.schema, visibility.hidden)
    const rows = form === null ? quote : withPresetRows(form.schema, quote)
    // The details the app sets are not typing, nor are the defaults a new quote starts with.
    const typed = !readOnly && quoteTyped(quotedSchema(quoted, locked), rows)
    return {
      rows,
      setCell,
      setRows,
      price,
      basis: form === null ? "" : valuesBasis(form.schema, visibility.hidden, quote),
      problems: typed ? quoteProblems(quoted, rows) : NO_PROBLEMS,
      visibility,
      quoted,
      readOnly,
      locked,
    }
  }, [form, quote, price, setCell, setRows, readOnly, locked])
}

/**
 * Which sheets and components show, for what draws outside the sheet (the tabs and the
 * section bars): a new quote's while building; in Preview, the sample's, or the quote's in
 * the underwriter app.
 */
export function useSheetVisibility(previewing: boolean): SheetVisibility {
  const form = useWorkbenchFormStore((s) => s.form)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const underwriterApp = useWorkbenchViewStore((s) => s.underwriterApp)
  if (form === null) return NO_VISIBILITY
  return sheetVisibility(form, !previewing ? newQuoteValues(form.schema) : underwriterApp ? quote : form.sample)
}
