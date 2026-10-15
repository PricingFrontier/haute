/**
 * The Quotes page (specs/workbench): the lists and stages with their counts, the list as a
 * table of the quote list's columns, the search and filters in the address, a page at a
 * time, each row's actions, and what an empty, loading or failed list says.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  fetchAppQuotes: vi.fn(),
  changeAppQuote: vi.fn(),
  fetchAppPeople: vi.fn(),
}))

import { changeAppQuote, fetchAppPeople, fetchAppQuotes, type QuotesQuery } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuote, WorkbenchAppQuotesResponse } from "../../api/types"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import QuotesPage from "../QuotesPage"
import { navigate, quotesQueryOf, setLeaveGuard } from "../route"
import useAppStore from "../useAppStore"
import useQuoteListStore from "../useQuoteListStore"
import { appQuote, release } from "./fixtures"

const MINE: QuotesQuery = quotesQueryOf("")
const page = (quotes: WorkbenchAppQuote[], next: string | null = null): WorkbenchAppQuotesResponse => ({
  quotes,
  next_cursor: next,
  counts: { mine: quotes.length, team: 5, archived: 1, draft: 2, priced: 3, final: 0 },
})

function show(query: QuotesQuery = MINE) {
  return render(
    <>
      <QuotesPage query={query} />
      <ToastContainer />
    </>,
  )
}

describe("QuotesPage", () => {
  beforeEach(async () => {
    const sheets = sheetForm()
    const form = { ...sheets, schema: { tables: [detailsTable({ broker: "Placing broker" }), ...sheets.schema.tables] } }
    useAppStore.setState({ release, form, revision: "rev-9", people: [] })
    useQuoteListStore.setState({ query: null, quotes: [], counts: {}, next: null, status: "idle", error: null, loadingMore: false })
    useToastStore.setState({ toasts: [] })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: ["ana@example.com", "jo@example.com"] })
    await navigate("/", { force: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    setLeaveGuard(null)
  })

  it("lists the quotes under their details' columns, with the lists' and stages' counts", async () => {
    const details = { reference: "Q-000001", quote_name: "Fleet", insured: "Acme", broker: "Marsh", inception_date: "2026-11-01" }
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([appQuote({ details, stage: "priced" }), appQuote({ id: "q2", reference: "Q-000002", title: "CA", assignee: "jo@example.com", stage: "draft" })]))
    show()

    const row = await screen.findByTestId("quote-row-Q-000001")
    expect(within(row).getByText("Fleet")).toBeInTheDocument()
    expect(within(row).getByText("Acme")).toBeInTheDocument()
    expect(within(row).getByText("Marsh")).toBeInTheDocument()
    expect(within(row).getByText(/2026/)).not.toHaveTextContent("2026-11-01")
    expect(within(row).getByText("You")).toBeInTheDocument()
    expect(within(row).getByTestId("stage-priced")).toBeInTheDocument()
    expect(within(screen.getByTestId("quote-row-Q-000002")).getByText("jo@example.com")).toBeInTheDocument()
    for (const header of ["Ref", "Quote", "Insured", "Placing broker", "Inception date", "Stage", "With", "Saved"]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument()
    }
    expect(screen.getByTestId("list-team")).toHaveTextContent("5")
    expect(screen.getByTestId("stage-filter-priced")).toHaveTextContent("3")
    expect(fetchAppQuotes).toHaveBeenCalledWith(MINE)
  })

  it("puts the list, stage, order and assignee in the address", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([]))
    show(quotesQueryOf("?view=team"))
    await screen.findByTestId("quotes-empty")

    fireEvent.click(screen.getByTestId("stage-filter-final"))
    expect(window.location.search).toBe("?view=team&stage=final")
    fireEvent.change(screen.getByLabelText("Order"), { target: { value: "insured" } })
    expect(window.location.search).toContain("sort=insured")
    await screen.findByRole("option", { name: "jo@example.com" })
    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })
    expect(window.location.search).toContain("assignee=jo%40example.com")
    fireEvent.click(screen.getByTestId("list-archived"))
    expect(window.location.search).toBe("?view=archived")
  })

  it("puts the search in the address after a pause, and takes one reached from the address", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      vi.mocked(fetchAppQuotes).mockResolvedValue(page([]))
      const { rerender } = show()
      fireEvent.change(screen.getByLabelText("Search quotes"), { target: { value: "acme" } })
      expect(window.location.search).toBe("")
      await act(async () => vi.advanceTimersByTime(300))
      expect(window.location.search).toBe("?q=acme")

      rerender(<QuotesPage query={quotesQueryOf("?q=fleet")} />)
      expect(screen.getByLabelText("Search quotes")).toHaveValue("fleet")
    } finally {
      vi.useRealTimers()
    }
  })

  it("says why a list is empty: nothing yet, nothing matching, nothing archived", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([]))
    show()
    expect(await screen.findByTestId("quotes-empty")).toHaveTextContent("No quotes are with you yet.")
    cleanup()

    show(quotesQueryOf("?q=zzz"))
    expect(await screen.findByTestId("quotes-empty")).toHaveTextContent("No quotes match.")
    fireEvent.click(screen.getByRole("button", { name: "Clear search" }))
    expect(window.location.search).toBe("")
    cleanup()

    show(quotesQueryOf("?view=archived"))
    expect(await screen.findByTestId("quotes-empty")).toHaveTextContent("Nothing is archived.")
  })

  it("says why the list could not be read, and reads it again on Retry", async () => {
    vi.mocked(fetchAppQuotes).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "The store is down")).mockResolvedValue(page([appQuote()]))
    show()

    expect(await screen.findByRole("alert")).toHaveTextContent("The quotes could not be read: The store is down")
    fireEvent.click(screen.getByRole("button", { name: "Retry" }))
    expect(await screen.findByTestId("quote-row-Q-000001")).toBeInTheDocument()
  })

  it("loads the next page on Load more", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page([appQuote()], "next-1")).mockResolvedValueOnce(page([appQuote({ id: "q2", reference: "Q-000002" })]))
    show()

    fireEvent.click(await screen.findByRole("button", { name: "Load more" }))

    expect(await screen.findByTestId("quote-row-Q-000002")).toBeInTheDocument()
    expect(fetchAppQuotes).toHaveBeenLastCalledWith(MINE, "next-1")
    expect(screen.getByText("2 quotes")).toBeInTheDocument()
  })

  it("opens a quote from its row, and duplicates one as a new quote", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([appQuote({ latest: 3 })]))
    show()

    fireEvent.click(await screen.findByTestId("quote-row-Q-000001"))
    expect(window.location.pathname).toBe("/quotes/q1")
    await navigate("/", { force: true })
    fireEvent.click(screen.getByRole("button", { name: "Duplicate Q-000001" }))
    expect(`${window.location.pathname}${window.location.search}`).toBe("/quotes/new?from=q1&revision=3&as=duplicate")
  })

  it("archives a quote with Undo, which restores it", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([appQuote()]))
    vi.mocked(changeAppQuote).mockResolvedValueOnce({ quote: appQuote({ version: 2, archived: true, archived_at: "2026-10-12T10:00:00+00:00" }), revision: null }).mockResolvedValueOnce({ quote: appQuote({ version: 3 }), revision: null })
    show()

    fireEvent.click(await screen.findByRole("button", { name: "Archive Q-000001" }))
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }))

    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledTimes(2))
    expect(vi.mocked(changeAppQuote).mock.calls).toEqual([
      ["q1", "archive", 1, {}],
      ["q1", "restore", 2, {}],
    ])
  })

  it("says when a quote could not be archived", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([appQuote()]))
    vi.mocked(changeAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "moved", {}, { message: "Q-000001 has changed" }))
    show()

    fireEvent.click(await screen.findByRole("button", { name: "Archive Q-000001" }))

    expect(await screen.findByText("Could not archive Q-000001: Q-000001 has changed")).toBeInTheDocument()
  })

  it("restores an archived quote from the archive", async () => {
    // A list row knows it is archived, not by whom or when: those are read with the quote.
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([appQuote({ archived: true })]))
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ version: 2 }), revision: null })
    show(quotesQueryOf("?view=archived"))

    fireEvent.click(await screen.findByRole("button", { name: "Restore Q-000001" }))

    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledWith("q1", "restore", 1, {}))
    expect(await screen.findByText("Restored Q-000001")).toBeInTheDocument()
  })

  it("focuses the search on / and starts a new quote on N, but not while typing", async () => {
    vi.mocked(fetchAppQuotes).mockResolvedValue(page([]))
    show()
    await screen.findByTestId("quotes-empty")

    fireEvent.keyDown(window, { key: "/" })
    expect(screen.getByLabelText("Search quotes")).toHaveFocus()
    fireEvent.keyDown(screen.getByLabelText("Search quotes"), { key: "n" })
    expect(window.location.pathname).toBe("/")
    fireEvent.keyDown(document.body, { key: "n" })
    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/new"))
  })

  it("offers one who may only read quotes nothing that changes them", async () => {
    useAppStore.setState({ release: { ...release, can_edit: false, edit_role: "Quotes.Edit" } })
    vi.mocked(fetchAppQuotes).mockResolvedValueOnce(page([appQuote()])).mockResolvedValueOnce(page([]))
    const view = show()

    await screen.findByTestId("quote-row-Q-000001")
    expect(screen.queryByTestId("new-quote")).toBeNull()
    expect(screen.queryByRole("button", { name: "Archive Q-000001" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Duplicate Q-000001" })).toBeNull()
    expect(screen.getByRole("button", { name: "Open Q-000001" })).toBeInTheDocument()
    fireEvent.keyDown(document.body, { key: "n" })
    expect(window.location.pathname).toBe("/")

    view.rerender(<QuotesPage query={quotesQueryOf("?view=team")} />)
    expect(await screen.findByTestId("quotes-empty")).not.toHaveTextContent("New quote")
  })
})
