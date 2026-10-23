/**
 * The underwriter app (specs/workbench): the release's sheets drawn as Preview draws them,
 * its quote's values rather than a sample, nothing of the builder, the release and who is
 * signed in, and a quote priced as it is typed through the app's own route on the form
 * revision the page was drawn from.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, render, screen } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  fetchAppRelease: vi.fn(),
  fetchAppForm: vi.fn(),
  priceAppQuote: vi.fn(),
  fetchAppQuotes: vi.fn(),
  fetchAppPeople: vi.fn(),
  openAppQuote: vi.fn(),
}))

import { fetchAppForm, fetchAppPeople, fetchAppQuotes, fetchAppRelease, openAppQuote, priceAppQuote } from "../../api/app"
import useAppStore from "../useAppStore"
import { ApiError } from "../../api/client"
import type { WorkbenchAppReleaseResponse } from "../../api/types"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../../stores/useWorkbenchPricingStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { navigate } from "../route"
import UnderwriterApp from "../UnderwriterApp"
import { answer } from "./fixtures"

const release: WorkbenchAppReleaseResponse = {
  id: "0a1b2c3d4e5f",
  form_revision: "rev-9",
  pipeline_name: "motor",
  haute_version: "1.2.0",
  created_at: "2026-10-12T09:00:00+00:00",
  host: "azure-container-apps",
  principal: "uw@example.com",
  quotes: false,
  edit_role: null,
  can_edit: true,
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
    useWorkbenchViewStore.setState({ mode: "build", section: "sheets", underwriterApp: false, pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("draws the release's sheets as Preview does, with the release and who is signed in, and nothing of the builder", async () => {
    await opened()

    expect(screen.getByTestId("app-release")).toHaveTextContent("motor")
    expect(screen.getByTestId("app-release")).toHaveTextContent("release 0a1b2c3d4e5f")
    expect(screen.getByTestId("app-principal")).toHaveTextContent("Signed in as uw@example.com")
    expect(screen.getByRole("tab", { name: "Sheet 1" })).toBeInTheDocument()
    expect(screen.getByText("Equipment")).toBeInTheDocument()
    expect(useWorkbenchViewStore.getState()).toMatchObject({ mode: "preview", section: "sheets", underwriterApp: true })
    for (const builder of ["Build", "Preview", "Sheets", "Schema", "Save"]) {
      expect(screen.queryByRole("button", { name: builder })).toBeNull()
    }
    // The quote is priced as it is typed, and nothing empties it.
    expect(screen.queryByRole("button", { name: "Price" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Clear" })).toBeNull()
  })

  it("prices the quote as it is typed, through the app's route on the revision the page was drawn from", async () => {
    vi.mocked(priceAppQuote).mockResolvedValue({
      tables: { premiums: [{ item: "Crane", premium: 120 }] },
      request: { policy: { state: "NY" } },
    })
    await opened()
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "NY"))

    await vi.waitFor(() => expect(useWorkbenchPreviewStore.getState().price).not.toBeNull())
    expect(priceAppQuote).toHaveBeenCalledWith("rev-9", "0a1b2c3d4e5f", { policy: [{ "policy.state": "NY" }] })
    expect(useWorkbenchPreviewStore.getState().price).toMatchObject({
      tables: { premiums: [{ item: "Crane", premium: 120 }] },
      sample: { policy: { state: "NY" } },
    })
  })

  it("says why a quote was not priced, a page drawn for another release among them", async () => {
    const refusal = "This page was drawn for another release of the sheets: reload it to price on this one."
    vi.mocked(priceAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, refusal))
    useAppStore.setState({ releaseChanged: false })
    await opened()
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", "NY"))

    expect(await screen.findByTestId("app-pricing-error")).toHaveTextContent(`Pricing failed: ${refusal}`)
    // The page is for another release: the Reload notice asks for it at once, before a save.
    expect(useAppStore.getState().releaseChanged).toBe(true)
    useAppStore.setState({ releaseChanged: false })
  })

  it("says why the sheets could not be opened, a request the host did not sign in among them", async () => {
    const refusal = "Sign in through Azure's built-in authentication: this request carries no signed-in principal."
    vi.mocked(fetchAppRelease).mockRejectedValue(new ApiError("HTTP 401", 401, refusal))

    render(<UnderwriterApp />)

    expect(await screen.findByRole("alert")).toHaveTextContent(`Could not open the sheets: ${refusal}`)
    expect(useWorkbenchPricingStore.getState().quotePricer).toBeNull()
  })

  it("says it keeps no quotes when it has no quote store", async () => {
    await opened()

    expect(screen.getByTestId("notice-no-store")).toHaveTextContent("This app keeps no quotes")
  })

  it("starts its one quote as a new quote, holding its columns' defaults and priced once something is typed", async () => {
    const form = sheetForm()
    form.schema.tables[0].columns[0].default_value = "NY"
    vi.mocked(fetchAppForm).mockResolvedValue({ form, revision: "rev-9" })
    await opened()

    expect(screen.getByRole("combobox", { name: "State" })).toHaveValue("NY")
    await act(() => useWorkbenchPreviewStore.getState().settle())
    expect(priceAppQuote).not.toHaveBeenCalled()
  })

  it("opens on the Quotes page when it keeps quotes, and a quote at its own address", async () => {
    vi.mocked(fetchAppRelease).mockResolvedValue({ ...release, quotes: true })
    vi.mocked(fetchAppQuotes).mockResolvedValue({ quotes: [], next_cursor: null, counts: {} })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: [] })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await navigate("/", { force: true })
    await opened()

    expect(await screen.findByTestId("quotes-page")).toBeInTheDocument()
    await act(async () => {
      await navigate("/quotes/q1")
    })
    expect(await screen.findByTestId("quote-line")).toHaveTextContent("Revision 1")
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
