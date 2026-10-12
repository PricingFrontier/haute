/**
 * A quote in the underwriter app (specs/workbench): its bar and line, saving a new quote to
 * its own address, marking final and reopening, the notices of what stands in the way and
 * what each offers, leaving with unsaved values, the keyboard, History, and assigning.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  carryAppQuote: vi.fn(),
  changeAppQuote: vi.fn(),
  createAppQuote: vi.fn(),
  fetchAppPeople: vi.fn(),
  fetchAppQuoteHistory: vi.fn(),
  openAppQuote: vi.fn(),
  saveAppQuote: vi.fn(),
}))

import { carryAppQuote, changeAppQuote, createAppQuote, fetchAppPeople, fetchAppQuoteHistory, openAppQuote, saveAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../../stores/useWorkbenchPreviewStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import { sheetForm, stubLayout } from "../../workbench/__tests__/fixtures"
import { APP_SET_COLUMNS } from "../openRelease"
import { draftKey, writeDraft } from "../quoteValues"
import QuoteView from "../QuoteView"
import { navigate, setLeaveGuard, type AppRoute } from "../route"
import { QuestionDialog } from "../ui"
import useAppStore from "../useAppStore"
import useOpenQuoteStore from "../useOpenQuoteStore"
import { answer, appQuote, release, VALUES } from "./fixtures"

type QuoteRoute = Extract<AppRoute, { page: "new" } | { page: "quote" }>

function show(route: QuoteRoute) {
  return render(
    <>
      <QuoteView route={route} />
      <QuestionDialog />
      <ToastContainer />
    </>,
  )
}

const opened = async (route: QuoteRoute = { page: "quote", id: "q1", revision: null }) => {
  const view = show(route)
  await screen.findByTestId("quote-line")
  return view
}

const type = (value: string) => act(() => useWorkbenchPreviewStore.getState().setCell("policy", 0, "policy.state", value))
const confirmQuestion = (label: string) => fireEvent.click(within(screen.getByTestId("app-question")).getByRole("button", { name: label }))

describe("QuoteView", () => {
  beforeEach(async () => {
    stubLayout()
    const form = sheetForm()
    useAppStore.setState({ release, form, revision: "rev-9", people: [], question: null })
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchViewStore.setState({ section: "preview", pageId: null, zoom: 1, fitted: false, viewport: null, sheet: null })
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null, error: null, pricing: false, readOnly: false, locked: APP_SET_COLUMNS })
    useOpenQuoteStore.setState({ status: "idle", quote: null, revision: null, past: false, olderSheets: null, source: null, pending: null, conflict: null, error: null, historyOpen: false, history: null })
    useToastStore.setState({ toasts: [] })
    vi.mocked(fetchAppPeople).mockResolvedValue({ people: ["jo@example.com"] })
    vi.mocked(fetchAppQuoteHistory).mockResolvedValue({ revisions: [], events: [], source: null, derived: [] })
    await navigate("/quotes/q1", { force: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    setLeaveGuard(null)
    window.localStorage.clear()
  })

  it("saves a new quote, which takes its own address, once something is typed", async () => {
    await navigate("/quotes/new", { force: true })
    vi.mocked(createAppQuote).mockResolvedValue(answer({ stage: "draft" }, { tables: null }))
    await opened({ page: "new", from: null })

    const save = screen.getByTestId("save-quote")
    expect(save).toBeDisabled()
    expect(screen.getByTestId("quote-reference")).toHaveTextContent("New quote")
    type("NY")
    expect(save).toHaveTextContent("Save unpriced")
    fireEvent.click(save)

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q1"))
    expect(await screen.findByText("Saved Q-000001, revision 1")).toBeInTheDocument()
    expect(openAppQuote).not.toHaveBeenCalled()
    expect(screen.getByTestId("quote-reference")).toHaveTextContent("Q-000001")
  })

  it("shows the revision, who saved it and the release that priced it, and saves on Ctrl+S", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2 }, { number: 2 }))
    await opened()

    const line = screen.getByTestId("quote-line")
    expect(line).toHaveTextContent("Revision 1")
    expect(line).toHaveTextContent("by ana@example.com")
    expect(line).toHaveTextContent("Priced on this release")
    expect(screen.getByTestId("save-quote")).toBeDisabled()
    type("CA")
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    fireEvent.keyDown(window, { key: "s", ctrlKey: true })

    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
  })

  it("asks before leaving with unsaved values, and stays on Cancel", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()
    type("CA")

    let left: Promise<boolean> = Promise.resolve(true)
    act(() => {
      left = navigate("/")
    })
    expect(await screen.findByTestId("app-question")).toHaveTextContent("Leave without saving?")
    confirmQuestion("Cancel")
    expect(await left).toBe(false)
    expect(window.location.pathname).toBe("/quotes/q1")
  })

  it("keeps unsaved values in the browser as they are typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    await opened()
    type("CA")

    await vi.waitFor(() => expect(window.localStorage.getItem(draftKey("motor", "ana@example.com", "q1"))).toContain("CA"))
  })

  it("marks a priced quote final once asked, and reopens it giving a reason", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce({ quote: appQuote({ stage: "final", final_revision: 1, final_by: "ana@example.com", final_at: "2026-10-12T10:00:00+00:00", version: 2 }), revision: null })
      .mockResolvedValueOnce({ quote: appQuote({ version: 3 }), revision: null })
    await opened()

    fireEvent.click(screen.getByRole("button", { name: "Mark final" }))
    confirmQuestion("Mark final")
    expect(await screen.findByTestId("notice-final")).toHaveTextContent("Final at revision 1")
    expect(screen.getByRole("button", { name: "Price" })).toBeDisabled()

    fireEvent.click(screen.getByRole("button", { name: "Reopen" }))
    const reopen = within(screen.getByTestId("app-question")).getByRole("button", { name: "Reopen" })
    expect(reopen).toBeDisabled()
    fireEvent.change(screen.getByLabelText("Why it is being reopened"), { target: { value: "Broker asked" } })
    fireEvent.click(reopen)

    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenLastCalledWith("q1", "reopen", 2, { reason: "Broker asked" }))
    await vi.waitFor(() => expect(screen.queryByTestId("notice-final")).toBeNull())
  })

  it("does not mark final a draft or a quote priced on an earlier release, saying why", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ release: "rel-0" }, { release: "rel-0" }))
    await opened()

    const final = screen.getByRole("button", { name: "Mark final" })
    expect(final).toBeDisabled()
    expect(final).toHaveAttribute("title", "Priced on an earlier release: price it again and save it first")
    expect(screen.getByTestId("quote-line")).toHaveTextContent("Priced on release rel-0")
  })

  it("offers to open the newest or save the values as a new quote when the quote moved on", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "moved", {}, { message: "Q-000001 has changed since you opened it: jo saved it.", error_code: "stale_quote_revision", quote: appQuote({ latest: 2, version: 3 }) }))
    vi.mocked(createAppQuote).mockResolvedValue(answer({ id: "q2", reference: "Q-000002" }))
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))

    expect(await screen.findByTestId("notice-conflict")).toHaveTextContent("jo saved it. Your changes are still on the sheets.")
    fireEvent.click(screen.getByRole("button", { name: "Save mine as a new quote" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q2"))
    expect(vi.mocked(createAppQuote).mock.calls[0][1]).toEqual({ quote_id: "q1", revision: 2, relation: "duplicate" })
  })

  it("offers a reload when the page was drawn for an earlier release", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "This page was drawn for another release of the app."))
    await opened()
    type("CA")
    fireEvent.click(screen.getByTestId("save-quote"))

    expect(await screen.findByTestId("notice-release")).toHaveTextContent("earlier release")
  })

  it("shows an earlier revision read only, with the newest a press away", async () => {
    vi.mocked(openAppQuote).mockResolvedValueOnce(answer({ latest: 2 }, { number: 1 })).mockResolvedValue(answer({ latest: 2 }, { number: 2 }))
    await navigate("/quotes/q1/revisions/1", { force: true })
    await opened({ page: "quote", id: "q1", revision: 1 })

    expect(screen.getByTestId("notice-past")).toHaveTextContent("Revision 1 as it was saved, read only. The newest is revision 2.")
    expect(screen.getByRole("combobox", { name: "State" })).toBeDisabled()
    fireEvent.click(screen.getByRole("button", { name: "Open the newest" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q1"))
    await vi.waitFor(() => expect(screen.queryByTestId("notice-past")).toBeNull())
  })

  it("moves a quote on earlier sheets onto the current ones, naming what no longer fits", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({}, {}, { form: { ...sheetForm(), name: "older" } }))
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { policy: [{ "policy.state": "NY" }] }, dropped: ["policy.old"] })
    await opened()

    fireEvent.click(screen.getByRole("button", { name: "Move to current sheets" }))

    expect(await screen.findByTestId("notice-dropped")).toHaveTextContent("No place on the current sheets for: policy.old.")
    expect(screen.queryByTestId("notice-older-sheets")).toBeNull()
  })

  it("restores an archived quote from its notice, and archives one with Undo", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ archived_at: "2026-10-12T10:00:00+00:00", archived_by: "jo@example.com" }))
    vi.mocked(changeAppQuote)
      .mockResolvedValueOnce({ quote: appQuote({ version: 2 }), revision: null })
      .mockResolvedValueOnce({ quote: appQuote({ version: 3, archived_at: "2026-10-12T11:00:00+00:00" }), revision: null })
      .mockResolvedValueOnce({ quote: appQuote({ version: 4 }), revision: null })
    await opened()
    expect(screen.getByTestId("notice-archived")).toHaveTextContent("Archived by jo@example.com")

    fireEvent.click(screen.getByRole("button", { name: "Restore" }))
    await vi.waitFor(() => expect(screen.queryByTestId("notice-archived")).toBeNull())
    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Archive" }))
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }))

    await vi.waitFor(() => expect(vi.mocked(changeAppQuote).mock.calls.map((call) => call[1])).toEqual(["restore", "archive", "restore"]))
  })

  it("starts a renewal from the menu at the revision showing", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ latest: 2 }, { number: 2 }))
    await opened()

    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Renew" }))

    await vi.waitFor(() => expect(`${window.location.pathname}${window.location.search}`).toBe("/quotes/new?from=q1&revision=2&as=renewal"))
  })

  it("copies the quote's link", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const writeText = vi.fn(async () => {})
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } })
    await opened()

    fireEvent.click(screen.getByTestId("quote-more"))
    fireEvent.click(screen.getByRole("menuitem", { name: "Copy link" }))

    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/quotes/q1`))
    expect(await screen.findByText("Link to Q-000001 copied")).toBeInTheDocument()
  })

  it("offers back values this browser kept", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    writeDraft(draftKey("motor", "ana@example.com", "q1"), { values: { policy: [{ "policy.state": "CA" }] }, version: 0, latest: 1, formRevision: "rev-9", keptAt: new Date().toISOString() })
    await opened()

    expect(screen.getByTestId("notice-recovered")).toHaveTextContent("not saved to the server. The quote has been saved since then.")
    fireEvent.click(screen.getByRole("button", { name: "Restore them" }))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "CA" }] })
  })

  it("assigns the quote to someone from the people, or to a name typed", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(changeAppQuote).mockResolvedValueOnce({ quote: appQuote({ assignee: "jo@example.com", version: 2 }), revision: null }).mockResolvedValueOnce({ quote: appQuote({ assignee: "sam@example.com", version: 3 }), revision: null })
    await opened()
    await screen.findByRole("option", { name: "jo@example.com" })

    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledWith("q1", "assignee", 1, { assignee: "jo@example.com" }))
    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "" } })
    fireEvent.change(await screen.findByLabelText("Their sign-in name"), { target: { value: "sam@example.com" } })
    confirmQuestion("Assign")
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenLastCalledWith("q1", "assignee", 2, { assignee: "sam@example.com" }))
  })

  it("takes the details an assignment set onto the sheets, what was typed kept", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    const details = { qd_reference: "Q-000001", qd_stage: "priced", qd_assignee: "jo@example.com", qd_archived: false }
    vi.mocked(changeAppQuote).mockResolvedValue({
      quote: appQuote({ assignee: "jo@example.com", latest: 2, version: 2 }),
      revision: answer({}, { number: 2, values: { ...VALUES, quote_details: [details] } }).revision,
    })
    await opened()
    await screen.findByRole("option", { name: "jo@example.com" })
    type("CA")
    // A cell the app sets takes no typing.
    act(() => useWorkbenchPreviewStore.getState().setCell("quote_details", 0, "qd_stage", "final"))

    fireEvent.change(screen.getByLabelText("Assigned to"), { target: { value: "jo@example.com" } })

    await vi.waitFor(() => expect(useOpenQuoteStore.getState().revision?.number).toBe(2))
    expect(useWorkbenchPreviewStore.getState().quote).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [details] })
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    expect(screen.getByTestId("quote-line")).toHaveTextContent("Revision 2")
  })

  it("names the quote in its quote_details.quote_name cell, saved with the quote", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer())
    vi.mocked(saveAppQuote).mockResolvedValue(answer({ latest: 2, version: 2 }, { number: 2 }))
    await opened()

    const name = screen.getByLabelText("Quote name")
    expect(name).toHaveAttribute("placeholder", "Fleet")
    fireEvent.change(name, { target: { value: " Fleet renewal " } })
    fireEvent.blur(name)

    expect(useWorkbenchPreviewStore.getState().quote.quote_details).toEqual([{ qd_quote_name: "Fleet renewal" }])
    expect(screen.getByTestId("quote-unsaved")).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("save-quote"))
    await vi.waitFor(() => expect(saveAppQuote).toHaveBeenCalled())
    expect(vi.mocked(saveAppQuote).mock.calls[0][1].values.quote_details).toEqual([{ qd_quote_name: "Fleet renewal" }])
    expect(changeAppQuote).not.toHaveBeenCalled()
  })

  it("shows the history: the note, the revisions and what was done", async () => {
    vi.mocked(openAppQuote).mockResolvedValue(answer({ latest: 2, final_revision: null }, { number: 2 }))
    vi.mocked(fetchAppQuoteHistory).mockResolvedValue({
      revisions: [
        { number: 1, saved_by: "ana@example.com", saved_at: "2026-10-12T09:00:00+00:00", priced: false, release: null, form_revision: "rev-9", stage: "draft" },
        { number: 2, saved_by: "jo@example.com", saved_at: "2026-10-12T10:00:00+00:00", priced: true, release: "rel-1", form_revision: "rev-9", stage: "priced" },
      ],
      events: [
        { id: 1, at: "2026-10-12T09:00:00+00:00", actor: "ana@example.com", kind: "created", revision: 1, detail: { reference: "Q-000001" } },
        { id: 2, at: "2026-10-12T10:00:00+00:00", actor: "jo@example.com", kind: "saved", revision: 2, detail: { priced: true } },
      ],
      source: { id: "q0", reference: "Q-000000", relation: "renewal", revision: 3 },
      derived: [{ id: "q3", reference: "Q-000003", relation: "alternative", revision: 2 }],
    })
    vi.mocked(changeAppQuote).mockResolvedValue({ quote: appQuote({ latest: 2, note: "Call the broker", note_by: "ana@example.com", note_at: "2026-10-12T11:00:00+00:00", version: 2 }), revision: null })
    await opened()

    fireEvent.click(screen.getByRole("button", { name: "History" }))
    const panel = await screen.findByTestId("quote-history")
    expect(await within(panel).findByText("jo@example.com saved revision 2, priced")).toBeInTheDocument()
    expect(within(panel).getByText("ana@example.com made it")).toBeInTheDocument()
    expect(within(panel).getByRole("link", { name: "Revision 1" })).toHaveAttribute("href", "/quotes/q1/revisions/1")
    expect(within(panel).getByRole("link", { name: "Revision 2" })).toHaveAttribute("href", "/quotes/q1")
    expect(within(panel).getByRole("link", { name: "Q-000000" })).toHaveAttribute("href", "/quotes/q0/revisions/3")
    expect(within(panel).getByRole("link", { name: "Q-000003" })).toHaveAttribute("href", "/quotes/q3")

    fireEvent.change(within(panel).getByLabelText("Handover note"), { target: { value: "Call the broker" } })
    fireEvent.click(within(panel).getByRole("button", { name: "Save note" }))
    await vi.waitFor(() => expect(changeAppQuote).toHaveBeenCalledWith("q1", "note", 1, { note: "Call the broker" }))
    expect(await within(panel).findByText(/By ana@example.com/)).toBeInTheDocument()
  })

  it("says why a quote could not be opened, with the way back", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "The app keeps no quote 'q1'."))
    show({ page: "quote", id: "q1", revision: null })

    expect(await screen.findByRole("alert")).toHaveTextContent("The quote could not be opened: The app keeps no quote 'q1'.")
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
