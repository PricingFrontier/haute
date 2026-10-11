/**
 * What a sheet's cells hold and show (specs/workbench), given to the components on it
 * through a context: the sample while building, an underwriter's quote in Preview. Each
 * gives the rows by table, how a cell or a grid's rows change, what the last pricing of
 * those values gave with the basis it was priced for, the cells that break their
 * column's rules, which sheets and components the values show, and the schema as the
 * values' quote reads it, without the columns the sheets hide.
 */
import { createContext, useContext, useMemo } from "react"
import type { FormSchema } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type SamplePrice } from "../stores/useWorkbenchPricingStore"
import { quoteProblems, valuesBasis, type RowsByTable } from "../utils/sheetValues"
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
  /** The cells that break their column's rules, by `cellKey`; none until checked. */
  problems: Readonly<Record<string, string>>
  /** The sheets and components these values show, and the columns they hide. */
  visibility: SheetVisibility
  /** The schema as these values' quote reads it: without the columns the sheets hide. */
  quoted: FormSchema
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
const NO_VISIBILITY: SheetVisibility = { pages: new Set(), widgets: new Set(), hidden: new Set() }
const NO_SCHEMA: FormSchema = { tables: [] }

/** The sample while building: the form's, each edit undone like any other, priced live. */
export function useSampleValues(): SheetValues {
  const form = useWorkbenchFormStore((s) => s.form)
  const change = useWorkbenchFormStore((s) => s.change)
  const price = useWorkbenchPricingStore((s) => s.price)
  return useMemo<SheetValues>(() => {
    const visibility = form === null ? NO_VISIBILITY : sheetVisibility(form, form.sample)
    return {
      rows: form?.sample ?? NO_ROWS,
      setCell: (tableId, row, columnId, value) => change((spec) => withSampleCell(spec, tableId, row, columnId, value)),
      setRows: (rowsByTable) => change((spec) => withSampleRows(spec, rowsByTable)),
      price,
      basis: form === null ? "" : pricingBasis(form),
      problems: NO_PROBLEMS,
      visibility,
      quoted: form === null ? NO_SCHEMA : quotedSchema(form.schema, visibility.hidden),
    }
  }, [form, change, price])
}

/**
 * An underwriter's quote in Preview: apart from the sample, checked against the columns'
 * rules once Price has been pressed, priced when Price is pressed, and read as the server
 * makes the quote, without the values the sheets hide.
 */
export function usePreviewValues(): SheetValues {
  const form = useWorkbenchFormStore((s) => s.form)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  const checked = useWorkbenchPreviewStore((s) => s.checked)
  const price = useWorkbenchPreviewStore((s) => s.price)
  const setCell = useWorkbenchPreviewStore((s) => s.setCell)
  const setRows = useWorkbenchPreviewStore((s) => s.setRows)
  return useMemo<SheetValues>(() => {
    const visibility = form === null ? NO_VISIBILITY : sheetVisibility(form, quote)
    const quoted = form === null ? NO_SCHEMA : quotedSchema(form.schema, visibility.hidden)
    return {
      rows: quote,
      setCell,
      setRows,
      price,
      basis: form === null ? "" : valuesBasis(quoted, quote),
      problems: checked && form !== null ? quoteProblems(quoted, quote) : NO_PROBLEMS,
      visibility,
      quoted,
    }
  }, [form, quote, checked, price, setCell, setRows])
}

/**
 * Which sheets and components show, for what draws outside the sheet (the tabs): the
 * sample's while building, the quote's in Preview.
 */
export function useSheetVisibility(previewing: boolean): SheetVisibility {
  const form = useWorkbenchFormStore((s) => s.form)
  const quote = useWorkbenchPreviewStore((s) => s.quote)
  if (form === null) return NO_VISIBILITY
  return sheetVisibility(form, previewing ? quote : form.sample)
}
