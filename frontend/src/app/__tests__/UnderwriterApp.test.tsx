/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * its quote's values rather than a sample, nothing of the builder, the release and who is
 * signed in, a quote priced as it is typed through the app's own route on the form
 * revision the page was drawn from, `/quotes/new` the Quotes page with the New quote window
 * open, and a creation kept in the tab offered back as the app opens.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  fetchAppRelease: vi.fn(),
  fetchAppForm: vi.fn(),
  priceAppQuote: vi.fn(),
  fetchAppQuotes: vi.fn(),
  fetchAppPeople: vi.fn(),
  openAppQuote: vi.fn(),
  editAppQuote: vi.fn(async () => ({ editor: "ana@example.com", lease_seconds: 180 })),
  releaseAppQuote: vi.fn(async () => {}),
  createAppQuote: vi.fn(),
}))

import { fetchAppForm, fetchAppPeople, fetchAppQuotes, fetchAppRelease, openAppQuote, priceAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppReleaseResponse } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { detailsTable, sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { keepCreation, keptCreationKey, QUOTES_RECHECK_MS } from "../quoteValues"
import { navigate } from "../route"
import UnderwriterApp from "../UnderwriterApp"
import useNewQuoteStore from "../useNewQuoteStore"
import { answer, CLOSED, MISFIT, OPEN, storeRefusal } from "./fixtures"

const release: WorkbenchAppReleaseResponse = {
  id: "0a1b2c3d4e5f",
  form_revision: "rev-9",
  pipeline_name: "motor",
  haute_version: "1.2.0",
  created_at: "2026-10-12T09:00:00+00:00",
  host: "azure-container-apps",
  principal: "uw@example.com",
  quotes: CLOSED,
  edit_role: null,
  can_edit: true,
  environment: "test",
}

async function opened() {
  render(<UnderwriterApp />)
  await screen.findByTestId("app-release")
}

describe("UnderwriterApp", () => {
  beforeEach(() => {
    stubLayout()
    vi.mocked(fetchAppRelease).mockResolvedValue(release)
    vi.mocked(fetchAppForm).mockResolvedValue({ form: sheetForm(), revision: "rev-9" })
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, underwriterApp: false, pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    useNewQuoteStore.setState({ shown: null })
    window.sessionStorage.clear()
  })

  /** The app keeping quotes, its form holding Haute's quote_details first, at `address`. */
  async function keeping(address: string) {
    const sheets = sheetForm()
    vi.mocked(fetchAppRelease).mockResolvedValue({ ...release, quotes: OPEN })
    vi.mocked(fetchAppForm).mockResolvedValue({ form: { ...sheets, schema: { tables: [detailsTable(), ...sheets.schema.tables] } }, revision: "rev-9" })
    vi.mocked(fetchAppQuotes).mockResolvedValue({ quotes: [], next_cursor: null, counts: {} })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: [] })
    await navigate(address, { force: true })
    await opened()
    await screen.findByTestId("quotes-page")
  }

  it("opens /quotes/new as the Quotes page with the New quote window over it, and Cancel there goes to /", async () => {
    await keeping("/quotes/new")

    const window_ = await screen.findByRole("dialog", { name: "New quote" })
    fireEvent.click(within(window_).getByRole("button", { name: "Cancel" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/"))
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
    expect(screen.getByTestId("quotes-page")).toBeInTheDocument()
  })

  it("opens a duplicate's address, reloaded, as the window filled from that quote", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ reference: "ABC 1", option: 2 }, { values: { quote_details: [{ insured: "Acme" }] } }))

    await keeping("/quotes/new?from=ABC+1&option=2&as=duplicate")

    const window_ = await screen.findByRole("dialog", { name: "Duplicate quote" })
    await vi.waitFor(() => expect(within(window_).getByLabelText("Insured")).toHaveValue("Acme"))
    expect(openAppQuote).toHaveBeenCalledWith("ABC 1", 2)
    await navigate("/", { force: true })
  })

  it("offers back a creation kept in the tab as the app opens, with Try again and Discard", async () => {
    keepCreation(keptCreationKey("motor", "uw@example.com"), {
      operation: "save-kept-0001",
      form_revision: "rev-9",
      release: release.id,
      reference: "ABC-1",
      values: { quote_details: [{ insured: "Acme" }] },
      source: null,
      title: "New quote",
    })

    await keeping("/")

    const window_ = await screen.findByRole("dialog", { name: "New quote" })
    expect(within(window_).getByTestId("new-quote-unknown")).toHaveTextContent("ABC-1 may have been made")
    expect(within(window_).getByRole("button", { name: "Try again" })).toBeInTheDocument()
    fireEvent.click(within(window_).getByRole("button", { name: "Discard" }))
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })

  it("puts the release's sheets where Preview draws from, with the release and who is signed in, and nothing of the builder", async () => {
    await opened()

    expect(screen.getByTestId("app-release")).toHaveTextContent("motor")
    expect(screen.getByTestId("app-release")).toHaveTextContent("release 0a1b2c3d4e5f")
    expect(screen.getByTestId("app-principal")).toHaveTextContent("Signed in as uw@example.com")
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "preview", schemaOpen: false, underwriterApp: true })
    for (const builder of ["Build", "Preview", "Schema", "Save", "Price", "Clear"]) {
      expect(screen.queryByRole("button", { name: builder })).toBeNull()
    }
  })

  it("prices a quote through the app's route on the revision and release the page was drawn for", async () => {
    vi.mocked(priceAppQuote).mockResolvedValue({
      tables: { premiums: [{ item: "Crane", premium: 120 }] },
      request: { policy: { state: "NY" } },
    })
    await opened()
    const pricer = useWorkbenchPricingStore.getState().quotePricer
    expect(pricer).not.toBeNull()

    const priced = await pricer?.(sheetForm(), { policy: [{ state: "NY" }] })

    expect(priceAppQuote).toHaveBeenCalledWith("rev-9", "0a1b2c3d4e5f", { policy: [{ state: "NY" }] })
    expect(priced).toMatchObject({ tables: { premiums: [{ item: "Crane", premium: 120 }] }, sample: { policy: { state: "NY" } } })
  })

  it("opens the Quotes page whatever its store holds, the list naming what does not fit until it is read", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      // The whole store does not fit, as the release says, and the list's own tables do not.
      vi.mocked(fetchAppQuotes).mockRejectedValueOnce(storeRefusal(MISFIT)).mockResolvedValue({ quotes: [], next_cursor: null, counts: {} })
      vi.mocked(fetchAppPeople).mockResolvedValue({ people: [] })
      await navigate("/", { force: true })
      await opened()

      expect(await screen.findByTestId("quotes-page")).toBeInTheDocument()
      const said = await screen.findByTestId("quotes-error")
      expect(said.textContent).toBe(
        [
          "The quotes could not be read: The dev database doesn't match the form:",
          "  Missing fields: policy.state",
        ].join("\n"),
      )
      expect(screen.queryByTestId("notice-quotes-closed")).toBeNull()
      expect(fetchAppQuotes).toHaveBeenCalledTimes(1)

      // Asked for again on the store's bound, the list is read once the tables fit.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(QUOTES_RECHECK_MS)
      })
      expect(await screen.findByTestId("quotes-empty")).toBeInTheDocument()
      expect(fetchAppQuotes).toHaveBeenCalledTimes(2)
      expect(release.quotes).toEqual(CLOSED)
    } finally {
      vi.useRealTimers()
      await navigate("/", { force: true })
    }
  })

  it("says why the sheets could not be opened, a request the host did not sign in among them", async () => {
    const refusal = "Sign in through Azure's built-in authentication: this request carries no signed-in principal."
    vi.mocked(fetchAppRelease).mockRejectedValue(new ApiError("HTTP 401", 401, refusal))

    render(<UnderwriterApp />)

    expect(await screen.findByRole("alert")).toHaveTextContent(`Could not open the sheets: ${refusal}`)
    expect(useWorkbenchPricingStore.getState().quotePricer).toBeNull()
  })

  it("opens on the Quotes page while its quotes are open, and a quote at its own address", async () => {
    vi.mocked(fetchAppRelease).mockResolvedValue({ ...release, quotes: OPEN })
    vi.mocked(fetchAppQuotes).mockResolvedValue({ quotes: [], next_cursor: null, counts: {} })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: [] })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await navigate("/", { force: true })
    await opened()

    expect(await screen.findByTestId("quotes-page")).toBeInTheDocument()
    await act(async () => {
      await navigate("/quotes/Q-000001/1")
    })
    expect(await screen.findByTestId("quote-line")).toHaveTextContent("by ana@example.com")
    expect(openAppQuote).toHaveBeenCalledWith("Q-000001", 1)
    await act(async () => {
      await navigate("/somewhere", { force: true })
    })
    expect(screen.getByRole("alert")).toHaveTextContent("There is no page at this address.")
    await navigate("/", { force: true })
  })

  it("leaves no quote pricer behind once it is gone", async () => {
    await opened()
    expect(useWorkbenchPricingStore.getState().quotePricer).not.toBeNull()

    cleanup()

    expect(useWorkbenchPricingStore.getState().quotePricer).toBeNull()
  })
})
