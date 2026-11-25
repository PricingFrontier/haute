/**
 * The underwriter app's quote store refusing its routes (specs/workbench), heard through the
 * app's own API module: the app stays open whatever the store holds, and each refusal is said
 * where it was met, naming each table and column that does not fit: the Quotes page's list, a
 * quote's own address, a pricing in the bar, and a save, which keeps the quote open with its
 * values kept in its draft.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"

vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/client")>()),
  request: vi.fn(),
  post: vi.fn(),
  sendKeyed: vi.fn(),
}))

import type { ApiError } from "../../api/client"
import { post, request, sendKeyed } from "../../api/client"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { detailsTable, sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { draftKey, QUOTES_RECHECK_MS } from "../quoteValues"
import { navigate, setLeaveGuard } from "../route"
import UnderwriterApp from "../UnderwriterApp"
import useOpenQuoteStore from "../useOpenQuoteStore"
import useQuoteListStore from "../useQuoteListStore"
import { answer, MISFIT, release, storeRefusal } from "./fixtures"

const KEPT = draftKey("motor", "ana@example.com", "Q-000001/1")

/** The refusal the store answers the quote routes with, or none while it fits. */
let refused: ApiError | null = null

function serve(path: string): unknown {
  if (path === "/api/app/release") return release
  if (path === "/api/app/form") {
    const sheets = sheetForm()
    return { form: { ...sheets, schema: { tables: [detailsTable(), ...sheets.schema.tables] } }, revision: "rev-9" }
  }
  if (path.startsWith("/api/app/") && refused !== null) throw refused
  if (path.startsWith("/api/app/quotes?")) return { quotes: [], next_cursor: null, counts: {} }
  if (path === "/api/app/people") return { people: [] }
  if (path === "/api/app/quotes/Q-000001/1") return answer()
  if (path.endsWith("/activity")) return { entries: [], source: null, derived: [] }
  throw new Error(`No route answers ${path}`)
}

describe("UnderwriterApp's quote store refusals", () => {
  beforeEach(() => {
    stubLayout()
    refused = null
    vi.mocked(request).mockImplementation(async (path: string) => serve(path) as never)
    vi.mocked(post).mockImplementation(async () => {
      if (refused !== null) throw refused
      return { tables: { premiums: [] }, request: {} } as never
    })
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, underwriterApp: false, pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
    useOpenQuoteStore.setState({ status: "idle", quote: null, base: null, pending: null, conflict: null, refusal: null, error: null, historyOpen: false, activity: null, recovered: null })
    useQuoteListStore.setState({ query: null, quotes: [], counts: {}, next: null, status: "idle", error: null, storeRefused: false, refusals: 0, loadingMore: false })
  })

  afterEach(async () => {
    cleanup()
    vi.clearAllMocks()
    setLeaveGuard(null)
    window.localStorage.clear()
    window.sessionStorage.clear()
    await navigate("/", { force: true })
  })

  it("says a refusal where it was met, the app staying open: the list, and a quote at its own address until it opens", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      refused = storeRefusal(MISFIT)
      await navigate("/", { force: true })
      render(<UnderwriterApp />)

      const listed = await screen.findByTestId("quotes-error")
      expect(listed).toHaveTextContent("The quotes could not be read: The dev database doesn't match the form:")
      expect(listed).toHaveTextContent("Missing fields: policy.state")
      expect(screen.queryByTestId("notice-quotes-closed")).toBeNull()

      await act(async () => {
        await navigate("/quotes/Q-000001/1")
      })
      const opened = await screen.findByTestId("quote-load-error")
      expect(opened.textContent).toBe(
        [
          "The quote could not be opened: The dev database doesn't match the form:",
          "  Missing fields: policy.state",
        ].join("\n"),
      )
      expect(screen.getByTestId("app-release")).toBeInTheDocument()

      // Asked for again on the store's bound, saying why meanwhile, it opens once the tables fit.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
      })
      expect(screen.getByTestId("quote-load-error")).toBeInTheDocument()
      refused = null
      await act(async () => {
        await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
      })
      expect(await screen.findByTestId("quote-line")).toBeInTheDocument()
      expect(screen.queryByTestId("quote-load-error")).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it("asks for a list the store refused again after each refusal, the same one twice, until it lists", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      refused = storeRefusal(MISFIT)
      await navigate("/", { force: true })
      render(<UnderwriterApp />)
      expect(await screen.findByTestId("quotes-error")).toBeInTheDocument()
      const listed = () => vi.mocked(request).mock.calls.filter(([path]) => String(path).startsWith("/api/app/quotes?")).length
      const first = listed()

      // Refused alike each time, saying why, every answer asks again on the store's bound.
      for (const asked of [1, 2]) {
        await act(async () => {
          await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
        })
        expect(listed()).toBe(first + asked)
        expect(screen.getByTestId("quotes-error")).toBeInTheDocument()
      }
      refused = null
      await act(async () => {
        await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
      })
      expect(listed()).toBe(first + 3)
      expect(screen.queryByTestId("quotes-error")).toBeNull()
    } finally {
      vi.useRealTimers()
    }
  })

  it("keeps a quote whose save the store refuses open, its values kept, naming what does not fit", async () => {
    await navigate("/quotes/Q-000001/1/edit", { force: true })
    render(<UnderwriterApp />)
    await screen.findByTestId("quote-line")
    act(() => useWorkbenchPreviewStore.getState().setCell("equipment", 0, "item", "Excavator"))
    const save = screen.getByTestId("save-quote")
    await vi.waitFor(() => expect(save).not.toBeDisabled())

    vi.mocked(sendKeyed).mockRejectedValueOnce(storeRefusal(MISFIT))
    fireEvent.click(save)

    const said = await screen.findByTestId("notice-error")
    expect(said).toHaveTextContent("The dev database doesn't match the form:")
    expect(said).toHaveTextContent("Missing fields: policy.state")
    expect(screen.getByTestId("quote-line")).toBeInTheDocument()
    expect(screen.getByRole("textbox", { name: "Item row 1" })).toHaveValue("Excavator")
    expect(window.localStorage.getItem(KEPT)).toContain("Excavator")
    // Refused before anything was written, the save is let go rather than held to repeat.
    expect(useOpenQuoteStore.getState().pending).toBeNull()
  })

  it("says a pricing the store refuses in the bar, on one line", async () => {
    await navigate("/quotes/Q-000001/1/edit", { force: true })
    render(<UnderwriterApp />)
    await screen.findByTestId("quote-line")

    refused = storeRefusal(MISFIT)
    act(() => useWorkbenchPreviewStore.getState().setCell("equipment", 0, "item", "Excavator"))

    expect(await screen.findByTestId("app-pricing-error")).toHaveTextContent(
      "Pricing failed: The dev database doesn't match the form: Missing fields: policy.state.",
    )
    expect(screen.getByTestId("quote-line")).toBeInTheDocument()
  })
})
