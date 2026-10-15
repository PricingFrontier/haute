/**
 * Zustand store for the test quotes an analyst keeps in Preview (specs/workbench): the
 * project's test quotes, the panel that lists them, and the last run's results.
 *
 * A save prices the quote on the sheets, the form's sample, in full, through a pricer fixed
 * on the pipeline as it is, and keeps it with its values and the form as the view holds it;
 * an open puts a test quote's values in the sample's place, one undo step. A run fixes the
 * pipeline, the data source and the form when it starts and prices each test quote on them,
 * one request each, its values carried onto that form, then asks the server what changed
 * since the revision it read; a change to any of the three afterwards makes the run stale.
 * Accepting a change saves the run's response after that revision, the one the run read
 * (its digest), never while a run is under way or once the run is stale, and never over a
 * run started since. A run is forgotten when the pipeline's document is adopted anew (a
 * branch switched): its results were read on another branch's test quotes. Each write's
 * capture on the save ledger is reported as a save's is, which keeps what the ledger did
 * not take for a later capture.
 */
import { create } from "zustand"
import {
  acceptTestQuote,
  compareTestQuote,
  deleteTestQuote,
  fetchTestQuotes,
  fetchTestQuotesBranch,
  openTestQuote,
  saveTestQuote,
  type PricedTables,
  type SheetQuote,
} from "../api/workbench"
import { apiErrorMessage, isStaleRevision } from "../api/errors"
import type { FormSpec, WorkbenchTestQuoteChange, WorkbenchTestQuoteSummary } from "../api/types"
import { quoteEligible } from "../utils/sheetVisibility"
import { withSample } from "../utils/workbenchForm"
import { reportSaveCapture } from "./saveCapture"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPricingStore, { contextKey, priceForm, pricingContext, type Pricer, type PricingContext } from "./useWorkbenchPricingStore"
import { latest } from "../utils/latest"

export type RunStatus = "running" | "unchanged" | "changed" | "failed"

/** A test quote as the last run left it. */
export interface QuoteRun {
  status: RunStatus
  /** The revision the run read, which an accept follows, and its digest: what it is beyond its number. */
  number: number | null
  digest: string | null
  /** The quote's values carried onto the run's form, and what had no place on it. */
  values: SheetQuote | null
  dropped: string[]
  /** What the run priced, in full. */
  tables: PricedTables | null
  changes: WorkbenchTestQuoteChange[]
  error: string | null
}

/** What a run priced on: the editor's pricing context and the form, the form as a string to compare. */
export interface RunBasis extends PricingContext {
  form: string
}

interface WorkbenchTestQuotesState {
  open: boolean
  quotes: WorkbenchTestQuoteSummary[]
  loading: boolean
  /** Why the last list, save, open, accept or delete failed, until the next succeeds. */
  error: string | null
  /** What the last open could not carry, or what the last write reported. */
  notice: string | null
  saving: boolean
  /** The test quotes ticked for a run. */
  selected: string[]
  runs: Record<string, QuoteRun>
  /** The form and the basis the last run priced on; null before a run. */
  runForm: FormSpec | null
  runBasis: RunBasis | null
  running: boolean
  /** An accept is under way: neither a run nor another accept starts meanwhile. */
  accepting: boolean
  setOpen: (open: boolean) => void
  load: () => Promise<void>
  /** Save the quote on the sheets as a new test quote; whether it was saved. */
  save: (name: string) => Promise<boolean>
  /** Put a test quote's values in the sample's place, one undo step, naming what has no place on the sheets. */
  openQuote: (id: string) => Promise<void>
  toggle: (id: string) => void
  /** Run the ticked test quotes, or every one when none is ticked. */
  run: () => Promise<void>
  accept: (id: string) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Forget the last run, as when the pipeline's document is adopted anew. */
  forget: () => void
}

/** What a run of the form as it is now would price on. */
export function currentBasis(form: FormSpec): RunBasis {
  return {
    ...pricingContext(),
    // What decides the request: the tables and the conditions that hide their cells.
    form: JSON.stringify([form.schema, form.pages]),
  }
}

/** Whether a run on *basis* priced on something that has changed since. */
export function runIsStale(basis: RunBasis | null, form: FormSpec | null): boolean {
  if (basis === null || form === null) return false
  const now = currentBasis(form)
  return contextKey(now) !== contextKey(basis) || now.form !== basis.form
}

