/**
 * The quote open in the underwriter app (specs/workbench): a saved quote at its newest
 * revision or an earlier one, or a new quote not yet saved, perhaps made from another's
 * revision; its values on the sheets through Preview's store, read only while the quote may
 * not change; its saves and changes, each over the version the values were opened or saved
 * at (`base`), a change's details taken onto the sheets, and each answer the open quote's
 * alone, dropped when another was opened meanwhile; the values kept in this browser while
 * they are not saved; and what a save or change that could not be made offers instead.
 */
import { create } from "zustand"
import {
  carryAppQuote,
  changeAppQuote,
  createAppQuote,
  fetchAppQuoteHistory,
  openAppQuote,
  saveAppQuote,
  type QuoteChange,
  type QuoteRelation,
  type QuoteSave,
} from "../api/app"
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from "../api/errors"
import type { FormSpec, WorkbenchAppQuote, WorkbenchAppQuoteHistoryResponse, WorkbenchAppQuoteResponse, WorkbenchAppQuoteRevision } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import { canonicalJson } from "../utils/canonicalJson"
import { quoteFilled } from "../utils/sheetValues"
import { quotedSchema } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, type SampleRow } from "../utils/workbenchForm"
import { basisOf, clearDraft, draftKey, onSheets, operationKey, readDraft, savedPrice, valuesKey, writeDraft, type Draft, type Values } from "./quoteValues"
import useAppStore from "./useAppStore"

/** A new quote's origin: the quote and revision it is made from, and how. */
export interface QuoteSource {
  quoteId: string
  revision: number
  relation: QuoteRelation
  reference: string
}

/** Why the quote's values may not change, or null when they may. */
export type ReadOnlyReason = "final" | "archived" | "past" | "older-sheets" | "reader" | null

interface OpenQuoteState {
  status: "idle" | "loading" | "ready" | "error"
  loadError: string | null
  /** The saved quote; null for a new one. */
  quote: WorkbenchAppQuote | null
  /** The revision on the sheets; null for a new quote. */
  revision: WorkbenchAppQuoteRevision | null
  /**
   * The quote's version the values on the sheets are edited over, which a save and a change
   * name: the newest revision as it was opened, saved or written by a change made here.
   * Anything else that refreshes the quote, a note, leaves it, so a save over a quote moved
   * on meanwhile is refused rather than written over it. Null for a new quote, or one shown
   * at an earlier revision.
   */
  base: number | null
  /** The revision on the sheets is not the newest. */
  past: boolean
  /** The sheets the revision was keyed into, installed in place of the release's. */
  olderSheets: FormSpec | null
  /** What a new quote is made from. */
  source: QuoteSource | null
  /** What the values were when they were opened or last saved. */
  baseline: string
  saving: boolean
  /** A change other than a save is being made. */
  changing: boolean
  /** Why the last save or change failed, when no other notice says. */
  error: string | null
  /** The quote moved on since it was opened: who moved it, and the quote as it is now. */
  conflict: { message: string; latest: WorkbenchAppQuote | null } | null
  /** The page was drawn for another release. */
  releaseChanged: boolean
  /** The last save's price, made again on the server, was not the one the sheets showed. */
  moved: boolean
  /** Each value that has no place on the current sheets, `table.column`. */
  dropped: string[]
  /** Values kept in the browser, not saved, offered back. */
  recovered: Draft | null
  /** The last save attempt's key, and what it saved: a retry of the same keeps the key. */
  pending: { key: string; basis: string } | null
  history: WorkbenchAppQuoteHistoryResponse | null
  historyOpen: boolean
  openNew: (from: { quoteId: string; revision: number; relation: QuoteRelation } | null) => Promise<void>
  openQuote: (id: string, revision: number | null) => Promise<void>
  save: () => Promise<WorkbenchAppQuote | null>
  /** Save the values on the sheets as a new quote made from the quote they were opened as. */
  saveAsNew: () => Promise<WorkbenchAppQuote | null>
  change: (change: QuoteChange, fields?: Record<string, string | number>) => Promise<WorkbenchAppQuote | null>
  /**
   * Keep the values on the sheets in this browser while they are not saved, and drop what it
   * kept once they are; false when the browser would not keep them.
   */
  keepDraft: () => boolean
  /** Leave the quote: what is typed kept in this browser, and any answer still to come dropped. */
  close: () => void
  moveToCurrentSheets: () => Promise<void>
  restoreDraft: () => void
  discardDraft: () => void
  loadHistory: () => Promise<void>
  setHistoryOpen: (open: boolean) => void
  dismiss: (notice: "error" | "conflict" | "moved" | "dropped") => void
}

