/**
 * The quote open in the underwriter app (specs/workbench): a quote as the store keeps it, or
 * an alternative made, as the underwriter asks for it, from another quote under its reference
 * and the next option; its values on the sheets through Preview's store, read only while the
 * quote may not change; its saves and changes, each over the version the values were opened
 * or saved at (`base`), a change's details taken onto the sheets, and each answer the open
 * quote's alone, dropped when another was opened meanwhile; the values kept in this browser
 * while they are not saved; and what a save or change that could not be made offers instead.
 * A quote under a new reference is made in the New quote window (`useNewQuoteStore`).
 */
import { create } from "zustand"
import {
  changeAppQuote,
  createAppQuote,
  fetchAppQuoteActivity,
  openAppQuote,
  saveAppQuote,
  type QuoteChange,
  type QuoteChangeFields,
  type QuoteSource,
} from "../api/app"
import { apiErrorMessage, apiErrorRawDetail, apiErrorRefused, isStaleRevision } from "../api/errors"
import type { FormSpec, WorkbenchAppQuote, WorkbenchAppQuoteActivityResponse, WorkbenchAppQuoteResponse } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import { basisOf, quoteEligible } from "../utils/sheetVisibility"
import { latest } from "../utils/latest"
import {
  appDetails,
  cellPlaces,
  clearDraft,
  creationKey,
  draftKey,
  heldOperation,
  lostPlaces,
  onSheets,
  operationKey,
  readDraft,
  refusalOf,
  releaseMoved,
  releaseOperation,
  sameQuote,
  savedPrice,
  valuesKey,
  withAppDetails,
  withoutAppDetails,
  writeDraft,
  writeSuperseded,
  type Draft,
  type PendingWrite,
  type QuoteWrite,
  type Refusal,
  type Values,
} from "./quoteValues"
import useAppStore from "./useAppStore"

/** Why the quote's values may not change, or null when they may. */
export type ReadOnlyReason = "final" | "archived" | "reader" | null

interface OpenQuoteState {
  status: "idle" | "loading" | "ready" | "error"
  loadError: string | null
  /** The quote; null until it is read or made. */
  quote: WorkbenchAppQuote | null
  /**
   * The quote's version the values on the sheets are edited over, which a save and a change
   * name: the version as it was opened, saved or moved on by a change made here. Anything
   * else that refreshes the quote, a note, leaves it, so a save over a quote moved on
   * meanwhile is refused rather than written over it. Null while no quote is open.
   */
  base: number | null
  /** The values as the quote holds them: as opened or last saved, with the details a change made here set since. */
  savedValues: Values
  /** `savedValues` as a quote (`valuesKey`), which the values on the sheets are compared with. */
  baseline: string
  /** The output tables the quote holds, as its pricing rendered them; null while it is unpriced. */
  savedTables: WorkbenchAppQuoteResponse["tables"]
  saving: boolean
  /** A change other than a save is being made. */
  changing: boolean
  /** Why the last save or change failed, when no other notice says. */
  error: string | null
  /**
   * The quote moved on since it was opened, or a save repeated after a lost answer landed and
   * it moved on since: who moved it, and the quote as it is now when the refusal holds it.
   */
  conflict: { message: string; quote: WorkbenchAppQuote | null } | null
  /** The last save or Mark final was refused for the places it names. */
  refusal: Refusal | null
  /** The last save's price, made again on the server, was not the one the sheets showed. */
  moved: boolean
  /**
   * What has no place on the current sheets: each row of the quote, `table row N (value)`,
   * and each value restored from this browser, `table.column`.
   */
  dropped: string[]
  /** Values kept in the browser, not saved, offered back. */
  recovered: Draft | null
  /**
   * The write a save sent and has no answer to, or a server failure for: it may have landed,
   * so it is held, and repeated exactly, before anything else is saved.
   */
  pending: PendingWrite | null
  /** What was done to the quote, and the quotes it came from and that were made from it; null until read. */
  activity: WorkbenchAppQuoteActivityResponse | null
  historyOpen: boolean
  /** The alternative `openNew` just made and opened, by its label: on the sheets already, its address opens nothing again. */
  adopted: string | null
  /**
   * Make an alternative of `from`, as the underwriter asks for it, and open it: the quote it
   * made, or null when it made none.
   */
  openNew: (from: QuoteSource) => Promise<WorkbenchAppQuote | null>
  /** Open the quote a route names, unless it is the alternative just made, which it forgets either way. */
  openRouted: (reference: string, option: number) => Promise<void>
  openQuote: (reference: string, option: number) => Promise<void>
  /** Save the values on the sheets, once their pricing settles, over the version they are edited over. */
  save: () => Promise<WorkbenchAppQuote | null>
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
  restoreDraft: () => void
  discardDraft: () => void
  loadActivity: () => Promise<void>
  setHistoryOpen: (open: boolean) => void
  dismiss: (notice: "error" | "conflict" | "refusal" | "moved" | "dropped") => void
}

