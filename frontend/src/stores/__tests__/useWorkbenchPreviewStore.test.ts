/**
 * The underwriter app's quote (specs/workbench): kept apart from the sample, its cells and
 * rows set without history, priced through the app's quote pricer once typing pauses,
 * whatever its cells hold, with the basis it was priced for and the reason when it has no
 * price; a blank quote, or a read-only one, never priced; and a pricing settled for a save.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { CollectionWidget, FormSchema, FormSpec, SchemaColumn, SchemaTable, ShowWhen } from "../../api/types"
import { valuesBasis } from "../../utils/sheetValues"
import { columnKey } from "../../utils/sheetVisibility"
import { BLANK_THEME, COLLECTION_OPTIONS, type SampleRow } from "../../utils/workbenchForm"
import { hiddenPerilsForm, section, sheet } from "../../workbench/__tests__/fixtures"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { PRICING_DELAY_MS, type PricedValues, type QuotePricer } from "../useWorkbenchPricingStore"

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
  options_source: null,
  row_per_value: false,
  index: false,
  help: "",
  placeholder: "",
  group: "",
  default_value: "",
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
  version: 1,
  name: "t",
  theme: { ...BLANK_THEME },
  schema: { tables: [policy, pricing] },
  pages: [sheet("p", "Sheet 1")],
  sample: { t1: [{ c1: "100000" }] },
}

/** A Collection of the policy's columns, always shown. */
const boxes = (id: string, columns: string[]): CollectionWidget => ({
  id, type: "collection", x: 0, y: 0, w: 400, h: 100, title: "", columns: 2, ...COLLECTION_OPTIONS, show_when: { match: "all", conditions: [] },
  fields: columns.map((columnId) => ({ table: "t1", column: columnId })),
})
/** The schema with a box, cover, and a name beside the policy's required exposure. */
const covered: FormSchema = { tables: [{ ...policy, columns: [...policy.columns, column("c3", "cover", { type: "bool" }), column("c4", "name")] }, pricing] }
/** Shown only while cover is ticked. */
const whileCovered: ShowWhen = { match: "all", conditions: [{ table: "t1", column: "c3", op: "ticked", values: [], field: null, days: 0 }] }

/** What the app's route answers: the premium, and the quote as the server typed it. */
const answered = (premium: number): PricedValues => ({ tables: { pricing_output: [{ model_premium: premium }] }, sample: { policy_details: { exposure: premium } } })

/** A quote pricer whose answers are given by hand, one per call, in turn. */
function heldPricer() {
  const answers: { resolve: (value: PricedValues) => void; reject: (reason: Error) => void }[] = []
  const quotePricer = vi.fn<QuotePricer>(
    () =>
      new Promise<PricedValues>((resolve, reject) => {
        answers.push({ resolve, reject })
      }),
  )
  return { quotePricer, answers }
}

const store = useWorkbenchPreviewStore.getState

