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
  editAppQuote: vi.fn(async () => ({ editor: "ana@example.com", lease_seconds: 180 })),
  releaseAppQuote: vi.fn(async () => {}),
  saveAppQuote: vi.fn(),
}))

import { changeAppQuote, createAppQuote, editAppQuote, fetchAppPeople, fetchAppQuoteActivity, openAppQuote, releaseAppQuote, saveAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuoteActivityEntry } from "../../api/types"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { detailsTable, sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { APP_SET_COLUMNS } from "../openRelease"
import NewQuoteWindow from "../NewQuoteWindow"
import { draftKey, EDIT_RENEW_MS, writeDraft } from "../quoteValues"
import QuoteView from "../QuoteView"
import { navigate, setLeaveGuard, type AppRoute } from "../route"
import QuestionDialog from "../../components/QuestionDialog"
import useQuestionStore from "../../stores/useQuestionStore"
import useAppStore from "../useAppStore"
import useOpenQuoteStore from "../useOpenQuoteStore"
import { answer, appQuote, changed, release, VALUES } from "./fixtures"

type QuoteRoute = Extract<AppRoute, { page: "quote" }>

const QUOTE: QuoteRoute = { page: "quote", reference: "Q-000001", option: 1, mode: "edit" }

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

const type = (value: string) => act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "state", value))
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
    await navigate("/quotes/Q-000001/1/edit", { force: true })
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

  it("shows who saved it, saying nothing of a price, and saves on Ctrl+S", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ version: 2 }))
    await opened()

    const line = screen.getByTestId("quote-line")
    expect(line).toHaveTextContent("by ana@example.com")
    expect(line).not.toHaveTextContent(/priced/i)
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
    expect(window.location.pathname).toBe("/quotes/Q-000001/1/edit")
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
    const saved = useWorkbenchPreviewStore.getState().quote.policy?.[0].state ?? ""
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
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { policy: [{ year: "2026" }] } }))
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
    act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "year", "abc"))
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
        <QuoteView route={{ page: "quote", reference: "Q-000002", option: 1, mode: "edit" }} />
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
    expect(final).toHaveAttribute("title", "Saved on an earlier release: save it again first")
    expect(screen.getByTestId("quote-line")).not.toHaveTextContent(/priced/i)

    view.unmount()
    await opened()
    expect(screen.getByRole("button", { name: "Mark final" })).toHaveAttribute("title", "The quote has no price yet")
    expect(screen.getByTestId("quote-line")).not.toHaveTextContent(/priced/i)
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
    expect(window.location.pathname).toBe("/quotes/Q-000001/1/edit")
    key("Quote reference", "DEF-2")
    key("Quote name", "Fleet, mine")
    fireEvent.click(screen.getByTestId("new-quote-create"))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/DEF-2/1/edit"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference]).toEqual([{ reference: "Q-000001", option: 1, relation: "duplicate" }, "DEF-2"])
    // The values on the sheets, unsaved ones and all, made unpriced, under the name keyed.
    expect(body.values.policy).toEqual([{ state: "CA" }])
    expect(body.values.quote_details).toEqual([{ quote_name: "Fleet, mine" }])
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
    expect(window.location.pathname).toBe("/quotes/Q-000001/1/edit")
    expect(vi.mocked(openAppQuote).mock.calls).toEqual([
      ["Q-000001", 1],
      ["Q-000001", 1],
    ])
  })

  it("names the rows of the quote the sheets have no place for", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { dropped: ["equipment row 2 (Crane)"] }))
    await opened()

    const notice = screen.getByTestId("notice-dropped")
    expect(notice).toHaveTextContent("No place on the current sheets for: equipment row 2 (Crane).")
    fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }))
    expect(screen.queryByTestId("notice-dropped")).toBeNull()
  })

  it("says an archived quote is archived, its Restore the Quotes page's alone", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ archived: true, archived_at: "2026-10-12T10:00:00+00:00", archived_by: "jo@example.com" }))
    await opened()

    expect(screen.getByTestId("notice-archived")).toHaveTextContent("Archived by jo@example.com")
    expect(screen.getByTestId("notice-archived")).toHaveTextContent("Restore it from the Quotes page to change it.")
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull()
  })

  it("keeps what is done to the quote as a whole off its bar, for the Quotes page", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()

    const bar = screen.getByRole("toolbar", { name: "Quote toolbar" })
    for (const name of ["More", "Duplicate", "Renew", "Create alternative", "Archive", "Copy link"]) {
      expect(within(bar).queryByRole("button", { name })).toBeNull()
    }
    expect(within(bar).getByRole("button", { name: "History" })).toBeInTheDocument()
  })

  it("offers Save once its pricing failed, saying why, never that it saves unpriced", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()

    type("CA")
    act(() => useWorkbenchPreviewStore.setState({ price: null, error: "Pricing failed: The pipeline refused it." }))

    const save = screen.getByTestId("save-quote")
    expect(save).toHaveTextContent(/^Save$/)
    expect(save).toHaveAttribute("title", "Save the quote (Ctrl+S)")
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

  it("offers back values this browser kept", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(draftKey("motor", "ana@example.com", "Q-000001/1"), { values: { policy: [{ state: "CA" }] }, version: 0, places: {}, keptAt: new Date().toISOString(), pending: null })
    await opened()

    expect(screen.getByTestId("notice-recovered")).toHaveTextContent("not saved to the server. The quote has changed since then.")
    fireEvent.click(screen.getByRole("button", { name: "Restore them" }))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ state: "CA" }] })
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
    const saved = { quote_reference: "Q-000001", quote_option: "1", stage: "draft", assignee: "ana@example.com" }
    const details = (assignee: string) => ({ quote_reference: "Q-000001", quote_option: 1, stage: "draft", assignee, archived: false })
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { ...VALUES, quote_details: [saved] } }))
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce(changed({ assignee: "jo@example.com", version: 2, details: details("jo@example.com") }))
      .mockResolvedValueOnce(changed({ assignee: "ana@example.com", version: 3, details: details("ana@example.com") }))
    await opened()
    await screen.findByRole("option", { name: "jo@example.com" })

    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })
    await vi.waitFor(() => expect(useOpenQuoteStore.getState().base).toBe(2))
    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ ...saved, assignee: "jo@example.com" }])
    expect(screen.queryByTestId("quote-unsaved")).toBeNull()

    type("CA")
    // A cell the app sets takes no typing.
    act(() => useWorkbenchPreviewStore.getState().setCell("quote_details", 0, "stage", "final"))
    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "ana@example.com" } })

    await vi.waitFor(() => expect(useOpenQuoteStore.getState().base).toBe(3))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ state: "CA" }], quote_details: [saved] })
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
  })

  it("reads a quote, changing nothing and holding nothing, with Edit to edit it", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await navigate("/quotes/Q-000001/1", { force: true })
    await opened({ ...QUOTE, mode: "read" })

    expect(editAppQuote).not.toHaveBeenCalled()
    expect(useWorkbenchPreviewStore.getState().readOnly).toBe(true)
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    expect(screen.getByRole("button", { name: "Mark final" })).toBeDisabled()
    expect(screen.getByLabelText("Assigned to")).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/Q-000001/1/edit"))
    expect(editAppQuote).toHaveBeenCalledWith("Q-000001", 1, expect.any(String))
  })

  it("takes no handover note on a quote opened to read", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(fetchAppQuoteActivity).mockResolvedValue({ entries: [], source: null, derived: [] })
    await opened({ ...QUOTE, mode: "read" })
    fireEvent.click(screen.getByRole("button", { name: "History" }))

    expect(await screen.findByLabelText("Handover note")).toBeDisabled()
    expect(await useOpenQuoteStore.getState().change("note", { note: "Call back" })).toBeNull()
    expect(changeAppQuote).not.toHaveBeenCalled()
  })

  it("keeps a quote_name the app set when rows are set whole, as an Import sets them", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { ...VALUES, quote_details: [{ quote_name: "Fleet" }] } }))
    await opened()

    act(() => useWorkbenchPreviewStore.getState().setRows({ quote_details: [{ quote_name: "Imported name", insured: "Acme" }] }))

    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ quote_name: "Fleet", insured: "Acme" }])
  })

  it("says why a quote opened to read takes no change: read only, and final or archived as it is", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ stage: "final", final_by: "jo@example.com", final_at: "2026-10-12T10:05:00+00:00" }))
    await opened({ ...QUOTE, mode: "read" })

    expect(screen.getByTestId("quote-read-only")).toHaveTextContent("Read only")
    expect(screen.getByTestId("notice-final")).toHaveTextContent("Marked final by jo@example.com")
    expect(screen.getByTestId("save-quote")).toHaveAttribute("title", "Press Edit to change this quote")
    expect(screen.getByRole("button", { name: "Amend" })).toHaveAttribute("title", "Press Edit to change this quote")
  })

  it("holds a quote open to edit, renewing it while it is open and letting it go as it is left", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      vi.mocked(openAppQuote).mockResolvedValue(answer())
      const view = await opened()
      expect(editAppQuote).toHaveBeenCalledTimes(1)
      expect(useWorkbenchPreviewStore.getState().readOnly).toBe(false)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(EDIT_RENEW_MS)
      })
      expect(editAppQuote).toHaveBeenCalledTimes(2)
      // Renewed and let go under the session it was taken under: this page's own.
      const sessions = vi.mocked(editAppQuote).mock.calls.map((call) => call[2])
      expect(new Set(sessions).size).toBe(1)

      // Hidden with a renewal of its session still unanswered, the page lets the quote go;
      // shown again as it was, it takes it again under a new session, which the release and
      // the renewal before say nothing of, whenever they are answered.
      let late!: (error: unknown) => void
      vi.mocked(editAppQuote).mockReturnValueOnce(new Promise((_resolve, reject) => { late = reject }))
      const before = useOpenQuoteStore.getState().keepEditing()
      // One renewal of a session at a time.
      await act(async () => useOpenQuoteStore.getState().keepEditing())
      expect(editAppQuote).toHaveBeenCalledTimes(3)
      act(() => void window.dispatchEvent(new Event("pagehide")))
      expect(releaseAppQuote).toHaveBeenLastCalledWith("Q-000001", 1, sessions[0], { keepalive: true })
      await act(async () => {
        window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true }))
      })
      const resumed = vi.mocked(editAppQuote).mock.calls.at(-1)?.[2]
      expect(editAppQuote).toHaveBeenCalledTimes(4)
      expect(resumed).not.toBe(sessions[0])
      // The old session's renewal refused late, naming someone else: the page, holding the
      // quote under its new session, stays editable.
      const detail = { message: "jo@example.com is editing Q-000001/1.", error_code: "quote_being_edited", editor: "jo@example.com" }
      await act(async () => {
        late(new ApiError("HTTP 409", 409, detail.message, { detail }, detail))
        await before
      })
      expect(screen.queryByTestId("notice-editing")).toBeNull()
      expect(useWorkbenchPreviewStore.getState().readOnly).toBe(false)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(EDIT_RENEW_MS)
      })
      expect(vi.mocked(editAppQuote).mock.calls.at(-1)?.[2]).toBe(resumed)
      sessions[0] = resumed as string

      // A renewal answered once the page has left lets the session go again, and arms nothing.
      let renewed!: (value: { editor: string; lease_seconds: number }) => void
      vi.mocked(editAppQuote).mockReturnValueOnce(new Promise((resolve) => { renewed = resolve }))
      const renewing = useOpenQuoteStore.getState().keepEditing()
      view.unmount()
      expect(releaseAppQuote).toHaveBeenLastCalledWith("Q-000001", 1, sessions[0], {})
      const released = vi.mocked(releaseAppQuote).mock.calls.length
      renewed({ editor: "ana@example.com", lease_seconds: 180 })
      await renewing
      expect(vi.mocked(releaseAppQuote).mock.calls.length).toBe(released + 1)
      expect(vi.mocked(releaseAppQuote).mock.calls.at(-1)?.slice(0, 3)).toEqual(["Q-000001", 1, sessions[0]])
    } finally {
      vi.useRealTimers()
    }
  })

  it("renews a quote whose opening took longer than a renewal, and keeps a save's late answer read only once the quote is taken", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    try {
      let opens!: (value: ReturnType<typeof answer>) => void
      vi.mocked(openAppQuote).mockReturnValue(new Promise((resolve) => { opens = resolve }))
      show(QUOTE)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(EDIT_RENEW_MS + 1000)
      })
      expect(editAppQuote).toHaveBeenCalledTimes(1)
      await act(async () => opens(answer()))
      await screen.findByTestId("quote-line")
      await act(async () => {
        await vi.advanceTimersByTimeAsync(EDIT_RENEW_MS)
      })
      expect(editAppQuote).toHaveBeenCalledTimes(2)

      // A save under way as someone else takes the quote: its answer leaves the sheets read only.
      let saves!: (value: ReturnType<typeof answer>) => void
      vi.mocked(saveAppQuote).mockReturnValue(new Promise((resolve) => { saves = resolve }))
      type("CA")
      const saving = useOpenQuoteStore.getState().save()
      await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
      const detail = { message: "jo@example.com is editing Q-000001/1.", error_code: "quote_being_edited", editor: "jo@example.com" }
      vi.mocked(editAppQuote).mockRejectedValueOnce(new ApiError("HTTP 409", 409, detail.message, { detail }, detail))
      await act(async () => useOpenQuoteStore.getState().keepEditing())
      await act(async () => {
        saves(answer({ version: 2 }, { values: { policy: [{ state: "CA" }] } }))
        await saving
      })
      expect(useWorkbenchPreviewStore.getState().readOnly).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it("opens a quote someone else is editing read only, naming them, and holds it read only once its lease is taken", async () => {
    const detail = { message: "jo@example.com is editing Q-000001/1.", error_code: "quote_being_edited", editor: "jo@example.com" }
    const taken = () => new ApiError("HTTP 409", 409, detail.message, { detail }, detail)
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(editAppQuote).mockRejectedValueOnce(taken())
    const view = await opened()

    expect(screen.getByTestId("notice-editing")).toHaveTextContent("jo@example.com is editing this quote, so it is open read only.")
    // Nothing was typed, so nothing is said to be kept.
    expect(screen.getByTestId("notice-editing")).not.toHaveTextContent("What you typed")
    expect(useWorkbenchPreviewStore.getState().readOnly).toBe(true)
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    // Try again takes the quote once its editor has let it go.
    fireEvent.click(within(screen.getByTestId("notice-editing")).getByRole("button", { name: "Try again" }))
    await vi.waitFor(() => expect(useWorkbenchPreviewStore.getState().readOnly).toBe(false))
    expect(screen.queryByTestId("notice-editing")).toBeNull()
    vi.mocked(editAppQuote).mockRejectedValueOnce(taken())
    await act(async () => useOpenQuoteStore.getState().keepEditing())
    view.unmount()
    // Held by someone else, it is not this page's to let go.
    expect(releaseAppQuote).not.toHaveBeenCalled()

    // Held, then lost: typed values are kept as the quote turns read only.
    await opened()
    expect(screen.queryByTestId("notice-editing")).toBeNull()
    type("CA")
    vi.mocked(editAppQuote).mockRejectedValueOnce(taken())
    await act(async () => useOpenQuoteStore.getState().keepEditing())

    expect(screen.getByTestId("notice-editing")).toHaveTextContent("What you typed is kept in this browser.")
    expect(useWorkbenchPreviewStore.getState().readOnly).toBe(true)
    expect(window.localStorage.getItem(draftKey("motor", "ana@example.com", "Q-000001/1"))).toContain("CA")
  })

  it("shows the quote's name as it was made, offering nothing to rename it, its cell taking no typing", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, { values: { ...VALUES, quote_details: [{ quote_name: "Fleet" }] } }))
    await opened()

    expect(screen.getByTestId("quote-name")).toHaveTextContent("Fleet")
    expect(screen.queryByRole("textbox", { name: "Quote name" })).toBeNull()
    // A quote_name cell on the sheets is the app's once the quote is made.
    act(() => useWorkbenchPreviewStore.getState().setCell("quote_details", 0, "quote_name", "Fleet renewal"))
    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ quote_name: "Fleet" }])
    expect(screen.queryByTestId("quote-unsaved")).toBeNull()
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
    expect(await within(panel).findByText("jo@example.com saved it")).toBeInTheDocument()
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
    expect(screen.getByLabelText("Assigned to")).toBeDisabled()
    expect(screen.getByRole("combobox", { name: "State" })).toBeDisabled()
  })
})
