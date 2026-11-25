/**
 * What the underwriter app works out of a quote's values (specs/workbench): one quote however
 * its blanks were left, the details the app sets as a quote holds them, a saved quote's
 * price, what the store's refusals name, the Quotes page's columns and values, what each
 * activity entry says, the values kept in the browser, a reference's rules, and a creation
 * kept in the tab until its answer comes.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { ApiError } from "../../api/client"
import type { FormSpec, SchemaColumn, ShowWhen, WorkbenchAppQuoteActivityEntry } from "../../api/types"
import { TABLE_OPTIONS, type Widget } from "../../utils/workbenchForm"
import { addTo, column, coverForm, detailsTable, fieldsWidget, indexColumn, sheetForm, table } from "../../workbench/__tests__/fixtures"
import {
  activityText,
  appDetails,
  appErrorMessage,
  asRenewal,
  clearDraft,
  draftKey,
  forgetCreation,
  KEPT_KEY,
  keepCreation,
  keptCreationKey,
  listColumns,
  listValue,
  normalisedValues,
  onSheets,
  operationKey,
  readDraft,
  readKeptCreation,
  referenceProblem,
  refusalOf,
  sameQuote,
  savedPrice,
  storeRefused,
  valuesKey,
  withHiddenCells,
  withoutAppDetails,
  writeDraft,
  writeSuperseded,
  type KeptCreation,
  type Values,
} from "../quoteValues"
import { MISFIT, storeRefusal, UNREACHED } from "./fixtures"

describe("values", () => {
  it("are the same quote however blank cells and trailing empty rows were left", () => {
    expect(normalisedValues({ policy: [{ a: "1", b: "" }, {}], empty: [{}] })).toEqual({ policy: [{ a: "1" }] })
    expect(valuesKey({ policy: [{ a: "1" }] })).toBe(valuesKey({ policy: [{ b: "", a: "1" }, {}] }))
    expect(valuesKey({ policy: [{ a: "1" }] })).not.toBe(valuesKey({ policy: [{ a: "2" }] }))
  })

  it("put back on the sheets keep each row's values in store columns the form dropped", () => {
    const kept = JSON.stringify({ mileage: 100 })
    const values: Values = { equipment: [{ item: "Crane", gone: "x", [KEPT_KEY]: kept }, { item: "Van" }] }
    expect(onSheets(sheetForm(), values)).toEqual({ equipment: [{ item: "Crane", [KEPT_KEY]: kept }, { item: "Van" }] })
  })

  it("show a saved quote's price for the values as they are, and none unpriced", () => {
    const form = sheetForm()
    const values = { policy: [{ state: "NY" }] }
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
      quote_details: [{ quote_reference: "ABC-1", quote_option: "2", stage: "final", assignee: "jo@example.com", created_date: "2026-10-12", created_by: "ana@example.com" }],
    })
    expect(appDetails(form, { archived: true }).quote_details).toEqual([{ archived: true }])
    // Sheets without quote_details take none.
    expect(appDetails(sheetForm(), details)).toEqual({})
  })

  it("takes the details the app set for a quote out of values a quote is made from", () => {
    expect(withoutAppDetails({ policy: [{ a: "1" }], quote_details: [{ quote_reference: "ABC-1", stage: "draft", insured: "Acme" }] })).toEqual({
      policy: [{ a: "1" }],
      quote_details: [{ insured: "Acme" }],
    })
    expect(withoutAppDetails({ quote_details: [{ quote_reference: "ABC-1" }] })).toEqual({})
  })

  it("keep what was typed into the cells a save hid as the server wrote it, with the details the app set on it", () => {
    // The Cover sheet's year shows on a quote Ana saved last.
    const form = coverForm()
    form.schema.tables.unshift(detailsTable())
    fieldsWidget(form, "w_boxes").fields.push({ table: "quote_details", column: "modified_by" })
    form.pages[1].show_when = {
      match: "all",
      conditions: [{ table: "quote_details", column: "modified_by", op: "equals", values: ["ana@example.com"], field: null, days: 0 }],
    }
    const sent = { quote_details: [{ modified_by: "ana@example.com" }], policy: [{ state: "NY", year: "1990" }] }
    // Jo's save hid the year, which the store keeps nothing of.
    const answered = { quote_details: [{ modified_by: "jo@example.com" }], policy: [{ state: "NY" }] }

    expect(withHiddenCells(form, answered, sent)).toEqual({ ...answered, policy: [{ state: "NY", year: "1990" }] })
  })

  // A table keyed by an index the underwriter types, whose limits show only while the policy's box is ticked.
  const keyedForm = (type: SchemaColumn["type"]): FormSpec => {
    const form = sheetForm()
    form.schema.tables = [
      table("policy", { inputs: [column("show_limits", { type: "bool" })] }),
      table("equipment", { rows: "many", inputs: [indexColumn("item", "typed"), column("units", { type: "int" }), column("limit", { type: "int" })] }),
    ]
    form.pages[0].sections[0].widgets = []
    const grid = (id: string, fields: string[], showWhen: ShowWhen): Widget => ({
      name: id,
      id,
      type: "tableInput",
      x: 16,
      y: 16,
      w: 400,
      h: 120,
      title: "",
      rows: 3,
      ...TABLE_OPTIONS,
      column_looks: [],
      fields: fields.map((name) => ({ table: "equipment", column: name })),
      show_when: showWhen,
    })
    addTo(form, grid("w_items", ["item", "units"], { match: "all", conditions: [] }))
    addTo(form, grid("w_limits", ["limit"], { match: "all", conditions: [{ table: "policy", column: "show_limits", op: "ticked", values: [], field: null, days: 0 }] }))
    form.schema.tables[1].inputs[0].type = type
    return form
  }

  it.each([
    ["Text", "str" as const, ["B", "A"], ["A", "B"]],
    ["Integer", "int" as const, ["20", "03"], ["3", "20"]],
  ])("keep each hidden cell on the row whose %s index it was typed on, in whatever order the answer gives the rows", (_kind, type, typedKeys, answeredKeys) => {
    const form = keyedForm(type)
    // The second key's row typed first, its limit 200; the box unticked as the quote is saved.
    const sent = { equipment: [{ item: typedKeys[0], units: "1", limit: "200" }, { item: typedKeys[1], units: "1", limit: "100" }] }
    // The store keeps nothing of a hidden cell, and answers the rows in their index's order.
    const answered = { equipment: [{ item: answeredKeys[0], units: "1" }, { item: answeredKeys[1], units: "1" }] }

    expect(withHiddenCells(form, answered, sent)).toEqual({
      equipment: [
        { item: answeredKeys[0], units: "1", limit: "100" },
        { item: answeredKeys[1], units: "1", limit: "200" },
      ],
    })
  })

  it("keep a hidden cell typed on a row the answer does not hold, after the rows it does", () => {
    const form = keyedForm("str")
    const sent: Values = { equipment: [{ item: "B", units: "1", limit: "200" }, { limit: "50" }] }
    const answered = { equipment: [{ item: "B", units: "1" }] }

    expect(withHiddenCells(form, answered, sent)).toEqual({ equipment: [{ item: "B", units: "1", limit: "200" }, { limit: "50" }] })
  })

  it("keep a hidden cell of a numbered table on its own row past those answered, blank rows before it", () => {
    const form = keyedForm("str")
    form.schema.tables[1].inputs[0] = indexColumn("row_number", "numbered")
    form.pages[0].sections[0].widgets.forEach((widget) => {
      if (widget.type === "tableInput") widget.fields = widget.fields.filter((field) => field.column !== "item")
    })
    // Row 3 holds its hidden limit alone, so it is not sent; row 2 is blank.
    const sent: Values = { equipment: [{ units: "1", limit: "200" }, {}, { limit: "300" }] }
    const answered = { equipment: [{ units: "1" }] }

    expect(withHiddenCells(form, answered, sent)).toEqual({ equipment: [{ units: "1", limit: "200" }, {}, { limit: "300" }] })
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

  it("name each check a quote marked final fails, and nothing for a price of another release", () => {
    expect(refusalOf(refused({ error_code: "quote_not_ready", message: "No", failing: ["policy.Drivers needs a value", "vehicles row 3.Value must be at least 1"] }))).toEqual({
      of: "final",
      places: ["policy.Drivers needs a value", "vehicles row 3.Value must be at least 1"],
    })
    expect(refusalOf(refused({ error_code: "quote_not_ready", message: "Priced on release rel-0" }))).toBeNull()
    expect(refusalOf(refused({ error_code: "quote_locked", message: "Final" }))).toBeNull()
    expect(refusalOf(new Error("down"))).toBeNull()
  })

  it("say tables that do not fit by what is wrong, each once before everything it is wrong of, each column by its table and name", () => {
    const misfits = [
      { table: "policy", column: "state", problem: "is missing" },
      { table: "policy", column: "year", problem: "holds string, not integer" },
      // One column wrong twice is named under each.
      { table: "policy", column: "year", problem: "takes a null, unlike the store's" },
      { table: "equipment", column: null, problem: "is missing" },
      { table: "drivers", column: null, problem: "is missing" },
      // A column of the same name in another table is told from it by its table.
      { table: "haute_activity", column: "state", problem: "is missing" },
      { table: "haute_activity", column: "at", problem: "takes a null, unlike the store's" },
      { table: "policy", column: null, problem: "needs SELECT granted to the identity the app signs in as" },
      { table: "quote_details", column: null, problem: "needs SELECT granted to the identity the app signs in as" },
    ]
    const refused = storeRefusal({ ...MISFIT, misfits })

    expect(appErrorMessage(refused)).toBe(
      [
        "The dev database doesn't match the form:",
        "  Missing fields: policy.state, haute_activity.state",
        "  Holds string, not integer: policy.year",
        "  Takes a null, unlike the store's: policy.year, haute_activity.at",
        "  Missing tables: equipment, drivers",
        "  Needs SELECT granted to the identity the app signs in as: policy, quote_details",
      ].join("\n"),
    )
    expect(appErrorMessage(refused, { oneLine: true })).toBe(
      "The dev database doesn't match the form: Missing fields: policy.state, haute_activity.state; Holds string, not integer: policy.year; Takes a null, unlike the store's: policy.year, haute_activity.at; Missing tables: equipment, drivers; Needs SELECT granted to the identity the app signs in as: policy, quote_details.",
    )
    // No environment named, the database is the store's.
    expect(appErrorMessage(storeRefusal({ ...MISFIT, environment: null }))).toBe("The quote store's database doesn't match the form:\n  Missing fields: policy.state")
  })

  it("say any other refusal of the store as its message, and know the store's from a busy pricing's", () => {
    const away = storeRefusal(UNREACHED)
    expect(appErrorMessage(away)).toBe(UNREACHED.message)
    expect(storeRefused(away)).toBe(true)
    expect(storeRefused(storeRefusal(MISFIT))).toBe(true)
    const busy = new ApiError("HTTP 503", 503, "The app is busy pricing other quotes: try again in a few seconds.", undefined, { error_code: "pricing_busy", message: "The app is busy pricing other quotes: try again in a few seconds." })
    expect(storeRefused(busy)).toBe(false)
    expect(appErrorMessage(busy)).toBe("The app is busy pricing other quotes: try again in a few seconds.")
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
    const column = { key: "k", label: "Premium", column: { ...sheetForm().schema.tables[0].inputs[0], type: "float" as const, number_format: "currency_gbp" as const, decimal_places: 0 } }
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
    [entry("saved", { priced_release: "rel-1" }), "saved it"],
    [entry("saved"), "saved it"],
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
    values: { quote_details: [{ insured: "Acme" }] },
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
    expect(asRenewal({ policy: [{ a: "1" }], quote_details: [{ insured: "Acme" }] })).toEqual({
      policy: [{ a: "1" }],
      quote_details: [{ insured: "Acme", is_renewal: true }],
    })
    expect(asRenewal({})).toEqual({ quote_details: [{ is_renewal: true }] })
  })
})
