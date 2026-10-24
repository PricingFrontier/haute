/**
 * The New quote window (specs/workbench): where the underwriter keys a new quote's reference
 * and the quote_details columns they type before the quote is made, over the page it was
 * asked for from, which it leaves as it was. New quote starts it from a new quote's defaults;
 * Duplicate and Renew from a quote's revision carried onto the current sheets, its reference
 * blank, a renewal ticked as one; Save as new from the values on the open quote's sheets.
 * Create makes the whole creation once and keeps it in the tab before it is sent: refused,
 * it is let go and the window takes typing again, saying why; unanswered, it may have been
 * made, so it is kept and sent again exactly as it was, and offered back the next time the
 * app opens in the tab, before anything else is made. An alternative is not made here: it
 * takes its source's reference (`useOpenQuoteStore.openNew`).
 */
import { create } from "zustand"
import { carryAppQuote, createAppQuote, type QuoteSource } from "../api/app"
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from "../api/errors"
import type { FormSpec } from "../api/types"
import useToastStore from "../stores/useToastStore"
import useWorkbenchPreviewStore from "../stores/useWorkbenchPreviewStore"
import { cellKey, cellProblem, newQuoteValues } from "../utils/sheetValues"
import { QUOTE_DETAILS_ID, QUOTE_DETAILS_MANAGED, type SampleRow, type SampleValue, type ShownField } from "../utils/workbenchForm"
import { latest } from "../utils/latest"
import {
  asRenewal,
  clearDraft,
  forgetCreation,
  keepCreation,
  keptCreationKey,
  operationKey,
  readDraft,
  readKeptCreation,
  referenceProblem,
  releaseMoved,
  valuesKey,
  type KeptCreation,
  type Values,
} from "./quoteValues"
import { navigate, quotePath } from "./route"
import useAppStore from "./useAppStore"
import useOpenQuoteStore, { openDraftKey } from "./useOpenQuoteStore"

/** The quote_details column of the quote's reference, keyed in the window alone. */
export const REFERENCE_COLUMN = "qd_reference"

/** A quote made from another's revision in the window: a duplicate or a renewal. */
export interface WindowOrigin {
  quoteId: string
  revision: number
  relation: "duplicate" | "renewal"
}

/**
 * Where the window is: being filled from a quote's revision, or failing to be; taking
 * typing; sending its creation; sent with no answer, or a server failure, so its creation may
 * have been made; or offering back a creation kept in the tab.
 */
export type WindowStatus = "loading" | "failed" | "editing" | "sending" | "unknown" | "offered"

export interface NewQuoteWindow {
  title: string
  status: WindowStatus
  /** The reference keyed, as typed. */
  reference: string
  /** The window's quote_details cells, by column id: the columns the underwriter types. */
  details: SampleRow
  /** The values the creation is made over: a new quote's defaults, the carried values or the sheets'. */
  base: Values
  source: QuoteSource | null
  /** Each carried value that has no place on the current sheets, `table.column`. */
  dropped: string[]
  /** Why the last Create was refused or found no answer, or why the window could not be filled. */
  error: string | null
  /** The quote holding the reference keyed, when that refused it. */
  taken: { quoteId: string; label: string } | null
  /** The page was drawn for an earlier release: refused until reloaded. */
  releaseMoved: boolean
  /** Create was pressed with a required field empty: those say so too. */
  checked: boolean
  /** The creation sent and not answered, kept in the tab. */
  kept: KeptCreation | null
  /** The address the window opened at: moving elsewhere, Back or Forward, closes it. */
  address: string
  /** Opened at an address of its own, `/quotes/new`: closing it replaces that with `/`. */
  addressed: boolean
  /** Save as new: the open quote's draft of these values, let go once the quote is made. */
  savedFrom: { draftKey: string | null; values: Values } | null
}

interface NewQuoteState {
  /** The window open; null while none is. */
  shown: NewQuoteWindow | null
  /** Open the window for a new quote, from its columns' defaults. */
  openNew: (options?: { addressed?: boolean }) => void
  /** Open the window for a duplicate or a renewal of `from`, filled from that revision. */
  openDerived: (from: WindowOrigin, options?: { label?: string; addressed?: boolean }) => Promise<void>
  /** Open the window over the open quote, its save refused, filled from the values on its sheets. */
  openSaveAsNew: () => void
  /** Offer back a creation kept in the tab, its answer lost: true when there is one. */
  offerKept: () => boolean
  setReference: (reference: string) => void
  setDetail: (columnId: string, value: SampleValue) => void
  /** Make the quote, once every field fits; with a field that does not, mark them all. */
  create: () => Promise<void>
  /** Send the kept creation again, exactly as it was. */
  tryAgain: () => Promise<void>
  /** Close the window: one that has sent nothing makes nothing, one whose creation is unanswered keeps it. */
  close: () => void
  /** Close the window where it is, as the page moved on: nothing is sent and no address replaced. */
  leave: () => void
  /** Let the kept creation go, and close the window. */
  discard: () => void
}

