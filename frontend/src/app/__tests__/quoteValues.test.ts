/**
 * What the underwriter app works out of a quote's values (specs/workbench): one quote however
 * its blanks were left, a saved revision's price, the quote list's columns and values, how
 * long ago, what an event says, and the values kept in the browser.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import type { FormSpec, WorkbenchAppQuoteEvent } from "../../api/types"
import { sheetForm } from "../../workbench/__tests__/fixtures"
import {
  clearDraft,
  draftKey,
  eventText,
  listColumns,
  listValue,
  normalisedValues,
  operationKey,
  readDraft,
  relativeTime,
  savedPrice,
  valuesKey,
  writeDraft,
} from "../quoteValues"

const withList = (quoteList: FormSpec["quote_list"]): FormSpec => ({ ...sheetForm(), quote_list: quoteList })

describe("values", () => {
  it("are the same quote however blank cells and trailing empty rows were left", () => {
    expect(normalisedValues({ policy: [{ a: "1", b: "" }, {}], empty: [{}] })).toEqual({ policy: [{ a: "1" }] })
    expect(valuesKey({ policy: [{ a: "1" }] })).toBe(valuesKey({ policy: [{ b: "", a: "1" }, {}] }))
    expect(valuesKey({ policy: [{ a: "1" }] })).not.toBe(valuesKey({ policy: [{ a: "2" }] }))
  })

  it("show a saved revision's price for the values as they are, and none unpriced", () => {
    const form = sheetForm()
    const values = { policy: [{ "policy.state": "NY" }] }
    const price = savedPrice(form, values, { premiums: [{ premium: 1 }] }, { policy: { state: "NY" } })
    expect(price).toMatchObject({ tables: { premiums: [{ premium: 1 }] }, sample: { policy: { state: "NY" } } })
    expect(price?.basis).toEqual(expect.any(String))
    expect(savedPrice(form, values, null, {})).toBeNull()
  })
})

describe("the quote list", () => {
  it("names its columns as the sheets label them, a missing one a dash", () => {
    const form = withList({
      title: { table: "policy", column: "policy.state" },
      fields: [{ table: "policy", column: "policy.year" }, { table: "policy", column: "gone" }],
      headline: null,
    })
    const columns = listColumns(form)
    expect(columns.title).toBe("State")
    expect(columns.fields.map((column) => column.label)).toEqual(["Year", "—"])
    expect(columns.fields.map((column) => column.numeric)).toEqual([true, false])
    expect(columns.headline).toBeNull()
    expect(listColumns(null)).toEqual({ fields: [], headline: null, title: "Title" })
  })

  it("shows a value as its column formats it, a dash when blank and Yes or No for a tick", () => {
    const column = { key: "k", label: "Premium", numeric: true, column: { ...sheetForm().schema.tables[0].columns[0], type: "float" as const, number_format: "currency_gbp" as const, decimal_places: 0 } }
    expect(listValue(column, 1234.4)).toBe("£1,234")
    expect(listValue(column, null)).toBe("—")
    expect(listValue({ ...column, column: null }, true)).toBe("Yes")
    expect(listValue({ ...column, column: null }, "Acme")).toBe("Acme")
  })
})

describe("relativeTime", () => {
  const now = new Date("2026-10-12T12:00:00Z")
  it.each([
    ["2026-10-12T11:59:40Z", "just now"],
    ["2026-10-12T11:50:00Z", "10m ago"],
    ["2026-10-12T09:00:00Z", "3h ago"],
    ["2026-10-11T10:00:00Z", "yesterday"],
    ["2026-10-08T12:00:00Z", "4d ago"],
  ])("says %s as %s", (iso, said) => {
    expect(relativeTime(iso, now)).toBe(said)
  })

  it("says a date for anything older", () => {
    expect(relativeTime("2026-09-01T12:00:00Z", now)).toMatch(/1|Sept|Sep/)
  })
})

describe("eventText", () => {
  const event = (kind: WorkbenchAppQuoteEvent["kind"], detail: Record<string, unknown> = {}, revision: number | null = 2): WorkbenchAppQuoteEvent => ({
    id: 1,
    at: "2026-10-12T12:00:00Z",
    actor: "ana",
    kind,
    revision,
    detail,
  })

  it.each([
    [event("created"), "made it"],
    [event("created", { source: { reference: "Q-000001", revision: 2, relation: "renewal" } }), "made it as a renewal of Q-000001 revision 2"],
    [event("saved", { priced: true }), "saved revision 2, priced"],
    [event("saved", { priced: false }), "saved revision 2, unpriced"],
    [event("renamed", { to: "Fleet" }), "named it “Fleet”"],
    [event("renamed", { to: "" }), "cleared its name"],
    [event("assigned", { to: "jo" }), "assigned it to jo"],
    [event("noted", { note: "x" }), "wrote the note"],
    [event("noted", { note: "" }), "cleared the note"],
    [event("archived"), "archived it"],
    [event("restored"), "restored it"],
    [event("finalised"), "marked revision 2 final"],
    [event("reopened", { reason: "Broker asked" }), "reopened revision 2: Broker asked"],
    [event("derived", { reference: "Q-000003", relation: "alternative" }), "made Q-000003 from revision 2, as an alternative"],
  ])("says what was done", (happened, said) => {
    expect(eventText(happened)).toBe(said)
  })
})

describe("drafts", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  const draft = { values: { policy: [{ a: "1" }] }, version: 2, latest: 1, formRevision: "rev", keptAt: "2026-10-12T12:00:00Z" }

  it("are kept per app, person and quote, read back and cleared", () => {
    const key = draftKey("motor", "ana", "q1")
    expect(key).not.toBe(draftKey("motor", "jo", "q1"))
    expect(writeDraft(key, draft)).toBe(true)
    expect(readDraft(key)).toEqual(draft)
    clearDraft(key)
    expect(readDraft(key)).toBeNull()
  })

  it("are none when the browser keeps none, and say so when one cannot be kept", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota")
    })
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked")
    })
    expect(writeDraft("k", draft)).toBe(false)
    expect(readDraft("k")).toBeNull()
    window.localStorage.setItem = Storage.prototype.setItem
  })

  it("read a stored value that is not a draft as none", () => {
    window.localStorage.setItem("k", "\"text\"")
    expect(readDraft("k")).toBeNull()
  })
})

describe("operationKey", () => {
  it("is a new key each time, as long as the server takes", () => {
    const one = operationKey()
    expect(one).not.toBe(operationKey())
    expect(one.length).toBeGreaterThanOrEqual(8)
    expect(one.length).toBeLessThanOrEqual(64)
    expect(one).toMatch(/^[A-Za-z0-9_-]+$/)
  })
})
