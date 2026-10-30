/**
 * A quote in the underwriter app (specs/workbench): its bar and line, an alternative made as
 * it is asked for and taken to its own address, Duplicate, Renew and Save as new opening the
 * New quote window over the quote, marking final and amending, what a save or Mark final was
 * refused for, the notices of what stands in the way and what each offers, leaving with
 * unsaved values, the keyboard, History, and assigning.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  changeAppQuote: vi.fn(),
  createAppQuote: vi.fn(),
  fetchAppPeople: vi.fn(),
  fetchAppQuoteActivity: vi.fn(),
  openAppQuote: vi.fn(),
  saveAppQuote: vi.fn(),
}))

import { changeAppQuote, createAppQuote, fetchAppPeople, fetchAppQuoteActivity, openAppQuote, saveAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuoteActivityEntry, WorkbenchAppQuoteResponse } from "../../api/types"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { detailsTable, sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { APP_SET_COLUMNS } from "../openRelease"
import NewQuoteWindow from "../NewQuoteWindow"
import { draftKey, writeDraft } from "../quoteValues"
import QuoteView from "../QuoteView"
import { navigate, setLeaveGuard, type AppRoute } from "../route"
import QuestionDialog from "../../components/QuestionDialog"
import useQuestionStore from "../../stores/useQuestionStore"
import useAppStore from "../useAppStore"
import useOpenQuoteStore from "../useOpenQuoteStore"
import { answer, appQuote, changed, release, VALUES } from "./fixtures"

type QuoteRoute = Extract<AppRoute, { page: "new" } | { page: "quote" }>

const QUOTE: QuoteRoute = { page: "quote", reference: "Q-000001", option: 1 }
const ALTERNATIVE = { reference: "Q-000001", option: 1, relation: "alternative" } as const

function show(route: QuoteRoute) {
  return render(
    <>
      <QuoteView route={route} />
      <NewQuoteWindow />
      <QuestionDialog />
      <ToastContainer />
    </>,
  )
}

const opened = async (route: QuoteRoute = QUOTE) => {
  const view = show(route)
  await screen.findByTestId("quote-line")
  return view
}

const type = (value: string) => act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", value))
/** Key `value` into the New quote window's field labelled `label`, committed as leaving it commits it. */
const key = (label: string, value: string) => {
  const field = within(screen.getByTestId("new-quote-window")).getByRole("textbox", { name: label })
  fireEvent.change(field, { target: { value } })
  fireEvent.blur(field)
}
const confirmQuestion = (label: string) => fireEvent.click(within(screen.getByTestId("question")).getByRole("button", { name: label }))
const stale = () =>
  new ApiError("HTTP 409", 409, "moved", {}, { message: "Q-000001/1 has changed since you opened it: jo saved it at 10:05 UTC on 12 Oct 2026.", error_code: "stale_quote", quote: appQuote({ version: 2 }) })
const entry = (action: WorkbenchAppQuoteActivityEntry["action"], actor: string, carried: Partial<WorkbenchAppQuoteActivityEntry> = {}): WorkbenchAppQuoteActivityEntry => ({
  action,
  actor,
  at: "2026-10-12T09:00:00+00:00",
  version: 1,
  assignee: null,
  note: null,
  reason: null,
  priced_release: null,
  ...carried,
})

