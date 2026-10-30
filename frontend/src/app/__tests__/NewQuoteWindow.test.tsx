/**
 * The New quote window (specs/workbench): the reference and the quote_details columns the
 * underwriter types, drawn as a Collection draws them and checked whatever the sheets show;
 * Create off until the reference fits, a required detail left for Mark final to ask for; a
 * reference taken as keyed, its case kept; a quote made over a new quote's defaults, or a
 * quote's values for a duplicate or a renewal, and taken to its own address; Cancel and
 * Escape making nothing; a reference in use refused with Open it; and a creation whose answer
 * is lost kept in the tab, tried again exactly as it was, and offered back after a reload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  createAppQuote: vi.fn(),
  openAppQuote: vi.fn(),
}))

import { createAppQuote, openAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { FormSpec, SchemaColumn, WorkbenchAppQuoteResponse } from "../../api/types"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import NewQuoteWindow from "../NewQuoteWindow"
import { keptCreationKey, readKeptCreation } from "../quoteValues"
import { navigate, setLeaveGuard } from "../route"
import useAppStore from "../useAppStore"
import useNewQuoteStore, { windowFields, windowProblems } from "../useNewQuoteStore"
import { answer, release } from "./fixtures"

const store = () => useNewQuoteStore.getState()
const KEPT = keptCreationKey("motor", "ana@example.com")
const made = (overrides: Parameters<typeof answer>[0] = {}) => answer({ reference: "ABC-1", priced_release: null, ...overrides })

/** The release's form: Haute's quote_details first, as every form holds it, its columns as `set` sets them, by id. */
function formWith(set: Record<string, Partial<SchemaColumn>> = {}): FormSpec {
  const sheets = sheetForm()
  const details = detailsTable()
  details.columns = details.columns.map((column) => ({ ...column, ...set[column.id] }))
  return { ...sheets, schema: { tables: [details, ...sheets.schema.tables] } }
}

const windowShown = () => screen.getByTestId("new-quote-window")
const field = (label: string) => within(windowShown()).getByLabelText(label)
/** Key `value` into the field labelled `label`, committed as leaving it commits it. */
const key = (label: string, value: string) => {
  fireEvent.change(field(label), { target: { value } })
  fireEvent.blur(field(label))
}
const create = () => screen.getByTestId("new-quote-create")

function shown() {
  return render(
    <>
      <NewQuoteWindow />
      <ToastContainer />
    </>,
  )
}

