/**
 * Zustand store for the test quotes an analyst keeps in Preview (specs/workbench): the
 * project's test quotes, the panel that lists them, and the last run's results.
 *
 * A save prices the quote on the sheets in full, through a pricer fixed on the pipeline as
 * it is, and keeps it with its values and the form as the view holds it. A run fixes the
 * pipeline, the data source and the form when it starts and prices each test quote on them,
 * one request each, its values carried onto that form, then asks the server what changed
 * since the revision it read; a change to any of the three afterwards makes the run stale.
 * Accepting a change saves the run's response after that revision, the one the run read
 * (its digest), never while a run is under way or once the run is stale, and never over a
 * run started since. A run is forgotten when the pipeline's document is adopted anew (a
 * branch switched): its results were read on another branch's test quotes. Each write's
 * capture on the save ledger is reported as a save's is, and one the ledger did not take
 * is taken again, nothing written, when the project is next saved.
 */
import { create } from "zustand"
import {
  acceptTestQuote,
  captureTestQuotes,
  compareTestQuote,
  deleteTestQuote,
  fetchTestQuotes,
  fetchTestQuotesBranch,
  openTestQuote,
  saveTestQuote,
  type PricedTables,
  type SheetQuote,
  type TestQuoteChange,
} from "../api/workbench"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchTestQuoteSummary, WorkbenchTestQuoteWriteResponse } from "../api/types"
import { canonicalJson } from "../utils/canonicalJson"
import { quoteFilled, quoteProblems } from "../utils/sheetValues"
import { quotedSchema, sheetVisibility } from "../utils/sheetVisibility"
import { reportSaveCapture } from "./saveCapture"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useGraphStore from "./useGraphStore"
import useSettingsStore from "./useSettingsStore"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPreviewStore from "./useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { priceForm, type Pricer } from "./useWorkbenchPricingStore"

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
  changes: TestQuoteChange[]
  error: string | null
}

/** What a run priced on: the pipeline, the data source and the form, as strings to compare. */
export interface RunBasis {
  pipeline: string
  source: string
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
  /** A write landed but the ledger did not capture it: the next project save captures it. */
  uncaptured: boolean
  setOpen: (open: boolean) => void
  load: () => Promise<void>
  /** Save the quote on the sheets as a new test quote; whether it was saved. */
  save: (name: string) => Promise<boolean>
  openQuote: (id: string) => Promise<void>
  toggle: (id: string) => void
  /** Run the ticked test quotes, or every one when none is ticked. */
  run: () => Promise<void>
  accept: (id: string) => Promise<void>
  remove: (id: string) => Promise<void>
  /** Forget the last run, as when the pipeline's document is adopted anew. */
  forget: () => void
  /** Capture the test-quote file on the ledger again when a write's capture was not taken. */
  flush: () => Promise<void>
}

/** The submodels' definitions as text, kept while the graph store keeps the same ones. */
let submodelsSeen: { submodels: Record<string, unknown>; text: string } | null = null

function submodelsText(submodels: Record<string, unknown>): string {
  if (submodelsSeen?.submodels !== submodels) submodelsSeen = { submodels, text: canonicalJson(submodels) }
  return submodelsSeen.text
}

/** What a run of the form as it is now would price on. */
export function currentBasis(form: FormSpec): RunBasis {
  const graph = useGraphStore.getState()
  return {
    // The root's structure leaves the submodels' definitions out: an edit inside one prices
    // differently, so it counts too.
    pipeline: `${graph.structuralFingerprint}|${submodelsText(graph.submodels)}`,
    source: useSettingsStore.getState().activeSource,
    // What decides the request: the tables and the conditions that hide their cells.
    form: JSON.stringify([form.schema, form.pages]),
  }
}

/** Whether a run on *basis* priced on something that has changed since. */
export function runIsStale(basis: RunBasis | null, form: FormSpec | null): boolean {
  if (basis === null || form === null) return false
  const now = currentBasis(form)
  return now.pipeline !== basis.pipeline || now.source !== basis.source || now.form !== basis.form
}

/** A pricer fixed on the pipeline and the source as they are now, or why there is none. */
function fixedPricer(): Pricer {
  const snapshot = useWorkbenchPricingStore.getState().snapshot
  if (snapshot === null) throw new Error("Open the workbench's sheets to price test quotes.")
  return snapshot()
}

/** Why the quote on the sheets cannot be priced as Price would refuse it, or null. */
function quoteRefusal(form: FormSpec, quote: SheetQuote): string | null {
  const quoted = quotedSchema(form.schema, sheetVisibility(form, quote).hidden)
  if (!quoteFilled(quoted, quote)) return "Type the quote first"
  const problems = Object.keys(quoteProblems(quoted, quote)).length
  return problems === 0 ? null : `${problems} ${problems === 1 ? "cell needs" : "cells need"} attention`
}