type Closed = Omit<OpenQuoteState, "status" | "loadError" | "historyOpen" | { [K in keyof OpenQuoteState]: OpenQuoteState[K] extends (...args: never[]) => unknown ? K : never }[keyof OpenQuoteState]>

/** Nothing open: what each open starts from. */
const CLOSED: Closed = {
  quote: null,
  base: null,
  savedValues: {},
  baseline: valuesKey({}),
  savedTables: null,
  saving: false,
  changing: false,
  error: null,
  conflict: null,
  refusal: null,
  moved: false,
  dropped: [],
  recovered: null,
  pending: null,
  activity: null,
  adopted: null,
}

/** Why `quote` may not change. */
export function readOnlyReason(state: Pick<OpenQuoteState, "quote">): ReadOnlyReason {
  if (state.quote?.archived) return "archived"
  if (state.quote?.stage === "final") return "final"
  if (!canEdit()) return "reader"
  return null
}

/** A save or a change is being made: no other is started, and the controls that make one are off. */
export const busy = (state: Pick<OpenQuoteState, "saving" | "changing">): boolean => state.saving || state.changing

/** Whoever is signed in may change quotes: false for one who may only read them. */
export const canEdit = (): boolean => useAppStore.getState().release?.can_edit ?? true

/** What a quote asked for is: one made from a quote, so the key held for one is not taken for another's. */
const askedFor = (from: QuoteSource): string => `new-from-${from.reference}/${from.option}-${from.relation}`

/** The open quote's draft key in this browser; null with no quote open. */
export function openDraftKey(state: Pick<OpenQuoteState, "quote">): string | null {
  const { release } = useAppStore.getState()
  if (release === null || state.quote === null) return null
  return draftKey(release.pipeline_name || "app", release.principal, state.quote.label)
}

/** The quote as a refusal holds it, when it does: as it is now, a stale save's or change's. */
function conflictQuote(error: unknown): WorkbenchAppQuote | null {
  const quote = apiErrorRawDetail(error)?.quote
  return typeof quote === "object" && quote !== null && "reference" in quote && "version" in quote ? (quote as WorkbenchAppQuote) : null
}