type Closed = Omit<OpenQuoteState, "status" | "loadError" | "historyOpen" | { [K in keyof OpenQuoteState]: OpenQuoteState[K] extends (...args: never[]) => unknown ? K : never }[keyof OpenQuoteState]>

/** Nothing open: what each open starts from. */
const CLOSED: Closed = {
  quote: null,
  revision: null,
  base: null,
  past: false,
  olderSheets: null,
  source: null,
  baseline: valuesKey({}),
  saving: false,
  changing: false,
  error: null,
  conflict: null,
  releaseChanged: false,
  moved: false,
  dropped: [],
  recovered: null,
  pending: null,
  history: null,
}

/** Why `quote` at this revision may not change. */
export function readOnlyReason(state: Pick<OpenQuoteState, "quote" | "past" | "olderSheets">): ReadOnlyReason {
  if (state.past) return "past"
  if (state.olderSheets !== null) return "older-sheets"
  if (state.quote?.archived) return "archived"
  if (state.quote?.stage === "final") return "final"
  if (!canEdit()) return "reader"
  return null
}

/** Whoever is signed in may change quotes: false for one who may only read them. */
export const canEdit = (): boolean => useAppStore.getState().release?.can_edit ?? true

/** The open quote's draft key in this browser. */
export function openDraftKey(state: Pick<OpenQuoteState, "quote" | "source">): string | null {
  const { release } = useAppStore.getState()
  if (release === null) return null
  const which = state.quote?.id ?? (state.source === null ? "new" : `new-from-${state.source.quoteId}-${state.source.revision}`)
  return draftKey(release.pipeline_name || "app", release.principal, which)
}

/** Put `form` on the sheets, the release's or a quote's older ones. */
function installSheets(form: FormSpec): void {
  if (useWorkbenchFormStore.getState().form === form) return
  useWorkbenchFormStore.setState({ form, savedForm: canonicalJson(form), dirty: false, undoStack: [], redoStack: [] })
}

/** The quote as a 409 refusal carries it, when it does. */
function conflictQuote(error: unknown): WorkbenchAppQuote | null {
  const raw = typeof error === "object" && error !== null && "rawDetail" in error ? (error as { rawDetail?: unknown }).rawDetail : null
  const quote = typeof raw === "object" && raw !== null && "quote" in raw ? (raw as { quote?: unknown }).quote : null
  return typeof quote === "object" && quote !== null && "id" in quote ? (quote as WorkbenchAppQuote) : null
}

/** `values` with the details the app sets taken from `saved`, the rest as typed. */
function withAppDetails(values: Values, saved: Values): Values {
  const pick = (row: SampleRow | undefined, set: boolean): SampleRow =>
    Object.fromEntries(Object.entries(row ?? {}).filter(([id]) => QUOTE_DETAILS_MANAGED.has(id) === set))
  const row = { ...pick(values[QUOTE_DETAILS_ID]?.[0], false), ...pick(saved[QUOTE_DETAILS_ID]?.[0], true) }
  const others = Object.fromEntries(Object.entries(values).filter(([table]) => table !== QUOTE_DETAILS_ID))
  return Object.keys(row).length === 0 ? others : { ...others, [QUOTE_DETAILS_ID]: [row] }
}

/**
 * The details the app set in `saved` put on the sheets in place of those there, what was
 * typed kept, and a price shown for the values kept current, as the server kept the price.
 */
function takeAppDetails(form: FormSpec, saved: Values): void {
  const preview = useWorkbenchPreviewStore.getState()
  const values = withAppDetails(preview.quote, saved)
  const { price } = preview
  const current = price !== null && price.basis === basisOf(form, preview.quote)
  useWorkbenchPreviewStore.setState({ quote: values, price: current ? { ...price, basis: basisOf(form, values) } : price })
}

