/**
 * The quote open in the underwriter app (specs/workbench): a quote as the store keeps it,
 * opened to read or to edit, held by its editor while it is edited (`editAppQuote`, renewed by
 * `keepEditing`, let go by `stopEditing`), read only while someone else is; its values on the
 * sheets through Preview's store, read only while the quote may not change; its saves and changes, each over the version the values were opened
 * or saved at (`base`), a change's details taken onto the sheets, and each answer the open
 * quote's alone, dropped when another was opened meanwhile; the values kept in this browser
 * while they are not saved; and what a save or change that could not be made offers instead.
 * A quote under a new reference is made in the New quote window (`useNewQuoteStore`).
 */
import { create } from "zustand"
import {
  changeAppQuote,
  editAppQuote,
  exportAppQuote,
  fetchAppQuoteActivity,
  openAppQuote,
  releaseAppQuote,
  saveAppQuote,
  type QuoteChange,
  type QuoteChangeFields,
} from "../api/app"
import { keyedWriteSettled, sendHeld, type KeyedHold, type KeyedTicket } from "../api/keyedWrites"
import type { CellsExporter } from "../api/workbench"
import { apiErrorMessage, apiErrorRawDetail, isStaleRevision } from "../api/errors"
import type { FormSpec, WorkbenchAppQuote, WorkbenchAppQuoteActivityResponse, WorkbenchAppQuoteResponse } from "../api/types"
import useWorkbenchFormStore from "../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import { answer, ask, newest, requestKind, under, watch, type Part } from "../utils/answers"
import { basisOf, quoteEligible, quoteIndexProblems } from "../utils/sheetVisibility"
import {
  appDetails,
  appErrorMessage,
  cellPlaces,
  clearDraft,
  beingEditedBy,
  draftKey,
  lostPlaces,
  onSheets,
  operationKey,
  readDraft,
  refusalOf,
  releaseMoved,
  sameQuote,
  savedPrice,
  storeRefused,
  valuesKey,
  withAppDetails,
  withHiddenCells,
  writeDraft,
  writeSuperseded,
  type Draft,
  type PendingWrite,
  type QuoteWrite,
  type Refusal,
  type Values,
} from "./quoteValues"
import { editPath, navigate, type QuoteMode } from "./route"
import useAppStore from "./useAppStore"
import useToastStore from "../stores/useToastStore"

/**
 * Why the quote's values may not change, or null when they may: one who may only read quotes,
 * opened to read, someone else editing it, archived, or final.
 */
export type ReadOnlyReason = "reader" | "read" | "editing" | "archived" | "final" | null

interface OpenQuoteState {
  status: "idle" | "loading" | "ready" | "error"
  loadError: string | null
  /** The quote whose opening the quote store refused, which a check past its bound may lift; asked for again quietly. */
  refusedOpen: { reference: string; option: number } | null
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
  /** How the quote open was opened: to read it, or to edit it. */
  mode: QuoteMode
  /** Who else is editing the quote opened to edit, which holds it read only; null when no one is. */
  editedBy: string | null
  /** The lease's renewals answered, each the page's cue to renew it again: no two at once. */
  renewals: number
  /** The quote held by someone else had values typed and not saved, kept in this browser. */
  keptTyping: boolean
  /** Open the quote `reference`/`option` to read it, or to edit it, held as it opens unless someone else is editing it. */
  openQuote: (reference: string, option: number, mode?: QuoteMode) => Promise<void>
  /** Renew the lease of the quote open to edit; one someone else took holds it read only, what was typed kept. */
  keepEditing: () => Promise<void>
  /** Let the quote open to edit go, as the page leaves it; `keepalive` as the page closes. */
  stopEditing: (options?: { keepalive?: boolean }) => void
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

type Closed = Omit<OpenQuoteState, "status" | "loadError" | "refusedOpen" | "historyOpen" | { [K in keyof OpenQuoteState]: OpenQuoteState[K] extends (...args: never[]) => unknown ? K : never }[keyof OpenQuoteState]>

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
  mode: "read",
  editedBy: null,
  renewals: 0,
  keptTyping: false,
}

/** Why `quote` may not change. */
export function readOnlyReason(state: Pick<OpenQuoteState, "quote" | "mode" | "editedBy">): ReadOnlyReason {
  if (!canEdit()) return "reader"
  if (state.mode === "read") return "read"
  if (state.editedBy !== null) return "editing"
  if (state.quote?.archived) return "archived"
  if (state.quote?.stage === "final") return "final"
  return null
}