/** The run asked for last: an answer for an earlier one is nobody's. */
let generation = 0

/** The writes under way (a save, an accept, a delete), which a project save waits for. */
const writing = new Set<Promise<unknown>>()

/** *work* tracked while it is under way, so `flush` settles it first. */
function tracked<T>(work: () => Promise<T>): Promise<T> {
  const running = work()
  const settled = running.then(() => undefined, () => undefined)
  writing.add(settled)
  void settled.then(() => writing.delete(settled))
  return running
}

/** Report a write's capture as a save's is; whether the ledger has yet to take it. */
function uncapturedAfter(written: WorkbenchTestQuoteWriteResponse): boolean {
  reportSaveCapture(written)
  return written.identity_required || (written.git_sha === null && written.warnings.length > 0)
}

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
  uncaptured: false,
  // The panel lists the quotes when it opens.
  setOpen: (open) => set({ open }),
  load: async () => {
    set({ loading: true })
    try {
      const { quotes } = await fetchTestQuotes()
      const ids = new Set(quotes.map((quote) => quote.id))
      set((s) => ({ quotes, error: null, selected: s.selected.filter((id) => ids.has(id)) }))
    } catch (error: unknown) {
      set({ error: apiErrorMessage(error) })
    } finally {
      set({ loading: false })
    }
  },
  save: (name) => tracked(async () => {
    const { form } = useWorkbenchFormStore.getState()
    const { quote } = useWorkbenchPreviewStore.getState()
    if (form === null || get().saving) return false
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
      const { tables } = await priceForm({ ...form, sample: quote }, fixedPricer())
      if (useDocumentStatusStore.getState().executionGeneration !== document) {
        set({ error: "Not saved: the branch changed while the quote was priced. Save it again on this one." })
        return false
      }
      const written = await saveTestQuote(name, form, quote, tables as PricedTables, branch)
      set({ uncaptured: uncapturedAfter(written) })
      await get().load()
      return true
    } catch (error: unknown) {
      set({ error: `Not saved: ${apiErrorMessage(error)}` })
      return false
    } finally {
      set({ saving: false })
    }
  }),
  openQuote: async (id) => {
    const { form } = useWorkbenchFormStore.getState()
    if (form === null) return
    try {
      const opened = await openTestQuote(id, form)
      const preview = useWorkbenchPreviewStore.getState()
      preview.clear()
      preview.setRows(opened.values)
      set({
        error: null,
        notice: opened.dropped.length === 0
          ? null
          : `Opened '${opened.name}'. No place on the sheets for: ${opened.dropped.join(", ")}.`,
      })
    } catch (error: unknown) {
      set({ error: apiErrorMessage(error) })
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
    const mine = ++generation
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
      if (generation !== mine) return
      const done = (patch: Partial<QuoteRun>) => {
        if (generation === mine) set((s) => ({ runs: { ...s.runs, [id]: { ...s.runs[id], ...patch } } }))
      }
      try {
        const opened = await openTestQuote(id, form)
        done({ number: opened.number, digest: opened.revision_digest, values: opened.values, dropped: opened.dropped })
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
    if (generation === mine) set({ running: false })
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
    const mine = generation
    set({ accepting: true })
    try {
      const written = await tracked(() => acceptTestQuote(id, number, digest, runForm, values, tables))
      set({ uncaptured: uncapturedAfter(written) })
      // A run started since, or forgotten since, holds its own result for the quote.
      if (generation === mine) {
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
        error: message.includes("stale_quote_revision")
          ? "The quote was saved since the run, or differently on this branch: run it again before accepting."
          : `Not accepted: ${message}`,
      })
    } finally {
      set({ accepting: false })
    }
  },
  remove: async (id) => {
    try {
      const written = await tracked(() => deleteTestQuote(id))
      set({ uncaptured: uncapturedAfter(written) })
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
    generation += 1
    set({ runs: {}, runForm: null, runBasis: null, running: false })
    if (get().open) void get().load()
  },
  flush: async () => {
    // A write under way lands, or fails, first: its capture is the one to take.
    await Promise.all(writing)
    if (!get().uncaptured) return
    try {
      set({ uncaptured: uncapturedAfter(await captureTestQuotes()) })
    } catch (error: unknown) {
      set({ error: `The test quotes were not captured: ${apiErrorMessage(error)}` })
    }
  },
}))

// The test quotes follow the pipeline's document: a run read on the branch before an
// adoption (a branch switched by hand, a change on disk) is nobody's now.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useWorkbenchTestQuotesStore.getState().forget()
})

export default useWorkbenchTestQuotesStore
