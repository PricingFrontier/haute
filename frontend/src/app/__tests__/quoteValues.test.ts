/**
 * What the underwriter app works out of a quote's values (specs/workbench): one quote however
 * its blanks were left, the details the app sets as a quote holds them, a saved quote's
 * price, what the store's refusals name, the Quotes page's columns and values, what each
 * activity entry says, the values kept in the browser, a reference's rules, and a creation
 * kept in the tab until its answer comes.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../api/client"
import type { WorkbenchAppQuoteActivityEntry } from "../../api/types"
import { detailsTable, sheetForm } from "../../workbench/__tests__/fixtures"
import {
  activityText,
  appDetails,
  asRenewal,
  clearDraft,
  draftKey,
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
  refusalOf,
  sameQuote,
  savedPrice,
  valuesKey,
  withoutAppDetails,
  writeDraft,
  writeSuperseded,
  type KeptCreation,
} from "../quoteValues"

describe("values", () => {
  it("are the same quote however blank cells and trailing empty rows were left", () => {
    expect(normalisedValues({ policy: [{ a: "1", b: "" }, {}], empty: [{}] })).toEqual({ policy: [{ a: "1" }] })
    expect(valuesKey({ policy: [{ a: "1" }] })).toBe(valuesKey({ policy: [{ b: "", a: "1" }, {}] }))
    expect(valuesKey({ policy: [{ a: "1" }] })).not.toBe(valuesKey({ policy: [{ a: "2" }] }))
  })

  it("show a saved quote's price for the values as they are, and none unpriced", () => {
    const form = sheetForm()
    const values = { policy: [{ "policy.state": "NY" }] }
    const price = savedPrice(form, values, { premiums: [{ premium: 1 }] }, { policy: { state: "NY" } })
    expect(price).toMatchObject({ tables: { premiums: [{ premium: 1 }] }, sample: { policy: { state: "NY" } } })
    expect(price?.basis).toEqual(expect.any(String))
    expect(savedPrice(form, values, null, {})).toBeNull()
  })
})

describe("a quote's identity and the details the app sets", () => {
  it("is one quote by its reference, case included, and its option", () => {
    expect(sameQuote({ reference: "ABC-1", option: 2 }, { reference: "ABC-1", option: 2 })).toBe(true)
    expect(sameQuote({ reference: "ABC-1", option: 2 }, { reference: "abc-1", option: 2 })).toBe(false)
    expect(sameQuote({ reference: "ABC-1", option: 2 }, { reference: "ABC-1", option: 1 })).toBe(false)
  })

  it("puts the details the app sets on the sheets as the quote holds them: a number as its text, a tick, a date as text, no cell for false or a blank", () => {
    const form = { ...sheetForm(), schema: { tables: [detailsTable(), ...sheetForm().schema.tables] } }
    const details = {
      quote_reference: "ABC-1",
      quote_option: 2,
      quote_name: "Fleet",
      stage: "final",
      assignee: "jo@example.com",
      archived: false,
      created_date: "2026-10-12",
      created_by: "ana@example.com",
      modified_by: "",
      haute_version: null,
    }

    // The underwriter's own, quote_name, are not the app's.
    expect(appDetails(form, details)).toEqual({
      quote_details: [{ qd_reference: "ABC-1", qd_option: "2", qd_stage: "final", qd_assignee: "jo@example.com", qd_created_date: "2026-10-12", qd_created_by: "ana@example.com" }],
    })
    expect(appDetails(form, { archived: true }).quote_details).toEqual([{ qd_archived: true }])
    // Sheets without quote_details take none.
    expect(appDetails(sheetForm(), details)).toEqual({})
  })

  it("takes the details the app set for a quote out of values a quote is made from", () => {
    expect(withoutAppDetails({ policy: [{ a: "1" }], quote_details: [{ qd_reference: "ABC-1", qd_stage: "draft", qd_insured: "Acme" }] })).toEqual({
      policy: [{ a: "1" }],
      quote_details: [{ qd_insured: "Acme" }],
    })
    expect(withoutAppDetails({ quote_details: [{ qd_reference: "ABC-1" }] })).toEqual({})
  })
})

describe("the quote store's refusals", () => {
  const refused = (detail: Record<string, unknown>) => new ApiError("HTTP 422", 422, "refused", undefined, detail)

  it("name each value a save held that is not of its column's type, by the sheets' labels", () => {
    const cells = [
      { table: "policy", row: null, column: "Year" },
      { table: "vehicles", row: 3, column: "Value" },
    ]
    expect(refusalOf(refused({ error_code: "quote_values_invalid", message: "No", cells }))).toEqual({ of: "save", places: ["policy, Year", "vehicles row 3, Value"] })
  })

  it("name each required field a quote marked final has no value in, and nothing for a price of another release", () => {
    expect(refusalOf(refused({ error_code: "quote_not_ready", message: "No", missing: ["policy.Drivers", "vehicles row 3.Value"] }))).toEqual({
      of: "final",
      places: ["policy.Drivers", "vehicles row 3.Value"],
    })
    expect(refusalOf(refused({ error_code: "quote_not_ready", message: "Priced on release rel-0" }))).toBeNull()
    expect(refusalOf(refused({ error_code: "quote_locked", message: "Final" }))).toBeNull()
    expect(refusalOf(new Error("down"))).toBeNull()
  })

  it("know a repeated write superseded since it landed", () => {
    expect(writeSuperseded(new ApiError("HTTP 409", 409, "landed", undefined, { error_code: "write_superseded", message: "landed" }))).toBe(true)
    expect(writeSuperseded(new ApiError("HTTP 409", 409, "stale", undefined, { error_code: "stale_quote", message: "stale" }))).toBe(false)
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

describe("activityText", () => {
  const entry = (action: WorkbenchAppQuoteActivityEntry["action"], carried: Partial<WorkbenchAppQuoteActivityEntry> = {}): WorkbenchAppQuoteActivityEntry => ({
    action,
    actor: "ana",
    at: "2026-10-12T12:00:00Z",
    version: 2,
    assignee: null,
    note: null,
    reason: null,
    priced_release: null,
    ...carried,
  })

  it.each([
    [entry("created"), "made it"],
    [entry("saved", { priced_release: "rel-1" }), "saved it, priced"],
    [entry("saved"), "saved it, unpriced"],
    [entry("assigned", { assignee: "jo" }), "assigned it to jo"],
    [entry("noted", { note: "x", version: null }), "wrote the note"],
    [entry("noted", { note: "", version: null }), "cleared the note"],
    [entry("archived"), "archived it"],
    [entry("restored"), "restored it"],
    [entry("finalised"), "marked it final"],
    [entry("amended", { reason: "Broker asked" }), "amended it: Broker asked"],
  ])("says what was done", (happened, said) => {
    expect(activityText(happened)).toBe(said)
  })

  it("says what a quote was made from", () => {
    expect(activityText(entry("created"), { reference: "Q-000001", option: 1, label: "Q-000001/1", relation: "renewal" })).toBe("made it as a renewal of Q-000001/1")
  })
})

describe("drafts", () => {
  afterEach(() => {
    vi.restoreAllMocks()
    window.localStorage.clear()
  })

  const draft = { values: { policy: [{ a: "1" }] }, version: 2, places: {}, keptAt: "2026-10-12T12:00:00Z", pending: null }

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

  it("read a stored value that is not a draft as none, and a held write naming no quote's reference and option as none held", () => {
    window.localStorage.setItem("k", "\"text\"")
    expect(readDraft("k")).toBeNull()

    const write = { kind: "save", reference: "ABC-1", option: 1, version: 2, body: { operation: "save-0001", values: {} } }
    writeDraft("k", { ...draft, pending: { basis: "b", write } as never })
    expect(readDraft("k")?.pending).toEqual({ basis: "b", write })
    writeDraft("k", { ...draft, pending: { basis: "b", write: { ...write, reference: undefined, quoteId: "q1" } } as never })
    expect(readDraft("k")).toMatchObject({ values: draft.values, pending: null })
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
  it("takes a reference trimmed, of 1 to 40 characters, with no / and no control character, and more than . or ..", () => {
    expect(referenceProblem("  ABC-1 ")).toBeNull()
    expect(referenceProblem("abc-1")).toBeNull()
    expect(referenceProblem("Straße 12")).toBeNull()
    expect(referenceProblem("x".repeat(40))).toBeNull()
    expect(referenceProblem("   ")).toBe("Required")
    expect(referenceProblem("x".repeat(41))).toBe("At most 40 characters")
    // Counted as the server counts them, by character: a letter beyond the basic plane is one,
    // and an accent keyed apart from its letter is one with it, in Unicode's composed form.
    expect(referenceProblem("𝔸".repeat(40))).toBeNull()
    expect(referenceProblem("e\u0301".repeat(40))).toBeNull()
    expect(referenceProblem("e\u0301".repeat(41))).toBe("At most 40 characters")
    expect(referenceProblem("ABC/1")).toBe("No / in a reference")
    expect(referenceProblem("AB\tC")).toBe("No control characters")
    expect(referenceProblem("AB\u200bC")).toBe("No control characters")
    // An address reads these as folders.
    expect(referenceProblem(".")).toBe("Not . or ..")
    expect(referenceProblem(" .. ")).toBe("Not . or ..")
    expect(referenceProblem("...")).toBeNull()
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