/** Drop the draft kept under `key` when it holds `sent`, a save's values, which landed. */
function forgetSaved(key: string | null, sent: Values): void {
  const draft = key === null ? null : readDraft(key)
  if (key !== null && draft !== null && valuesKey(draft.values) === valuesKey(sent)) clearDraft(key)
}

/** A page drawn for another release: 409 without a code of the store's. */
const releaseMoved = (error: unknown): boolean => apiErrorStatus(error) === 409 && apiErrorCode(error) === null

/**
 * The open quote's loads, newest wins: a save, a change or a read started under one load
 * answers that load alone, and its answer is dropped once another has started.
 */
let opening = 0

const useOpenQuoteStore = create<OpenQuoteState>()((set, get) => {
  /** Read what failed into the notice it calls for. */
  const failed = (error: unknown): void => {
    if (apiErrorCode(error) === "stale_quote_revision") {
      set({ conflict: { message: apiErrorMessage(error), latest: conflictQuote(error) }, pending: null })
    } else if (releaseMoved(error)) {
      set({ releaseChanged: true, pending: null })
    } else {
      // A save that may have reached the server keeps its key, so trying again writes once.
      const reached = apiErrorStatus(error) !== null
      set((s) => ({ error: apiErrorMessage(error), pending: reached ? null : s.pending }))
    }
  }

  /** Show `answer`'s revision on the sheets: its values, its price, read only as the quote is. */
  const show = (answer: WorkbenchAppQuoteResponse, past: boolean): void => {
    const { form } = useAppStore.getState()
    if (form === null) return
    const sheets = answer.form ?? form
    installSheets(sheets)
    const values = answer.revision.values as Values
    const olderSheets = answer.form ?? null
    const state = { quote: answer.quote, past, olderSheets }
    useWorkbenchPreviewStore
      .getState()
      .open(values, savedPrice(sheets, values, answer.revision.tables, answer.revision.request), readOnlyReason(state) !== null)
    set({ ...state, revision: answer.revision, base: past ? null : answer.revision.number, baseline: valuesKey(values) })
  }

  /**
   * Offer back what this browser kept of the open quote, when it differs from what is saved
   * and the quote may change as it is shown.
   */
  const offerDraft = (): void => {
    const key = openDraftKey(get())
    const draft = key === null || readOnlyReason(get()) !== null ? null : readDraft(key)
    set({ recovered: draft !== null && valuesKey(draft.values) !== get().baseline ? draft : null })
  }

  const keepDraft = (): boolean => {
    const key = openDraftKey(get())
    // Only a quote open, and as it may change, has values of its own to keep.
    if (key === null || get().status !== "ready" || readOnlyReason(get()) !== null) return true
    const values = useWorkbenchPreviewStore.getState().quote
    if (valuesKey(values) === get().baseline) {
      // A draft offered back and not yet answered stays offered.
      if (get().recovered === null) clearDraft(key)
      return true
    }
    return writeDraft(key, {
      values,
      version: get().base,
      latest: get().quote?.latest ?? null,
      formRevision: useAppStore.getState().revision,
      keptAt: new Date().toISOString(),
    })
  }

  /**
   * Values on the sheets as a save sends them, or null when there is nothing to save; `to`
   * says what they are saved as, so a retry of the same keeps the same operation key.
   */
  const saveBody = (to: string): { body: QuoteSave; basis: string; values: Values } | null => {
    const { form, revision, release } = useAppStore.getState()
    if (form === null || release === null) return null
    const preview = useWorkbenchPreviewStore.getState()
    const values = preview.quote
    // The details the app sets are not typing.
    if (!quoteFilled(quotedSchema(form.schema, preview.locked), values)) return null
    const price = preview.price
    const shown = price !== null && price.basis === basisOf(form, values) ? (price.tables as QuoteSave["shown"]) : null
    const basis = `${valuesKey(values)}|${to}|${shown === null ? "draft" : canonicalJson(shown)}`
    const pending = get().pending
    const operation = pending !== null && pending.basis === basis ? pending.key : operationKey()
    return { body: { operation, form_revision: revision, release: release.id, values, shown }, basis, values }
  }

  /**
   * A save's answer adopted: the quote as saved, its values and price on the sheets unless
   * typed over since, and what this browser keeps of them, the typing since kept again.
   */
  const saved = (answer: WorkbenchAppQuoteResponse, sent: Values): void => {
    forgetSaved(openDraftKey(get()), sent)
    const { form } = useAppStore.getState()
    const preview = useWorkbenchPreviewStore.getState()
    const values = answer.revision.values as Values
    if (form !== null && valuesKey(preview.quote) === valuesKey(sent)) {
      preview.open(values, savedPrice(form, values, answer.revision.tables, answer.revision.request), false)
    } else if (form !== null) {
      // Typed over since: what was typed stays, and the details the app set reach the sheets.
      takeAppDetails(form, values)
    }
    set({
      quote: answer.quote,
      revision: answer.revision,
      base: answer.revision.number,
      past: false,
      olderSheets: null,
      source: null,
      baseline: valuesKey(values),
      moved: answer.moved,
      conflict: null,
      error: null,
      dropped: [],
      pending: null,
      recovered: null,
      history: null,
    })
    keepDraft()
    if (get().historyOpen) void get().loadHistory()
  }

  /**
   * The revision a change wrote adopted: the details it set on the sheets in place of those
   * there, what was typed kept, and a price shown for the values kept current, as the change
   * kept the revision's prices. A change made from an earlier revision, or on earlier sheets,
   * leaves the sheets as they are.
   */
  const adopt = (written: WorkbenchAppQuoteRevision): void => {
    const { form } = useAppStore.getState()
    if (form === null || get().base === null || get().olderSheets !== null) return
    const saved = written.values as Values
    takeAppDetails(form, saved)
    set({ revision: written, base: written.number, baseline: valuesKey(saved) })
  }

  return {
    status: "idle",
    loadError: null,
    historyOpen: false,
    ...CLOSED,

    openNew: async (from) => {
      const attempt = ++opening
      const { form } = useAppStore.getState()
      if (form === null) return
      installSheets(form)
      useWorkbenchPreviewStore.getState().open({}, null, !canEdit())
      set({ ...CLOSED, status: from === null ? "ready" : "loading", loadError: null })
      if (from === null) {
        offerDraft()
        return
      }
      try {
        const [origin, carried] = await Promise.all([openAppQuote(from.quoteId, from.revision), carryAppQuote(from.quoteId, from.revision)])
        if (attempt !== opening) return
        useWorkbenchPreviewStore.getState().open(carried.values as Values, null, !canEdit())
        set({
          status: "ready",
          source: { ...from, reference: origin.quote.reference },
          dropped: carried.dropped,
        })
        offerDraft()
      } catch (error: unknown) {
        if (attempt === opening) set({ status: "error", loadError: apiErrorMessage(error) })
      }
    },

    openQuote: async (id, revision) => {
      const attempt = ++opening
      // Until it is read, the sheets hold nothing of the quote before and take no typing.
      useWorkbenchPreviewStore.getState().open({}, null, true)
      set({ ...CLOSED, status: "loading", loadError: null })
      try {
        const answer = await openAppQuote(id, revision)
        if (attempt !== opening) return
        show(answer, revision !== null && revision !== answer.quote.latest)
        set({ status: "ready" })
        if (!get().past) offerDraft()
        if (get().historyOpen) void get().loadHistory()
      } catch (error: unknown) {
        if (attempt === opening) set({ status: "error", loadError: apiErrorMessage(error) })
      }
    },

    save: async () => {
      const { quote, source, saving, base, status } = get()
      if (status !== "ready" || saving || readOnlyReason(get()) !== null || (quote !== null && base === null)) return null
      const prepared = saveBody(String(base ?? "new"))
      if (prepared === null) return null
      const session = opening
      const key = openDraftKey(get())
      set({ saving: true, error: null, moved: false, pending: { key: prepared.body.operation, basis: prepared.basis } })
      try {
        const answer =
          quote === null || base === null
            ? await createAppQuote(
                prepared.body,
                source === null ? null : { quote_id: source.quoteId, revision: source.revision, relation: source.relation },
              )
            : await saveAppQuote(quote.id, prepared.body, base)
        if (session !== opening) {
          forgetSaved(key, prepared.values)
          return null
        }
        saved(answer, prepared.values)
        return answer.quote
      } catch (error: unknown) {
        if (session === opening) failed(error)
        return null
      } finally {
        if (session === opening) set({ saving: false })
      }
    },

    saveAsNew: async () => {
      const { conflict, quote, status, saving } = get()
      const origin = conflict?.latest ?? quote
      if (origin === null || status !== "ready" || saving || !canEdit()) return null
      const prepared = saveBody(`new-from:${origin.id}:${origin.latest}`)
      if (prepared === null) return null
      const session = opening
      const key = openDraftKey(get())
      set({ saving: true, error: null, pending: { key: prepared.body.operation, basis: prepared.basis } })
      try {
        const answer = await createAppQuote(prepared.body, { quote_id: origin.id, revision: origin.latest, relation: "duplicate" })
        if (session !== opening) {
          forgetSaved(key, prepared.values)
          return null
        }
        saved(answer, prepared.values)
        return answer.quote
      } catch (error: unknown) {
        if (session === opening) failed(error)
        return null
      } finally {
        if (session === opening) set({ saving: false })
      }
    },

    change: async (change, fields = {}) => {
      const { quote, changing, base } = get()
      if (quote === null || changing || !canEdit()) return null
      const session = opening
      set({ changing: true, error: null })
      try {
        const { quote: changed, revision: written } = await changeAppQuote(quote.id, change, base ?? quote.version, fields)
        if (session !== opening) return changed
        // An archive leaves the quote read only: what was typed, before the change or while
        // it was made, is kept in this browser while the quote may still change.
        keepDraft()
        const wasReadOnly = readOnlyReason(get()) !== null
        set({ quote: changed })
        if (written !== null && written !== undefined) adopt(written)
        if (!get().past && get().olderSheets === null) {
          useWorkbenchPreviewStore.getState().setReadOnly(readOnlyReason(get()) !== null)
        }
        // Restored or reopened, it may change again: what this browser kept of it is offered back.
        if (wasReadOnly && readOnlyReason(get()) === null) offerDraft()
        if (get().historyOpen) void get().loadHistory()
        return changed
      } catch (error: unknown) {
        if (session === opening) failed(error)
        return null
      } finally {
        if (session === opening) set({ changing: false })
      }
    },

    keepDraft,

    close: () => {
      keepDraft()
      opening += 1
      set({ ...CLOSED, status: "idle", loadError: null })
    },

    moveToCurrentSheets: async () => {
      const { quote, revision, olderSheets } = get()
      const { form } = useAppStore.getState()
      if (quote === null || revision === null || olderSheets === null || form === null) return
      const session = opening
      try {
        const carried = await carryAppQuote(quote.id, revision.number)
        if (session !== opening) return
        installSheets(form)
        set({ olderSheets: null, past: false, dropped: carried.dropped })
        useWorkbenchPreviewStore.getState().open(carried.values as Values, null, readOnlyReason(get()) !== null)
      } catch (error: unknown) {
        if (session === opening) failed(error)
      }
    },

    restoreDraft: () => {
      const { recovered } = get()
      const { form } = useAppStore.getState()
      if (recovered === null || form === null) return
      set({ recovered: null })
      // Nothing typed is put back on a quote that may not change as it is shown.
      if (readOnlyReason(get()) !== null) return
      // Kept on these sheets alone, the details the app sets as the quote has them now.
      const values = withAppDetails(onSheets(form, recovered.values), useWorkbenchPreviewStore.getState().quote)
      useWorkbenchPreviewStore.getState().open(values, null, false)
    },

    discardDraft: () => {
      const key = openDraftKey(get())
      if (key !== null) clearDraft(key)
      set({ recovered: null })
    },

    loadHistory: async () => {
      const { quote } = get()
      if (quote === null) return
      const session = opening
      try {
        const history = await fetchAppQuoteHistory(quote.id)
        if (session === opening && get().quote?.id === quote.id) set({ history })
      } catch (error: unknown) {
        if (session === opening) set({ error: `The quote's history could not be read: ${apiErrorMessage(error)}` })
      }
    },

    setHistoryOpen: (open) => {
      set({ historyOpen: open })
      if (open) void get().loadHistory()
    },

    dismiss: (notice) => {
      if (notice === "error") set({ error: null })
      else if (notice === "conflict") set({ conflict: null })
      else if (notice === "moved") set({ moved: false })
      else set({ dropped: [] })
    },
  }
})

export default useOpenQuoteStore
