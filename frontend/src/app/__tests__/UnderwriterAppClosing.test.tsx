/**
 * The underwriter app's quotes closing as its routes answer (specs/workbench), heard through
 * the app's own API module: a closed store's 503, from whichever call met it, closes them, a
 * busy pricing's does not, and text typed into a field and not yet left is kept in the open
 * quote's draft as the quote closes under it, offered back once the quotes open again.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  request: vi.fn(),
  post: vi.fn(),
}))

import { fetchAppQuotes, priceAppQuote, type QuotesQuery } from "../../api/app"
import { ApiError, post, request } from "../../api/client"
import type { WorkbenchAppQuoteStore } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { detailsTable, sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { draftKey } from "../quoteValues"
import { navigate, setLeaveGuard } from "../route"
import UnderwriterApp, { QUOTES_RECHECK_MS } from "../UnderwriterApp"
import useOpenQuoteStore from "../useOpenQuoteStore"
import { answer, OPEN, release } from "./fixtures"

const LIST: QuotesQuery = { view: "mine", stage: null, search: "", assignee: null, sort: "saved" }
const UNREACHED = { message: "The app cannot reach its quote store just now.", error_code: "quote_store_unreachable", differences: [] }
const KEPT = draftKey("motor", "ana@example.com", "Q-000001/1")

/** What the app's routes answer: the store `quotes` says, its quote routes refused while it is closed. */
let quotes: WorkbenchAppQuoteStore = OPEN

const refusal = () => new ApiError("HTTP 503", 503, UNREACHED.message, { detail: UNREACHED }, UNREACHED)

function serve(path: string): unknown {
  if (path === "/api/app/release") return { ...release, quotes }
  if (path === "/api/app/form") {
    const sheets = sheetForm()
    return { form: { ...sheets, schema: { tables: [detailsTable(), ...sheets.schema.tables] } }, revision: "rev-9" }
  }
  if (path.startsWith("/api/app/") && !quotes.open) throw refusal()
  if (path.startsWith("/api/app/quotes?")) return { quotes: [], next_cursor: null, counts: {} }
  if (path === "/api/app/people") return { people: [] }
  if (path === "/api/app/quotes/Q-000001/1") return answer()
  if (path.endsWith("/activity")) return { entries: [], source: null, derived: [] }
  throw new Error(`No route answers ${path}`)
}

describe("UnderwriterApp's quotes closing", () => {
  beforeEach(() => {
    stubLayout()
    quotes = OPEN
    vi.mocked(request).mockImplementation(async (path: string) => serve(path) as never)
    vi.mocked(post).mockImplementation(async () => {
      if (!quotes.open) throw refusal()
      return { tables: { premiums: [] }, request: {} } as never
    })
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, underwriterApp: false, pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
    useOpenQuoteStore.setState({ status: "idle", quote: null, base: null, pending: null, conflict: null, refusal: null, error: null, historyOpen: false, activity: null, recovered: null })
  })

  afterEach(async () => {
    cleanup()
    vi.clearAllMocks()
    setLeaveGuard(null)
    window.localStorage.clear()
    window.sessionStorage.clear()
    await navigate("/", { force: true })
  })

  it("closes when a route refuses because the store is closed, and not for a busy pricing", async () => {
    await navigate("/", { force: true })
    render(<UnderwriterApp />)
    await screen.findByTestId("quotes-page")

    const busy = new ApiError("HTTP 503", 503, "The app is busy pricing other quotes: try again in a few seconds.")
    vi.mocked(post).mockRejectedValueOnce(busy)
    await act(async () => {
      await expect(priceAppQuote("rev-9", release.id, {})).rejects.toBe(busy)
    })
    expect(screen.getByTestId("quotes-page")).toBeInTheDocument()

    quotes = { ...UNREACHED, open: false }
    await act(async () => {
      await expect(fetchAppQuotes(LIST)).rejects.toMatchObject({ status: 503 })
    })

    expect(await screen.findByTestId("notice-quotes-closed")).toHaveTextContent(UNREACHED.message)
    expect(screen.queryByTestId("quotes-page")).toBeNull()
  })

  it("keeps text typed into a field and not yet left as the quote closes, offering it back once they open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      await navigate("/quotes/Q-000001/1", { force: true })
      render(<UnderwriterApp />)
      await screen.findByTestId("quote-line")
      const field = screen.getByRole("textbox", { name: "Item row 1" })
      act(() => field.focus())
      fireEvent.change(field, { target: { value: "Excavator" } })

      quotes = { ...UNREACHED, open: false }
      await act(async () => {
        await expect(fetchAppQuotes(LIST)).rejects.toMatchObject({ status: 503 })
      })

      expect(await screen.findByTestId("notice-quotes-closed")).toBeInTheDocument()
      expect(window.localStorage.getItem(KEPT)).toContain("Excavator")

      quotes = OPEN
      await act(async () => {
        await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
      })
      expect(await screen.findByTestId("notice-recovered")).toBeInTheDocument()
    } finally {
      vi.useRealTimers()
    }
  })
})
