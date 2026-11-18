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
const page = (references: string[], next: string | null = null): WorkbenchAppQuotesResponse => ({
  quotes: references.map((reference) => appQuote({ reference })),
  next_cursor: next,
  counts: { mine: references.length },
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
    expect(store()).toMatchObject({ status: "ready", quotes: [expect.objectContaining({ reference: "a" })] })
    await again
    expect(store().quotes.map((quote) => quote.reference)).toEqual(["a", "b"])
  })

  it("loads the list shown again, whichever it is now", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue({ quotes: [], counts: {}, next_cursor: null })
    await store().reload()
    expect(fetchAppQuotes).not.toHaveBeenCalled()

    await store().load(MINE)
    await store().load(TEAM)
    await store().reload()

    expect(vi.mocked(fetchAppQuotes).mock.calls.at(-1)?.[0]).toEqual(TEAM)
    expect(fetchAppQuotes).toHaveBeenCalledTimes(3)
  })

  it("drops an answer to a list since left", async () => {
    let first: (value: WorkbenchAppQuotesResponse) => void = () => {}
    vi.mocked(fetchAppQuotes).mockReturnValueOnce(new Promise((resolve) => (first = resolve))).mockResolvedValueOnce(page(["team"]))
    const mine = store().load(MINE)
    await store().load(TEAM)
    first(page(["mine"]))
    await mine

    expect(store().query).toEqual(TEAM)
    expect(store().quotes.map((quote) => quote.reference)).toEqual(["team"])
  })

  it("loads the next page after the last, and says why one could not be read", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page(["a"], "c1")).mockResolvedValueOnce(page(["b"], "c2")).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "down"))
    await store().load(MINE)
    await store().more()
    expect(store().quotes.map((quote) => quote.reference)).toEqual(["a", "b"])
    expect(fetchAppQuotes).toHaveBeenLastCalledWith(MINE, "c1")

    await store().more()
    expect(store()).toMatchObject({ error: "down", loadingMore: false, next: "c2" })
  })

  it("lets another list load its own further page while the first's is awaited, and keeps it when that answer comes", async () => {
    let firstMore: (value: WorkbenchAppQuotesResponse) => void = () => {}
    vi.mocked(fetchAppQuotes)
      .mockResolvedValueOnce(page(["a"], "mine-1"))
      .mockReturnValueOnce(new Promise((resolve) => (firstMore = resolve)))
      .mockResolvedValueOnce(page(["t"], "team-1"))
      .mockResolvedValueOnce(page(["t2"], null))
    await store().load(MINE)
    const awaited = store().more()
    expect(store().loadingMore).toBe(true)

    await store().load(TEAM)
    expect(store().loadingMore).toBe(false)
    await store().more()
    expect(vi.mocked(fetchAppQuotes).mock.calls.at(-1)).toEqual([TEAM, "team-1"])
    expect(store().quotes.map((quote) => quote.reference)).toEqual(["t", "t2"])

    firstMore(page(["b"], "mine-2"))
    await awaited
    expect(store()).toMatchObject({ query: TEAM, next: null, loadingMore: false })
    expect(store().quotes.map((quote) => quote.reference)).toEqual(["t", "t2"])
  })

  it("keeps a further page the newer list is loading when the first list's page answers", async () => {
    let firstMore: (value: WorkbenchAppQuotesResponse) => void = () => {}
    let teamMore: (value: WorkbenchAppQuotesResponse) => void = () => {}
    vi.mocked(fetchAppQuotes)
      .mockResolvedValueOnce(page(["a"], "mine-1"))
      .mockReturnValueOnce(new Promise((resolve) => (firstMore = resolve)))
      .mockResolvedValueOnce(page(["t"], "team-1"))
      .mockReturnValueOnce(new Promise((resolve) => (teamMore = resolve)))
    await store().load(MINE)
    const awaited = store().more()
    await store().load(TEAM)
    const teams = store().more()
    firstMore(page(["b"], "mine-2"))
    await awaited
    expect(store().loadingMore).toBe(true)
    teamMore(page(["t2"], null))
    await teams
    expect(store()).toMatchObject({ loadingMore: false, next: null })
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