/** What `answer` says the quote holds: its values, as a quote's key too, and its output tables. */
function holding(answer: WorkbenchAppQuoteResponse): Pick<OpenQuoteState, "savedValues" | "baseline" | "savedTables"> {
  const values = answer.values as Values
  return { savedValues: values, baseline: valuesKey(values), savedTables: answer.tables }
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

/** Send `write`. */
const sendWrite = (write: QuoteWrite): Promise<WorkbenchAppQuoteResponse> => saveAppQuote(write.reference, write.option, write.body, write.version)

/**
 * The open quote's loads, newest wins: a save, a change or a read started under one load
 * answers that load alone, and its answer is dropped once another has started.
 */
const loads = latest()
/** The activity asked for last. */
const activityAsked = latest()

const useOpenQuoteStore = create<OpenQuoteState>()((set, get) => {
  /** Read what failed into the notice it calls for. */
  const failed = (error: unknown): void => {
    const refusal = refusalOf(error)
    if (isStaleRevision(error) || writeSuperseded(error)) {
      set({ conflict: { message: apiErrorMessage(error), quote: conflictQuote(error) } })
    } else if (refusal !== null) {
      set({ refusal })
    } else if (releaseMoved(error)) {
      useAppStore.getState().noteReleaseChanged()
    } else {
      set({ error: apiErrorMessage(error) })
    }
  }

  /**
   * A write that failed, read as any failure: let go once refused, which wrote nothing, or
   * superseded, which landed and is done; with no answer, or a server failure, it may have
   * landed, so it stays held, in the draft too.
   */
  const writeFailed = (error: unknown): void => {
    failed(error)
    if (apiErrorRefused(error)) letGo()
    keepDraft()
  }

  /** Show `answer` on the sheets: its values, its price, read only as the quote is. */
  const show = (answer: WorkbenchAppQuoteResponse): void => {
    const { form, release } = useAppStore.getState()
    if (form === null) return
    const values = answer.values as Values
    const readOnly = readOnlyReason({ quote: answer.quote }) !== null
    // A quote that may change shows this release's price: one priced on another release is
    // priced again as it opens, as an unpriced one is.
    const ownPrice = readOnly || answer.quote.priced_release === release?.id
    useWorkbenchPreviewStore.getState().open(values, ownPrice ? savedPrice(form, values, answer.tables, answer.request) : null, readOnly)
    set({ quote: answer.quote, base: answer.quote.version, ...holding(answer), dropped: answer.dropped })
  }

  /**
   * Offer back what this browser kept of the open quote, when it differs from what is saved,
   * or holds a write whose answer was lost, and the quote may change.
   */
  const offerDraft = (): void => {
    const key = openDraftKey(get())
    const draft = key === null || readOnlyReason(get()) !== null ? null : readDraft(key)
    // Offered only when it differs from the sheet, values already there not offered back,
    // or when a write is held: restored, it is repeated, so it is written once.
    const shown = valuesKey(useWorkbenchPreviewStore.getState().quote)
    set({ recovered: draft !== null && (valuesKey(draft.values) !== shown || draft.pending !== null) ? draft : null })
  }

  /**
   * The write held after a lost answer: the open quote's own, else the one a draft offered
   * back and not yet answered holds, which a Save repeats first whether or not the draft's
   * values were restored, so it is written once.
   */
  const heldWrite = (): PendingWrite | null => get().pending ?? get().recovered?.pending ?? null

  /** The held write let go: never landed, it never will, or landed, it is done. The offer keeps its values. */
  const letGo = (): void => {
    const { recovered } = get()
    set({ pending: null, recovered: recovered === null ? null : { ...recovered, pending: null } })
  }

  const keepDraft = (): boolean => {
    const key = openDraftKey(get())
    // Only a quote open, and as it may change, has values of its own to keep.
    if (key === null || get().status !== "ready" || readOnlyReason(get()) !== null) return true
    const values = useWorkbenchPreviewStore.getState().quote
    // Values as they opened keep nothing, unless a write of them is held: a quote saved as
    // it opened, its answer lost, is written once.
    if (valuesKey(values) === get().baseline && get().pending === null) {
      // A draft offered back and not yet answered stays offered, whole.
      if (get().recovered === null) clearDraft(key)
      return true
    }
    // Typed over a draft offered back and not yet answered: the write it held is kept.
    const pending = heldWrite()
    const sheets = useWorkbenchFormStore.getState().form
    return writeDraft(key, {
      values,
      version: get().base,
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
    const basis = `${valuesKey(values)}|${to}|${shown === null ? "unpriced" : "priced"}`
    const pending = heldWrite()
    const operation = pending !== null && pending.basis === basis ? pending.write.body.operation : operationKey()
    return { basis, write: write({ operation, form_revision: revision, release: release.id, values, shown }) }
  }

  /** The values on the sheets saved over the version they are edited over. */
  const nextSave = (): PendingWrite | null => {
    const { quote, base } = get()
    if (quote === null || base === null) return null
    return prepare(String(base), (body) => ({ kind: "save", reference: quote.reference, option: quote.option, version: base, body }))
  }

  /**
   * The quote a write held after a lost answer saved, taken on without touching the sheets,
   * which hold what was typed since: the details the app set put on them.
   */
  const adoptQuote = (answer: WorkbenchAppQuoteResponse): void => {
    const { form } = useAppStore.getState()
    if (form !== null) takeAppDetails(form, answer.values as Values)
    // An offer whose write this was keeps its values to restore, its write done.
    const { recovered } = get()
    set({
      quote: answer.quote,
      base: answer.quote.version,
      ...holding(answer),
      pending: null,
      recovered: recovered === null ? null : { ...recovered, pending: null },
      conflict: null,
      activity: null,
    })
    keepDraft()
    if (get().historyOpen) void get().loadActivity()
  }

  /**
   * Send the write `first` prepares, once the pricing settles; a write still held after a
   * lost answer, when it saves something else, is first repeated exactly, so it is written
   * once, and the values are then saved over the version it moved the quote to. Answered, the
   * write's quote is adopted; failed, it is held or let go as `writeFailed` says.
   */
  const commit = async (first: () => PendingWrite | null): Promise<WorkbenchAppQuote | null> => {
    const session = loads.current()
    set({ saving: true, error: null, refusal: null, moved: false })
    try {
      // The save keeps the price the values settle on; another quote opened meanwhile is not this save's.
      await useWorkbenchPreviewStore.getState().settle()
      if (!loads.is(session)) return null
      let next = first()
      const held = heldWrite()
      if (next !== null && held !== null && held.basis !== next.basis) {
        try {
          const repeated = await sendWrite(held.write)
          if (!loads.is(session)) return null
          adoptQuote(repeated)
          next = nextSave()
          if (next === null) return repeated.quote
        } catch (error: unknown) {
          if (!loads.is(session) || !apiErrorRefused(error)) throw error
          // Landed, and the quote moved on past the version the values are edited over since:
          // what was typed is not saved over a version gone, and the newest is offered.
          if (writeSuperseded(error) && held.write.version === get().base) throw error
          // Refused, so it never landed and never will, or landed under a version the quote
          // was opened past: let go, and the values saved as asked.
          letGo()
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
    const values = answer.values as Values
    if (form !== null && valuesKey(preview.quote) === valuesKey(sent)) {
      preview.open(values, savedPrice(form, values, answer.tables, answer.request), false)
    } else if (form !== null) {
      // Typed over since: what was typed stays, and the details the app set reach the sheets.
      takeAppDetails(form, values)
    }
    set({
      quote: answer.quote,
      base: answer.quote.version,
      ...holding(answer),
      moved: answer.moved,
      conflict: null,
      error: null,
      refusal: null,
      dropped: answer.dropped,
      pending: null,
      recovered: null,
      activity: null,
    })
    keepDraft()
    if (get().historyOpen) void get().loadActivity()
  }

  /**
   * The quote a change left adopted: the details it set on the sheets in place of those
   * there, what was typed kept, a price shown for the values kept current, as the change kept
   * the quote's prices, and the values edited over the version it moved the quote to.
   */
  const adoptChange = (changed: WorkbenchAppQuote): void => {
    const { form } = useAppStore.getState()
    if (form === null || get().base === null) return
    const details = appDetails(form, changed.details)
    takeAppDetails(form, details)
    const savedValues = withAppDetails(get().savedValues, details)
    set({ base: changed.version, savedValues, baseline: valuesKey(savedValues) })
  }

  return {
    status: "idle",
    loadError: null,
    historyOpen: false,
    ...CLOSED,

    openNew: async (from) => {
      const attempt = loads.take()
      const { form, revision, release } = useAppStore.getState()
      if (form === null || release === null) return null
      // Until it is made, the sheets hold nothing and take no typing.
      useWorkbenchPreviewStore.getState().open({}, null, true)
      if (!canEdit()) {
        set({ ...CLOSED, status: "error", loadError: "Making a quote needs the app's edit role." })
        return null
      }
      set({ ...CLOSED, status: "loading", loadError: null })
      // Held in this tab until the answer comes, so a reload asks with it again and the
      // quote is made once.
      const held = creationKey(release.pipeline_name || "app", release.principal, askedFor(from))
      try {
        // Made from its source's values, without the details the app set for the source,
        // under its reference: it names none of its own.
        const read = await openAppQuote(from.reference, from.option)
        if (!loads.is(attempt)) return null
        const creation = { operation: heldOperation(held), form_revision: revision, release: release.id, values: withoutAppDetails(read.values as Values) }
        const source = { reference: from.reference, option: from.option, relation: from.relation }
        let answer: WorkbenchAppQuoteResponse
        try {
          answer = await createAppQuote(creation, source, null)
        } catch (error: unknown) {
          // Refused, it was not made and never will be: the next asks anew. Unanswered, it
          // may have been: the key stays held.
          if (apiErrorRefused(error)) releaseOperation(held)
          throw error
        }
        releaseOperation(held)
        if (!loads.is(attempt)) return answer.quote
        show(answer)
        // What of its source has no place on the sheets was not made part of it: named.
        set({ status: "ready", dropped: [...new Set([...read.dropped, ...answer.dropped])], adopted: answer.quote.label })
        return answer.quote
      } catch (error: unknown) {
        if (loads.is(attempt)) set({ status: "error", loadError: apiErrorMessage(error) })
        return null
      }
    },

    openRouted: async (reference, option) => {
      const { adopted, quote } = get()
      set({ adopted: null })
      if (quote !== null && adopted === quote.label && sameQuote(quote, { reference, option })) return
      await get().openQuote(reference, option)
    },

    openQuote: async (reference, option) => {
      const attempt = loads.take()
      // Until it is read, the sheets hold nothing of the quote before and take no typing.
      useWorkbenchPreviewStore.getState().open({}, null, true)
      set({ ...CLOSED, status: "loading", loadError: null })
      try {
        const answer = await openAppQuote(reference, option)
        if (!loads.is(attempt)) return
        show(answer)
        set({ status: "ready" })
        offerDraft()
        if (get().historyOpen) void get().loadActivity()
      } catch (error: unknown) {
        if (loads.is(attempt)) set({ status: "error", loadError: apiErrorMessage(error) })
      }
    },

    save: async () => {
      if (get().status !== "ready" || busy(get()) || readOnlyReason(get()) !== null) return null
      // Read only by now: nothing is saved.
      return commit(() => (readOnlyReason(get()) !== null ? null : nextSave()))
    },

    change: async (change, fields, asked) => {
      const { quote, base } = get()
      if (quote === null || busy(get()) || !canEdit()) return null
      if (asked !== undefined && !sameQuote(asked, quote)) {
        set({ error: `${asked.label} is no longer the quote open, so it was not changed.` })
        return null
      }
      const session = loads.current()
      set({ changing: true, error: null, refusal: null })
      try {
        const { quote: changed } = await changeAppQuote(quote.reference, quote.option, change, base ?? quote.version, fields)
        if (!loads.is(session)) return changed
        // Every change but a note moves the quote's version on.
        const moving = change !== "note"
        // A save held after a lost answer named the version this change moved on from: it
        // never landed, or this change would have been refused, and now it never will.
        if (moving) set({ pending: null })
        // An archive leaves the quote read only: what was typed, before the change or while
        // it was made, is kept in this browser while the quote may still change.
        keepDraft()
        const wasReadOnly = readOnlyReason(get()) !== null
        set({ quote: changed })
        if (moving) adoptChange(changed)
        useWorkbenchPreviewStore.getState().setReadOnly(readOnlyReason(get()) !== null)
        // Restored or amended, it may change again: what this browser kept of it is offered
        // back. One that could change all along keeps what is typed over its new version.
        if (wasReadOnly && readOnlyReason(get()) === null) offerDraft()
        else keepDraft()
        if (get().historyOpen) void get().loadActivity()
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

    restoreDraft: () => {
      const { recovered } = get()
      const { form } = useAppStore.getState()
      if (recovered === null || form === null) return
      set({ recovered: null })
      // Nothing typed is put back on a quote that may not change.
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

    loadActivity: async () => {
      const { quote } = get()
      if (quote === null) return
      const session = loads.current()
      // The activity asked for last is the one shown: an earlier answer lacks what came since.
      const asked = activityAsked.take()
      const answering = (): boolean => {
        const open = get().quote
        return loads.is(session) && activityAsked.is(asked) && open !== null && sameQuote(open, quote)
      }
      try {
        const activity = await fetchAppQuoteActivity(quote.reference, quote.option)
        if (answering()) set({ activity })
      } catch (error: unknown) {
        // A read asked for since answers in its place.
        if (answering()) set({ error: `The quote's history could not be read: ${apiErrorMessage(error)}` })
      }
    },

    setHistoryOpen: (open) => {
      set({ historyOpen: open })
      if (open) void get().loadActivity()
    },

    dismiss: (notice) => {
      if (notice === "error") set({ error: null })
      else if (notice === "conflict") set({ conflict: null })
      else if (notice === "refusal") set({ refusal: null })
      else if (notice === "moved") set({ moved: false })
      else set({ dropped: [] })
    },
  }
})

export default useOpenQuoteStore
