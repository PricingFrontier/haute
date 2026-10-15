/**
 * The underwriter app's client on the wire (specs/workbench): each quote route's method,
 * address and body, each body checked against the generated model of the route that reads
 * it, so a field the server renames fails here and not in an underwriter's browser.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { answer, changed } from "../../app/__tests__/fixtures"
import {
  validateWorkbenchAppPriceRequest,
  validateWorkbenchAppQuoteAssign,
  validateWorkbenchAppQuoteChange,
  validateWorkbenchAppQuoteCreateRequest,
  validateWorkbenchAppQuoteFinal,
  validateWorkbenchAppQuoteNote,
  validateWorkbenchAppQuoteReopen,
  validateWorkbenchAppQuoteReviseRequest,
} from "../../generated/api-contracts.workbench-app-requests.validators.mjs"
import { carryAppQuote, changeAppQuote, createAppQuote, priceAppQuote, saveAppQuote, type QuoteSave } from "../app"

const SAVE: QuoteSave = {
  operation: "save-0123456789",
  form_revision: "rev-9",
  release: "rel-1",
  values: { policy: [{ "policy.state": "NY" }] },
  shown: { premiums: [{ item: "Crane", premium: 120 }] },
}

let fetchMock: ReturnType<typeof vi.fn>

const answering = (body: unknown) =>
  fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } }))

/** The one request sent: its method, address and body. */
function sent(): { method: string; url: string; body: unknown } {
  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
  return { method: init.method ?? "GET", url, body: JSON.parse(String(init.body)) }
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

  it("creates a quote with its operation key, values, prices shown and source", async () => {
    answering(answer())

    await createAppQuote(SAVE, { quote_id: "q0", revision: 2, relation: "renewal" })

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", "/api/app/quotes"])
    expect(validateWorkbenchAppQuoteCreateRequest(body)).toBe(true)
    expect(body).toEqual({ ...SAVE, source: { quote_id: "q0", revision: 2, relation: "renewal" } })
  })

  it("carries a revision without the app's details for a quote made from it, and with them for the same quote", async () => {
    answering({ values: {}, dropped: [] })
    await carryAppQuote("q 1", 2)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/app/quotes/q%201/revisions/2/carry")

    fetchMock.mockClear()
    answering({ values: {}, dropped: [] })
    await carryAppQuote("q 1", 2, true)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/app/quotes/q%201/revisions/2/carry?details=true")
  })

  it("saves a quote's next revision over the version it names", async () => {
    answering(answer({ latest: 3 }))

    await saveAppQuote("q 1", { ...SAVE, shown: null }, 2)

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", "/api/app/quotes/q%201/revisions"])
    expect(validateWorkbenchAppQuoteReviseRequest(body)).toBe(true)
    expect(body).toEqual({ ...SAVE, shown: null, version: 2 })
  })

  it.each([
    ["assignee", { assignee: "jo@example.com" }, validateWorkbenchAppQuoteAssign, { version: 4, assignee: "jo@example.com" }],
    ["archive", {}, validateWorkbenchAppQuoteChange, { version: 4 }],
    ["restore", {}, validateWorkbenchAppQuoteChange, { version: 4 }],
    ["final", { revision: 4 }, validateWorkbenchAppQuoteFinal, { version: 4, revision: 4 }],
    ["reopen", { reason: "Broker asked" }, validateWorkbenchAppQuoteReopen, { version: 4, reason: "Broker asked" }],
    // A note is no revision, so it names none.
    ["note", { note: "Call the broker" }, validateWorkbenchAppQuoteNote, { note: "Call the broker" }],
  ] as const)("sends the %s change the body its route reads", async (change, fields, validate, expected) => {
    answering(changed({ latest: 5 }))

    await changeAppQuote("q1", change, 4, fields)

    const { method, url, body } = sent()
    expect([method, url]).toEqual(["POST", `/api/app/quotes/q1/${change}`])
    expect(validate(body)).toBe(true)
    expect(body).toEqual(expected)
  })
})
