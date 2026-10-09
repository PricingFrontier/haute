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

import type { FormSpec, SchemaColumn, SchemaTable, WorkbenchTablesResponse } from "../../api/types"
import { fetchWorkbenchFormTables } from "../../api/workbench"
import { cellKey, valuesBasis } from "../../utils/sheetValues"
import type { PricedSample } from "../../utils/workbenchTables"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchPricingStore from "../useWorkbenchPricingStore"

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
  version: 1,
  name: "t",
  schema: { tables: [policy, pricing] },
  pages: [{ id: "p", title: "Sheet 1", widgets: [] }],
  sample: { t1: [{ c1: "100000" }] },
}

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
    useWorkbenchPricingStore.setState({ pricer: null })
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null, error: null, pricing: false })
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
      price: { basis: valuesBasis(form.schema, { t1: [{ c1: "$250,000" }] }), tables: { pricing_output: [{ model_premium: 5000 }] } },
    })
    // The basis moves on with the quote, so the price reads as out of date until Price again.
    store().setCell("t1", 0, "c1", "1")
    expect(store().price?.basis).not.toBe(valuesBasis(form.schema, store().quote))
  })

  it("marks the cells that break their column's rules instead of pricing, and says how many", async () => {
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(5000)))
    useWorkbenchPricingStore.setState({ pricer })
    const store = useWorkbenchPreviewStore.getState
    store().setCell("t1", 0, "c1", "-5")

    await store().priceQuote()

    expect(pricer).not.toHaveBeenCalled()
    expect(store()).toMatchObject({ checked: true, error: "1 cell needs attention", price: null })
    useWorkbenchPreviewStore.setState({ quote: {} })
    await store().priceQuote()
    expect(store().error).toBe("1 cell needs attention")
    expect(Object.keys({ [cellKey("t1", 0, "c1")]: "Required" })).toHaveLength(1)
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
