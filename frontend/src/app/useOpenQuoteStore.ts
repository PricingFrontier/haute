/**
 * The quote open in the underwriter app (specs/workbench): a saved quote at its newest
 * revision or an earlier one, or a new quote not yet saved, perhaps made from another's
 * revision; its values on the sheets through Preview's store, read only while the quote may
 * not change; its saves and changes, each over the version the values were opened or saved
 * at (`base`), a change's details taken onto the sheets, and each answer the open quote's
 * alone, dropped when another was opened meanwhile; the values kept in this browser while
 * they are not saved; and what a save or change that could not be made offers instead. A
 * renewal opens with `is_renewal` ticked.
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
  type QuoteChangeFields,
  type QuoteRelation,
} from "../api/app"
import { apiErrorMessage, apiErrorStatus, isStaleRevision } from "../api/errors"
import type { FormSpec, WorkbenchAppQuote, WorkbenchAppQuoteHistoryResponse, WorkbenchAppQuoteResponse, WorkbenchAppQuoteRevision } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import { newQuoteValues } from "../utils/sheetValues"
import { quoteEligible } from "../utils/sheetVisibility"
import { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, type SampleRow } from "../utils/workbenchForm"
import { latest } from "../utils/latest"
import { basisOf, cellPlaces, clearDraft, draftKey, lostPlaces, onSheets, operationKey, readDraft, releaseMoved, savedPrice, valuesKey, writeDraft, type Draft, type PendingWrite, type QuoteWrite, type Values } from "./quoteValues"
import useAppStore from "./useAppStore"

/** A new quote's origin: the quote and revision it is made from, and how. */
export interface QuoteSource {
  quoteId: string
  revision: number
  relation: QuoteRelation
  reference: string
}

