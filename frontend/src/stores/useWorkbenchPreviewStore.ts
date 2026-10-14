/**
 * Zustand store for the underwriter app's quote (specs/workbench): the quote keyed into the
 * sheets, never undone, and what pricing it through the app's quote pricer gave, with the
 * basis it was priced for, or why there is no price. The quote is priced once typing
 * pauses, whatever its cells hold, and as it opens while it may change and has no price; a
 * quote with nothing typed in the cells the sheets show, the details the app sets and the
 * defaults a new quote starts with aside, is not priced. The app opens a saved quote into it, with its price, holds it read only while
 * the quote may not change, and locks the columns it sets itself.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import { quoteTyped, valuesBasis } from "../utils/sheetValues"
import { columnKey, quotedSchema, sheetVisibility } from "../utils/sheetVisibility"
import { withCell, type SampleRow, type SampleValue } from "../utils/workbenchForm"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPricingStore, { PRICING_DELAY_MS, type SamplePrice } from "./useWorkbenchPricingStore"

interface WorkbenchPreviewState {
  /** The quote, by table id, rows keyed by column id, as typed. */
  quote: Record<string, SampleRow[]>
  /** What the last pricing of the quote gave, for the basis it was priced for. */
  price: SamplePrice | null
  /** Why the quote has no price, as the toolbar says it, until a pricing succeeds. */
  error: string | null
  pricing: boolean
  /** The quote may not change: its cells take no typing and it is not priced. */
  readOnly: boolean
  /**
   * The columns, by `columnKey`, whose cells take no typing: the underwriter app sets them
   * itself. They are not what makes a quote typed.
   */
  locked: ReadonlySet<string>
  /** Set one cell, adding empty rows up to it, and price once typing pauses; a locked column's is not set. */
  setCell: (tableId: string, row: number, columnId: string, value: SampleValue) => void
  /** Replace tables' rows, as a grid adds and deletes a row in every table it shows, and price once typing pauses. */
  setRows: (rowsByTable: Record<string, SampleRow[]>) => void
  /**
   * Put a quote in place of the one there, with the price it was saved with (null when
   * unpriced), read only or not; a pricing under way is nobody's, and a quote that may
   * change and has no price is priced once the pause has passed.
   */
  open: (quote: Record<string, SampleRow[]>, price: SamplePrice | null, readOnly: boolean) => void
  /** Hold the quote read only, or let it change, pricing it then if it has no price. */
  setReadOnly: (readOnly: boolean) => void
  setLocked: (locked: ReadonlySet<string>) => void
  /** Price the quote once typing pauses (`PRICING_DELAY_MS`). */
  schedule: () => void
  /**
   * Price the quote as it is now, through the quote pricer the host set, the underwriter
   * app's, unless it is read only, already priced for these values, or holds nothing typed
   * in the cells the sheets show, the details the app sets and the defaults aside, when it has no price and
   * nothing to say: a deployed request never holds a blank quote. The quote is read as the
   * server makes it, the cells the sheets hide left out, and priced whatever its cells
   * hold. One pricing runs at a time: asked again meanwhile, it prices once more when it
   * finishes, on the quote as it is then. An answer that arrives after another quote was
   * opened, or after the host dropped its pricer, is nobody's.
   */
  priceNow: () => Promise<void>
  /**
   * Price now what waits for typing to pause, and wait for the pricing under way to finish,
   * again for a value entered meanwhile, until nothing waits and nothing runs: a save keeps
   * the price the quote has once it settles.
   */
  settle: () => Promise<void>
}

/** The pricing asked for last; an answer to an earlier one is dropped. */
let asked = 0

/** The pricing waiting for typing to pause, the one running, and whether to run another after it. */
let timer: ReturnType<typeof setTimeout> | null = null
let run: Promise<void> | null = null
let again = false

const NOTHING_LOCKED: ReadonlySet<string> = new Set()

const useWorkbenchPreviewStore = create<WorkbenchPreviewState>()((set, get) => {
  const stopWaiting = () => {
    if (timer !== null) clearTimeout(timer)
    timer = null
  }

  const priceOnce = async () => {
    const { form, status } = useWorkbenchFormStore.getState()
    const { quotePricer } = useWorkbenchPricingStore.getState()
    const { quote, locked, readOnly, price } = get()
    if (readOnly || form === null || status !== "ready" || quotePricer === null) return
    const { hidden } = sheetVisibility(form, quote)
    if (!quoteTyped(quotedSchema(quotedSchema(form.schema, hidden), locked), quote)) {
      set({ price: null, error: null })
      return
    }
    const basis = valuesBasis(form.schema, hidden, quote)
    if (price !== null && price.basis === basis) return
    const attempt = ++asked
    set({ pricing: true, error: null })
    // Another quote opened meanwhile, or the host left: the answer is nobody's.
    const current = () => attempt === asked && useWorkbenchPricingStore.getState().quotePricer === quotePricer
    try {
      const priced = await quotePricer(form, quote)
      if (current()) set({ price: { basis, ...priced } })
    } catch (error: unknown) {
      if (current()) set({ price: null, error: `Pricing failed: ${apiErrorMessage(error)}` })
    } finally {
      if (attempt === asked) set({ pricing: false })
    }
  }

  return {
    quote: {},
    price: null,
    error: null,
    pricing: false,
    readOnly: false,
    locked: NOTHING_LOCKED,
    setCell: (tableId, row, columnId, value) => {
      const { quote, readOnly, locked } = get()
      if (readOnly || locked.has(columnKey(tableId, columnId))) return
      set({ quote: { ...quote, [tableId]: withCell(quote[tableId] ?? [], row, columnId, value) } })
      get().schedule()
    },
    setRows: (rowsByTable) => {
      if (get().readOnly) return
      set((s) => ({ quote: { ...s.quote, ...rowsByTable } }))
      get().schedule()
    },
    open: (quote, price, readOnly) => {
      asked += 1
      stopWaiting()
      set({ quote, price, error: null, pricing: false, readOnly })
      if (!readOnly && price === null) get().schedule()
    },
    setReadOnly: (readOnly) => {
      set({ readOnly })
      if (!readOnly) get().schedule()
    },
    setLocked: (locked) => set({ locked }),
    schedule: () => {
      stopWaiting()
      timer = setTimeout(() => {
        timer = null
        void get().priceNow()
      }, PRICING_DELAY_MS)
    },
    priceNow: () => {
      if (run !== null) {
        again = true
        return run
      }
      run = (async () => {
        try {
          do {
            again = false
            await priceOnce()
          } while (again)
        } finally {
          run = null
        }
      })()
      return run
    },
    settle: async () => {
      // A value entered while it waits is priced before it settles: it settles once
      // nothing waits for the pause and nothing is being priced.
      while (timer !== null || run !== null) {
        if (timer !== null) {
          stopWaiting()
          await get().priceNow()
        } else {
          await run
        }
      }
    },
  }
})

export default useWorkbenchPreviewStore
