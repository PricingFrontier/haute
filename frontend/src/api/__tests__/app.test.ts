/**
 * The underwriter app's client on the wire (specs/workbench): each quote route's method,
 * address and body, each body checked against the generated model of the route that reads
 * it, so a field the server renames fails here and not in an underwriter's browser.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { answer, changed } from "../../app/__tests__/fixtures"
import {
  validateWorkbenchAppPriceRequest,
  validateWorkbenchAppQuoteAmend,
  validateWorkbenchAppQuoteAssign,
  validateWorkbenchAppQuoteChange,
  validateWorkbenchAppQuoteCreateRequest,
  validateWorkbenchAppQuoteNote,
  validateWorkbenchAppQuoteSaveRequest,
} from "../../generated/api-contracts.workbench-app-requests.validators.mjs"
import { changeAppQuote, createAppQuote, fetchAppQuoteActivity, openAppQuote, priceAppQuote, saveAppQuote, type QuoteCreation, type QuoteSave } from "../app"

const CREATION: QuoteCreation = {
  operation: "save-0123456789",
  form_revision: "rev-9",
  release: "rel-1",
  values: { policy: [{ "policy.state": "NY" }] },
}
const SAVE: QuoteSave = { ...CREATION, shown: { premiums: [{ item: "Crane", premium: 120 }] } }

let fetchMock: ReturnType<typeof vi.fn>

const answering = (body: unknown) =>
  fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }))

/** The one request sent: its method, address and body. */
function sent(): { method: string; url: string; body: unknown } {
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  return { method: init.method ?? "GET", url, body: init.body === undefined ? undefined : JSON.parse(String(init.body)) }
}

describe("the app's quote routes on the wire", () => {
  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)
  })

  afterEach(() => vi.unstubAllGlobals())

  it("prices the values typed on the sheets of a revision and a release", async () => {
    answering({ tables: {}, request: {} })

    await priceAppQuote("rev-9", "rel-1", SAVE.values)

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", "/api/app/price"])
    expect(validateWorkbenchAppPriceRequest(body)).toBe(true)
    expect(body).toEqual({ form_revision: "rev-9", release: "rel-1", values: SAVE.values })
  })

  it("creates a quote, unpriced, with its operation key, values, source and the reference keyed", async () => {
    answering(answer())

    await createAppQuote(CREATION, { reference: "Q 0", option: 2, relation: "renewal" }, "ABC-1")

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", "/api/app/quotes"])
    expect(validateWorkbenchAppQuoteCreateRequest(body)).toBe(true)
    expect(body).toEqual({ ...CREATION, source: { reference: "Q 0", option: 2, relation: "renewal" }, reference: "ABC-1" })
  })

  it("opens a quote, and reads its activity, by its reference encoded and its option", async () => {
    answering(answer())
    await openAppQuote("Q 1/x?#", 2)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/app/quotes/Q%201%2Fx%3F%23/2")

    fetchMock.mockClear()
    answering({ entries: [], source: null, derived: [] })
    await fetchAppQuoteActivity("Q 1", 2)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/app/quotes/Q%201/2/activity")
  })

  it("saves a quote over the version it names", async () => {
    answering(answer({ version: 3 }))

    await saveAppQuote("Q 1", 2, { ...SAVE, shown: null }, 2)

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", "/api/app/quotes/Q%201/2/save"])
    expect(validateWorkbenchAppQuoteSaveRequest(body)).toBe(true)
    expect(body).toEqual({ ...SAVE, shown: null, version: 2 })
  })

  it.each([
    ["assignee", { assignee: "jo@example.com" }, validateWorkbenchAppQuoteAssign, { version: 4, assignee: "jo@example.com" }],
    ["archive", {}, validateWorkbenchAppQuoteChange, { version: 4 }],
    ["restore", {}, validateWorkbenchAppQuoteChange, { version: 4 }],
    ["final", {}, validateWorkbenchAppQuoteChange, { version: 4 }],
    ["amend", { reason: "Broker asked" }, validateWorkbenchAppQuoteAmend, { version: 4, reason: "Broker asked" }],
    // A note moves no version, so it names none.
    ["note", { note: "Call the broker" }, validateWorkbenchAppQuoteNote, { note: "Call the broker" }],
  ] as const)("sends the %s change the body its route reads", async (change, fields, validate, expected) => {
    answering(changed({ version: 5 }))

    await changeAppQuote("ABC-1", 2, change, 4, fields)

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", `/api/app/quotes/ABC-1/2/${change}`])
    expect(validate(body)).toBe(true)
    expect(body).toEqual(expected)
  })
})