/** Whether a quote read only for `reason` still takes changes of its details: one open to edit, final included. */
export const takesChanges = (reason: ReadOnlyReason): boolean => reason === null || reason === "final"

/**
 * Open `quote` to edit when no one else is editing it, held as the asker's; else say who is,
 * the page staying where it is.
 */
export async function editQuote(quote: Pick<WorkbenchAppQuote, "reference" | "option" | "label">): Promise<void> {
  try {
    await editAppQuote(quote.reference, quote.option)
  } catch (error: unknown) {
    const editor = beingEditedBy(error)
    const said = editor !== null ? `${editor} is editing ${quote.label}` : appErrorMessage(error, useAppStore.getState().form, { oneLine: true })
    useToastStore.getState().addToast("error", said)
    return
  }
  await navigate(editPath(quote.reference, quote.option))
}

/** A save or a change is being made: no other is started, and the controls that make one are off. */
export const busy = (state: Pick<OpenQuoteState, "saving" | "changing">): boolean => state.saving || state.changing

/** Whoever is signed in may change quotes: false for one who may only read them. */
export const canEdit = (): boolean => useAppStore.getState().release?.can_edit ?? true

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

/** What `answer` says the quote holds: its values, `values` when given, as a quote's key too, and its output tables. */
function holding(answer: WorkbenchAppQuoteResponse, values: Values = answer.values as Values): Pick<OpenQuoteState, "savedValues" | "baseline" | "savedTables"> {
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
const sendWrite = (write: QuoteWrite, ticket: KeyedTicket): Promise<WorkbenchAppQuoteResponse> => saveAppQuote(write.reference, write.option, write.body, write.version, ticket)

/**
 * The open quote's loads, newest wins: a save, a change or a read started under one load
 * answers that load alone, and its answer is dropped once another has started.
 */
const loads = requestKind()
/** The activity asked for last. */
const activityAsked = requestKind()

/**
 * The quote open now, as an answer's part (`utils/answers.ts`): it holds until another quote
 * is opened or made, or the quote is left.
 */
export const openQuotePart = (): Part => under(loads)

const useOpenQuoteStore = create<OpenQuoteState>()((set, get, api) => {
  /**
   * The quote open to edit held by `editor` now: what was typed kept in this browser first,
   * then the quote read only, saying who is editing it.
   */
  const heldBy = (editor: string): void => {
    const typed = get().status === "ready" && get().editedBy === null && valuesKey(useWorkbenchPreviewStore.getState().quote) !== get().baseline
    keepDraft()
    set({ editedBy: editor, keptTyping: typed })
    useWorkbenchPreviewStore.getState().setReadOnly(true)
  }

  /** Read what failed into the notice it calls for. */
  const failed = (error: unknown): void => {
    const refusal = refusalOf(error)
    const editor = beingEditedBy(error)
    if (editor !== null) {
      heldBy(editor)
    } else if (isStaleRevision(error) || writeSuperseded(error)) {
      set({ conflict: { message: apiErrorMessage(error), quote: conflictQuote(error) } })
    } else if (refusal !== null) {
      set({ refusal })
    } else if (releaseMoved(error)) {
      useAppStore.getState().noteReleaseChanged()
    } else {
      set({ error: appErrorMessage(error, useAppStore.getState().form) })
    }
  }

  /** Show `answer` on the sheets: its values, its price, read only as the quote is. */
  const show = (answer: WorkbenchAppQuoteResponse): void => {
    const { form, release } = useAppStore.getState()
    if (form === null) return
    const values = answer.values as Values
    const readOnly = readOnlyReason({ ...get(), quote: answer.quote }) !== null
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

  /**
   * Where the open quote's save is held until an answer settles it (`sendHeld`): the store,
   * and the draft kept in this browser, so a reload repeats it. A write of the quote open
   * under `key` holds nothing once another is open.
   */
  const quoteHold = (key: string | null): KeyedHold<PendingWrite> => ({
    held: heldWrite,
    hold: (write) => {
      if (openDraftKey(get()) !== key) return
      if (write === null) {
        letGo()
        return
      }
      set({ pending: write })
      keepDraft()
    },
    key: (write) => write.write.body.operation,
    // The key is bound to what the write saves: the values, the version saved over and
    // priced or not (`basis`); the server prices the values again.
    content: (write) => write.basis,
  })

  /**
   * A save that failed, its pricing or its write, read as any failure, and the values kept in
   * the draft. Only the write's own answer lets a held write go, through its hold: refused,
   * it wrote nothing; with no answer, or a server failure, it may have landed, so it stays.
   */
  const sentFailed = (error: unknown): void => {
    failed(error)
    keepDraft()
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
   * nothing to save, or while a typed index has a problem (`quoteIndexProblems`), which the
   * server refuses and the Save button names. Its operation key is the held write's when
   * that saves the same, its `basis`: the values, what they are saved as, and priced or not
   * (the server prices them again), so a retry writes once.
   */
  const prepare = (to: string, write: (body: QuoteWrite["body"]) => QuoteWrite): PendingWrite | null => {
    const { form, revision, release } = useAppStore.getState()
    if (form === null || release === null) return null
    const preview = useWorkbenchPreviewStore.getState()
    const values = preview.quote
    // As the server counts typing: what the sheets show, the details the app sets and the defaults aside.
    if (!quoteEligible(form, values, preview.locked) || quoteIndexProblems(form, values).length > 0) return null
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
   * write's quote is adopted; failed, it stays held or is let go as its answer settles it.
   */
  const commit = async (first: () => PendingWrite | null): Promise<WorkbenchAppQuote | null> => {
    // Another quote opened meanwhile is not this save's.
    const asked = ask([under(loads)])
    set({ saving: true, error: null, refusal: null, moved: false })
    let made: WorkbenchAppQuote | null = null
    /** Send `next`, held until its answer comes; saved, the quote as saved is adopted. */
    const hold = quoteHold(openDraftKey(get()))
    const send = async (next: PendingWrite): Promise<void> => {
      const key = openDraftKey(get())
      await asked.answer(sendHeld(hold, next, (write, ticket) => sendWrite(write.write, ticket)), {
        ok: (answer) => {
          saved(answer, next.write.body.values)
          made = answer.quote
        },
        failed: sentFailed,
        stale: (settled) => {
          if ("value" in settled) forgetSaved(key, next.write.body.values)
        },
      })
    }
    try {
      // The save keeps the price the values settle on.
      await asked.answer(useWorkbenchPreviewStore.getState().settle(), {
        ok: async () => {
          const next = first()
          const held = heldWrite()
          if (next === null || held === null || held.basis === next.basis) {
            if (next !== null) await send(next)
            return
          }
          await asked.answer(sendHeld(hold, held, (write, ticket) => sendWrite(write.write, ticket)), {
            ok: async (repeated) => {
              adoptQuote(repeated)
              const after = nextSave()
              if (after === null) made = repeated.quote
              else await send(after)
            },
            failed: async (error) => {
              // Still unknown, it stays held. Landed, and the quote moved on past the version
              // the values are edited over since: what was typed is not saved over a version
              // gone, and the newest is offered.
              if (!keyedWriteSettled(error) || (writeSuperseded(error) && held.write.version === get().base)) {
                sentFailed(error)
                return
              }
              // Refused, so it never landed and never will, or landed under a version the quote
              // was opened past: let go, and the values saved as asked.
              const again = first()
              if (again !== null) await send(again)
            },
          })
        },
        failed: sentFailed,
      })
    } finally {
      asked.apply(() => set({ saving: false }))
      asked.release()
    }
    return made
  }

  /**
   * A save's answer adopted: the quote as saved, its values and price on the sheets unless
   * typed over since, and what this browser keeps of them, the typing since kept again.
   */
  const saved = (answer: WorkbenchAppQuoteResponse, sent: Values): void => {
    forgetSaved(openDraftKey(get()), sent)
    const { form } = useAppStore.getState()
    const preview = useWorkbenchPreviewStore.getState()
    // What was typed into the cells the sheets hide, which the store keeps nothing of, stays.
    const values = form === null ? (answer.values as Values) : withHiddenCells(form, answer.values as Values, sent)
    if (form !== null && valuesKey(preview.quote) === valuesKey(sent)) {
      preview.open(values, savedPrice(form, values, answer.tables, answer.request), false)
    } else if (form !== null) {
      // Typed over since: what was typed stays, and the details the app set reach the sheets.
      takeAppDetails(form, values)
    }
    set({
      quote: answer.quote,
      base: answer.quote.version,
      ...holding(answer, values),
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
    refusedOpen: null,
    historyOpen: false,
    ...CLOSED,

    openQuote: async (reference, option, mode = "read") => {
      const belongsTo = [newest(loads)] as const
      // The quote the store refused keeps saying why while it is asked for again; any other is
      // read afresh: until it is read, the sheets hold nothing of the quote before and take no
      // typing.
      const { status, refusedOpen } = get()
      if (!(status === "error" && refusedOpen !== null && sameQuote(refusedOpen, { reference, option }))) {
        useWorkbenchPreviewStore.getState().open({}, null, true)
        set({ ...CLOSED, status: "loading", loadError: null, refusedOpen: null })
      }
      set({ mode, editedBy: null, keptTyping: false })
      // Opened to edit, it is held before it is shown: someone else editing it holds it read
      // only. Any other failure is the open's to say, and the next renewal takes it.
      if (mode === "edit" && canEdit()) {
        try {
          await editAppQuote(reference, option)
        } catch (error: unknown) {
          set({ editedBy: beingEditedBy(error) })
        }
      }
      await answer(belongsTo, openAppQuote(reference, option), {
        ok: (answer) => {
          show(answer)
          set({ status: "ready", loadError: null, refusedOpen: null })
          offerDraft()
          if (get().historyOpen) void get().loadActivity()
        },
        failed: (error) =>
          set({
            status: "error",
            loadError: appErrorMessage(error, useAppStore.getState().form),
            refusedOpen: storeRefused(error) ? { reference, option } : null,
          }),
      })
    },

    keepEditing: async () => {
      const { mode, editedBy, quote } = get()
      if (mode !== "edit" || editedBy !== null || quote === null || !canEdit()) return
      try {
        await editAppQuote(quote.reference, quote.option)
      } catch (error: unknown) {
        // Taken by someone else once its lease lapsed: read only now, what was typed kept. No
        // answer, or a server failure, says nothing of who holds it: the next renewal asks.
        const editor = beingEditedBy(error)
        const open = get().quote
        if (editor !== null && open !== null && sameQuote(open, quote)) heldBy(editor)
      } finally {
        set((state) => ({ renewals: state.renewals + 1 }))
      }
    },

    stopEditing: (options = {}) => {
      const { mode, editedBy, quote } = get()
      if (mode !== "edit" || editedBy !== null || quote === null) return
      // Let go as the page leaves; one never let go lapses with its lease.
      releaseAppQuote(quote.reference, quote.option, options).catch(() => undefined)
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
      const inQuote = ask([under(loads)])
      set({ changing: true, error: null, refusal: null })
      let made: WorkbenchAppQuote | null = null
      try {
        await inQuote.answer(changeAppQuote(quote.reference, quote.option, change, base ?? quote.version, fields), {
          ok: ({ quote: changed }) => {
            made = changed
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
          },
          failed,
          // Made after another quote was opened, the change is made all the same.
          stale: (settled) => {
            if ("value" in settled) made = settled.value.quote
          },
        })
      } finally {
        inQuote.apply(() => set({ changing: false }))
        inQuote.release()
      }
      return made
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
      // The activity asked for last is the one shown: an earlier answer lacks what came since.
      const belongsTo = [
        under(loads),
        newest(activityAsked),
        watch(api, (state) => state.quote, (_, open) => open !== null && sameQuote(open, quote)),
      ] as const
      await answer(belongsTo, fetchAppQuoteActivity(quote.reference, quote.option), {
        ok: (activity) => set({ activity }),
        failed: (error) => set({ error: `The quote's history could not be read: ${appErrorMessage(error, useAppStore.getState().form)}` }),
      })
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

/**
 * What runs an Export of `quote` on the sheets: a final quote's, at the version open, while
 * it is final; null for any other, which an Export does not run on.
 */
export const quoteExporter = (quote: WorkbenchAppQuote | null): CellsExporter | null =>
  quote === null || quote.stage !== "final" ? null : (componentId) => exportAppQuote(quote.reference, quote.option, componentId, quote.version)

// The sheets' Export runs on the quote open while it is final, whatever opened or changed it.
useOpenQuoteStore.subscribe((state, previous) => {
  if (state.quote !== previous.quote) useWorkbenchPreviewStore.getState().setExporter(quoteExporter(state.quote))
})

export default useOpenQuoteStore
