/**
 * Zustand store for the sample priced live while building (specs/workbench): the form's
 * sample priced on the pipeline open in the editor, through the pricer the view host
 * gives it, whenever the schema or the sample changes once typing pauses; what the last
 * pricing gave, with the basis it was priced for, and why it last failed. `priceForm`,
 * the pricing itself, is Preview's too.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchTablesResponse } from "../api/types"
import { fetchWorkbenchFormTables } from "../api/workbench"
import { pricingBasis } from "../utils/workbenchForm"
import type { PricedSample } from "../utils/workbenchTables"
import useWorkbenchFormStore from "./useWorkbenchFormStore"

/** Prices a sample, given the tables, sample and response tables Haute takes from the form. */
export type Pricer = (workbench: WorkbenchTablesResponse) => Promise<PricedSample>

/** What pricing a form's values gave, with those values as the server typed them. */
export interface PricedValues extends PricedSample {
  /**
   * The values priced, as the server typed them: each many-row input table's filled rows
   * in order, which lines a Table's output rows up with its input rows by key.
   */
  sample: Readonly<Record<string, unknown>>
}

/** A priced sample, with the basis (the schema and the sample) it was priced for. */
export interface SamplePrice extends PricedValues {
  basis: string
}

/**
 * Price a form's values on the pipeline: the tables, sample and response tables the
 * server takes from the form (`POST /api/workbench/tables`), then the pricer on them.
 */
export async function priceForm(form: FormSpec, pricer: Pricer): Promise<PricedValues> {
  const workbench = await fetchWorkbenchFormTables(form)
  const { tables } = await pricer(workbench)
  return { tables, sample: workbench.sample }
}

/** How long the form stays unchanged before its sample is priced: a pause in typing. */
export const PRICING_DELAY_MS = 300

interface WorkbenchPricingState {
  /** The view host's pricer while the view shows; null otherwise, when nothing is priced. */
  pricer: Pricer | null
  /** What the last pricing gave; output columns show it, dimmed once the basis has moved on. */
  price: SamplePrice | null
  /** Why the last pricing failed, until one succeeds. */
  error: string | null
  pricing: boolean
  setPricer: (pricer: Pricer | null) => void
  /** Price the sample once typing pauses (`PRICING_DELAY_MS`). */
  schedule: () => void
  /**
   * Price the sample as the form has it now. One pricing runs at a time: asked again
   * meanwhile, it prices once more when it finishes, on the form as it is then.
   */
  priceNow: () => Promise<void>
}

/** The pricing waiting for typing to pause, the one running, and whether to run another after it. */
let timer: ReturnType<typeof setTimeout> | null = null
let run: Promise<void> | null = null
let again = false

const useWorkbenchPricingStore = create<WorkbenchPricingState>()((set, get) => ({
  pricer: null,
  price: null,
  error: null,
  pricing: false,
  setPricer: (pricer) => {
    if (pricer === null && timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    set({ pricer })
  },
  schedule: () => {
    if (get().pricer === null) return
    if (timer !== null) clearTimeout(timer)
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
    const priceOnce = async () => {
      const { pricer } = get()
      const { form, status } = useWorkbenchFormStore.getState()
      if (pricer === null || form === null || status !== "ready") return
      const basis = pricingBasis(form)
      set({ pricing: true })
      try {
        const priced = await priceForm(form, pricer)
        // The view left meanwhile: a late answer is nobody's.
        if (get().pricer !== pricer) return
        set({ price: { basis, ...priced }, error: null })
      } catch (error: unknown) {
        if (get().pricer !== pricer) return
        set({ price: null, error: apiErrorMessage(error) })
      } finally {
        set({ pricing: false })
      }
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
}))

export default useWorkbenchPricingStore
