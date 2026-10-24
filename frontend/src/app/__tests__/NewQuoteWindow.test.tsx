/**
 * The New quote window (specs/workbench): the reference and the quote_details columns the
 * underwriter types, drawn as a Collection draws them and checked whatever the sheets show;
 * Create off until every field fits; a quote made over a new quote's defaults, or a revision
 * carried for a duplicate or a renewal, and taken to its own address; Cancel and Escape
 * making nothing; a reference in use refused with Open it; and a creation whose answer is
 * lost kept in the tab, tried again exactly as it was, and offered back after a reload.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"

vi.mock("../../api/app", () => ({
  carryAppQuote: vi.fn(),
  createAppQuote: vi.fn(),
}))

import { carryAppQuote, createAppQuote } from "../../api/app"
import { ApiError } from "../../api/client"
import type { FormSpec, SchemaColumn, WorkbenchAppQuoteResponse } from "../../api/types"
import ToastContainer from "../../components/Toast"
import useToastStore from "../../stores/useToastStore"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import NewQuoteWindow from "../NewQuoteWindow"
import { keptCreationKey, readKeptCreation } from "../quoteValues"
import { navigate, setLeaveGuard } from "../route"
import useAppStore from "../useAppStore"
import useNewQuoteStore from "../useNewQuoteStore"
import { answer, release } from "./fixtures"

const store = () => useNewQuoteStore.getState()
const KEPT = keptCreationKey("motor", "ana@example.com")
const made = (overrides: Parameters<typeof answer>[0] = {}) => answer({ id: "q7", reference: "ABC-1", stage: "draft", priced: false, release: null, ...overrides }, { tables: null })

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

  it("keeps Create off until the reference fits and each required column is filled, though no sheet shows them", () => {
    // Insured required, as is_renewal: a tick box is never empty, so it asks nothing more.
    form = formWith({ qd_insured: { required: true }, qd_is_renewal: { required: true } })
    useAppStore.setState({ form })
    shown()
    act(() => store().openNew())

    key("Quote reference", "ABC/1")
    expect(create()).toBeDisabled()
    expect(field("Quote reference")).toHaveAttribute("title", "No / in a reference")
    key("Quote reference", "x".repeat(41))
    expect(field("Quote reference")).toHaveAttribute("title", "At most 40 characters")
    key("Quote reference", "ABC-1")
    key("Insured", "   ")
    // Text of spaces alone is empty.
    expect(create()).toBeDisabled()
    expect(create()).toHaveAttribute("title", "Insured: Required")
    key("Insured", "Acme")
    expect(create()).toBeEnabled()
    expect(field("Is renewal")).not.toBeChecked()
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

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q7"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference, body.shown, body.form_revision, body.release]).toEqual([null, "ABC-1", null, "rev-9", "rel-1"])
    // The default of a column in another one-row input table is held as a new quote holds it.
    expect(body.values).toEqual({
      policy: [{ "policy.state": "NY" }],
      quote_details: [{ qd_quote_name: "Fleet", qd_insured: "Acme", qd_inception_date: "2026-11-01" }],
    })
    expect(screen.queryByTestId("new-quote-window")).toBeNull()
    expect(readKeptCreation(KEPT)).toBeNull()
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
    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q7"))
  })

  it("refuses a reference in use, naming the quote holding it, with Open it, and takes typing again", async () => {
    vi.mocked(createAppQuote).mockRejectedValue(
      new ApiError("HTTP 409", 409, "taken", {}, { message: "ABC-1 is taken: ABC-1/1 holds it.", error_code: "reference_taken", quote_id: "q1", label: "ABC-1/1" }),
    )
    shown()
    act(() => store().openNew())
    key("Quote reference", "abc-1")
    fireEvent.click(create())

    expect(await screen.findByTestId("new-quote-refused")).toHaveTextContent("ABC-1 is taken: ABC-1/1 holds it.")
    // Refused, nothing was made: the creation is let go, and the window takes typing again.
    expect(readKeptCreation(KEPT)).toBeNull()
    expect(field("Quote reference")).toBeEnabled()
    fireEvent.click(within(windowShown()).getByRole("button", { name: "Open it" }))

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q1"))
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

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q7"))
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

  it("fills a renewal from the revision carried, its reference blank and is_renewal ticked, and makes it from that revision", async () => {
    vi.mocked(carryAppQuote).mockResolvedValue({ values: { policy: [{ "policy.state": "CA" }], quote_details: [{ qd_insured: "Acme" }] }, dropped: ["policy.gone"] })
    vi.mocked(createAppQuote).mockResolvedValue(made({ reference: "DEF-2", relation: "renewal" }))
    shown()

    await act(() => store().openDerived({ quoteId: "q1", revision: 2, relation: "renewal" }, { label: "Q-000001/1" }))

    expect(screen.getByRole("dialog", { name: "Renew Q-000001/1" })).toBeInTheDocument()
    expect(carryAppQuote).toHaveBeenCalledWith("q1", 2)
    expect(field("Insured")).toHaveValue("Acme")
    expect(field("Is renewal")).toBeChecked()
    expect(field("Quote reference")).toHaveValue("")
    expect(screen.getByTestId("new-quote-dropped")).toHaveTextContent("No place on the current sheets for: policy.gone.")
    key("Quote reference", "DEF-2")
    fireEvent.click(create())

    await vi.waitFor(() => expect(window.location.pathname).toBe("/quotes/q7"))
    const [body, source, reference] = vi.mocked(createAppQuote).mock.calls[0]
    expect([source, reference]).toEqual([{ quote_id: "q1", revision: 2, relation: "renewal" }, "DEF-2"])
    expect(body.values).toEqual({ policy: [{ "policy.state": "CA" }], quote_details: [{ qd_insured: "Acme", qd_is_renewal: true }] })
  })

  it("says why the revision to fill it from could not be read, and makes nothing", async () => {
    vi.mocked(carryAppQuote).mockRejectedValue(new ApiError("HTTP 404", 404, "The app keeps no quote 'q9'."))
    shown()

    await act(() => store().openDerived({ quoteId: "q9", revision: 1, relation: "duplicate" }))

    expect(within(windowShown()).getByRole("alert")).toHaveTextContent("The quote could not be read: The app keeps no quote 'q9'.")
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

  it("opens nothing for one who may only read quotes", () => {
    useAppStore.setState({ release: { ...release, can_edit: false } })
    shown()

    act(() => store().openNew())

    expect(screen.queryByTestId("new-quote-window")).toBeNull()
  })
})