/** The window's fields: the reference, required, then the columns of quote_details the underwriter types, in the table's order. */
export function windowFields(form: FormSpec): ShownField[] {
  const table = form.schema.tables.find((candidate) => candidate.id === QUOTE_DETAILS_ID)
  if (table === undefined) return []
  const reference = table.columns.find((column) => column.id === REFERENCE_COLUMN)
  const typed = table.columns.filter((column) => !QUOTE_DETAILS_MANAGED.has(column.id))
  const columns = [...(reference === undefined ? [] : [{ ...reference, required: true }]), ...typed]
  return columns.map((column) => ({ key: `${table.id}:${column.id}`, found: { table, column } }))
}

/**
 * What stops each field fitting, by `cellKey`: the reference's rules, and each column's
 * (`cellProblem`), checked whatever the sheets show and whatever else is typed.
 */
export function windowProblems(fields: readonly ShownField[], reference: string, details: SampleRow): Record<string, string> {
  const problems: Record<string, string> = {}
  for (const { found } of fields) {
    if (found === null) continue
    const { table, column } = found
    const problem = column.id === REFERENCE_COLUMN ? referenceProblem(reference) : cellProblem(column, details[column.id])
    if (problem !== null) problems[cellKey(table.id, 0, column.id)] = problem
  }
  return problems
}

/** The quote_details cells of `values` the underwriter types: the app's own left out. */
function typedDetails(values: Values): SampleRow {
  const row = values[QUOTE_DETAILS_ID]?.[0] ?? {}
  return Object.fromEntries(Object.entries(row).filter(([id]) => !QUOTE_DETAILS_MANAGED.has(id)))
}

/** `base` with the window's details over its quote_details, the app's own left out: what the quote is made of. */
function creationValues(base: Values, details: SampleRow): Values {
  return { ...base, [QUOTE_DETAILS_ID]: [{ ...typedDetails(base), ...details }] }
}

/** The quote holding a reference, as a `reference_taken` refusal names it. */
function takenBy(error: unknown): { quoteId: string; label: string } | null {
  const raw = typeof error === "object" && error !== null && "rawDetail" in error ? (error as { rawDetail?: unknown }).rawDetail : null
  if (typeof raw !== "object" || raw === null) return null
  const { quote_id: quoteId, label } = raw as { quote_id?: unknown; label?: unknown }
  return typeof quoteId === "string" && typeof label === "string" ? { quoteId, label } : null
}

/** The server refused what was sent, a status below 500, so nothing of it was written. */
const refused = (error: unknown): boolean => {
  const status = apiErrorStatus(error)
  return status !== null && status < 500
}

/** Where the creation is kept in this tab; null before the release is read. */
function keptKey(): string | null {
  const { release } = useAppStore.getState()
  return release === null ? null : keptCreationKey(release.pipeline_name || "app", release.principal)
}

const here = (): string => `${window.location.pathname}${window.location.search}`

/** A window for `title`, taking typing, over `base`. */
function opened(title: string, base: Values, source: QuoteSource | null, addressed: boolean): NewQuoteWindow {
  return {
    title,
    status: "editing",
    reference: "",
    details: typedDetails(base),
    base,
    source,
    dropped: [],
    error: null,
    taken: null,
    releaseMoved: false,
    checked: false,
    kept: null,
    address: here(),
    addressed,
    savedFrom: null,
  }
}

/** The window filling being read, newest wins: a carry answered after the window closed or opened again fills nothing. */
const fills = latest()

