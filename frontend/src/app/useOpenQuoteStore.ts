/**
 * The quote open in the underwriter app (specs/workbench): a saved quote at its newest
 * revision or an earlier one, or a new quote not yet saved, perhaps made from another's
 * revision; its values on the sheets through Preview's store, read only while the quote may
 * not change; its saves and changes, each over the version it was opened at, a change's
 * details taken onto the sheets; and what a save or change that could not be made offers
 * instead.
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
import { basisOf, clearDraft, draftKey, operationKey, readDraft, savedPrice, valuesKey, type Draft, type Values } from "./quoteValues"
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
  return { ...values, [QUOTE_DETAILS_ID]: [row] }
}

/** A page drawn for another release: 409 without a code of the store's. */
const releaseMoved = (error: unknown): boolean => apiErrorStatus(error) === 409 && apiErrorCode(error) === null

/** The open quote's loads, newest wins. */
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
    set({ ...state, revision: answer.revision, baseline: valuesKey(values) })
  }

  /** Offer back what this browser kept of the open quote, when it differs from what is saved. */
  const offerDraft = (): void => {
    const key = openDraftKey(get())
    const draft = key === null ? null : readDraft(key)
    set({ recovered: draft !== null && valuesKey(draft.values) !== get().baseline ? draft : null })
  }

  /** Values on the sheets as a save sends them, or null when there is nothing to save. */
  const saveBody = (): { body: QuoteSave; basis: string; values: Values } | null => {
    const { form, revision, release } = useAppStore.getState()
    if (form === null || release === null) return null
    const preview = useWorkbenchPreviewStore.getState()
    const values = preview.quote
    // The details the app sets are not typing.
    if (!quoteFilled(quotedSchema(form.schema, preview.locked), values)) return null
    const price = preview.price
    const shown = price !== null && price.basis === basisOf(form, values) ? (price.tables as QuoteSave["shown"]) : null
    const basis = `${valuesKey(values)}|${get().quote?.version ?? "new"}|${shown === null ? "draft" : canonicalJson(shown)}`
    const pending = get().pending
    const operation = pending !== null && pending.basis === basis ? pending.key : operationKey()
    return { body: { operation, form_revision: revision, release: release.id, values, shown }, basis, values }
  }

  /** A save's answer adopted: the quote as saved, and its values and price on the sheets unless typed over since. */
  const saved = (answer: WorkbenchAppQuoteResponse, sent: Values): void => {
    const draft = openDraftKey(get())
    if (draft !== null) clearDraft(draft)
    const { form } = useAppStore.getState()
    const preview = useWorkbenchPreviewStore.getState()
    const values = answer.revision.values as Values
    if (form !== null && valuesKey(preview.quote) === valuesKey(sent)) {
      preview.open(values, savedPrice(form, values, answer.revision.tables, answer.revision.request), false)
    }
    set({
      quote: answer.quote,
      revision: answer.revision,
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
    if (get().historyOpen) void get().loadHistory()
  }

  /**
   * The revision a change wrote adopted: the details it set on the sheets in place of those
   * there, what was typed kept, and a price shown for the values kept current, as the change
   * kept the revision's prices.
   */
  const adopt = (written: WorkbenchAppQuoteRevision): void => {
    const { form } = useAppStore.getState()
    if (form === null || get().past || get().olderSheets !== null) return
    const preview = useWorkbenchPreviewStore.getState()
    const saved = written.values as Values
    const values = withAppDetails(preview.quote, saved)
    const { price } = preview
    const current = price !== null && price.basis === basisOf(form, preview.quote)
    useWorkbenchPreviewStore.setState({ quote: values, price: current ? { ...price, basis: basisOf(form, values) } : price })
    set({ revision: written, baseline: valuesKey(saved) })
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
      const { quote, source, saving } = get()
      if (saving || readOnlyReason(get()) !== null) return null
      const prepared = saveBody()
      if (prepared === null) return null
      set({ saving: true, error: null, moved: false, pending: { key: prepared.body.operation, basis: prepared.basis } })
      try {
        const answer =
          quote === null
            ? await createAppQuote(
                prepared.body,
                source === null ? null : { quote_id: source.quoteId, revision: source.revision, relation: source.relation },
              )
            : await saveAppQuote(quote.id, prepared.body, quote.version)
        saved(answer, prepared.values)
        return answer.quote
      } catch (error: unknown) {
        failed(error)
        return null
      } finally {
        set({ saving: false })
      }
    },

    saveAsNew: async () => {
      const { conflict, quote } = get()
      const origin = conflict?.latest ?? quote
      if (origin === null || !canEdit()) return null
      const prepared = saveBody()
      if (prepared === null) return null
      set({ saving: true, error: null })
      try {
        const answer = await createAppQuote(prepared.body, { quote_id: origin.id, revision: origin.latest, relation: "duplicate" })
        saved(answer, prepared.values)
        return answer.quote
      } catch (error: unknown) {
        failed(error)
        return null
      } finally {
        set({ saving: false })
      }
    },

    change: async (change, fields = {}) => {
      const { quote, changing } = get()
      if (quote === null || changing || !canEdit()) return null
      set({ changing: true, error: null })
      try {
        const { quote: changed, revision: written } = await changeAppQuote(quote.id, change, quote.version, fields)
        set({ quote: changed })
        if (written !== null && written !== undefined) adopt(written)
        if (!get().past && get().olderSheets === null) {
          useWorkbenchPreviewStore.getState().setReadOnly(readOnlyReason(get()) !== null)
        }
        if (get().historyOpen) void get().loadHistory()
        return changed
      } catch (error: unknown) {
        failed(error)
        return null
      } finally {
        set({ changing: false })
      }
    },

    moveToCurrentSheets: async () => {
      const { quote, revision, olderSheets } = get()
      const { form } = useAppStore.getState()
      if (quote === null || revision === null || olderSheets === null || form === null) return
      try {
        const carried = await carryAppQuote(quote.id, revision.number)
        installSheets(form)
        set({ olderSheets: null, past: false, dropped: carried.dropped })
        useWorkbenchPreviewStore.getState().open(carried.values as Values, null, readOnlyReason(get()) !== null)
      } catch (error: unknown) {
        failed(error)
      }
    },

    restoreDraft: () => {
      const { recovered } = get()
      if (recovered === null) return
      useWorkbenchPreviewStore.getState().open(recovered.values, null, false)
      set({ recovered: null })
    },

    discardDraft: () => {
      const key = openDraftKey(get())
      if (key !== null) clearDraft(key)
      set({ recovered: null })
    },

    loadHistory: async () => {
      const { quote } = get()
      if (quote === null) return
      try {
        const history = await fetchAppQuoteHistory(quote.id)
        if (get().quote?.id === quote.id) set({ history })
      } catch (error: unknown) {
        set({ error: `The quote's history could not be read: ${apiErrorMessage(error)}` })
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
