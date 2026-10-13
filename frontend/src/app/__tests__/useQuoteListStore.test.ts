/**
 * The Quotes page's list (specs/workbench): a page of the list asked for, the next pages,
 * a reload that keeps the rows showing, and an answer to a list since left dropped.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/app", () => ({ fetchAppQuotes: vi.fn() }))

import { fetchAppQuotes } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuotesResponse } from "../../api/types"
import { quotesQueryOf } from "../route"
import useQuoteListStore from "../useQuoteListStore"
import { appQuote } from "./fixtures"

const MINE = quotesQueryOf("")
const TEAM = quotesQueryOf("?view=team")
const page = (ids: string[], next: string | null = null): WorkbenchAppQuotesResponse => ({
  quotes: ids.map((id) => appQuote({ id, reference: id })),
  next_cursor: next,
  counts: { mine: ids.length },
})
const store = () => useQuoteListStore.getState()

describe("useQuoteListStore", () => {
  beforeEach(() => {
    useQuoteListStore.setState({ query: null, quotes: [], counts: {}, next: null, status: "idle", error: null, loadingMore: false })
  })

  afterEach(() => vi.clearAllMocks())

  it("loads a list, and the same list again only when asked, keeping its rows meanwhile", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page(["a"])).mockResolvedValueOnce(page(["a", "b"]))
    await store().load(MINE)
    await store().load(MINE)
    expect(fetchAppQuotes).toHaveBeenCalledTimes(1)

    const again = store().load(MINE, true)
    expect(store()).toMatchObject({ status: "ready", quotes: [expect.objectContaining({ id: "a" })] })
    await again
    expect(store().quotes.map((quote) => quote.id)).toEqual(["a", "b"])
  })

  it("drops an answer to a list since left", async () => {
    let first: (value: WorkbenchAppQuotesResponse) => void = () => {}
    vi.mocked(fetchAppQuotes).mockReturnValueOnce(new Promise((resolve) => (first = resolve))).mockResolvedValueOnce(page(["team"]))
    const mine = store().load(MINE)
    await store().load(TEAM)
    first(page(["mine"]))
    await mine

    expect(store().query).toEqual(TEAM)
    expect(store().quotes.map((quote) => quote.id)).toEqual(["team"])
  })

  it("loads the next page after the last, and says why one could not be read", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page(["a"], "c1")).mockResolvedValueOnce(page(["b"], "c2")).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "down"))
    await store().load(MINE)
    await store().more()
    expect(store().quotes.map((quote) => quote.id)).toEqual(["a", "b"])
    expect(fetchAppQuotes).toHaveBeenLastCalledWith(MINE, "c1")

    await store().more()
    expect(store()).toMatchObject({ error: "down", loadingMore: false, next: "c2" })
  })

  it("loads nothing more past the last page", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page(["a"]))
    await store().load(MINE)
    await store().more()
    expect(fetchAppQuotes).toHaveBeenCalledTimes(1)
  })

  it("says why a list could not be read, and reads it again after", async () => {
    vi.mocked(fetchAppQuotes).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "down")).mockResolvedValueOnce(page(["a"]))
    await store().load(MINE)
    expect(store()).toMatchObject({ status: "error", error: "down" })
    await store().load(MINE)
    expect(store().status).toBe("ready")
  })
})