describe("QuoteView", () => {
  beforeEach(async () => {
    stubLayout()
    const sheets = sheetForm()
    // Haute's quote_details first, as every release's form holds it.
    const form = { ...sheets, schema: { tables: [detailsTable(), ...sheets.schema.tables] } }
    useAppStore.setState({ release, form, revision: "rev-9", people: [], releaseChanged: false })
    useQuestionStore.setState({ question: null })
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchViewStore.setState({ mode: "preview", underwriterApp: true, pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false, locked: APP_SET_COLUMNS })
    useOpenQuoteStore.setState({ status: "idle", quote: null, base: null, pending: null, conflict: null, refusal: null, error: null, historyOpen: false, activity: null })
    useToastStore.setState({ toasts: [] })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: ["jo@example.com"] })
    vi.mocked(fetchAppQuoteActivity).mockResolvedValue({ entries: [], source: null, derived: [] })
    await navigate("/quotes/Q-000001/1", { force: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    setLeaveGuard(null)
    window.localStorage.clear()
    window.sessionStorage.clear()
  })

  it("makes an alternative as it is asked for, from its source's values under its reference, and takes it to its own address", async () => {
    await navigate("/quotes/new?from=Q-000001&option=1&as=alternative", { force: true })
    const values = { policy: [{ "policy.state": "NY" }] }
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values }))
    vi.mocked(createAppQuote).mockResolvedValue(
      answer({ option: 2, relation: "alternative", source_reference: "Q-000001", source_option: 1, source_label: "Q-000001/1", priced_release: null }, { values }),
    )
    show({ page: "new", from: ALTERNATIVE })

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/Q-000001/2"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([body.values, source, reference]).toEqual([values, ALTERNATIVE, null])
    // Its source read once, and the quote made not read again at its address.
    expect(vi.mocked(openAppQuote).mock.calls).toEqual([["Q-000001", 1]])
    expect(screen.getByTestId("quote-reference")).toHaveTextContent("Q-000001/2")
    expect(screen.getByTestId("quote-line")).toHaveTextContent("Alternative of Q-000001/1")
    expect(screen.getByRole("link", { name: "Q-000001/1" })).toHaveAttribute("href", "/quotes/Q-000001/1")
    expect(screen.queryByTestId("quote-unsaved")).toBeNull()
    // No window: an alternative takes its source's reference.
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })

  it("makes an alternative once though its answer is lost and the page is loaded again", async () => {
    await navigate("/quotes/new?from=Q-000001&option=1&as=alternative", { force: true })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(createAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(answer({ option: 2, relation: "alternative", priced_release: null }))
    const view = show({ page: "new", from: ALTERNATIVE })
    expect(await screen.findByText(/Failed to fetch/)).toBeInTheDocument()
    view.unmount()

    show({ page: "new", from: ALTERNATIVE })

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/Q-000001/2"))
    const [first, second] = vi.mocked(createAppQuote).mock.calls.map(([body]) => body.operation)
    expect(second).toBe(first)
  })

  it("stays on the Quotes page when an alternative's answer comes after it was left", async () => {
    await navigate("/quotes/new?from=Q-000001&option=1&as=alternative", { force: true })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    let creating: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(createAppQuote).mockReturnValue(new Promise((resolve) => (creating = resolve)))
    const view = show({ page: "new", from: ALTERNATIVE })
    await vi.waitFor(() => expect(createAppQuote).toHaveBeenCalled())

    await navigate("/", { force: true })
    view.unmount()
    await act(async () => creating(answer({ option: 2, priced_release: null })))

    expect(window.location.pathname).toBe("/")
  })

  it("shows who saved it and the release that priced it, and saves on Ctrl+S", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    await opened()

    const line = screen.getByTestId("quote-line")
    expect(line).toHaveTextContent("by ana@example.com")
    expect(line).toHaveTextContent("Priced on this release")
    expect(line).not.toHaveTextContent("Revision")
    expect(screen.getByTestId("stage-draft")).toBeInTheDocument()
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    type("CA")
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    fireEvent.keyDown(window, { key: "s", ctrlKey: true })

    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    expect(await screen.findByText("Saved Q-000001/1")).toBeInTheDocument()
  })

  it("asks before leaving with unsaved values, and stays on Cancel", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()
    type("CA")

    let left: ReturnType<typeof navigate> = Promise.resolve("moved")
    act(() => {
      left = navigate("/")
    })
    expect(await screen.findByTestId("question")).toHaveTextContent("Leave without saving?")
    confirmQuestion("Cancel")
    expect(await left).toBe("kept")
    expect(window.location.pathname).toBe("/quotes/Q-000001/1")
  })

  it("keeps unsaved values in the browser as they are typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()
    type("CA")

    await vi.waitFor(() => expect(window.localStorage.getItem(draftKey("motor", "ana@example.com", "Q-000001/1"))).toContain("CA"))
  })

  it("keeps no draft once what was typed is typed back to the saved values", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()
    const kept = draftKey("motor", "ana@example.com", "Q-000001/1")
    const saved = useWorkbenchPreviewStore.getState().quote.policy?.[0]["policy.state"] ?? ""
    type("CA")
    await vi.waitFor(() => expect(window.localStorage.getItem(kept)).toContain("CA"))

    type(String(saved))

    await vi.waitFor(() => expect(window.localStorage.getItem(kept)).toBeNull())
  })

  it("marks a priced quote final once asked, and amends it giving a reason", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ stage: "final", final_by: "ana@example.com", final_at: "2026-10-12T10:00:00+00:00", version: 2 }))
      .mockResolvedValueOnce(changed({ version: 3 }))
    await opened()

    fireEvent.click(screen.getByRole("button", { name: "Mark final" }))
    expect(screen.getByTestId("question")).toHaveTextContent("Mark Q-000001/1 final?")
    confirmQuestion("Mark final")
    expect(await screen.findByTestId("notice-final")).toHaveTextContent("Marked final by ana@example.com")
    expect(changeAppQuote).toHaveBeenCalledWith("Q-000001", 1, "final", 1, {})
    expect(screen.getByTestId("stage-final")).toBeInTheDocument()
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    expect(screen.queryByRole("button", { name: "Mark final" })).toBeNull()

    fireEvent.click(screen.getByRole("button", { name: "Amend" }))
    const amend = within(screen.getByTestId("question")).getByRole("button", { name: "Amend" })
    expect(amend).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Why it is being amended"), { target: { value: "Broker asked" } })
    fireEvent.click(amend)

    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenLastCalledWith("Q-000001", 1, "amend", 2, { reason: "Broker asked" }))
    await vi.waitFor(() => expect(screen.queryByTestId("notice-final")).toBeNull())
    expect(screen.getByTestId("stage-draft")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Mark final" })).toBeInTheDocument()
  })

  it("marks final with a required field empty, for the server to name each field that keeps it from being final", async () => {
    // The sheets' required State is empty: only the server checks Required, as it is marked final.
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { policy: [{ "policy.year": "2026" }] } }))
    vi.mocked(changeAppQuote).mockRejectedValue(
      new ApiError("HTTP 409", 409, "not ready", {}, {
        message: "Q-000001/1 needs a value in policy.State, equipment row 2.Value before it is marked final.",
        error_code: "quote_not_ready",
        missing: ["policy.State", "equipment row 2.Value"],
      }),
    )
    await opened()

    const final = screen.getByRole("button", { name: "Mark final" })
    expect(final).toBeEnabled()
    fireEvent.click(final)
    confirmQuestion("Mark final")

    const notice = await screen.findByTestId("notice-not-ready")
    expect(notice).toHaveTextContent("Not marked final, as these have no value: policy.State; equipment row 2.Value.")
    expect(within(notice).getByText("policy.State")).toBeInTheDocument()
    expect(within(notice).getByText("equipment row 2.Value")).toBeInTheDocument()
    expect(screen.getByTestId("stage-draft")).toBeInTheDocument()
    fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }))
    expect(screen.queryByTestId("notice-not-ready")).toBeNull()
  })

  it("names each value a save was refused for, the values kept on the sheets unsaved", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(
      new ApiError("HTTP 422", 422, "refused", {}, {
        message: "These values are not of their columns' types, so the quote was not saved: policy, Year.",
        error_code: "quote_values_invalid",
        cells: [
          { table: "policy", row: null, column: "Year" },
          { table: "equipment", row: 3, column: "Value" },
        ],
      }),
    )
    await opened()
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.year", "abc"))
    fireEvent.click(screen.getByTestId("save-quote"))

    const notice = await screen.findByTestId("notice-invalid")
    expect(notice).toHaveTextContent("Not saved, as these values are not of their columns' types: policy, Year; equipment row 3, Value.")
    expect(screen.queryByTestId("notice-error")).toBeNull()
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
  })

  it("drops a question about a quote once the page leaves it, changing nothing", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ reference: "Q-000002" }))
    const view = await opened()
    fireEvent.click(screen.getByRole("button", { name: "Mark final" }))
    expect(screen.getByTestId("question")).toHaveTextContent("Mark Q-000001/1 final?")

    // Back, with the question open: another quote, at the same version, opens.
    view.rerender(
      <>
        <QuoteView route={{ page: "quote", reference: "Q-000002", option: 1 }} />
        <QuestionDialog />
        <ToastContainer />
      </>,
    )
    await vi.waitFor(() => expect(screen.getByTestId("quote-reference")).toHaveTextContent("Q-000002"))

    expect(screen.queryByTestId("question")).toBeNull()
    expect(changeAppQuote).not.toHaveBeenCalled()
  })

  it("does not mark final a quote unpriced or priced on an earlier release, saying why", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ priced_release: "rel-0" })).mockResolvedValueOnce(answer({ priced_release: null }))
    const view = await opened()

    const final = screen.getByRole("button", { name: "Mark final" })
    expect(final).toBeDisabled()
    expect(final).toHaveAttribute("title", "Priced on an earlier release: save it again first")
    expect(screen.getByTestId("quote-line")).toHaveTextContent("Priced on release rel-0")

    view.unmount()
    await opened()
    expect(screen.getByRole("button", { name: "Mark final" })).toHaveAttribute("title", "Save the quote with its price first")
    expect(screen.getByTestId("quote-line")).toHaveTextContent("Unpriced")
  })

  it("offers to open the newest or save the values as a new quote, keyed in the window, when the quote moved on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    vi.mocked(createAppQuote).mockResolvedValue(answer({ reference: "DEF-2", relation: "duplicate", priced_release: null }))
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))

    expect(await screen.findByTestId("notice-conflict")).toHaveTextContent("jo saved it at 10:05 UTC on 12 Oct 2026. Your changes are still on the sheets.")
    fireEvent.click(screen.getByRole("button", { name: "Save mine as a new quote" }))
    // The window over the quote, filled from the sheets, its reference blank.
    expect(screen.getByRole("dialog", { name: "Save as a new quote" })).toBeInTheDocument()
    expect(window.location.pathname).toBe("/quotes/Q-000001/1")
    key("Quote reference", "DEF-2")
    fireEvent.click(screen.getByTestId("new-quote-create"))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/DEF-2/1"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference]).toEqual([{ reference: "Q-000001", option: 1, relation: "duplicate" }, "DEF-2"])
    // The values on the sheets, unsaved ones and all, made unpriced.
    expect(body.values.policy).toEqual([{ "policy.state": "CA" }])
    expect(body).not.toHaveProperty("shown")
  })

  it("offers the newest when a save sent again had landed and the quote was changed since", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(
        new ApiError("HTTP 409", 409, "superseded", {}, {
          message: "Your save of Q-000001/1 landed at 10:00 UTC; jo changed it at 10:05 UTC since. Open the newest to go on.",
          error_code: "write_superseded",
          landed_at: "2026-10-12T10:00:00+00:00",
          changed_by: "jo",
          changed_at: "2026-10-12T10:05:00+00:00",
        }),
      )
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))
    expect(await screen.findByTestId("notice-error")).toHaveTextContent("Failed to fetch")

    fireEvent.click(screen.getByTestId("save-quote"))

    const notice = await screen.findByTestId("notice-conflict")
    expect(notice).toHaveTextContent("Your save of Q-000001/1 landed at 10:00 UTC; jo changed it at 10:05 UTC since.")
    expect(within(notice).getByRole("button", { name: "Open the newest" })).toBeInTheDocument()
    expect(useOpenQuoteStore.getState().pending).toBeNull()
  })

  it("offers a reload when the page was drawn for an earlier release", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "This page was drawn for another release of the app."))
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))

    expect(await screen.findByTestId("notice-release")).toHaveTextContent("earlier release")
  })

  it("opens the newest from a conflict at the quote's own address, reading it once", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValue(answer({ version: 2 }))
    vi.mocked(saveAppQuote).mockRejectedValue(stale())
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))
    fireEvent.click(within(await screen.findByTestId("notice-conflict")).getByRole("button", { name: "Open the newest" }))

    await vi.waitFor(() => expect(useOpenQuoteStore.getState().quote?.version).toBe(2))
    expect(window.location.pathname).toBe("/quotes/Q-000001/1")
    expect(vi.mocked(openAppQuote).mock.calls).toEqual([
      ["Q-000001", 1],
      ["Q-000001", 1],
    ])
  })

  it("says a duplicate was made when leaving the quote's unsaved values for it is cancelled", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({}, { values: { ...VALUES, quote_details: [{ qd_insured: "Acme" }] } }))
    vi.mocked(createAppQuote).mockResolvedValue(answer({ reference: "DEF-2", relation: "duplicate", priced_release: null }))
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Duplicate" }))
    const window_ = await screen.findByRole("dialog", { name: "Duplicate Q-000001/1" })
    await vi.waitFor(() => expect(within(window_).getByRole("textbox", { name: "Insured" })).toHaveValue("Acme"))
    key("Quote reference", "DEF-2")
    fireEvent.click(screen.getByTestId("new-quote-create"))

    // Made, and leaving for it asks first: Cancel keeps the quote and its unsaved values.
    expect(await screen.findByTestId("question")).toHaveTextContent("Leave without saving?")
    confirmQuestion("Cancel")
    expect(await screen.findByText("Made DEF-2/1")).toBeInTheDocument()
    expect(window.location.pathname).toBe("/quotes/Q-000001/1")
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    expect(createAppQuote).toHaveBeenCalledTimes(1)
  })

  it("names the rows of the quote the sheets have no place for", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { dropped: ["equipment row 2 (Crane)"] }))
    await opened()

    const notice = screen.getByTestId("notice-dropped")
    expect(notice).toHaveTextContent("No place on the current sheets for: equipment row 2 (Crane).")
    fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }))
    expect(screen.queryByTestId("notice-dropped")).toBeNull()
  })

  it("restores an archived quote from its notice, and archives one with Undo", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ archived: true, archived_at: "2026-10-12T10:00:00+00:00", archived_by: "jo@example.com" }))
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ version: 2 }))
      .mockResolvedValueOnce(changed({ version: 3, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }))
      .mockResolvedValueOnce(changed({ version: 4 }))
    await opened()
    expect(screen.getByTestId("notice-archived")).toHaveTextContent("Archived by jo@example.com")

    fireEvent.click(screen.getByRole("button", { name: "Restore" }))
    await vi.waitFor(() => expect(screen.queryByTestId("notice-archived")).toBeNull())
    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }))
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }))

    await vi.waitFor(() => expect(vi.mocked(changeAppQuote).mock.calls.map((call) => call.slice(2, 4))).toEqual([["restore", 1], ["archive", 2], ["restore", 3]]))
  })

  it("undoes an archive on the quote it archived, though another is open by then", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ reference: "Q-000002", archived: true, archived_at: "2026-10-12T09:00:00+00:00" }))
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ version: 2, archived: true, archived_at: "2026-10-12T11:00:00+00:00" }))
    const view = await opened()
    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }))
    const undo = await screen.findByRole("button", { name: "Undo" })

    view.rerender(
      <>
        <QuoteView route={{ page: "quote", reference: "Q-000002", option: 1 }} />
        <NewQuoteWindow />
        <QuestionDialog />
        <ToastContainer />
      </>,
    )
    await vi.waitFor(() => expect(screen.getByTestId("quote-reference")).toHaveTextContent("Q-000002"))
    fireEvent.click(undo)

    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenLastCalledWith("Q-000001", 1, "restore", 2, {}))
    expect(useOpenQuoteStore.getState().quote?.reference).toBe("Q-000002")
  })

  it("offers to save a quote unpriced once its pricing failed, saying why", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()

    type("CA")
    act(() => useWorkbenchPreviewStore.setState({ price: null, error: "Pricing failed: The pipeline refused it." }))

    const save = screen.getByTestId("save-quote")
    expect(save).toHaveTextContent("Save unpriced")
    expect(save).toHaveAttribute("title", "Save the quote unpriced: it could not be priced (Ctrl+S)")
    expect(screen.getByTestId("app-pricing-error")).toHaveTextContent("Pricing failed: The pipeline refused it.")
  })

  it("offers Save for a price made since the quote was saved, or one of another release", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer()).mockResolvedValueOnce(answer({ priced_release: "rel-0" }))
    const view = await opened()
    const save = screen.getByTestId("save-quote")
    expect(save).toBeDisabled()
    expect(save).toHaveAttribute("title", "Nothing has changed since the quote was saved")

    // Priced again with the values as they are: the price kept would change.
    act(() => {
      const { price } = useWorkbenchPreviewStore.getState()
      if (price !== null) useWorkbenchPreviewStore.setState({ price: { ...price, tables: { premiums: [{ item: "Crane", premium: 130 }] } } })
    })
    expect(save).toBeEnabled()

    view.unmount()
    await opened()
    expect(screen.getByTestId("save-quote")).toBeEnabled()
  })

  it("starts a renewal from the menu in the window over the quote, filled from the quote, and Cancel leaves the quote as it was", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ version: 2 })).mockResolvedValueOnce(answer({ version: 2 }, { values: { ...VALUES, quote_details: [{ qd_insured: "Acme" }] } }))
    await opened()
    type("CA")

    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Renew" }))

    const window_ = await screen.findByRole("dialog", { name: "Renew Q-000001/1" })
    await vi.waitFor(() => expect(within(window_).getByRole("textbox", { name: "Insured" })).toHaveValue("Acme"))
    expect(openAppQuote).toHaveBeenLastCalledWith("Q-000001", 1)
    expect(within(window_).getByRole("checkbox", { name: "Is renewal" })).toBeChecked()
    expect(within(window_).getByRole("textbox", { name: "Quote reference" })).toHaveValue("")
    // Nothing is installed on the sheets while it is open, and the address stays the quote's.
    expect(window.location.pathname).toBe("/quotes/Q-000001/1")
    expect(useWorkbenchPreviewStore.getState().quote.policy).toEqual([{ "policy.state": "CA" }])

    fireEvent.click(within(window_).getByRole("button", { name: "Cancel" }))
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
    expect(createAppQuote).not.toHaveBeenCalled()
    expect(useWorkbenchPreviewStore.getState().quote.policy).toEqual([{ "policy.state": "CA" }])
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
  })

  it("copies the quote's link", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ reference: "ABC 1" }))
    const writeText = vi.fn(async () => {})
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } })
    await opened({ page: "quote", reference: "ABC 1", option: 1 })

    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy link" }))

    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/quotes/ABC%201/1`))
    expect(await screen.findByText("Link to ABC 1/1 copied")).toBeInTheDocument()
  })

  it("offers back values this browser kept", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(draftKey("motor", "ana@example.com", "Q-000001/1"), { values: { policy: [{ "policy.state": "CA" }] }, version: 0, places: {}, keptAt: new Date().toISOString(), pending: null })
    await opened()

    expect(screen.getByTestId("notice-recovered")).toHaveTextContent("not saved to the server. The quote has changed since then.")
    fireEvent.click(screen.getByRole("button", { name: "Restore them" }))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "CA" }] })
  })

  it("assigns the quote to someone from the people, or to a name typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ assignee: "jo@example.com", version: 2 }))
      .mockResolvedValueOnce(changed({ assignee: "sam@example.com", version: 3 }))
    await opened()
    await screen.findByRole("option", { name: "jo@example.com" })

    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledWith("Q-000001", 1, "assignee", 1, { assignee: "jo@example.com" }))
    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "" } })
    fireEvent.change(await screen.findByLabelText("Their sign-in name"), { target: { value: "sam@example.com" } })
    confirmQuestion("Assign")
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenLastCalledWith("Q-000001", 1, "assignee", 2, { assignee: "sam@example.com" }))
  })

  it("takes the details an assignment set onto the sheets from the quote, nothing unsaved by it, and what was typed kept", async () => {
    const saved = { qd_reference: "Q-000001", qd_option: "1", qd_stage: "draft", qd_assignee: "ana@example.com" }
    const details = (assignee: string) => ({ quote_reference: "Q-000001", quote_option: 1, stage: "draft", assignee, archived: false })
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { ...VALUES, quote_details: [saved] } }))
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ assignee: "jo@example.com", version: 2, details: details("jo@example.com") }))
      .mockResolvedValueOnce(changed({ assignee: "ana@example.com", version: 3, details: details("ana@example.com") }))
    await opened()
    await screen.findByRole("option", { name: "jo@example.com" })

    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })
    await vi.waitFor(() => expect(useOpenQuoteStore.getState().base).toBe(2))
    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ ...saved, qd_assignee: "jo@example.com" }])
    expect(screen.queryByTestId("quote-unsaved")).toBeNull()

    type("CA")
    // A cell the app sets takes no typing.
    act(() => useWorkbenchPreviewStore.getState().setCell("quote_details", 0, "qd_stage", "final"))
    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "ana@example.com" } })

    await vi.waitFor(() => expect(useOpenQuoteStore.getState().base).toBe(3))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [saved] })
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
  })

  it("names the quote in its quote_details.quote_name cell, saved with the quote", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    await opened()

    const name = screen.getByLabelText("Quote name")
    expect(name).toHaveAttribute("placeholder", "Fleet")
    fireEvent.change(name, { target: { value: " Fleet renewal " } })
    fireEvent.blur(name)

    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ qd_quote_name: "Fleet renewal" }])
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("save-quote"))
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    expect(vi.mocked(saveAppQuote).mock.calls[0][2].values.quote_details).toEqual([{ qd_quote_name: "Fleet renewal" }])
    expect(changeAppQuote).not.toHaveBeenCalled()
  })

  it("shows the history: the note, the related quotes and what was done, newest first", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ version: 3, relation: "renewal", source_reference: "Q-000000", source_option: 1, source_label: "Q-000000/1" }))
    vi.mocked(fetchAppQuoteActivity).mockResolvedValue({
      entries: [
        entry("created", "ana@example.com"),
        entry("saved", "jo@example.com", { version: 2, priced_release: "rel-1" }),
        entry("amended", "jo@example.com", { version: 3, reason: "Broker asked" }),
      ],
      source: { reference: "Q-000000", option: 1, label: "Q-000000/1", relation: "renewal" },
      derived: [{ reference: "Q-000001", option: 2, label: "Q-000001/2", relation: "alternative" }],
    })
    vi.mocked(changeAppQuote).mockResolvedValue(changed({ version: 3, note: "Call the broker", note_by: "ana@example.com", note_at: "2026-10-12T11:00:00+00:00" }))
    await opened()

    fireEvent.click(screen.getByRole("button", { name: "History" }))
    const panel = await screen.findByTestId("quote-history")
    expect(await within(panel).findByText("jo@example.com saved it, priced")).toBeInTheDocument()
    expect(within(panel).getByText("ana@example.com made it as a renewal of Q-000000/1")).toBeInTheDocument()
    expect(within(panel).getByText("jo@example.com amended it: Broker asked")).toBeInTheDocument()
    expect(within(panel).getAllByTestId(/^activity-/).map((item) => item.dataset.testid)).toEqual(["activity-amended", "activity-saved", "activity-created"])
    expect(within(panel).getByRole("link", { name: "Q-000000/1" })).toHaveAttribute("href", "/quotes/Q-000000/1")
    expect(within(panel).getByRole("link", { name: "Q-000001/2" })).toHaveAttribute("href", "/quotes/Q-000001/2")
    expect(within(panel).queryByText(/Revision/)).toBeNull()

    fireEvent.change(within(panel).getByLabelText("Handover note"), { target: { value: "Call the broker" } })
    fireEvent.click(within(panel).getByRole("button", { name: "Save note" }))
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledWith("Q-000001", 1, "note", 3, { note: "Call the broker" }))
    expect(await within(panel).findByText(/By ana@example.com/)).toBeInTheDocument()
  })

  it("says why a quote could not be opened, with the way back", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "The app keeps no quote Q-000001/1."))
    show(QUOTE)

    expect(await screen.findByRole("alert")).toHaveTextContent("The quote could not be opened: The app keeps no quote Q-000001/1.")
    expect(screen.getByRole("link", { name: "Back to Quotes" })).toHaveAttribute("href", "/")
  })

  it("shows one who may only read quotes the quote, saying so, with nothing that changes it", async () => {
    useAppStore.setState({ release: { ...release, can_edit: false, edit_role: "Quotes.Edit" } })
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()

    expect(screen.getByTestId("notice-reader")).toHaveTextContent("changing them needs the Quotes.Edit role")
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    expect(screen.getByRole("button", { name: "Mark final" })).toHaveAttribute("title", "Changing quotes needs the app's edit role")
    expect(screen.getByLabelText("Quote name")).toBeDisabled()
    expect(screen.getByLabelText("Assigned to")).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "State" })).toBeDisabled()
    fireEvent.click(screen.getByTestId("quote-more"))
    for (const item of ["Duplicate", "Renew", "Create alternative", "Archive"]) {
      expect(screen.getByRole("menuitem", { name: item })).toBeDisabled()
    }
  })
})
