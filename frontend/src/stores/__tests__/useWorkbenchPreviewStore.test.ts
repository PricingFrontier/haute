/**
 * Preview's quote (specs/workbench): kept apart from the sample, its cells and rows set
 * without history, checked against the columns' rules when Price is pressed and priced
 * on the pipeline through the same pricing as the sample, with the basis it was priced
 * for and the reason when it has no price.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
}))

import type { CollectionWidget, FormSchema, FormSpec, SchemaColumn, SchemaTable, ShowWhen, WorkbenchTablesResponse } from "../../api/types"
import { fetchWorkbenchFormTables } from "../../api/workbench"
import { cellKey, quoteProblems, valuesBasis } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import type { SampleRow } from "../../utils/workbenchForm"
import type { PricedSample } from "../../utils/workbenchTables"
import { section, sheet } from "../../workbench/__tests__/fixtures"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type PricedValues } from "../useWorkbenchPricingStore"

const column = (id: string, name: string, overrides: Partial<SchemaColumn> = {}): SchemaColumn => ({
  id,
  name,
  type: "str",
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
  help: "",
  placeholder: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  ...overrides,
})
const policy: SchemaTable = {
  id: "t1",
  name: "policy_details",
  role: "input",
  rows: "one",
  columns: [column("c1", "exposure", { type: "float", required: true, min: 0 })],
}
const pricing: SchemaTable = { id: "t2", name: "pricing_output", role: "output", rows: "one", columns: [column("c2", "model_premium", { type: "float" })] }
const form: FormSpec = {
  version: 1, quote_list: { title: null, fields: [], headline: null },
  name: "t",
  schema: { tables: [policy, pricing] },
  pages: [sheet("p", "Sheet 1")],
  sample: { t1: [{ c1: "100000" }] },
}

/** A Collection of the policy's columns, always shown. */
const boxes = (id: string, columns: string[]): CollectionWidget => ({
  id, type: "collection", x: 0, y: 0, w: 400, h: 100, title: "", columns: 2, show_when: { match: "all", conditions: [] },
  fields: columns.map((columnId) => ({ table: "t1", column: columnId })),
})
/** The schema with a box, cover, and a name beside the policy's required exposure. */
const covered: FormSchema = { tables: [{ ...policy, columns: [...policy.columns, column("c3", "cover", { type: "bool" }), column("c4", "name")] }, pricing] }
/** Shown only while cover is ticked. */
const whileCovered: ShowWhen = { match: "all", conditions: [{ table: "t1", column: "c3", op: "ticked", values: [] }] }

const priced = (premium: number | null): PricedSample => ({ tables: { pricing_output: [{ model_premium: premium }] } })

/** What the server makes of a form: here, its sample's exposure as typed. */
const workbenchOf = (spec: FormSpec): WorkbenchTablesResponse => ({
  tables: [],
  sample: { policy_details: { exposure: Number(spec.sample.t1?.[0]?.c1) } },
  response_tables: [],
})

