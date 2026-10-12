/**
 * Zustand store for the test quotes an analyst keeps in Preview (specs/workbench): the
 * project's test quotes, the panel that lists them, and the last run's results.
 *
 * A save prices the quote on the sheets in full, through a pricer fixed on the pipeline as
 * it is, and keeps it with its values and the form as the view holds it. A run fixes the
 * pipeline, the data source and the form when it starts and prices each test quote on them,
 * one request each, its values carried onto that form, then asks the server what changed
 * since the revision it read; a change to any of the three afterwards makes the run stale.
 * Accepting a change saves the run's response after that revision.
 */
import { create } from "zustand"
import {
  acceptTestQuote,
  compareTestQuote,
  deleteTestQuote,
  fetchTestQuotes,
  openTestQuote,
  saveTestQuote,
  type PricedTables,
  type SheetQuote,
  type TestQuoteChange,
} from "../api/workbench"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchTestQuoteSummary } from "../api/types"
import { quoteFilled, quoteProblems } from "../utils/sheetValues"
import { quotedSchema, sheetVisibility } from "../utils/sheetVisibility"
import useGraphStore from "./useGraphStore"
import useSettingsStore from "./useSettingsStore"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchPreviewStore from "./useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { priceForm, type Pricer } from "./useWorkbenchPricingStore"

export type RunStatus = "running" | "unchanged" | "changed" | "failed"

/** A test quote as the last run left it. */
export interface QuoteRun {
  status: RunStatus
  /** The revision the run read, which an accept follows. */
  number: number | null
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
}

/** What a run of the form as it is now would price on. */
export function currentBasis(form: FormSpec): RunBasis {
  return {
    pipeline: useGraphStore.getState().structuralFingerprint,
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

const runOf = (patch: Partial<QuoteRun>): QuoteRun => ({
  status: "running",
  number: null,
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
  save: async (name) => {
    const { form } = useWorkbenchFormStore.getState()
    const { quote } = useWorkbenchPreviewStore.getState()
    if (form === null || get().saving) return false
    const refusal = quoteRefusal(form, quote)
    if (refusal !== null) {
      set({ error: refusal })
      return false
    }
    set({ saving: true, error: null, notice: null })
    try {
      const { tables } = await priceForm({ ...form, sample: quote }, fixedPricer())
      await saveTestQuote(name, form, quote, tables as PricedTables)
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
    if (form === null || get().running) return
    let pricer: Pricer
    try {
      pricer = fixedPricer()
    } catch (error: unknown) {
      set({ error: apiErrorMessage(error) })
      return
    }
    const { quotes, selected } = get()
    const ids = selected.length > 0 ? selected : quotes.map((quote) => quote.id)
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
      const done = (patch: Partial<QuoteRun>) =>
        set((s) => ({ runs: { ...s.runs, [id]: { ...s.runs[id], ...patch } } }))
      try {
        const opened = await openTestQuote(id, form)
        done({ number: opened.number, values: opened.values, dropped: opened.dropped })
        const { tables } = await priceForm({ ...form, sample: opened.values }, pricer)
        const compared = await compareTestQuote(id, form, tables as PricedTables, opened.number)
        done({
          status: compared.changes.length === 0 ? "unchanged" : "changed",
          tables: tables as PricedTables,
          changes: compared.changes,
        })
      } catch (error: unknown) {
        done({ status: "failed", error: apiErrorMessage(error) })
      }
    }
    set({ running: false })
  },
  accept: async (id) => {
    const { runs, runForm } = get()
    const run = runs[id]
    if (run === undefined || runForm === null || run.number === null || run.values === null || run.tables === null) return
    try {
      await acceptTestQuote(id, run.number, runForm, run.values, run.tables)
      set((s) => ({
        error: null,
        runs: { ...s.runs, [id]: { ...run, status: "unchanged", changes: [], number: run.number === null ? null : run.number + 1 } },
      }))
      await get().load()
    } catch (error: unknown) {
      const message = apiErrorMessage(error)
      set({
        error: message.includes("stale_quote_revision")
          ? "The quote was saved since the run: run it again before accepting."
          : `Not accepted: ${message}`,
      })
    }
  },
  remove: async (id) => {
    try {
      await deleteTestQuote(id)
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
}))

export default useWorkbenchTestQuotesStore