describe("useWorkbenchPreviewStore", () => {
  beforeEach(() => {
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPricingStore.setState({ pricer: null, quotePricer: null })
    // Nothing waits or runs from a test before.
    store().open({}, null, true)
    useWorkbenchPreviewStore.setState({ quote: {}, price: null, error: null, pricing: false, readOnly: false, locked: new Set() })
  })

  afterEach(() => {
    vi.clearAllMocks()
    vi.useRealTimers()
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
  })

  it("holds the quote apart from the sample: cells set and rows replaced, without history", () => {
    store().setCell("t1", 0, "c1", "250000")
    store().setCell("t3", 2, "c9", "Crane")
    expect(store().quote).toEqual({ t1: [{ c1: "250000" }], t3: [{}, {}, { c9: "Crane" }] })
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "100000" }] })
    expect(useWorkbenchFormStore.getState().undoStack).toEqual([])

    store().setRows({ t3: [{ c9: "Paver" }], t4: [{}] })
    expect(store().quote).toEqual({ t1: [{ c1: "250000" }], t3: [{ c9: "Paver" }], t4: [{}] })
  })

  it("prices the quote through the app's quote pricer once typing pauses, whatever its cells hold, and not again for the same values", async () => {
    vi.useFakeTimers()
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(5000)))
    useWorkbenchPricingStore.setState({ quotePricer })

    // Below the exposure's minimum, and priced all the same.
    store().setCell("t1", 0, "c1", "-5")
    store().setCell("t1", 0, "c1", "-50")
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS - 1)
    expect(quotePricer).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(1)
    await store().settle()

    expect(quotePricer).toHaveBeenCalledTimes(1)
    expect(quotePricer).toHaveBeenCalledWith(form, { t1: [{ c1: "-50" }] })
    expect(store()).toMatchObject({
      error: null,
      pricing: false,
      price: { basis: valuesBasis(form.schema, new Set(), { t1: [{ c1: "-50" }] }), ...answered(5000) },
    })

    // The same values again price nothing; new ones do.
    store().setCell("t1", 0, "c1", "-50")
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await store().settle()
    expect(quotePricer).toHaveBeenCalledTimes(1)
    store().setCell("t1", 0, "c1", "1")
    expect(store().price?.basis).not.toBe(valuesBasis(form.schema, new Set(), store().quote))
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await store().settle()
    expect(quotePricer).toHaveBeenCalledTimes(2)
  })

  it("prices nothing for a blank quote, nothing typed in the tables the schema has now, and says nothing", async () => {
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(1)))
    useWorkbenchPricingStore.setState({ quotePricer })
    const blanks: Record<string, SampleRow[]>[] = [
      {},
      // A value in a column since removed.
      { t1: [{ gone: "x" }] },
      { gone: [{ c1: "y" }] },
      // A second row of a table with one row per quote: the server reads the first alone.
      { t1: [{}, { c1: "100" }] },
    ]
    for (const quote of blanks) {
      useWorkbenchPreviewStore.setState({ quote, price: { basis: "b", ...answered(1) }, error: "x" })
      await store().priceNow()
      expect(quotePricer).not.toHaveBeenCalled()
      expect(store()).toMatchObject({ price: null, error: null })
    }
  })

  it("prices nothing for a quote holding its defaults alone, and prices it once anything is typed", async () => {
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(1)))
    useWorkbenchPricingStore.setState({ quotePricer })
    const defaulted: FormSchema = {
      tables: [{ ...covered.tables[0], columns: covered.tables[0].columns.map((c) => (c.id === "c3" ? { ...c, default_value: true } : c.id === "c4" ? { ...c, default_value: "Acme" } : c)) }, pricing],
    }
    useWorkbenchFormStore.setState({ form: { ...form, schema: defaulted } })
    store().open({ t1: [{ c3: true, c4: "Acme" }] }, null, false)

    await store().priceNow()
    expect(quotePricer).not.toHaveBeenCalled()
    expect(store()).toMatchObject({ price: null, error: null })

    store().setCell("t1", 0, "c1", "5")
    await store().settle()
    expect(quotePricer).toHaveBeenCalledWith({ ...form, schema: defaulted }, { t1: [{ c3: true, c4: "Acme", c1: "5" }] })
  })

  it("prices nothing while the only value typed is in a row its table's rows leave out, the column giving them hidden", async () => {
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(1)))
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchFormStore.setState({ form: hiddenPerilsForm() })
    // A limit held for NW, which the perils no longer hold.
    useWorkbenchPreviewStore.setState({ quote: { coverage: [{ "coverage.peril": "NW", "coverage.limit": "5000" }] } })
    await store().priceNow()
    expect(quotePricer).not.toHaveBeenCalled()

    useWorkbenchPreviewStore.setState({ quote: { coverage: [{ "coverage.peril": "EQ", "coverage.limit": "5000" }] } })
    await store().priceNow()
    expect(quotePricer).toHaveBeenCalledTimes(1)
  })

  it("keeps a locked column as the app set it: no typing, and not what makes a quote typed", async () => {
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(1)))
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchFormStore.setState({ form: { ...form, schema: covered } })
    store().open({ t1: [{ c4: "Q-000001" }] }, null, false)
    store().setLocked(new Set(["t1:c4"]))

    store().setCell("t1", 0, "c4", "Q-9")
    expect(store().quote).toEqual({ t1: [{ c4: "Q-000001" }] })
    await store().priceNow()
    expect(quotePricer).not.toHaveBeenCalled()

    store().setCell("t1", 0, "c1", "5")
    await store().settle()
    expect(quotePricer).toHaveBeenCalledWith({ ...form, schema: covered }, { t1: [{ c4: "Q-000001", c1: "5" }] })
  })

  it("opens a saved quote with its price, pricing one that may change and has none, and holds a read-only quote as it is", async () => {
    vi.useFakeTimers()
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(7)))
    useWorkbenchPricingStore.setState({ quotePricer })
    const price = { basis: valuesBasis(form.schema, new Set(), { t1: [{ c1: "5" }] }), ...answered(5) }
    useWorkbenchPreviewStore.setState({ error: "x" })

    // Read only: as it was saved, never typed into or priced.
    store().open({ t1: [{ c1: "5" }] }, price, true)
    expect(store()).toMatchObject({ quote: { t1: [{ c1: "5" }] }, price, error: null, readOnly: true })
    store().setCell("t1", 0, "c1", "6")
    store().setRows({ t1: [] })
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await store().priceNow()
    expect(store().quote).toEqual({ t1: [{ c1: "5" }] })
    expect(quotePricer).not.toHaveBeenCalled()

    // Let change, its price still its values': nothing to price.
    store().setReadOnly(false)
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await store().settle()
    expect(quotePricer).not.toHaveBeenCalled()
    store().setCell("t1", 0, "c1", "6")
    expect(store().quote).toEqual({ t1: [{ c1: "6" }] })

    // Opened unpriced, as it may change: priced once the pause has passed.
    store().open({ t1: [{ c1: "8" }] }, null, false)
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await store().settle()
    expect(quotePricer).toHaveBeenCalledTimes(1)
    expect(quotePricer).toHaveBeenLastCalledWith(form, { t1: [{ c1: "8" }] })
    expect(store().price).toMatchObject(answered(7))
  })

  it("leaves a value in a section the quote hides out of what is priced, the price for the schema without it", async () => {
    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(5000)))
    useWorkbenchPricingStore.setState({ quotePricer })
    // One sheet, whose second section holds the exposure and shows only while the box,
    // cover, in its first is ticked; the exposure's own Collection has no conditions.
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

    // The exposure alone: the quote holds nothing the sheets show typed.
    store().setCell("t1", 0, "c1", "-5")
    await store().settle()
    expect(quotePricer).not.toHaveBeenCalled()
    expect(store()).toMatchObject({ error: null, price: null })

    // A name typed beside it: priced, the quote going to the server as typed, which leaves
    // the exposure out where it makes the quote, and the price is for the schema without it.
    store().setCell("t1", 0, "c4", "Acme")
    await store().settle()
    const { quote } = store()
    expect(quote).toEqual({ t1: [{ c1: "-5", c4: "Acme" }] })
    expect(quotePricer).toHaveBeenCalledWith(sectioned, quote)
    const hidden = new Set([columnKey("t1", "c1")])
    expect(store().price?.basis).toBe(valuesBasis(covered, hidden, quote))
    expect(store().price?.basis).not.toBe(valuesBasis(covered, new Set(), quote))
  })

  it("prices once more after a pricing under way, on the quote as it is then, and settles both for a save", async () => {
    const { quotePricer, answers } = heldPricer()
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "100" }] } })

    const first = store().priceNow()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    store().setCell("t1", 0, "c1", "200")
    // A save starts: what waits for the pause is priced now, after the pricing under way.
    let settled = false
    const settling = store()
      .settle()
      .then(() => {
        settled = true
      })
    answers[0].resolve(answered(100))
    await vi.waitFor(() => expect(quotePricer).toHaveBeenCalledTimes(2))
    expect(quotePricer).toHaveBeenLastCalledWith(form, { t1: [{ c1: "200" }] })
    expect(settled).toBe(false)
    answers[1].resolve(answered(200))
    await settling
    await first
    expect(store()).toMatchObject({ pricing: false, price: { basis: valuesBasis(form.schema, new Set(), { t1: [{ c1: "200" }] }), ...answered(200) } })
  })

  it("settles a value entered after the save began: the save waits for its pricing too", async () => {
    const { quotePricer, answers } = heldPricer()
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "100" }] } })

    const first = store().priceNow()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    // A save starts while 100 is priced, and 200 is entered while it waits.
    const settling = store()
      .settle()
      .then(() => ({ priced: quotePricer.mock.calls.length, basis: store().price?.basis }))
    store().setCell("t1", 0, "c1", "200")
    // 100's answer comes before the pause after 200 has passed: 200 is priced at once.
    answers[0].resolve(answered(100))
    await vi.waitFor(() => expect(quotePricer).toHaveBeenCalledTimes(2))
    expect(quotePricer).toHaveBeenLastCalledWith(form, { t1: [{ c1: "200" }] })
    answers[1].resolve(answered(200))
    // When the save reads them, the values and their price agree.
    expect(await settling).toEqual({ priced: 2, basis: valuesBasis(form.schema, new Set(), { t1: [{ c1: "200" }] }) })
    await first
  })

  it("drops an answer that arrives after another quote was opened, or after the host dropped its pricer", async () => {
    const { quotePricer, answers } = heldPricer()
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "100" }] } })

    const first = store().priceNow()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    store().open({ t1: [{ c1: "5" }] }, null, true)
    expect(store().pricing).toBe(false)
    answers[0].resolve(answered(1))
    await first
    expect(store()).toMatchObject({ quote: { t1: [{ c1: "5" }] }, price: null, error: null, pricing: false })

    store().open({ t1: [{ c1: "300" }] }, null, false)
    const second = store().priceNow()
    await vi.waitFor(() => expect(store().pricing).toBe(true))
    useWorkbenchPricingStore.setState({ quotePricer: null })
    answers[1].reject(new Error("late"))
    await second
    expect(store()).toMatchObject({ price: null, error: null })
  })

  it("says why pricing failed, and prices nothing without a quote pricer or before the form is read", async () => {
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "100" }] } })
    await store().priceNow()
    expect(store()).toMatchObject({ price: null, error: null, pricing: false })

    useWorkbenchPricingStore.setState({ quotePricer: () => Promise.reject(new Error("The pipeline refused it.")) })
    await store().priceNow()
    expect(store()).toMatchObject({ price: null, error: "Pricing failed: The pipeline refused it." })

    const quotePricer = vi.fn<QuotePricer>(() => Promise.resolve(answered(1)))
    useWorkbenchPricingStore.setState({ quotePricer })
    useWorkbenchFormStore.setState({ status: "loading" })
    await store().priceNow()
    expect(quotePricer).not.toHaveBeenCalled()
  })
})