describe("useWorkbenchPreviewStore", () => {
  beforeEach(() => {
    vi.mocked(fetchWorkbenchFormTables).mockImplementation((spec) => Promise.resolve(workbenchOf(spec)))
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null, error: null, pricing: false, readOnly: false })
  })

  afterEach(() => {
    vi.clearAllMocks()
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
  })

  it("holds the quote apart from the sample: cells set, rows replaced, cleared, without history", () => {
    const store = useWorkbenchPreviewStore.getState

    store().setCell("t1", 0, "c1", "250000")
    store().setCell("t3", 2, "c9", "Crane")
    expect(store().quote).toEqual({ t1: [{ c1: "250000" }], t3: [{}, {}, { c9: "Crane" }] })
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "100000" }] })
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])

    store().setRows({ t3: [{ c9: "Paver" }], t4: [{}] })
    expect(store().quote).toEqual({ t1: [{ c1: "250000" }], t3: [{ c9: "Paver" }], t4: [{}] })

    useWorkbenchPreviewStore.setState({ checked: true, error: "x", price: { basis: "b", tables: {}, sample: {} } })
    store().clear()
    expect(store()).toMatchObject({ quote: {}, checked: false, error: null, price: null })
  })

  it("prices the quote on the pipeline as the sample is priced, keeping what the server typed", async () => {
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(5000)))
    useWorkbenchPricingStore.setState({ pricer })
    const store = useWorkbenchPreviewStore.getState
    store().setCell("t1", 0, "c1", "$250,000")

    await store().priceQuote()

    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith({ ...form, sample: { t1: [{ c1: "$250,000" }] } })
    expect(pricer).toHaveBeenCalledWith({ tables: [], sample: { policy_details: { exposure: NaN } }, response_tables: [] })
    expect(store()).toMatchObject({
      checked: true,
      error: null,
      pricing: false,
      price: { basis: valuesBasis(form.schema, new Set(), { t1: [{ c1: "$250,000" }] }), tables: { pricing_output: [{ model_premium: 5000 }] } },
    })
    // The basis moves on with the quote, so the price reads as out of date until Price again.
    store().setCell("t1", 0, "c1", "1")
    expect(store().price?.basis).not.toBe(valuesBasis(form.schema, new Set(), store().quote))
  })

  it("opens a saved quote with its price, read only or not, and holds a read-only quote as it is", async () => {
    const quotePricer = vi.fn()
    useWorkbenchPricingStore.setState({ quotePricer })
    const store = useWorkbenchPreviewStore.getState
    const price = { basis: "b", tables: { pricing_output: [{ model_premium: 7 }] }, sample: {} }
    useWorkbenchPreviewStore.setState({ checked: true, error: "x" })

    store().open({ t1: [{ c1: "5" }] }, price, true)
    expect(store()).toMatchObject({ quote: { t1: [{ c1: "5" }] }, price, checked: false, error: null, readOnly: true })
    store().setCell("t1", 0, "c1", "6")
    store().setRows({ t1: [] })
    await store().priceQuote()
    expect(store().quote).toEqual({ t1: [{ c1: "5" }] })
    expect(quotePricer).not.toHaveBeenCalled()

    store().setReadOnly(false)
    store().setCell("t1", 0, "c1", "6")
    expect(store().quote).toEqual({ t1: [{ c1: "6" }] })
  })

  it("prices through the host's quote pricer when one is set, the underwriter app's, without the pipeline's", async () => {
    let answer: (value: PricedValues) => void = () => {}
    const quotePricer = vi.fn((_form: FormSpec, _quote: Record<string, SampleRow[]>) =>
      new Promise<PricedValues>((resolve) => { answer = resolve }))
    useWorkbenchPricingStore.setState({ quotePricer })
    const store = useWorkbenchPreviewStore.getState
    store().setCell("t1", 0, "c1", "250000")

    const pricing = store().priceQuote()
    answer({ tables: { pricing_output: [{ model_premium: 7 }] }, sample: { policy_details: { exposure: 250000 } } })
    await pricing

    expect(quotePricer).toHaveBeenCalledWith(form, { t1: [{ c1: "250000" }] })
    expect(fetchWorkbenchFormTables).not.toHaveBeenCalled()
    expect(store().price).toMatchObject({
      tables: { pricing_output: [{ model_premium: 7 }] },
      sample: { policy_details: { exposure: 250000 } },
    })

    // The host dropped its quote pricer meanwhile: the answer is nobody's.
    useWorkbenchPreviewStore.setState({ price: null })
    const late = store().priceQuote()
    useWorkbenchPricingStore.setState({ quotePricer: null })
    answer({ tables: { pricing_output: [{ model_premium: 8 }] }, sample: {} })
    await late
    expect(store().price).toBeNull()
  })

  it("marks the cells that break their column's rules instead of pricing, and says how many", async () => {
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(5000)))
    useWorkbenchPricingStore.setState({ pricer })
    const store = useWorkbenchPreviewStore.getState
    store().setCell("t1", 0, "c1", "-5")

    await store().priceQuote()

    expect(pricer).not.toHaveBeenCalled()
    expect(store()).toMatchObject({ checked: true, error: "1 cell needs attention", price: null })
    // Nothing typed in the tables the schema has now, a value in a column since removed
    // included: not priced, as a deployed request never holds a blank quote.
    const blanks: Record<string, Record<string, string>[]>[] = [
      {},
      { t1: [{ gone: "x" }] },
      { gone: [{ c1: "y" }] },
      // A second row of a table with one row per quote: the server reads the first alone.
      { t1: [{}, { c1: "100" }] },
    ]
    for (const quote of blanks) {
      useWorkbenchPreviewStore.setState({ quote, error: null, checked: false })
      await store().priceQuote()
      expect(pricer).not.toHaveBeenCalled()
      expect(store()).toMatchObject({ checked: true, error: "Type the quote first", price: null })
    }
    expect(quoteProblems(form.schema, {})).toEqual({ [cellKey("t1", 0, "c1")]: "Required" })
  })

  it("reads the quote without the cells the sheets hide: a hidden required cell faults nothing, and hidden values alone are blank", async () => {
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(5000)))
    useWorkbenchPricingStore.setState({ pricer })
    // The required exposure is on a sheet shown only while the box, cover, is ticked.
    useWorkbenchFormStore.setState({
      form: {
        ...form,
        schema: covered,
        pages: [sheet("p", "Main", [boxes("w_main", ["c3", "c4"])]), sheet("p2", "Exposure", [boxes("w_exposure", ["c1"])], whileCovered)],
      },
    })
    const store = useWorkbenchPreviewStore.getState

    store().setCell("t1", 0, "c1", "-5")
    await store().priceQuote()
    expect(store()).toMatchObject({ checked: true, error: "Type the quote first" })

    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c4: "Acme" }] }, error: null, checked: false })
    await store().priceQuote()
    expect(pricer).toHaveBeenCalledTimes(1)
    expect(store()).toMatchObject({ error: null, price: { tables: { pricing_output: [{ model_premium: 5000 }] } } })

    store().setCell("t1", 0, "c3", true)
    await store().priceQuote()
    expect(pricer).toHaveBeenCalledTimes(1)
    expect(store()).toMatchObject({ error: "1 cell needs attention" })
  })

  it("leaves a value in a section the quote hides out of what is priced, and checks it nowhere", async () => {
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(5000)))
    useWorkbenchPricingStore.setState({ pricer })
    // One sheet, whose second section holds the required exposure and shows only while the
    // box, cover, in its first is ticked; the exposure's own Collection has no conditions.
    const sectioned: FormSpec = {
      ...form,
      schema: covered,
      pages: [
        {
          ...sheet("p", "Main"),
          sections: [section("s_main", [boxes("w_main", ["c3", "c4"])]), section("s_exposure", [boxes("w_exposure", ["c1"])], { show_when: whileCovered })],
        },
      ],
    }
    useWorkbenchFormStore.setState({ form: sectioned })
    const store = useWorkbenchPreviewStore.getState

    // The exposure alone, below its minimum: the quote holds nothing typed.
    store().setCell("t1", 0, "c1", "-5")
    await store().priceQuote()
    expect(pricer).not.toHaveBeenCalled()
    expect(store()).toMatchObject({ checked: true, error: "Type the quote first", price: null })

    // A name typed beside it: priced, the exposure neither checked nor counted. The quote
    // goes to the server as typed, which leaves the exposure out where it makes the quote,
    // and the price is for the schema without it.
    store().setCell("t1", 0, "c4", "Acme")
    await store().priceQuote()
    const { quote } = store()
    expect(quote).toEqual({ t1: [{ c1: "-5", c4: "Acme" }] })
    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith({ ...sectioned, sample: quote })
    expect(pricer).toHaveBeenCalledTimes(1)
    expect(store()).toMatchObject({ error: null, price: { tables: { pricing_output: [{ model_premium: 5000 }] } } })
    const hidden = new Set([columnKey("t1", "c1")])
    expect(store().price?.basis).toBe(valuesBasis(covered, hidden, quote))
    expect(store().price?.basis).not.toBe(valuesBasis(covered, new Set(), quote))
    // The hidden column is still declared to the pipeline: renamed, the price is out of date.
    const renamed = { tables: covered.tables.map((table) => ({ ...table, columns: table.columns.map((c) => (c.id === "c1" ? { ...c, name: "exposure_si" } : c)) })) }
    expect(store().price?.basis).not.toBe(valuesBasis(renamed, hidden, quote))

    // Cover ticked: the section shows, and its exposure is read and checked again.
    store().setCell("t1", 0, "c3", true)
    await store().priceQuote()
    expect(pricer).toHaveBeenCalledTimes(1)
    expect(store()).toMatchObject({ error: "1 cell needs attention" })
  })

  it("drops an answer that arrives after Clear, or after the view replaced its pricer", async () => {
    const store = useWorkbenchPreviewStore.getState
    let answer: (value: PricedSample) => void = () => {}
    let refuse: (reason: Error) => void = () => {}
    const pricer = () =>
      new Promise<PricedSample>((resolve, reject) => {
        answer = resolve
        refuse = reject
      })
    useWorkbenchPricingStore.setState({ pricer })
    store().setCell("t1", 0, "c1", "100")

    const first = store().priceQuote()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    store().clear()
    expect(store().pricing).toBe(false)
    answer(priced(1))
    await first
    expect(store()).toMatchObject({ price: null, error: null, pricing: false })

    store().setCell("t1", 0, "c1", "200")
    const second = store().priceQuote()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    store().clear()
    refuse(new Error("late"))
    await second
    expect(store()).toMatchObject({ price: null, error: null })

    store().setCell("t1", 0, "c1", "300")
    const third = store().priceQuote()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    useWorkbenchPricingStore.setState({ pricer: null })
    answer(priced(3))
    await third
    expect(store()).toMatchObject({ price: null, pricing: false })
  })

  it("says why pricing failed, and prices nothing without a pricer, before the form is read, or while a pricing runs", async () => {
    const store = useWorkbenchPreviewStore.getState
    store().setCell("t1", 0, "c1", "100")
    await store().priceQuote()
    expect(fetchWorkbenchFormTables).not.toHaveBeenCalled()

    useWorkbenchPricingStore.setState({ pricer: () => Promise.reject(new Error("Connect a frame to the Workbench Output's 'pricing_output' table.")) })
    await store().priceQuote()
    expect(store()).toMatchObject({ price: null, error: "Pricing failed: Connect a frame to the Workbench Output's 'pricing_output' table." })

    let answer: (value: PricedSample) => void = () => {}
    useWorkbenchPricingStore.setState({ pricer: () => new Promise<PricedSample>((resolve) => { answer = resolve }) })
    const running = store().priceQuote()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    await store().priceQuote()
    expect(fetchWorkbenchFormTables).toHaveBeenCalledTimes(2)
    answer(priced(1))
    await running
    expect(store()).toMatchObject({ pricing: false, error: null })

    useWorkbenchFormStore.setState({ status: "loading" })
    await store().priceQuote()
    expect(fetchWorkbenchFormTables).toHaveBeenCalledTimes(2)
  })
})
