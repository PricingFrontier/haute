/**
 * The sample priced live in Preview (specs/workbench): priced as the form stands, once
 * typing pauses, one pricing at a time with one more for changes made meanwhile, the last
 * values kept and dimmed until the next answer, the reason when pricing fails, an answer
 * held to the rows it priced as the server holds a quote's, and a sample with nothing typed
 * in the cells the sheets show not priced at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
}))

import type { FormSpec, SchemaColumn, SchemaTable, WorkbenchTable, WorkbenchTablesResponse } from "../../api/types"
import { fetchWorkbenchFormTables } from "../../api/workbench"
import { BLANK_THEME, COLLECTION_OPTIONS, withSampleCell } from "../../utils/workbenchForm"
import type { PricedSample } from "../../utils/workbenchTables"
import { hiddenPerilsForm } from "../../workbench/__tests__/fixtures"
import useDataEpochStore from "../useDataEpochStore"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGraphStore from "../useGraphStore"
import useSettingsStore from "../useSettingsStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPricingStore, { PRICING_DELAY_MS, previewBasis } from "../useWorkbenchPricingStore"

const column = (name: string, type: SchemaColumn["type"]): SchemaColumn => ({
  name,
  type,
  label: "",
  key: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
  number_format: "general",
  decimal_places: null,
  use_grouping: true,
  required: false,
  min: null,
  max: null,
  options: [],
  options_source: null,
  row_per_value: false,
})
const policy: SchemaTable = { name: "policy_details", rows: "one", inputs: [column("exposure", "float")], outputs: [] }
const pricing: SchemaTable = { name: "pricing_output", rows: "one", inputs: [], outputs: [column("model_premium", "float")] }
const form: FormSpec = {
  version: 1,
  name: "t",
  theme: { ...BLANK_THEME },
  schema: { tables: [policy, pricing] },
  pages: [
    {
      id: "p",
      title: "Sheet 1",
      width: 1280,
      show_when: { match: "all", conditions: [] },
      sections: [{ id: "section_1", title: "Section 1", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] }],
    },
  ],
  sample: { policy_details: [{ exposure: "100000" }] },
}

const priced = (premium: number | null): PricedSample => ({ tables: { pricing_output: [{ model_premium: premium }] } })

/** What the server makes of a form: here, its sample's exposure as typed. */
const workbenchOf = (spec: FormSpec): WorkbenchTablesResponse => ({
  tables: [],
  sample: { policy_details: { exposure: spec.sample.policy_details?.[0]?.exposure } },
  response_tables: [],
  store_columns: {},
})

/** What a Collection would show for the model premium now, and whether it is dimmed. */
const premium = () => {
  const { price } = useWorkbenchPricingStore.getState()
  const { form: current } = useWorkbenchFormStore.getState()
  return {
    value: price?.tables.pricing_output?.[0]?.model_premium,
    stale: price !== null && current !== null && price.basis !== previewBasis(current),
  }
}

/** A pricer that answers when told to, with a premium of the exposure it was given / 50. */
function pricerOnRequest() {
  const asked: { workbench: WorkbenchTablesResponse; answer: () => void }[] = []
  const pricer = vi.fn(
    (_form: FormSpec, workbench: WorkbenchTablesResponse) =>
      new Promise<PricedSample>((resolve) => {
        const exposure = Number((workbench.sample.policy_details as { exposure: string }).exposure)
        asked.push({ workbench, answer: () => resolve(priced(exposure / 50)) })
      }),
  )
  return { pricer, asked }
}

const type = (value: string) => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "policy_details", 0, "exposure", value))

