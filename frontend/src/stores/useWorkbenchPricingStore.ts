/**
 * Zustand store for the sample priced live in Preview (specs/workbench): the form's sample
 * priced on the pipeline open in the editor, through the pricer the view host gives it,
 * whenever the schema, the sample or the pricing context changes once typing pauses,
 * whatever its cells hold, a sample with nothing typed in the cells the sheets show priced
 * not at all; what the last pricing gave, with the basis it was priced for (its values and
 * the context), and why it last failed.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchTablesResponse } from "../api/types"
import { fetchWorkbenchFormTables } from "../api/workbench"
import { canonicalJson } from "../utils/canonicalJson"
import { quoteEligible } from "../utils/sheetVisibility"
import { pricingBasis, type SampleRow } from "../utils/workbenchForm"
import type { PricedSample } from "../utils/workbenchTables"
import useDataEpochStore from "./useDataEpochStore"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useGraphStore from "./useGraphStore"
import useSettingsStore from "./useSettingsStore"
import { pricingLoop } from "./pricingLoop"
import useWorkbenchFormStore from "./useWorkbenchFormStore"

/**
 * What the editor prices a form's values on besides the values: the document as it was
 * adopted (`executionGeneration`, moved by a branch switched or the document read again),
 * the pipeline's structure with its submodels' definitions (which the root's structure
 * leaves out; an edit, the Assistant's too, moves it), the data source, and the data's epoch
 * (`useDataEpochStore`). Preview's price is stamped with it and goes stale when it moves.
 */
export interface PricingContext {
  document: number
  pipeline: string
  source: string
  data: number
}

/** The submodels' definitions as text, kept while the graph store keeps the same ones. */
let submodelsSeen: { submodels: Record<string, unknown>; text: string } | null = null

function submodelsText(submodels: Record<string, unknown>): string {
  if (submodelsSeen?.submodels !== submodels) submodelsSeen = { submodels, text: canonicalJson(submodels) }
  return submodelsSeen.text
}

/** The editor's pricing context as it is now. */
function pricingContext(): PricingContext {
  const graph = useGraphStore.getState()
  return {
    document: useDocumentStatusStore.getState().executionGeneration,
    pipeline: `${graph.structuralFingerprint}|${submodelsText(graph.submodels)}`,
    source: useSettingsStore.getState().activeSource,
    data: useDataEpochStore.getState().epoch,
  }
}

/** A context as one string to compare. */
const contextKey = (context: PricingContext): string =>
  JSON.stringify([context.document, context.pipeline, context.source, context.data])

/**
 * What a Preview price of `form` is stamped with, and compared with: its values and the
 * context (`contextKey`), the context now unless one is given.
 */
export const previewBasis = (form: FormSpec, context: string = contextKey(pricingContext())): string =>
  `${pricingBasis(form)}|${context}`

/** The editor's pricing context as a string, re-read as any part of it moves. */
export function usePricingContext(): string {
  useDocumentStatusStore((s) => s.executionGeneration)
  useGraphStore((s) => s.structuralFingerprint)
  useGraphStore((s) => s.submodels)
  useSettingsStore((s) => s.activeSource)
  useDataEpochStore((s) => s.epoch)
  return contextKey(pricingContext())
}

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

/**
 * Prices a quote typed into a form's sheets in place of the pipeline open in the editor:
 * the underwriter app's, through its own route, which the Preview store's quote is priced
 * through.
 */
export type QuotePricer = (form: FormSpec, quote: Record<string, SampleRow[]>) => Promise<PricedValues>

/** A priced sample, with the basis (the schema and the sample) it was priced for. */
export interface SamplePrice extends PricedValues {
  basis: string
}

/**
 * Price a form's values on the pipeline: the tables, sample and response tables the
 * server takes from the form (`POST /api/workbench/tables`), then the pricer on them.
 */
async function priceForm(form: FormSpec, pricer: Pricer): Promise<PricedValues> {
  const workbench = await fetchWorkbenchFormTables(form)
  const { tables } = await pricer(workbench)
  return { tables, sample: workbench.sample }
}

/** How long the form stays unchanged before its sample is priced: a pause in typing. */
export const PRICING_DELAY_MS = 300

interface WorkbenchPricingState {
  /** The view host's pricer while the view shows; null otherwise, when nothing is priced. */
  pricer: Pricer | null
  /** The quote pricer the underwriter app set, which its quote is priced through; null in the builder. */
  quotePricer: QuotePricer | null
  setQuotePricer: (quotePricer: QuotePricer | null) => void
  /** What the last pricing gave; output columns show it, dimmed once the basis has moved on. */
  price: SamplePrice | null
  /** Why the last pricing failed, until one succeeds. */
  error: string | null
  pricing: boolean
  setPricer: (pricer: Pricer | null) => void
  /** Price the sample once typing pauses (`PRICING_DELAY_MS`). */
  schedule: () => void
  /**
   * Drop the pricing waiting for typing to pause and the one more asked for meanwhile, as
   * when Preview is left: nothing is priced while building. A pricing under way finishes.
   */
  cancel: () => void
  /**
   * Price the sample as the form has it now, or, with nothing typed in the cells the sheets
   * show, have no price and nothing to say: a deployed request never holds a blank quote.
   * One pricing runs at a time: asked again meanwhile, it prices once more when it
   * finishes, on the form as it is then.
   */
  priceNow: () => Promise<void>
}

const useWorkbenchPricingStore = create<WorkbenchPricingState>()((set, get) => {
  /** The sample as the form has it now, through the view host's pricer; an answer once it left is nobody's. */
  const priceOnce = async () => {
    const { pricer } = get()
    const { form, status } = useWorkbenchFormStore.getState()
    if (pricer === null || form === null || status !== "ready") return
    if (!quoteEligible(form, form.sample)) {
      set({ price: null, error: null })
      return
    }
    // Stamped with the context it is priced in: an answer for one moved on since is shown
    // stale, and the move asks for another.
    const basis = previewBasis(form)
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
  const loop = pricingLoop(priceOnce, PRICING_DELAY_MS)

  return {
    pricer: null,
    quotePricer: null,
    setQuotePricer: (quotePricer) => set({ quotePricer }),
    price: null,
    error: null,
    pricing: false,
    setPricer: (pricer) => {
      if (pricer === null) loop.cancel()
      set({ pricer })
    },
    schedule: () => {
      if (get().pricer !== null) loop.schedule()
    },
    cancel: loop.cancel,
    priceNow: loop.priceNow,
  }
})

export default useWorkbenchPricingStore
