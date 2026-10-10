/**
 * The sample priced live while building (specs/workbench): priced as the form stands,
 * once typing pauses, one pricing at a time with one more for changes made meanwhile, the
 * last values kept and dimmed until the next answer, and the reason when pricing fails.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
}))

import type { FormSpec, SchemaColumn, SchemaTable, WorkbenchTablesResponse } from "../../api/types"
import { fetchWorkbenchFormTables } from "../../api/workbench"
import { pricingBasis, withSampleCell } from "../../utils/workbenchForm"
import type { PricedSample } from "../../utils/workbenchTables"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPricingStore, { PRICING_DELAY_MS } from "../useWorkbenchPricingStore"

const column = (id: string, name: string, type: SchemaColumn["type"]): SchemaColumn => ({
  id,
  name,
  type,
  label: "",
  key: false,
  index: false,
  required: false,
  min: null,
  max: null,
  options: [],
})
const policy: SchemaTable = { id: "t1", name: "policy_details", role: "input", rows: "one", columns: [column("c1", "exposure", "float")] }
const pricing: SchemaTable = { id: "t2", name: "pricing_output", role: "output", rows: "one", columns: [column("c2", "model_premium", "float")] }
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
  sample: { policy_details: { exposure: spec.sample.t1?.[0]?.c1 } },
  response_tables: [],
})

/** What a Collection would show for the model premium now, and whether it is dimmed. */
const premium = () => {
  const { price } = useWorkbenchPricingStore.getState()
  const { form: current } = useWorkbenchFormStore.getState()
  return {
    value: price?.tables.pricing_output?.[0]?.model_premium,
    stale: price !== null && current !== null && price.basis !== pricingBasis(current),
  }
}

/** A pricer that answers when told to, with a premium of the exposure it was given / 50. */
function pricerOnRequest() {
  const asked: { workbench: WorkbenchTablesResponse; answer: () => void }[] = []
  const pricer = vi.fn(
    (workbench: WorkbenchTablesResponse) =>
      new Promise<PricedSample>((resolve) => {
        const exposure = Number((workbench.sample.policy_details as { exposure: string }).exposure)
        asked.push({ workbench, answer: () => resolve(priced(exposure / 50)) })
      }),
  )
  return { pricer, asked }
}

const type = (value: string) => useWorkbenchFormStore.getState().change((spec) => withSampleCell(spec, "t1", 0, "c1", value))

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
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(2000)))
    useWorkbenchPricingStore.getState().setPricer(pricer)
    type("250000")

    await useWorkbenchPricingStore.getState().priceNow()

    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith(useWorkbenchFormStore.getState().form)
    expect(pricer).toHaveBeenCalledWith(workbenchOf(useWorkbenchFormStore.getState().form!))
    expect(premium()).toEqual({ value: 2000, stale: false })
    expect(useWorkbenchPricingStore.getState().error).toBeNull()
  })

  it("prices once typing pauses", async () => {
    vi.useFakeTimers()
    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(2000)))
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

  it("prices nothing without a pricer or before the form is read", async () => {
    vi.useFakeTimers()
    useWorkbenchPricingStore.getState().schedule()
    await vi.advanceTimersByTimeAsync(PRICING_DELAY_MS)
    await useWorkbenchPricingStore.getState().priceNow()
    expect(fetchWorkbenchFormTables).not.toHaveBeenCalled()

    const pricer = vi.fn((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(1)))
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
})