describe("NewQuoteWindow", () => {
  let form: FormSpec
  beforeEach(async () => {
    form = formWith()
    useAppStore.setState({ release, form, revision: "rev-9", people: [], releaseChanged: false })
    useNewQuoteStore.setState({ shown: null })
    useToastStore.setState({ toasts: [] })
    await navigate("/", { force: true })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    setLeaveGuard(null)
    window.sessionStorage.clear()
    window.localStorage.clear()
  })

  it("asks for the reference and the details the underwriter types, in the table's order, each drawn as a Collection draws it", () => {
    form = formWith({ qd_broker: { options: ["Marsh", "Aon"] }, qd_insured: { placeholder: "Company name" } })
    useAppStore.setState({ form })
    shown()

    act(() => store().openNew())

    expect(screen.getByRole("dialog", { name: "New quote" })).toBeInTheDocument()
    const labels = within(windowShown()).getAllByTestId(/^label-quote_details:/).map((label) => label.dataset.testid?.slice("label-quote_details:".length))
    expect(labels).toEqual(["qd_reference", "qd_quote_name", "qd_insured", "qd_broker", "qd_inception_date", "qd_expiry_date", "qd_is_renewal"])
    // The reference is always required, and typed here though the sheets keep it read only.
    expect(within(windowShown()).getByTestId("label-quote_details:qd_reference")).toHaveTextContent("Quote reference *")
    expect(field("Quote reference")).toBeEnabled()
    expect(field("Quote reference")).toHaveFocus()
    expect(field("Broker").tagName).toBe("SELECT")
    expect(field("Insured")).toHaveAttribute("placeholder", "Company name")
    expect(field("Inception date")).toHaveAttribute("type", "date")
    expect(field("Is renewal")).toHaveAttribute("type", "checkbox")
  })

  it("keeps Create off until the reference fits, a required detail left empty for marking the quote final to ask for", () => {
    form = formWith({ qd_insured: { required: true } })
    useAppStore.setState({ form })
    shown()
    act(() => store().openNew())

    key("Quote reference", "ABC/1")
    expect(create()).toBeDisabled()
    expect(field("Quote reference")).toHaveAttribute("title", "No / in a reference")
    key("Quote reference", "x".repeat(41))
    expect(field("Quote reference")).toHaveAttribute("title", "At most 40 characters")
    // An address reads them as folders.
    for (const folder of [".", ".."]) {
      key("Quote reference", folder)
      expect(create()).toBeDisabled()
      expect(field("Quote reference")).toHaveAttribute("title", "Not . or ..")
    }
    key("Quote reference", "ABC-1")
    key("Insured", "   ")

    // Insured is required: its star says so, but only Mark final asks for it.
    expect(within(windowShown()).getByTestId("label-quote_details:qd_insured")).toHaveTextContent("Insured *")
    expect(create()).toBeEnabled()
    expect(field("Insured")).not.toHaveAttribute("aria-invalid")
  })

  it("marks the reference left empty when Create is pressed from it being typed in, then sends what it holds, a required detail left empty", async () => {
    form = formWith({ qd_insured: { required: true } })
    useAppStore.setState({ form })
    vi.mocked(createAppQuote).mockResolvedValue(made())
    shown()
    act(() => store().openNew())

    act(() => field("Quote reference").focus())
    expect(create()).toBeEnabled()
    expect(field("Quote reference")).not.toHaveAttribute("aria-invalid")
    // The press keeps the focus in the field, so its blur cannot turn Create off first.
    expect(fireEvent.mouseDown(create())).toBe(false)
    fireEvent.click(create())

    expect(createAppQuote).not.toHaveBeenCalled()
    expect(field("Quote reference")).toHaveAttribute("title", "Required")
    expect(field("Quote reference")).not.toHaveFocus()

    // Typed and not yet left, the press commits it before checking.
    act(() => field("Quote reference").focus())
    fireEvent.change(field("Quote reference"), { target: { value: "ABC-1" } })
    fireEvent.mouseDown(create())
    fireEvent.click(create())

    await vi.waitFor(() => expect(createAppQuote).toHaveBeenCalledOnce())
    const [body, , reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([reference, body.values.quote_details]).toEqual(["ABC-1", [{}]])
  })

  it("makes the quote with its reference and details over a new quote's defaults, and takes it to its own address", async () => {
    form = formWith({ qd_quote_name: { default_value: "Fleet" } })
    form.schema.tables[1].columns[0].default_value = "NY"
    useAppStore.setState({ form })
    vi.mocked(createAppQuote).mockResolvedValue(made())
    shown()
    act(() => store().openNew())

    // A column of quote_details with a default starts the window holding it.
    expect(field("Quote name")).toHaveValue("Fleet")
    key("Quote reference", "  ABC-1 ")
    key("Insured", "Acme")
    key("Inception date", "2026-11-01")
    fireEvent.click(create())

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/ABC-1/1"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference, body.form_revision, body.release]).toEqual([null, "ABC-1", "rev-9", "rel-1"])
    // Made unpriced: a creation sends no prices.
    expect(body).not.toHaveProperty("shown")
    // The default of a column in another one-row input table is held as a new quote holds it.
    expect(body.values).toEqual({
      policy: [{ "policy.state": "NY" }],
      quote_details: [{ qd_quote_name: "Fleet", qd_insured: "Acme", qd_inception_date: "2026-11-01" }],
    })
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
    expect(readKeptCreation(KEPT)).toBeNull()
  })

  it("takes the reference as keyed, its case kept, and opens the quote at it, encoded", async () => {
    vi.mocked(createAppQuote).mockResolvedValue(made({ reference: "abc 1" }))
    shown()
    act(() => store().openNew())

    key("Quote reference", "abc 1")
    fireEvent.click(create())

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/abc%201/1"))
    expect(vi.mocked(createAppQuote).mock.calls[0][2]).toBe("abc 1")
  })

  it("makes nothing on Cancel or Escape", () => {
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Cancel" }))
    expect(screen.queryByTestId("new-quote-window")).toBeNull()

    act(() => store().openNew())
    fireEvent.keyDown(document, { key: "Escape" })
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
    expect(createAppQuote).not.toHaveBeenCalled()
  })

  it("takes no typing and does not close while its creation is unanswered", async () => {
    let answering: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(createAppQuote).mockReturnValue(new Promise((resolve) => (answering = resolve)))
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(create())

    await vi.waitFor(() => expect(create()).toHaveTextContent("Creating…"))
    expect(create()).toBeDisabled()
    expect(field("Quote reference")).toBeDisabled()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(windowShown()).toBeInTheDocument()
    await act(async () => answering(made()))
    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/ABC-1/1"))
  })

  it("leaves a page that moved on before its creation was answered where it is, the quote made", async () => {
    let answering: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(createAppQuote).mockReturnValue(new Promise((resolve) => (answering = resolve)))
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(create())
    await vi.waitFor(() => expect(create()).toHaveTextContent("Creating…"))

    await act(async () => {
      await navigate("/?view=team", { force: true })
    })
    await act(async () => answering(made()))

    await vi.waitFor(() => expect(screen.queryByTestId("new-quote-window")).toBeNull())
    expect(`${window.location.pathname}${window.location.search}`).toBe("/?view=team")
    expect(readKeptCreation(KEPT)).toBeNull()
  })

  it("refuses a reference in use, naming the quote holding it, with Open it, and takes typing again", async () => {
    vi.mocked(createAppQuote).mockRejectedValue(
      new ApiError("HTTP 409", 409, "taken", {}, {
        message: "ABC-1/1 already holds the reference ABC-1: open it, or create an alternative of it.",
        error_code: "reference_taken",
        reference: "ABC-1",
        option: 1,
        label: "ABC-1/1",
      }),
    )
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(create())

    expect(await screen.findByTestId("new-quote-refused")).toHaveTextContent("ABC-1/1 already holds the reference ABC-1")
    // Refused, nothing was made: the creation is let go, and the window takes typing again.
    expect(readKeptCreation(KEPT)).toBeNull()
    expect(field("Quote reference")).toBeEnabled()
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Open it" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/ABC-1/1"))
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })

  it("says a page drawn for an earlier release must be reloaded", async () => {
    vi.mocked(createAppQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "This page was drawn for another release of the app: reload it to save on this one."))
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(create())

    expect(await within(windowShown()).findByRole("button", { name: "Reload" })).toBeInTheDocument()
    expect(useAppStore.getState().releaseChanged).toBe(true)
  })

  it("keeps a creation whose answer is lost and sends it again exactly as it was, after a reload too", async () => {
    vi.mocked(createAppQuote)
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockRejectedValueOnce(new ApiError("HTTP 504", 504, "Gateway timeout"))
      .mockResolvedValue(made())
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    key("Insured", "Acme")
    fireEvent.click(create())

    expect(await screen.findByTestId("new-quote-unknown")).toHaveTextContent("No answer came, so ABC-1 may have been made.")
    // What is typed after it was sent is not what it sends.
    act(() => store().setDetail("qd_insured", "Other"))
    expect(field("Insured")).toBeDisabled()
    fireEvent.click(screen.getByTestId("new-quote-try-again"))
    await vi.waitFor(() => expect(createAppQuote).toHaveBeenCalledTimes(2))
    expect(await screen.findByTestId("new-quote-unknown")).toBeInTheDocument()
    // Closed, it is kept; the page reloaded, it is offered back before anything else is made.
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Close" }))
    expect(readKeptCreation(KEPT)?.reference).toBe("ABC-1")
    cleanup()
    useNewQuoteStore.setState({ shown: null })
    shown()
    act(() => store().openNew())
    expect(screen.getByRole("dialog", { name: "New quote" })).toBeInTheDocument()
    expect(within(windowShown()).getByRole("button", { name: "Discard" })).toBeInTheDocument()
    fireEvent.click(screen.getByTestId("new-quote-try-again"))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/ABC-1/1"))
    const bodies = vi.mocked(createAppQuote).mock.calls
    expect(bodies[1]).toEqual(bodies[0])
    expect(bodies[2]).toEqual(bodies[0])
    expect(bodies[0][0].values.quote_details).toEqual([{ qd_insured: "Acme" }])
    expect(readKeptCreation(KEPT)).toBeNull()
  })

  it("lets a kept creation go on Discard, and the next New quote starts afresh", async () => {
    vi.mocked(createAppQuote).mockRejectedValue(new TypeError("Failed to fetch"))
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")
    fireEvent.click(create())
    await screen.findByTestId("new-quote-unknown")
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Close" }))

    act(() => store().openNew())
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Discard" }))
    expect(readKeptCreation(KEPT)).toBeNull()
    act(() => store().openNew())

    expect(field("Quote reference")).toHaveValue("")
    expect(within(windowShown()).getByRole("button", { name: "Create" })).toBeInTheDocument()
  })

  it("fills a renewal from the quote as it is kept, its reference blank and is_renewal ticked, and makes it from that quote", async () => {
    const values = { policy: [{ "policy.state": "CA" }], quote_details: [{ qd_reference: "Q-000001", qd_option: "1", qd_stage: "final", qd_insured: "Acme" }] }
    vi.mocked(openAppQuote).mockResolvedValue(answer({ stage: "final" }, { values, dropped: ["equipment row 2 (Crane)"] }))
    vi.mocked(createAppQuote).mockResolvedValue(made({ reference: "DEF-2", relation: "renewal" }))
    shown()

    await act(() => store().openDerived({ reference: "Q-000001", option: 1, relation: "renewal" }, { label: "Q-000001/1" }))

    expect(screen.getByRole("dialog", { name: "Renew Q-000001/1" })).toBeInTheDocument()
    expect(openAppQuote).toHaveBeenCalledWith("Q-000001", 1)
    expect(field("Insured")).toHaveValue("Acme")
    expect(field("Is renewal")).toBeChecked()
    expect(field("Quote reference")).toHaveValue("")
    expect(screen.getByTestId("new-quote-dropped")).toHaveTextContent("No place on the current sheets for: equipment row 2 (Crane).")
    key("Quote reference", "DEF-2")
    fireEvent.click(create())

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/DEF-2/1"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference]).toEqual([{ reference: "Q-000001", option: 1, relation: "renewal" }, "DEF-2"])
    // Without the details the app set for its source, which are not the new quote's.
    expect(body.values).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [{ qd_insured: "Acme", qd_is_renewal: true }] })
  })

  it("says why the quote to fill it from could not be read, and makes nothing", async () => {
    vi.mocked(openAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "The app keeps no quote Q-000009/1."))
    shown()

    await act(() => store().openDerived({ reference: "Q-000009", option: 1, relation: "duplicate" }))

    expect(within(windowShown()).getByRole("alert")).toHaveTextContent("The quote could not be read: The app keeps no quote Q-000009/1.")
    expect(create()).toBeDisabled()
  })

  it("closes a window that has sent nothing as the page moves, Back included", async () => {
    shown()
    act(() => store().openNew())

    await act(async () => {
      await navigate("/?view=team", { force: true })
    })

    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })

  it("opens one window at a time, filled by no quote read after it closed", async () => {
    let reading: (value: WorkbenchAppQuoteResponse) => void = () => {}
    vi.mocked(openAppQuote).mockReturnValue(new Promise((resolve) => (reading = resolve)))
    const origin = { reference: "Q-000001", option: 1, relation: "duplicate" as const }
    shown()
    act(() => store().openNew())

    // Over a window already open, neither another window nor Save as new opens.
    await act(async () => store().openDerived(origin))
    act(() => store().openSaveAsNew())
    expect(store().shown?.title).toBe("New quote")
    expect(openAppQuote).not.toHaveBeenCalled()

    act(() => store().close())
    const filling = store().openDerived(origin)
    act(() => store().close())
    await act(async () => {
      reading(answer({}, { values: { policy: [{ "policy.state": "CA" }] } }))
      await filling
    })
    expect(store().shown).toBeNull()
    // A failure answered after it closed says nothing either.
    vi.mocked(openAppQuote).mockRejectedValueOnce(new ApiError("HTTP 404", 404, "gone"))
    const failing = store().openDerived(origin)
    act(() => store().close())
    await act(async () => failing)
    expect(store().shown).toBeNull()
  })

  it("opens Save as new over a quote open alone, and nothing while the release's form is unread", () => {
    act(() => store().openSaveAsNew())
    expect(store().shown).toBeNull()

    useAppStore.setState({ form: null })
    act(() => store().openNew())
    expect(store().shown).toBeNull()
  })

  it("takes no typing, no second Create and no Try again while its creation is being sent", async () => {
    vi.mocked(createAppQuote).mockReturnValue(new Promise(() => {}))
    shown()
    act(() => store().openNew())
    act(() => store().setReference("ABC-1"))
    await act(async () => store().tryAgain())
    expect(createAppQuote).not.toHaveBeenCalled()

    void store().create()
    await vi.waitFor(() => expect(store().shown?.status).toBe("sending"))
    act(() => store().setReference("XYZ-9"))
    await act(async () => store().create())

    expect(store().shown?.reference).toBe("ABC-1")
    expect(createAppQuote).toHaveBeenCalledOnce()
  })

  it("names no quote for a reference taken when the refusal does not say which holds it", async () => {
    vi.mocked(createAppQuote).mockRejectedValue(
      new ApiError("HTTP 409", 409, "taken", {}, { message: "ABC-1 is taken.", error_code: "reference_taken" }),
    )
    shown()
    act(() => store().openNew())
    key("Quote reference", "ABC-1")

    fireEvent.click(create())

    await vi.waitFor(() => expect(store().shown?.status).toBe("editing"))
    expect(store().shown).toMatchObject({ error: "ABC-1 is taken.", taken: null })
  })

  it("asks for nothing on a form without quote_details, and the reference alone as its table holds it", () => {
    expect(windowFields(sheetForm())).toEqual([])

    const details = detailsTable()
    details.columns = details.columns.filter((column) => column.id !== "qd_reference")
    const fields = windowFields({ ...sheetForm(), schema: { tables: [details] } })
    expect(fields.map((field) => field.found?.column.id)).not.toContain("qd_reference")
    expect(windowProblems([{ key: "gone", found: null }, ...fields], "", {})).toEqual({})
  })

  it("opens nothing for one who may only read quotes", () => {
    useAppStore.setState({ release: { ...release, can_edit: false } })
    shown()

    act(() => store().openNew())

    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })
})
