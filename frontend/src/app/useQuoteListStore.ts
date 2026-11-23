/**
 * The Quotes page's list (specs/workbench): a page of the list the address names, the next
 * pages as they are asked for, and each list's and stage's count; a list the quote store
 * refused says why, naming each table and column that does not fit, until a load is answered
 * (`appErrorMessage`). The newest load wins: an
 * answer to a list since left is dropped, and a further page belongs to the list loaded, so
 * loading another list ends its loading at once (specs/frontend-shared, "Late answers").
 */
import { create } from "zustand"
import { fetchAppQuotes, type QuotesQuery } from "../api/app"
import type { WorkbenchAppQuote } from "../api/types"
import { answer, newest, requestKind, under } from "../utils/answers"
import { canonicalJson } from "../utils/canonicalJson"
import { appErrorMessage, storeRefused } from "./quoteValues"
import useAppStore from "./useAppStore"

interface QuoteListState {
  /** The list loaded, as its query. */
  query: QuotesQuery | null
  quotes: WorkbenchAppQuote[]
  counts: Record<string, number>
  next: string | null
  status: "idle" | "loading" | "ready" | "error"
  error: string | null
  /** The list's error is the quote store's refusal, which a load past its bound may lift. */
  storeRefused: boolean
  /**
   * The refusals answered in a row, each one the page's cue to ask again: a refusal the same
   * as the last changes nothing else.
   */
  refusals: number
  loadingMore: boolean
  /** Load `query`'s first page, unless it is the list already loaded and `again` is not asked. */
  load: (query: QuotesQuery, again?: boolean) => Promise<void>
  /** Load the list shown again, whichever it is now. */
  reload: () => Promise<void>
  more: () => Promise<void>
}

const listAsked = requestKind()

const useQuoteListStore = create<QuoteListState>()((set, get) => ({
  query: null,
  quotes: [],
  counts: {},
  next: null,
  status: "idle",
  error: null,
  storeRefused: false,
  refusals: 0,
  loadingMore: false,
  load: async (query, again = false) => {
    const { status, storeRefused: refused } = get()
    const same = get().query !== null && canonicalJson(get().query) === canonicalJson(query)
    if (same && !again && status !== "error") return
    const belongsTo = [newest(listAsked)] as const
    // A reload keeps the rows showing, and a list the store refused keeps saying why until it
    // is answered; another list shows placeholders while it loads.
    if (same && status === "ready") set({ error: null })
    else if (!(same && status === "error" && refused)) set({ query, status: "loading", error: null, storeRefused: false, quotes: [], next: null })
    await answer(belongsTo, fetchAppQuotes(query), {
      ok: (page) => set({ query, quotes: page.quotes, counts: page.counts, next: page.next_cursor ?? null, status: "ready", storeRefused: false, refusals: 0 }),
      failed: (error) => {
        const refused = storeRefused(error)
        set((s) => ({ status: "error", error: appErrorMessage(error, useAppStore.getState().form), storeRefused: refused, refusals: refused ? s.refusals + 1 : 0 }))
      },
    })
  },
  reload: async () => {
    const { query } = get()
    if (query !== null) await get().load(query, true)
  },
  more: async () => {
    const { query, next, loadingMore } = get()
    if (query === null || next === null || loadingMore) return
    await answer([under(listAsked)], fetchAppQuotes(query, next), {
      ok: (page) => set((s) => ({ quotes: [...s.quotes, ...page.quotes], counts: page.counts, next: page.next_cursor ?? null })),
      failed: (error) => set({ error: appErrorMessage(error, useAppStore.getState().form, { oneLine: true }) }),
    }, { pending: (waiting) => set({ loadingMore: waiting }) })
  },
}))

export default useQuoteListStore