const useNewQuoteStore = create<NewQuoteState>()((set, get) => {
  const update = (change: Partial<NewQuoteWindow>): void => {
    const { shown } = get()
    if (shown !== null) set({ shown: { ...shown, ...change } })
  }

  /** Whoever is signed in may make quotes, and the window is free for one. */
  const free = (): boolean => get().shown === null && (useAppStore.getState().release?.can_edit ?? true)

  /** Send `creation`, kept in the tab, and act on its answer. */
  const send = async (creation: KeptCreation): Promise<void> => {
    const key = keptKey()
    const sentFrom = here()
    update({ status: "sending", kept: creation, error: null, taken: null, releaseMoved: false })
    const { operation, form_revision, release, values, source, reference } = creation
    try {
      const answer = await createAppQuote({ operation, form_revision, release, values, shown: null }, source, reference)
      if (key !== null) forgetCreation(key)
      const done = get().shown
      set({ shown: null })
      // Saved as new: the open quote's values kept in this browser are in the new quote now.
      if (done?.savedFrom != null) {
        const { draftKey, values: sheets } = done.savedFrom
        const draft = draftKey === null ? null : readDraft(draftKey)
        if (draftKey !== null && draft !== null && valuesKey(draft.values) === valuesKey(sheets)) clearDraft(draftKey)
        useToastStore.getState().addToast("success", `Saved as ${answer.quote.label}`)
      }
      // Answered once the page moved on, Back included, the quote is made and the page stays.
      if (here() === sentFrom) await navigate(quotePath(answer.quote.id), { force: done?.savedFrom != null })
    } catch (error: unknown) {
      if (refused(error)) {
        // Refused, it was not made and never will be: the window takes typing again, saying why.
        if (key !== null) forgetCreation(key)
        const moved = releaseMoved(error)
        if (moved) useAppStore.getState().noteReleaseChanged()
        update({
          status: "editing",
          kept: null,
          error: apiErrorMessage(error),
          taken: apiErrorCode(error) === "reference_taken" ? takenBy(error) : null,
          releaseMoved: moved,
        })
      } else {
        // No answer, or a server failure: it may have been made, so it stays kept.
        update({ status: "unknown", error: apiErrorMessage(error) })
      }
    }
  }

  const offerKept = (): boolean => {
    const key = keptKey()
    const kept = key === null ? null : readKeptCreation(key)
    if (kept === null || get().shown !== null) return false
    fills.bump()
    set({
      shown: {
        ...opened(kept.title, kept.values, kept.source, false),
        status: "offered",
        reference: kept.reference,
        details: typedDetails(kept.values),
        kept,
      },
    })
    return true
  }

  const shut = (replaceAddress: boolean): void => {
    const { shown } = get()
    if (shown === null || shown.status === "sending") return
    fills.bump()
    set({ shown: null })
    if (replaceAddress && shown.addressed) void navigate("/", { replace: true, force: true })
  }

  return {
    shown: null,

    openNew: (options = {}) => {
      // A creation kept, its answer lost, is offered back before anything else is made.
      if (!free() || offerKept()) return
      const { form } = useAppStore.getState()
      if (form === null) return
      fills.bump()
      set({ shown: opened("New quote", structuredClone(newQuoteValues(form.schema)) as Values, null, options.addressed ?? false) })
    },

    openDerived: async (from, options = {}) => {
      if (!free() || offerKept()) return
      const named = options.label === undefined ? "quote" : options.label
      const title = from.relation === "renewal" ? `Renew ${named}` : `Duplicate ${named}`
      const source = { quote_id: from.quoteId, revision: from.revision, relation: from.relation }
      const ticket = fills.take()
      set({ shown: { ...opened(title, {}, source, options.addressed ?? false), status: "loading" } })
      try {
        // That revision's values on the current sheets, without the details the app set for it.
        const carried = await carryAppQuote(from.quoteId, from.revision)
        if (!fills.is(ticket)) return
        const values = from.relation === "renewal" ? asRenewal(carried.values as Values) : (carried.values as Values)
        update({ status: "editing", base: values, details: typedDetails(values), dropped: carried.dropped })
      } catch (error: unknown) {
        if (fills.is(ticket)) update({ status: "failed", error: apiErrorMessage(error) })
      }
    },

    openSaveAsNew: () => {
      if (!free() || offerKept()) return
      const open = useOpenQuoteStore.getState()
      const origin = open.conflict?.latest ?? open.quote
      if (origin === null) return
      // The values on the sheets, unsaved ones and all, as they are now.
      const values = structuredClone(useWorkbenchPreviewStore.getState().quote) as Values
      const source = { quote_id: origin.id, revision: origin.latest, relation: "duplicate" as const }
      fills.bump()
      set({ shown: { ...opened("Save as a new quote", values, source, false), savedFrom: { draftKey: openDraftKey(open), values } } })
    },

    offerKept,

    setReference: (reference) => {
      if (get().shown?.status === "editing") update({ reference })
    },

    setDetail: (columnId, value) => {
      const { shown } = get()
      if (shown?.status === "editing") update({ details: { ...shown.details, [columnId]: value } })
    },

    create: async () => {
      const { shown } = get()
      const { form, revision, release } = useAppStore.getState()
      if (shown === null || shown.status !== "editing" || form === null || release === null) return
      if (Object.keys(windowProblems(windowFields(form), shown.reference, shown.details)).length > 0) {
        update({ checked: true })
        return
      }
      // Made once and kept before it is sent: tried again, after a reload too, it is sent as it was.
      const creation: KeptCreation = {
        operation: operationKey(),
        form_revision: revision,
        release: release.id,
        reference: shown.reference.trim(),
        values: creationValues(shown.base, shown.details),
        source: shown.source,
        title: shown.title,
      }
      const key = keptKey()
      // A tab that keeps nothing sends it all the same.
      if (key !== null) keepCreation(key, creation)
      await send(creation)
    },

    tryAgain: async () => {
      const { shown } = get()
      if (shown === null || shown.kept === null || (shown.status !== "unknown" && shown.status !== "offered")) return
      await send(shown.kept)
    },

    close: () => shut(true),

    leave: () => shut(false),

    discard: () => {
      const key = keptKey()
      if (key !== null) forgetCreation(key)
      shut(true)
    },
  }
})

export default useNewQuoteStore
