/**
 * The Quotes page's list (specs/workbench): a page of the list the address names, the next
 * pages as they are asked for, and each list's and stage's count. The newest load wins: an
 * answer to a list since left is dropped.
 */
import { create } from "zustand"
import { fetchAppQuotes, type QuotesQuery } from "../api/app"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchAppQuote } from "../api/types"
import { canonicalJson } from "../utils/canonicalJson"

interface QuoteListState {
  /** The list loaded, as its query. */
  query: QuotesQuery | null
  quotes: WorkbenchAppQuote[]
  counts: Record<string, number>
  next: string | null
  status: "idle" | "loading" | "ready" | "error"
  error: string | null
  loadingMore: boolean
  /** Load `query`'s first page, unless it is the list already loaded and `again` is not asked. */
  load: (query: QuotesQuery, again?: boolean) => Promise<void>
  /** Load the list shown again, whichever it is now. */
  reload: () => Promise<void>
  more: () => Promise<void>
}

let asked = 0

const useQuoteListStore = create<QuoteListState>()((set, get) => ({
  query: null,
  quotes: [],
  counts: {},
  next: null,
  status: "idle",
  error: null,
  loadingMore: false,
  load: async (query, again = false) => {
    const same = get().query !== null && canonicalJson(get().query) === canonicalJson(query)
    if (same && !again && get().status !== "error") return
    const attempt = ++asked
    // A reload keeps the rows showing; another list shows placeholders while it loads.
    set(same && get().status === "ready" ? { error: null } : { query, status: "loading", error: null, quotes: [], next: null })
    try {
      const page = await fetchAppQuotes(query)
      if (attempt !== asked) return
      set({ query, quotes: page.quotes, counts: page.counts, next: page.next_cursor ?? null, status: "ready" })
    } catch (error: unknown) {
      if (attempt === asked) set({ status: "error", error: apiErrorMessage(error) })
    }
  },
  reload: async () => {
    const { query } = get()
    if (query !== null) await get().load(query, true)
  },
  more: async () => {
    const { query, next, loadingMore } = get()
    if (query === null || next === null || loadingMore) return
    const attempt = asked
    set({ loadingMore: true })
    try {
      const page = await fetchAppQuotes(query, next)
      if (attempt !== asked) return
      set((s) => ({ quotes: [...s.quotes, ...page.quotes], counts: page.counts, next: page.next_cursor ?? null }))
    } catch (error: unknown) {
      if (attempt === asked) set({ error: apiErrorMessage(error) })
    } finally {
      set({ loadingMore: false })
    }
  },
}))

export default useQuoteListStore
