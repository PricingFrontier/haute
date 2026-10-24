/**
 * What the underwriter app works out of a quote's values (specs/workbench): one quote however
 * its blanks were left, a saved revision's price, the Quotes page's columns and values, how
 * long ago, what an event says, the values kept in the browser, a reference's rules, and a
 * creation kept in the tab until its answer comes.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import type { WorkbenchAppQuoteEvent } from "../../api/types"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import {
  asRenewal,
  clearDraft,
  draftKey,
  eventText,
  forgetCreation,
  keepCreation,
  keptCreationKey,
  listColumns,
  listValue,
  normalisedValues,
  operationKey,
  readDraft,
  readKeptCreation,
  referenceProblem,
  relativeTime,
  savedPrice,
  valuesKey,
  writeDraft,
  type KeptCreation,
} from "../quoteValues"

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

describe("the Quotes page's columns", () => {
  it("are insured, broker and inception, as the sheets label them, else by their names", () => {
    const sheets = sheetForm()
    const form = { ...sheets, schema: { tables: [detailsTable({ insured: "Client" }), ...sheets.schema.tables] } }
    const columns = listColumns(form)
    expect(columns.map((column) => [column.key, column.label])).toEqual([
      ["insured", "Client"],
      ["broker", "Broker"],
      ["inception_date", "Inception date"],
    ])
    expect(columns[2].column?.type).toBe("date")
    expect(listColumns(null).map((column) => [column.label, column.column])).toEqual([
      ["Insured", null],
      ["Broker", null],
      ["Inception date", null],
    ])
  })

  it("shows a date as a day, not as it is kept", () => {
    const [, , inception] = listColumns({ ...sheetForm(), schema: { tables: [detailsTable()] } })
    expect(listValue(inception, "2026-11-01")).toBe(new Date(2026, 10, 1).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))
    expect(listValue(inception, "soon")).toBe("soon")
  })

  it("shows a value as its column formats it, a dash when blank and Yes or No for a tick", () => {
    const column = { key: "k", label: "Premium", column: { ...sheetForm().schema.tables[0].columns[0], type: "float" as const, number_format: "currency_gbp" as const, decimal_places: 0 } }
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
    [event("created", { source: { reference: "Q-000001", option: 1, revision: 2, relation: "renewal" } }), "made it as a renewal of Q-000001/1 revision 2"],
    // An event written before options names none: every quote was option 1.
    [event("created", { source: { reference: "Q-000001", revision: 2, relation: "duplicate" } }), "made it as a duplicate of Q-000001/1 revision 2"],
    [event("saved", { priced: true }), "saved revision 2, priced"],
    [event("saved", { priced: false }), "saved revision 2, unpriced"],
    [event("assigned", { to: "jo" }), "assigned it to jo"],
    [event("noted", { note: "x" }), "wrote the note"],
    [event("noted", { note: "" }), "cleared the note"],
    [event("archived"), "archived it"],
    [event("restored"), "restored it"],
    [event("finalised"), "marked revision 2 final"],
    [event("reopened", { reason: "Broker asked", final_revision: 2 }), "reopened final revision 2: Broker asked"],
    [event("derived", { reference: "Q-000001", option: 3, relation: "alternative" }), "made Q-000001/3 from revision 2, as an alternative"],
  ])("says what was done", (happened, said) => {
    expect(eventText(happened)).toBe(said)
  })
})

describe("drafts", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  const draft = { values: { policy: [{ a: "1" }] }, version: 2, latest: 1, places: {}, keptAt: "2026-10-12T12:00:00Z", pending: null }

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

describe("referenceProblem", () => {
  it("takes a reference trimmed, of 1 to 40 characters, with no / and no control character", () => {
    expect(referenceProblem("  ABC-1 ")).toBeNull()
    expect(referenceProblem("Straße 12")).toBeNull()
    expect(referenceProblem("x".repeat(40))).toBeNull()
    expect(referenceProblem("   ")).toBe("Required")
    expect(referenceProblem("x".repeat(41))).toBe("At most 40 characters")
    // Counted as the server counts them, by character: a letter beyond the basic plane is one.
    expect(referenceProblem("𝔸".repeat(40))).toBeNull()
    expect(referenceProblem("ABC/1")).toBe("No / in a reference")
    expect(referenceProblem("AB\tC")).toBe("No control characters")
    expect(referenceProblem("AB\u200bC")).toBe("No control characters")
  })
})

describe("a kept creation", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.sessionStorage.clear()
  })

  const creation: KeptCreation = {
    operation: "save-0001",
    form_revision: "rev-9",
    release: "rel-1",
    reference: "ABC-1",
    values: { quote_details: [{ qd_insured: "Acme" }] },
    source: null,
    title: "New quote",
  }

  it("is kept in the tab per app and person, read back whole and let go", () => {
    const key = keptCreationKey("motor", "ana")
    expect(key).not.toBe(keptCreationKey("motor", "jo"))
    expect(keepCreation(key, creation)).toBe(true)
    expect(readKeptCreation(key)).toEqual(creation)
    forgetCreation(key)
    expect(readKeptCreation(key)).toBeNull()
  })

  it("is none when the tab keeps nothing, or holds something that is not one", () => {
    window.sessionStorage.setItem("k", JSON.stringify({ operation: "x" }))
    expect(readKeptCreation("k")).toBeNull()
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota")
    })
    expect(keepCreation("k2", creation)).toBe(false)
  })
})

describe("asRenewal", () => {
  it("ticks is_renewal in the details carried, the rest as they were", () => {
    expect(asRenewal({ policy: [{ a: "1" }], quote_details: [{ qd_insured: "Acme" }] })).toEqual({
      policy: [{ a: "1" }],
      quote_details: [{ qd_insured: "Acme", qd_is_renewal: true }],
    })
    expect(asRenewal({})).toEqual({ quote_details: [{ qd_is_renewal: true }] })
  })
})