/** A pricer fixed on the pipeline and the source as they are now, or why there is none. */
function fixedPricer(): Pricer {
  const snapshot = useWorkbenchPricingStore.getState().snapshot
  if (snapshot === null) throw new Error("Open the workbench's sheets to price test quotes.")
  return snapshot()
}

/**
 * Why the quote on the sheets cannot be saved as a test quote, or null: nothing typed in
 * the cells the sheets show is no quote. Its values are priced whatever their cells hold,
 * as Preview prices them.
 */
function quoteRefusal(form: FormSpec, quote: SheetQuote): string | null {
  return quoteEligible(form, quote) ? null : "Type the quote first"
}

/** The run asked for last: an answer for an earlier one is nobody's. */
const runAsked = latest()
/**
 * The list read and the quote opened last, each its own ticket so neither drops a run's
 * answer: a late answer to an earlier read or open is nobody's, nor is one asked before the
 * pipeline's document was adopted anew (`forget`).
 */
const listAsked = latest()
const openAsked = latest()

const runOf = (patch: Partial<QuoteRun>): QuoteRun => ({
  status: "running",
  number: null,
  digest: null,
  values: null,
  dropped: [],
  tables: null,
  changes: [],
  error: null,
  ...patch,
})

const useWorkbenchTestQuotesStore = create<WorkbenchTestQuotesState>()((set, get) => ({
  open: false,
  quotes: [],
  loading: false,
  error: null,
  notice: null,
  saving: false,
  selected: [],
  runs: {},
  runForm: null,
  runBasis: null,
  running: false,
  accepting: false,
  // The panel lists the quotes when it opens.
  setOpen: (open) => set({ open }),
  load: async () => {
    const mine = listAsked.take()
    set({ loading: true })
    try {
      const { quotes } = await fetchTestQuotes()
      if (!listAsked.is(mine)) return
      const ids = new Set(quotes.map((quote) => quote.id))
      set((s) => ({ quotes, error: null, selected: s.selected.filter((id) => ids.has(id)) }))
    } catch (error: unknown) {
      if (listAsked.is(mine)) set({ error: apiErrorMessage(error) })
    } finally {
      if (listAsked.is(mine)) set({ loading: false })
    }
  },
  save: async (name) => {
    const { form } = useWorkbenchFormStore.getState()
    if (form === null || get().saving) return false
    const quote = form.sample
    const refusal = quoteRefusal(form, quote)
    if (refusal !== null) {
      set({ error: refusal })
      return false
    }
    // The document the quote is priced on: one adopted meanwhile (a branch switched) holds
    // another branch's test quotes, which this quote is not saved into.
    const document = useDocumentStatusStore.getState().executionGeneration
    set({ saving: true, error: null, notice: null })
    try {
      // The branch the save starts on, which the server holds it to: a checkout outside the
      // editor that changes nothing but the test-quote file adopts no document.
      const branch = await fetchTestQuotesBranch()
      const { tables } = await priceForm(form, fixedPricer())
      if (useDocumentStatusStore.getState().executionGeneration !== document) {
        set({ error: "Not saved: the branch changed while the quote was priced. Save it again on this one." })
        return false
      }
      reportSaveCapture(await saveTestQuote(name, form, quote, tables as PricedTables, branch))
      await get().load()
      return true
    } catch (error: unknown) {
      set({ error: `Not saved: ${apiErrorMessage(error)}` })
      return false
    } finally {
      set({ saving: false })
    }
  },
  openQuote: async (id) => {
    const { form } = useWorkbenchFormStore.getState()
    if (form === null) return
    const mine = openAsked.take()
    try {
      const opened = await openTestQuote(id, form)
      // Its values were carried onto the form as it was asked for: on any other they are
      // not the quote's, and a later open, or an adoption, has the sheets now.
      if (!openAsked.is(mine) || useWorkbenchFormStore.getState().form !== form) return
      // In the sample's place, one undo step.
      useWorkbenchFormStore.getState().change((spec) => withSample(spec, opened.values))
      set({
        error: null,
        notice: opened.dropped.length === 0
          ? null
          : `Opened '${opened.name}'. No place on the sheets for: ${opened.dropped.join(", ")}.`,
      })
    } catch (error: unknown) {
      if (openAsked.is(mine)) set({ error: apiErrorMessage(error) })
    }
  },
  toggle: (id) =>
    set((s) => ({ selected: s.selected.includes(id) ? s.selected.filter((x) => x !== id) : [...s.selected, id] })),
  run: async () => {
    const { form } = useWorkbenchFormStore.getState()
    if (form === null || get().running || get().accepting) return
    let pricer: Pricer
    try {
      pricer = fixedPricer()
    } catch (error: unknown) {
      set({ error: apiErrorMessage(error) })
      return
    }
    const { quotes, selected } = get()
    const ids = selected.length > 0 ? selected : quotes.map((quote) => quote.id)
    const mine = runAsked.take()
    set({
      running: true,
      error: null,
      notice: null,
      runForm: form,
      runBasis: currentBasis(form),
      runs: Object.fromEntries(ids.map((id) => [id, runOf({})])),
    })
    // One quote at a time: each is its own request, as deploy scores them.
    for (const id of ids) {
      if (!runAsked.is(mine)) return
      const done = (patch: Partial<QuoteRun>) => {
        if (runAsked.is(mine)) set((s) => ({ runs: { ...s.runs, [id]: { ...s.runs[id], ...patch } } }))
      }
      try {
        const opened = await openTestQuote(id, form)
        done({ number: opened.number, digest: opened.revision_digest, values: opened.values, dropped: opened.dropped })
        // Nothing typed left on the sheets is no quote, as a save refuses one: the null
        // quote is not priced, nor accepted, since no deployed request is one.
        if (quoteRefusal(form, opened.values) !== null) {
          done({ status: "failed", error: "Nothing typed in it has a place on the sheets now" })
          continue
        }
        const { tables } = await priceForm({ ...form, sample: opened.values }, pricer)
        // Compared with the revision the run opened, by its digest: one saved apart since
        // (another branch's under the same number) is refused, not compared.
        const compared = await compareTestQuote(id, form, opened.values, tables as PricedTables, opened.number, opened.revision_digest)
        done({
          status: compared.changes.length === 0 ? "unchanged" : "changed",
          tables: tables as PricedTables,
          changes: compared.changes,
        })
      } catch (error: unknown) {
        done({ status: "failed", error: apiErrorMessage(error) })
      }
    }
    if (runAsked.is(mine)) set({ running: false })
  },
  accept: async (id) => {
    const { runs, runForm, runBasis, running, accepting } = get()
    const run = runs[id]
    if (run === undefined || runForm === null) return
    const { number, digest, values, tables } = run
    if (number === null || digest === null || values === null || tables === null) return
    if (running || accepting) return
    if (runIsStale(runBasis, useWorkbenchFormStore.getState().form)) {
      set({ error: "The pipeline, the data source or the form has changed since this run: run again before accepting." })
      return
    }
    const mine = runAsked.current()
    set({ accepting: true })
    try {
      reportSaveCapture(await acceptTestQuote(id, number, digest, runForm, values, tables))
      // A run started since, or forgotten since, holds its own result for the quote.
      if (runAsked.is(mine)) {
        set((s) => ({
          error: null,
          // The accepted revision's digest is the server's to give: the next run reads it.
          runs: { ...s.runs, [id]: { ...run, status: "unchanged", changes: [], number: number + 1, digest: null } },
        }))
      }
      await get().load()
    } catch (error: unknown) {
      const message = apiErrorMessage(error)
      set({
        error: isStaleRevision(error)
          ? "The quote was saved since the run, or differently on this branch: run it again before accepting."
          : `Not accepted: ${message}`,
      })
    } finally {
      set({ accepting: false })
    }
  },
  remove: async (id) => {
    try {
      reportSaveCapture(await deleteTestQuote(id))
      set((s) => {
        const runs = { ...s.runs }
        delete runs[id]
        return { error: null, runs, selected: s.selected.filter((x) => x !== id) }
      })
      await get().load()
    } catch (error: unknown) {
      set({ error: `Not deleted: ${apiErrorMessage(error)}` })
    }
  },
  forget: () => {
    runAsked.bump()
    listAsked.bump()
    openAsked.bump()
    set({ runs: {}, runForm: null, runBasis: null, running: false })
    if (get().open) void get().load()
  },
}))

// The test quotes follow the pipeline's document: a run read on the branch before an
// adoption (a branch switched by hand, a change on disk) is nobody's now.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useWorkbenchTestQuotesStore.getState().forget()
})

export default useWorkbenchTestQuotesStore
