/**
 * Zustand store for Preview (specs/workbench): an underwriter's quote keyed into the
 * sheets, kept apart from the sample, never saved and never undone, lasting while the
 * editor is open, through Build and back; whether Price has checked it against the
 * columns' rules; and what pricing it on the pipeline gave, with the basis it was priced
 * for, or why there is no price.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { quoteFilled, quoteProblems, valuesBasis } from "../utils/sheetValues"
import { withCell, type SampleRow, type SampleValue } from "../utils/workbenchForm"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPricingStore, { priceForm, type SamplePrice } from "./useWorkbenchPricingStore"

interface WorkbenchPreviewState {
  /** The quote, by table id, rows keyed by column id, as typed. */
  quote: Record<string, SampleRow[]>
  /**
   * Price has been pressed: the cells that break their column's rules are marked from
   * then on, as they are edited, until the quote is cleared.
   */
  checked: boolean
  /** What the last pricing of the quote gave, for the basis it was priced for. */
  price: SamplePrice | null
  /** Why the quote was not priced, as the toolbar says it, until a pricing succeeds. */
  error: string | null
  pricing: boolean
  /** Set one cell, adding empty rows up to it. */
  setCell: (tableId: string, row: number, columnId: string, value: SampleValue) => void
  /** Replace tables' rows, as a grid adds and deletes a row in every table it shows. */
  setRows: (rowsByTable: Record<string, SampleRow[]>) => void
  /** Start over: no quote, nothing marked, no price. */
  clear: () => void
  /**
   * Check the quote against the columns' rules and, when every cell passes, price it on
   * the pipeline through the pricer the view gave the pricing store; one pricing at a
   * time, a press meanwhile doing nothing. A quote with nothing typed in the tables the
   * schema has now is not priced: a deployed request never holds a blank quote, and the
   * server would read one as the null quote. An answer that arrives after Clear, or after
   * the view replaced or dropped its pricer, is nobody's.
   */
  priceQuote: () => Promise<void>
}

/** The pricing asked for last; an answer to an earlier one is dropped. */
let asked = 0

const useWorkbenchPreviewStore = create<WorkbenchPreviewState>()((set, get) => ({
  quote: {},
  checked: false,
  price: null,
  error: null,
  pricing: false,
  setCell: (tableId, row, columnId, value) =>
    set((s) => ({ quote: { ...s.quote, [tableId]: withCell(s.quote[tableId] ?? [], row, columnId, value) } })),
  setRows: (rowsByTable) => set((s) => ({ quote: { ...s.quote, ...rowsByTable } })),
  clear: () => {
    asked += 1
    set({ quote: {}, checked: false, price: null, error: null, pricing: false })
  },
  priceQuote: async () => {
    const { form, status } = useWorkbenchFormStore.getState()
    const { pricer } = useWorkbenchPricingStore.getState()
    if (get().pricing || form === null || status !== "ready" || pricer === null) return
    const { quote } = get()
    if (!quoteFilled(form.schema, quote)) {
      set({ checked: true, price: null, error: "Type the quote first" })
      return
    }
    const problems = Object.keys(quoteProblems(form.schema, quote)).length
    if (problems > 0) {
      set({ checked: true, error: `${problems} ${problems === 1 ? "cell needs" : "cells need"} attention` })
      return
    }
    const basis = valuesBasis(form.schema, quote)
    const attempt = ++asked
    set({ checked: true, pricing: true, error: null })
    // Cleared meanwhile, or the view left: the answer is nobody's.
    const current = () => attempt === asked && useWorkbenchPricingStore.getState().pricer === pricer
    try {
      const priced = await priceForm({ ...form, sample: quote }, pricer)
      if (current()) set({ price: { basis, ...priced } })
    } catch (error: unknown) {
      if (current()) set({ price: null, error: `Pricing failed: ${apiErrorMessage(error)}` })
    } finally {
      if (attempt === asked) set({ pricing: false })
    }
  },
}))

export default useWorkbenchPreviewStore