describe("useWorkbenchPricingStore", () => {
  beforeEach(() => {
    vi.mocked(fetchWorkbenchFormTables).mockImplementation((spec) => Promise.resolve(workbenchOf(spec)))
    useWorkbenchFormStore.setState({ form, status: "ready", savedForm: JSON.stringify(form), dirty: false, undoStack: [], redoStack: [] })
    useWorkbenchPricingStore.setState({ pricer: null, price: null, error: null, pricing: false })
  })

  afterEach(async () => {
    vi.useRealTimers()
    // Let a pricing a test left running finish, so the next test starts with none.
    useWorkbenchPricingStore.getState().setPricer(null)
    await useWorkbenchPricingStore.getState().priceNow()
    vi.mocked(fetchWorkbenchFormTables).mockReset()
  })

  it("prices the sample as the form stands, saved or not", async () => {
    const pricer = vi.fn((_form: FormSpec, _workbench: WorkbenchTablesResponse) => Promise.resolve(priced(2000)))
    useWorkbenchPricingStore.getState().setPricer(pricer)
    type("250000")

    await useWorkbenchPricingStore.getState().priceNow()

    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith(useWorkbenchFormStore.getState().form)
    // The pricer is given the form whose tables it was given, which the preview sends.
    const spec = useWorkbenchFormStore.getState().form!
    expect(pricer).toHaveBeenCalledWith(spec, workbenchOf(spec))
    expect(premium()).toEqual({ value: 2000, stale: false })
    expect(useWorkbenchPricingStore.getState().error).toBeNull()
  })

  it("shows a price stale once the pipeline, its submodels, its source, its data or the document moves", async () => {
    useWorkbenchPricingStore.getState().setPricer(vi.fn(() => Promise.resolve(priced(2000))))
    type("250000")
    await useWorkbenchPricingStore.getState().priceNow()
    expect(premium()).toEqual({ value: 2000, stale: false })

    const graph = useGraphStore.getState()
    const { activeSource } = useSettingsStore.getState()
    const { executionGeneration } = useDocumentStatusStore.getState()
    const moves: [string, () => void, () => void][] = [
      // The Assistant, or any edit, changing the pipeline.
      ["pipeline", () => useGraphStore.setState({ structuralFingerprint: "edited" }), () => useGraphStore.setState({ structuralFingerprint: graph.structuralFingerprint })],
      ["submodel", () => useGraphStore.setState({ submodels: { rating: { graph: { nodes: [], edges: [] } } } }), () => useGraphStore.setState({ submodels: graph.submodels })],
      ["source", () => useSettingsStore.setState({ activeSource: "batch" }), () => useSettingsStore.setState({ activeSource })],
      // A branch switched, or the document read again.
      ["document", () => useDocumentStatusStore.setState({ executionGeneration: executionGeneration + 1 }), () => useDocumentStatusStore.setState({ executionGeneration })],
    ]
    for (const [, move, back] of moves) {
      move()
      expect(premium().stale).toBe(true)
      back()
      expect(premium().stale).toBe(false)
    }
    // A data file written through Files: no way back, so last.
    useDataEpochStore.getState().moved()
    expect(premium().stale).toBe(true)
  })

  it("prices once typing pauses", async () => {
    vi.useFakeTimers()
    const pricer = vi.fn((_form: FormSpec, _workbench: WorkbenchTablesResponse) => Promise.resolve(priced(2000)))
    useWorkbenchPricingStore.getState().setPricer(pricer)

    useWorkbenchPricingStore.getState().schedule()
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS - 1)
    useWorkbenchPricingStore.getState().schedule()
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS - 1)
    expect(pricer).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    expect(pricer).toHaveBeenCalledOnce()
  })

  it("keeps the last values, dimmed, while a change is priced, then prices once more for the latest", async () => {
    const { pricer, asked } = pricerOnRequest()
    useWorkbenchPricingStore.getState().setPricer(pricer)
    const first = useWorkbenchPricingStore.getState().priceNow()
    await vi.waitFor(() => expect(asked).toHaveLength(1))
    asked[0].answer()
    await first
    expect(premium()).toEqual({ value: 2000, stale: false })

    // Two changes while the first of them is priced: one more pricing, for the latest.
    type("150000")
    const second = useWorkbenchPricingStore.getState().priceNow()
    await vi.waitFor(() => expect(asked).toHaveLength(2))
    type("250000")
    void useWorkbenchPricingStore.getState().priceNow()
    void useWorkbenchPricingStore.getState().priceNow()
    expect(premium()).toEqual({ value: 2000, stale: true })
    expect(useWorkbenchPricingStore.getState().pricing).toBe(true)

    asked[1].answer()
    await vi.waitFor(() => expect(asked).toHaveLength(3))
    expect(asked[2].workbench.sample).toEqual({ policy_details: { exposure: "250000" } })
    asked[2].answer()
    await second
    expect(pricer).toHaveBeenCalledTimes(3)
    expect(premium()).toEqual({ value: 5000, stale: false })
    expect(useWorkbenchPricingStore.getState().pricing).toBe(false)
  })

  it("drops the pricing waiting for the pause and the one more asked for meanwhile once cancelled, as Preview is left", async () => {
    vi.useFakeTimers()
    const { pricer, asked } = pricerOnRequest()
    useWorkbenchPricingStore.getState().setPricer(pricer)
    const first = useWorkbenchPricingStore.getState().priceNow()
    await vi.waitFor(() => expect(asked).toHaveLength(1))

    // A change asked for while the first is priced, and another waiting for the pause.
    type("150000")
    void useWorkbenchPricingStore.getState().priceNow()
    useWorkbenchPricingStore.getState().schedule()
    useWorkbenchPricingStore.getState().cancel()
    asked[0].answer()
    await first
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)

    // The pricing under way finished; neither of the others ran.
    expect(pricer).toHaveBeenCalledOnce()
    expect(premium()).toEqual({ value: 2000, stale: true })
  })

  it("shows nothing and says why when pricing fails, until it next succeeds", async () => {
    useWorkbenchPricingStore.getState().setPricer(() => Promise.resolve(priced(2000)))
    await useWorkbenchPricingStore.getState().priceNow()
    useWorkbenchPricingStore.getState().setPricer(() => Promise.reject(new Error("Connect a frame to the Workbench Output's 'pricing_output' table.")))

    await useWorkbenchPricingStore.getState().priceNow()
    expect(premium().value).toBeUndefined()
    expect(useWorkbenchPricingStore.getState().error).toBe("Connect a frame to the Workbench Output's 'pricing_output' table.")

    useWorkbenchPricingStore.getState().setPricer(() => Promise.resolve(priced(null)))
    await useWorkbenchPricingStore.getState().priceNow()
    expect(premium().value).toBeNull()
    expect(useWorkbenchPricingStore.getState().error).toBeNull()
  })

  it("prices nothing, and says nothing, while the sample holds nothing typed in the cells the sheets show", async () => {
    const pricer = vi.fn((_form: FormSpec, _workbench: WorkbenchTablesResponse) => Promise.resolve(priced(1)))
    useWorkbenchPricingStore.getState().setPricer(pricer)
    useWorkbenchPricingStore.setState({ price: { basis: "b", sample: {}, tables: priced(1).tables }, error: "x" })
    // Nothing typed, or only a value in a column since removed.
    for (const sample of [{}, { policy_details: [{ gone: "1" }] }] as FormSpec["sample"][]) {
      useWorkbenchFormStore.setState({ form: { ...form, sample } })
      await useWorkbenchPricingStore.getState().priceNow()
      expect(pricer).not.toHaveBeenCalled()
      expect(useWorkbenchPricingStore.getState()).toMatchObject({ price: null, error: null })
    }
    // The exposure's box on a sheet that shows only once it is 0: hidden, it is nothing typed.
    const hiding = { match: "all" as const, conditions: [{ table: "policy_details", column: "exposure", op: "equals" as const, values: ["0"], field: null, days: 0 }] }
    const exposure = { id: "w", name: "w", type: "collection" as const, x: 0, y: 0, w: 400, h: 100, title: "", columns: 2, ...COLLECTION_OPTIONS, fields: [{ table: "policy_details", column: "exposure" }], show_when: { match: "all" as const, conditions: [] } }
    const [sheet] = form.pages
    useWorkbenchFormStore.setState({ form: { ...form, pages: [{ ...sheet, show_when: hiding, sections: [{ ...sheet.sections[0], widgets: [exposure] }] }] } })
    await useWorkbenchPricingStore.getState().priceNow()
    expect(pricer).not.toHaveBeenCalled()
  })

  it("prices nothing while the only value typed is in a row its table's rows leave out, the column giving them hidden", async () => {
    const pricer = vi.fn((_form: FormSpec, _workbench: WorkbenchTablesResponse) => Promise.resolve(priced(1)))
    useWorkbenchPricingStore.getState().setPricer(pricer)
    const perils = hiddenPerilsForm()
    // A limit held for NW, which the perils no longer hold.
    useWorkbenchFormStore.setState({ form: { ...perils, sample: { coverage: [{ peril: "NW", limit: "5000" }] } } })
    await useWorkbenchPricingStore.getState().priceNow()
    expect(pricer).not.toHaveBeenCalled()

    useWorkbenchFormStore.setState({ form: { ...perils, sample: { coverage: [{ peril: "EQ", limit: "5000" }] } } })
    await useWorkbenchPricingStore.getState().priceNow()
    expect(pricer).toHaveBeenCalledTimes(1)
  })

  it("prices nothing without a pricer or before the form is read", async () => {
    vi.useFakeTimers()
    useWorkbenchPricingStore.getState().schedule()
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await useWorkbenchPricingStore.getState().priceNow()
    expect(fetchWorkbenchFormTables).not.toHaveBeenCalled()

    const pricer = vi.fn((_form: FormSpec, _workbench: WorkbenchTablesResponse) => Promise.resolve(priced(1)))
    useWorkbenchPricingStore.getState().setPricer(pricer)
    useWorkbenchFormStore.setState({ status: "loading" })
    await useWorkbenchPricingStore.getState().priceNow()
    expect(pricer).not.toHaveBeenCalled()
    expect(useWorkbenchPricingStore.getState().price).toBeNull()
  })

  it("drops an answer that arrives after the view has left, and a pricing it had scheduled", async () => {
    vi.useFakeTimers()
    const { pricer, asked } = pricerOnRequest()
    useWorkbenchPricingStore.getState().setPricer(pricer)
    const running = useWorkbenchPricingStore.getState().priceNow()
    await vi.waitFor(() => expect(asked).toHaveLength(1))
    useWorkbenchPricingStore.getState().schedule()

    useWorkbenchPricingStore.getState().setPricer(null)
    asked[0].answer()
    await running
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)

    expect(useWorkbenchPricingStore.getState().price).toBeNull()
    expect(pricer).toHaveBeenCalledTimes(1)
  })
  describe("an answer held to the rows it priced", () => {
    const identity: WorkbenchTable["columns"] = [{ name: "quote_reference", type: "str" }, { name: "quote_option", type: "int" }]
    // A schedule's output side, keyed by its index, which its answer carries.
    const equipment: WorkbenchTable = {
      name: "ce_equipment",
      side: "output",
      port: "ce_equipment_output",
      rows: "many",
      columns: [...identity, { name: "item_number", type: "int" }, { name: "final_premium", type: "float" }, { name: "referral", type: "str" }],
      key: "item_number",
      null_key: null, index: null,
    }
    const unkeyed: WorkbenchTable = { name: "pricing_output", side: "output", port: "pricing_output_output", rows: "one", columns: [{ name: "model_premium", type: "float" }], key: null, null_key: null, index: null }
    /** What the server makes of the form: the schedule's typed rows, numbered by its index, and both output sides. */
    const keyedOf = (spec: FormSpec): WorkbenchTablesResponse => ({
      ...workbenchOf(spec),
      sample: { ...workbenchOf(spec).sample, ce_equipment: [{ item_number: 1, limit: 10 }, { item_number: 2, limit: 20 }] },
      response_tables: [equipment, unkeyed],
    })
    const answered = (rows: Record<string, unknown>[]): PricedSample => ({ tables: { ce_equipment: rows, pricing_output: [{ model_premium: null }] } })
    const NAN = { __haute_type__: "non_finite_float", value: "nan" }
    const row = (item: unknown, premium: unknown, referral: unknown = null) =>
      ({ quote_reference: "PREVIEW", quote_option: 1, item_number: item, final_premium: premium, referral })

    beforeEach(() => vi.mocked(fetchWorkbenchFormTables).mockImplementation((spec) => Promise.resolve(keyedOf(spec))))

    it("keeps an answer fitting the rows priced, in its own order, a row priced nothing left out", async () => {
      useWorkbenchPricingStore.getState().setPricer(vi.fn(() => Promise.resolve(answered([row(2, 7.5)]))))
      await useWorkbenchPricingStore.getState().priceNow()

      const { price, error } = useWorkbenchPricingStore.getState()
      expect(error).toBeNull()
      expect(price?.tables.ce_equipment).toEqual([row(2, 7.5)])
      expect(price?.sample.ce_equipment).toEqual([{ item_number: 1, limit: 10 }, { item_number: 2, limit: 20 }])
    })

    it("fails the pricing with the server's words when the answer's rows do not fit the rows priced", async () => {
      const pricer = vi.fn(() => Promise.resolve(answered([row(1, 5), row(null, 6), row(2, 1), row(2, 2), row(7, 3)])))
      useWorkbenchPricingStore.getState().setPricer(pricer)
      await useWorkbenchPricingStore.getState().priceNow()

      expect(useWorkbenchPricingStore.getState().price).toBeNull()
      expect(useWorkbenchPricingStore.getState().error).toBe(
        "ce_equipment_output does not fit ce_equipment's rows: 1 row has no item_number; item_number 2 is given twice; item_number 7 is on no row priced.",
      )
    })

    it("leaves out an answer row holding no output value, each null or a number that is not finite, the identity and the key aside", async () => {
      const pricer = vi.fn(() => Promise.resolve(answered([row(1, null), row(2, NAN)])))
      useWorkbenchPricingStore.getState().setPricer(pricer)
      await useWorkbenchPricingStore.getState().priceNow()

      const { price, error } = useWorkbenchPricingStore.getState()
      expect(error).toBeNull()
      expect(price?.tables.ce_equipment).toEqual([])
      // A one-row table, which no key holds, keeps its row of nulls.
      expect(price?.tables.pricing_output).toEqual([{ model_premium: null }])
    })

    it("keeps an answer row holding any output value, text or zero included", async () => {
      const pricer = vi.fn(() => Promise.resolve(answered([row(1, NAN, "Over limit"), row(2, 0)])))
      useWorkbenchPricingStore.getState().setPricer(pricer)
      await useWorkbenchPricingStore.getState().priceNow()

      expect(useWorkbenchPricingStore.getState().price?.tables.ce_equipment).toEqual([row(1, NAN, "Over limit"), row(2, 0)])
    })
  })
})