/** A renewal's values: those carried from its source, with `is_renewal` ticked in its details. */
function asRenewal(values: Values): Values {
  const [details = {}] = values[QUOTE_DETAILS_ID] ?? []
  return { ...values, [QUOTE_DETAILS_ID]: [{ ...details, qd_is_renewal: true }] }
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
  /** The last save's price, made again on the server, was not the one the sheets showed. */
  moved: boolean
  /** Each value that has no place on the current sheets, `table.column`. */
  dropped: string[]
  /** Values kept in the browser, not saved, offered back. */
  recovered: Draft | null
  /**
   * The write a save sent and has no answer to, or a server failure for: it may have landed,
   * so it is held, and repeated exactly, before anything else is saved.
   */
  pending: PendingWrite | null
  history: WorkbenchAppQuoteHistoryResponse | null
  historyOpen: boolean
  openNew: (from: { quoteId: string; revision: number; relation: QuoteRelation } | null) => Promise<void>
  openQuote: (id: string, revision: number | null) => Promise<void>
  /** Save the values on the sheets, once their pricing settles, as the quote's next revision or a new quote. */
  save: () => Promise<WorkbenchAppQuote | null>
  /** Save the values on the sheets as a new quote made from the quote they were opened as. */
  saveAsNew: () => Promise<WorkbenchAppQuote | null>
  /**
   * Make `change` to the open quote over the version its values are edited over. Given
   * `asked`, the quote the change was asked about (a question may be answered after the page
   * moved on), it is made only while that quote is the one open, and refused saying so otherwise.
   */
  change: <C extends QuoteChange>(change: C, fields: QuoteChangeFields<C>, asked?: WorkbenchAppQuote) => Promise<WorkbenchAppQuote | null>
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

/** A save or a change is being made: no other is started, and the controls that make one are off. */
export const busy = (state: Pick<OpenQuoteState, "saving" | "changing">): boolean => state.saving || state.changing

/** Whoever is signed in may change quotes: false for one who may only read them. */
export const canEdit = (): boolean => useAppStore.getState().release?.can_edit ?? true

/**
 * Which new quote the open one is: a plain one, or one made from a quote's revision as a
 * duplicate, a renewal or an alternative, each a quote of its own, so neither its draft nor
 * a write held after a lost answer is taken for another's.
 */
const newQuoteName = (source: QuoteSource | null): string =>
  source === null ? "new" : `new-from-${source.quoteId}-${source.revision}-${source.relation}`

/** The open quote's draft key in this browser. */
export function openDraftKey(state: Pick<OpenQuoteState, "quote" | "source">): string | null {
  const { release } = useAppStore.getState()
  if (release === null) return null
  return draftKey(release.pipeline_name || "app", release.principal, state.quote?.id ?? newQuoteName(state.source))
}

/** Put `form` on the sheets, the release's or a quote's older ones. */
function installSheets(form: FormSpec): void {
  const forms = useWorkbenchFormStore.getState()
  if (forms.form !== form) forms.install(form, forms.revision)
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

/** The server refused what was sent, a status below 500, so nothing of it was written. */
const refused = (error: unknown): boolean => {
  const status = apiErrorStatus(error)
  return status !== null && status < 500
}

/** Send `write`. */
function sendWrite(write: QuoteWrite): Promise<WorkbenchAppQuoteResponse> {
  return write.kind === "create" ? createAppQuote(write.body, write.source) : saveAppQuote(write.quoteId, write.body, write.version)
}

/**
 * The open quote's loads, newest wins: a save, a change or a read started under one load
 * answers that load alone, and its answer is dropped once another has started.
 */
const loads = latest()
/** The history asked for last. */
const historyAsked = latest()

const useOpenQuoteStore = create<OpenQuoteState>()((set, get) => {
  /** Read what failed into the notice it calls for. */
  const failed = (error: unknown): void => {
    if (isStaleRevision(error)) {
      set({ conflict: { message: apiErrorMessage(error), latest: conflictQuote(error) } })
    } else if (releaseMoved(error)) {
      useAppStore.getState().noteReleaseChanged()
    } else {
      set({ error: apiErrorMessage(error) })
    }
  }

  /**
   * A write that failed, read as any failure: let go once refused, which wrote nothing; with
   * no answer, or a server failure, it may have landed, so it stays held, in the draft too.
   */
  const writeFailed = (error: unknown): void => {
    failed(error)
    if (refused(error)) set({ pending: null })
    keepDraft()
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
    const readOnly = readOnlyReason(state) !== null
    // A quote that may change shows this release's price: one priced on another release is
    // priced again as it opens, as an unpriced one is.
    const ownPrice = readOnly || answer.revision.release === useAppStore.getState().release?.id
    useWorkbenchPreviewStore
      .getState()
      .open(values, ownPrice ? savedPrice(sheets, values, answer.revision.tables, answer.revision.request) : null, readOnly)
    set({ ...state, revision: answer.revision, base: past ? null : answer.revision.number, baseline: valuesKey(values) })
  }

  /**
   * Offer back what this browser kept of the open quote, when it differs from what is saved,
   * or holds a write whose answer was lost, and the quote may change as it is shown.
   */
  const offerDraft = (): void => {
    const key = openDraftKey(get())
    const draft = key === null || readOnlyReason(get()) !== null ? null : readDraft(key)
    // Offered only when it differs from the sheet, values already there not offered back,
    // or when a write is held: restored, it is repeated, so it is written once.
    const shown = valuesKey(useWorkbenchPreviewStore.getState().quote)
    set({ recovered: draft !== null && (valuesKey(draft.values) !== shown || draft.pending !== null) ? draft : null })
  }

  const keepDraft = (): boolean => {
    const key = openDraftKey(get())
    // Only a quote open, and as it may change, has values of its own to keep.
    if (key === null || get().status !== "ready" || readOnlyReason(get()) !== null) return true
    const values = useWorkbenchPreviewStore.getState().quote
    // A draft offered back and not yet answered keeps the write it held.
    const pending = get().pending ?? get().recovered?.pending ?? null
    // Values as they opened keep nothing, unless a write of them is held: a quote made from
    // another and saved as it opened, its answer lost, is written once.
    if (valuesKey(values) === get().baseline && pending === null) {
      // A draft offered back and not yet answered stays offered.
      if (get().recovered === null) clearDraft(key)
      return true
    }
    const sheets = useWorkbenchFormStore.getState().form
    return writeDraft(key, {
      values,
      version: get().base,
      latest: get().quote?.latest ?? null,
      // Named on the sheets installed, the ones typed into.
      places: sheets === null ? {} : cellPlaces(sheets, values),
      keptAt: new Date().toISOString(),
      pending,
    })
  }

  /**
   * The values on the sheets as a save sends them, saved as `to` says, or null when there is
   * nothing to save. Its operation key is the held write's when that saves the same, its
   * `basis`: the values, what they are saved as, and priced or not (the server prices them
   * again), so a retry writes once.
   */
  const prepare = (to: string, write: (body: QuoteWrite["body"]) => QuoteWrite): PendingWrite | null => {
    const { form, revision, release } = useAppStore.getState()
    if (form === null || release === null) return null
    const preview = useWorkbenchPreviewStore.getState()
    const values = preview.quote
    // As the server counts typing: what the sheets show, the details the app sets and the defaults aside.
    if (!quoteEligible(form, values, preview.locked)) return null
    const price = preview.price
    const shown = price !== null && price.basis === basisOf(form, values) ? (price.tables as QuoteWrite["body"]["shown"]) : null
    const basis = `${valuesKey(values)}|${to}|${shown === null ? "draft" : "priced"}`
    const pending = get().pending
    const operation = pending !== null && pending.basis === basis ? pending.write.body.operation : operationKey()
    return { basis, write: write({ operation, form_revision: revision, release: release.id, values, shown }) }
  }

  /** The values on the sheets as the open quote's next revision, over the version they are edited over. */
  const nextRevision = (): PendingWrite | null => {
    const { quote, base } = get()
    if (quote === null || base === null) return null
    return prepare(String(base), (body) => ({ kind: "save", quoteId: quote.id, version: base, body }))
  }

  /**
   * The quote a write held after a lost answer made or moved, taken on without touching the
   * sheets, which hold what was typed since: the details the app set put on them, and the
   * draft kept under the quote's own key from now on.
   */
  const adoptQuote = (answer: WorkbenchAppQuoteResponse): void => {
    const earlier = openDraftKey(get())
    const { form } = useAppStore.getState()
    if (form !== null) takeAppDetails(form, answer.revision.values as Values)
    set({
      quote: answer.quote,
      revision: answer.revision,
      base: answer.revision.number,
      past: false,
      olderSheets: null,
      source: null,
      baseline: valuesKey(answer.revision.values as Values),
      pending: null,
      conflict: null,
      history: null,
    })
    if (earlier !== null && earlier !== openDraftKey(get())) clearDraft(earlier)
    keepDraft()
    if (get().historyOpen) void get().loadHistory()
  }

  /**
   * Send the write `first` prepares, once the pricing settles; a write still held after a
   * lost answer, when it saves something else, is first repeated exactly, so it is written
   * once, and the values are then saved over the quote it made or moved. Answered, the
   * write's quote is adopted; failed, it is held or let go as `writeFailed` says.
   */
  const commit = async (first: () => PendingWrite | null): Promise<WorkbenchAppQuote | null> => {
    const session = loads.current()
    set({ saving: true, error: null, moved: false })
    try {
      // The save keeps the price the values settle on; another quote opened meanwhile is not this save's.
      await useWorkbenchPreviewStore.getState().settle()
      if (!loads.is(session)) return null
      let next = first()
      const held = get().pending
      if (next !== null && held !== null && held.basis !== next.basis) {
        try {
          const repeated = await sendWrite(held.write)
          if (!loads.is(session)) return null
          adoptQuote(repeated)
          next = nextRevision()
          if (next === null) return repeated.quote
        } catch (error: unknown) {
          if (!loads.is(session) || !refused(error)) throw error
          // Refused, so it never landed and never will: let go, and the values saved as asked.
          set({ pending: null })
          next = first()
        }
      }
      if (next === null) return null
      const key = openDraftKey(get())
      set({ pending: next })
      keepDraft()
      const answer = await sendWrite(next.write)
      if (!loads.is(session)) {
        forgetSaved(key, next.write.body.values)
        return null
      }
      saved(answer, next.write.body.values)
      return answer.quote
    } catch (error: unknown) {
      if (loads.is(session)) writeFailed(error)
      return null
    } finally {
      if (loads.is(session)) set({ saving: false })
    }
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
      const attempt = loads.take()
      const { form } = useAppStore.getState()
      if (form === null) return
      installSheets(form)
      // A new quote starts with its columns' defaults, which are not typing: left as it
      // opened, it keeps no draft.
      const values = from === null ? (structuredClone(newQuoteValues(form.schema)) as Values) : {}
      useWorkbenchPreviewStore.getState().open(values, null, !canEdit())
      set({ ...CLOSED, status: from === null ? "ready" : "loading", loadError: null, baseline: valuesKey(values) })
      if (from === null) {
        offerDraft()
        return
      }
      try {
        const [origin, carried] = await Promise.all([openAppQuote(from.quoteId, from.revision), carryAppQuote(from.quoteId, from.revision)])
        if (!loads.is(attempt)) return
        // The values carried, without the details the app set for the source, are the new
        // quote as it opens: left as they are, it keeps no draft and asks nothing on leaving.
        const carriedValues = carried.values as Values
        const opened = from.relation === "renewal" ? asRenewal(carriedValues) : carriedValues
        useWorkbenchPreviewStore.getState().open(opened, null, !canEdit())
        set({
          status: "ready",
          source: { ...from, reference: origin.quote.reference },
          dropped: carried.dropped,
          baseline: valuesKey(opened),
        })
        offerDraft()
      } catch (error: unknown) {
        if (loads.is(attempt)) set({ status: "error", loadError: apiErrorMessage(error) })
      }
    },

    openQuote: async (id, revision) => {
      const attempt = loads.take()
      // Until it is read, the sheets hold nothing of the quote before and take no typing.
      useWorkbenchPreviewStore.getState().open({}, null, true)
      set({ ...CLOSED, status: "loading", loadError: null })
      try {
        const answer = await openAppQuote(id, revision)
        if (!loads.is(attempt)) return
        show(answer, revision !== null && revision !== answer.quote.latest)
        set({ status: "ready" })
        if (!get().past) offerDraft()
        if (get().historyOpen) void get().loadHistory()
      } catch (error: unknown) {
        if (loads.is(attempt)) set({ status: "error", loadError: apiErrorMessage(error) })
      }
    },

    save: async () => {
      if (get().status !== "ready" || busy(get()) || readOnlyReason(get()) !== null) return null
      return commit(() => {
        const { quote, source, base } = get()
        // Read only by now, or shown at an earlier revision: nothing is saved.
        if (readOnlyReason(get()) !== null || (quote !== null && base === null)) return null
        if (quote !== null) return nextRevision()
        const from = source === null ? null : { quote_id: source.quoteId, revision: source.revision, relation: source.relation }
        return prepare(newQuoteName(source), (body) => ({ kind: "create", body, source: from }))
      })
    },

    saveAsNew: async () => {
      if (get().status !== "ready" || busy(get()) || !canEdit()) return null
      return commit(() => {
        const { conflict, quote } = get()
        const origin = conflict?.latest ?? quote
        if (origin === null) return null
        const from = { quote_id: origin.id, revision: origin.latest, relation: "duplicate" as const }
        return prepare(`new-from:${origin.id}:${origin.latest}`, (body) => ({ kind: "create", body, source: from }))
      })
    },

    change: async (change, fields, asked) => {
      const { quote, base } = get()
      if (quote === null || busy(get()) || !canEdit()) return null
      if (asked !== undefined && asked.id !== quote.id) {
        set({ error: `${asked.reference} is no longer the quote open, so it was not changed.` })
        return null
      }
      const session = loads.current()
      set({ changing: true, error: null })
      try {
        const { quote: changed, revision: written } = await changeAppQuote(quote.id, change, base ?? quote.latest, fields)
        if (!loads.is(session)) return changed
        // A save held after a lost answer named the version this revision moved on from: it
        // never landed, or this change would have been refused, and now it never will.
        if (written !== null && written !== undefined && get().pending?.write.kind === "save") set({ pending: null })
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
        if (loads.is(session)) failed(error)
        return null
      } finally {
        if (loads.is(session)) set({ changing: false })
      }
    },

    keepDraft,

    close: () => {
      keepDraft()
      loads.bump()
      set({ ...CLOSED, status: "idle", loadError: null })
    },

    moveToCurrentSheets: async () => {
      const { quote, revision, olderSheets } = get()
      const { form } = useAppStore.getState()
      if (quote === null || revision === null || olderSheets === null || form === null) return
      const session = loads.current()
      try {
        const carried = await carryAppQuote(quote.id, revision.number, true)
        if (!loads.is(session)) return
        installSheets(form)
        set({ olderSheets: null, past: false, dropped: carried.dropped })
        useWorkbenchPreviewStore.getState().open(carried.values as Values, null, readOnlyReason(get()) !== null)
      } catch (error: unknown) {
        if (loads.is(session)) failed(error)
      }
    },

    restoreDraft: () => {
      const { recovered } = get()
      const { form } = useAppStore.getState()
      if (recovered === null || form === null) return
      set({ recovered: null })
      // Nothing typed is put back on a quote that may not change as it is shown.
      if (readOnlyReason(get()) !== null) return
      // The write a save held when the values were kept comes back with them.
      set({ pending: get().pending ?? recovered.pending })
      // Kept on these sheets alone, the details the app sets as the quote has them now, and
      // what has no place on them named.
      const kept = onSheets(form, recovered.values)
      const values = withAppDetails(kept, useWorkbenchPreviewStore.getState().quote)
      useWorkbenchPreviewStore.getState().open(values, null, false)
      set({ dropped: lostPlaces(recovered.values, kept, recovered.places) })
    },

    discardDraft: () => {
      const key = openDraftKey(get())
      if (key !== null) clearDraft(key)
      set({ recovered: null })
    },

    loadHistory: async () => {
      const { quote } = get()
      if (quote === null) return
      const session = loads.current()
      // The history asked for last is the one shown: an earlier answer lacks what came since.
      const asked = historyAsked.take()
      try {
        const history = await fetchAppQuoteHistory(quote.id)
        if (loads.is(session) && historyAsked.is(asked) && get().quote?.id === quote.id) set({ history })
      } catch (error: unknown) {
        // A read asked for since answers in its place.
        if (loads.is(session) && historyAsked.is(asked) && get().quote?.id === quote.id) {
          set({ error: `The quote's history could not be read: ${apiErrorMessage(error)}` })
        }
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
