/**
 * Test quotes in Preview (specs/workbench): the quote on the sheets, the form's sample,
 * saved in full whatever its cells hold, opened back in the sample's place as one undo
 * step, run one request each on a pipeline fixed when the run starts, a change accepted
 * after the revision the run read, and a quote deleted.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
  fetchTestQuotes: vi.fn(),
  fetchTestQuotesBranch: vi.fn(),
  saveTestQuote: vi.fn(),
  openTestQuote: vi.fn(),
  compareTestQuote: vi.fn(),
  acceptTestQuote: vi.fn(),
  deleteTestQuote: vi.fn(),
  captureTestQuotes: vi.fn(),
}))

import { ApiError } from "../../api/client"
import type { FormSpec, SchemaColumn, SchemaTable, WorkbenchTablesResponse, WorkbenchTestQuoteSummary } from "../../api/types"
import {
  acceptTestQuote,
  captureTestQuotes,
  compareTestQuote,
  deleteTestQuote,
  fetchTestQuotes,
  fetchTestQuotesBranch,
  fetchWorkbenchFormTables,
  openTestQuote,
  saveTestQuote,
} from "../../api/workbench"
import type { PricedSample } from "../../utils/workbenchTables"
import { hiddenPerilsForm, sheet } from "../../workbench/__tests__/fixtures"
import useDataEpochStore from "../useDataEpochStore"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGraphStore from "../useGraphStore"
import useSettingsStore from "../useSettingsStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPricingStore, { type Pricer } from "../useWorkbenchPricingStore"
import useWorkbenchTestQuotesStore, { runIsStale } from "../useWorkbenchTestQuotesStore"
import { BLANK_THEME } from "../../utils/workbenchForm"

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
const pricing: SchemaTable = { id: "t2", name: "pricing_output", role: "output", rows: "one", columns: [column("c2", "premium", { type: "float" })] }
const form: FormSpec = { version: 1, name: "t", theme: { ...BLANK_THEME }, schema: { tables: [policy, pricing] }, pages: [sheet("p", "Sheet 1")], sample: {} }
const workbench: WorkbenchTablesResponse = { tables: [], sample: {}, response_tables: [] }
const priced = (premium: number): PricedSample => ({ tables: { pricing_output: [{ premium }] } })
const summary = (id: string, name: string): WorkbenchTestQuoteSummary => ({
  id, name, number: 1, saved_by: "ana@example.com", saved_at: "2026-10-12T09:00:00", priced: true,
})
const written = { git_sha: null, warnings: [], identity_required: false, quote: null }
const opened = (id: string, exposure: string, dropped: string[] = []) => ({
  id, name: id, number: 3, revision_digest: "read-3", values: { t1: [{ c1: exposure }] }, dropped, response: { pricing_output: { premium: 1 } },
})

const store = useWorkbenchTestQuotesStore.getState

/** The quote on the sheets: the form's sample, as Preview types it. */
const typeSample = (sample: FormSpec["sample"]) => useWorkbenchFormStore.setState({ form: { ...form, sample } })

describe("useWorkbenchTestQuotesStore", () => {
  let pricer: Mock<Pricer>
  let snapshots: number

  beforeEach(() => {
    snapshots = 0
    pricer = vi.fn<Pricer>((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(500)))
    vi.mocked(fetchWorkbenchFormTables).mockResolvedValue(workbench)
    vi.mocked(fetchTestQuotes).mockResolvedValue({ quotes: [summary("a", "Northern"), summary("b", "Southern")] })
    vi.mocked(fetchTestQuotesBranch).mockResolvedValue("work")
    vi.mocked(saveTestQuote).mockResolvedValue(written)
    vi.mocked(acceptTestQuote).mockResolvedValue(written)
    vi.mocked(deleteTestQuote).mockResolvedValue(written)
    useWorkbenchFormStore.setState({ form, status: "ready", undoStack: [], redoStack: [] })
    useWorkbenchPricingStore.setState({
      snapshot: () => {
        snapshots += 1
        return pricer
      },
    })
    useWorkbenchTestQuotesStore.setState({
      open: false, quotes: [], loading: false, error: null, notice: null, saving: false,
      selected: [], runs: {}, runForm: null, runBasis: null, running: false, accepting: false, uncaptured: false,
    })
  })

  afterEach(() => {
    vi.clearAllMocks()
    useWorkbenchFormStore.setState({ form: null, status: "idle" })
    useWorkbenchPricingStore.setState({ snapshot: null })
  })

  it("saves the quote on the sheets, priced in full on a pipeline fixed for the save", async () => {
    typeSample({ t1: [{ c1: "250000" }] })

    expect(await store().save("Northern")).toBe(true)

    expect(snapshots).toBe(1)
    const sampled = { ...form, sample: { t1: [{ c1: "250000" }] } }
    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith(sampled)
    // On the branch named as the save started.
    expect(saveTestQuote).toHaveBeenCalledWith("Northern", sampled, { t1: [{ c1: "250000" }] }, priced(500).tables, "work")
    expect(store().quotes.map((quote) => quote.name)).toEqual(["Northern", "Southern"])
  })

  it("refuses to save a blank quote, saying why, and saves one whose cells break their column's rules, as Preview prices it", async () => {
    expect(await store().save("Blank")).toBe(false)
    expect(store().error).toBe("Type the quote first")
    expect(saveTestQuote).not.toHaveBeenCalled()

    typeSample({ t1: [{ c1: "-5" }] })
    expect(await store().save("Negative")).toBe(true)
    expect(saveTestQuote).toHaveBeenCalledWith("Negative", { ...form, sample: { t1: [{ c1: "-5" }] } }, { t1: [{ c1: "-5" }] }, priced(500).tables, "work")
  })

  it("refuses to save a quote whose only value is in a row its table's rows leave out, the column giving them hidden", async () => {
    useWorkbenchFormStore.setState({ form: { ...hiddenPerilsForm(), sample: { coverage: [{ "coverage.peril": "NW", "coverage.limit": "5000" }] } } })

    expect(await store().save("Gone")).toBe(false)
    expect(store().error).toBe("Type the quote first")
    expect(saveTestQuote).not.toHaveBeenCalled()
  })

  it("opens a test quote in the sample's place, one undo step, naming what has no place on the sheets", async () => {
    vi.mocked(openTestQuote).mockResolvedValue(opened("a", "125000", ["policy_details.cover"]))
    typeSample({ t9: [{ x: "old" }] })

    await store().openQuote("a")

    expect(openTestQuote).toHaveBeenCalledWith("a", { ...form, sample: { t9: [{ x: "old" }] } })
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "125000" }] })
    expect(store().notice).toBe("Opened 'a'. No place on the sheets for: policy_details.cover.")
    // Undone, the sample is back as it was.
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    useWorkbenchFormStore.getState().undo()
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t9: [{ x: "old" }] })
  })

  it("runs every test quote one at a time on one fixed pipeline and says what changed", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, id === "a" ? "1" : "2"))
    vi.mocked(compareTestQuote).mockImplementation(async (id) => ({
      number: 3,
      digest: "compared-3",
      changes: id === "a" ? [] : [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    }))

    await store().run()

    // One pricer, fixed when the run started, for every quote.
    expect(snapshots).toBe(1)
    expect(pricer).toHaveBeenCalledTimes(2)
    expect(compareTestQuote).toHaveBeenCalledWith("b", form, { t1: [{ c1: "2" }] }, priced(500).tables, 3, "read-3")
    expect(store().runs.a).toMatchObject({ status: "unchanged", number: 3 })
    expect(store().runs.b).toMatchObject({ status: "changed", changes: [{ path: "pricing_output.premium" }] })
    expect(store().running).toBe(false)
  })

  it("runs the ticked test quotes alone, and a failure is that quote's", async () => {
    await store().load()
    store().toggle("b")
    vi.mocked(openTestQuote).mockRejectedValue(new ApiError("HTTP 409", 409, "quote_not_found: gone", null, null))

    await store().run()

    expect(Object.keys(store().runs)).toEqual(["b"])
    expect(store().runs.b).toMatchObject({ status: "failed", error: "quote_not_found: gone" })
  })

  it("fails a test quote with nothing typed left on the sheets without pricing it", async () => {
    await store().load()
    store().toggle("a")
    // Its one column was removed since: carried onto the form, nothing of it is left.
    vi.mocked(openTestQuote).mockResolvedValue({ ...opened("a", "1", ["policy_details.limit"]), values: {} })

    await store().run()

    expect(pricer).not.toHaveBeenCalled()
    expect(compareTestQuote).not.toHaveBeenCalled()
    expect(store().runs.a).toMatchObject({ status: "failed", error: "Nothing typed in it has a place on the sheets now", tables: null })
  })

  it("is stale once the pipeline, the source, the data or the form changes after the run started", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({ number: 3, digest: "compared-3", changes: [] })
    await store().run()
    const { runBasis } = store()
    expect(runIsStale(runBasis, form)).toBe(false)

    const pipeline = useGraphStore.getState().structuralFingerprint
    useGraphStore.setState({ structuralFingerprint: "edited" })
    expect(runIsStale(runBasis, form)).toBe(true)
    useGraphStore.setState({ structuralFingerprint: pipeline })
    useSettingsStore.setState({ activeSource: "batch" })
    expect(runIsStale(runBasis, form)).toBe(true)
    useSettingsStore.setState({ activeSource: runBasis?.source ?? "live" })
    expect(runIsStale(runBasis, { ...form, schema: { tables: [policy] } })).toBe(true)
    // An edit inside a submodel, which the root's structure leaves out, prices differently.
    const submodels = useGraphStore.getState().submodels
    useGraphStore.setState({ submodels: { ...submodels, rating: { graph: { nodes: [], edges: [] } } } })
    expect(runIsStale(runBasis, form)).toBe(true)
    useGraphStore.setState({ submodels })
    expect(runIsStale(runBasis, form)).toBe(false)
    // A data file written through Files since prices differently too.
    useDataEpochStore.getState().moved()
    expect(runIsStale(runBasis, form)).toBe(true)
  })

  it("accepts a change after the revision the run read, and says so when the quote moved on", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({
      number: 3, digest: "compared-3", changes: [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    })
    await store().run()

    await store().accept("a")
    // The revision the run opened, by its digest, whatever the compare read.
    expect(acceptTestQuote).toHaveBeenCalledWith("a", 3, "read-3", form, { t1: [{ c1: "1" }] }, priced(500).tables)
    expect(store().runs.a).toMatchObject({ status: "unchanged", changes: [], number: 4 })

    vi.mocked(acceptTestQuote).mockRejectedValueOnce(new ApiError("HTTP 409", 409, "stale_quote_revision: moved", null, null))
    await store().accept("b")
    expect(store().error).toBe("The quote was saved since the run, or differently on this branch: run it again before accepting.")
  })

  it("accepts nothing while a run is under way, nor once the run is stale", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({
      number: 3, digest: "compared-3", changes: [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    })
    // The second quote's pricing waits, so the run is under way once the first has changed.
    let finish: () => void = () => {}
    pricer
      .mockImplementationOnce(() => Promise.resolve(priced(500)))
      .mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(priced(500)) }))
    const running = store().run()
    await vi.waitFor(() => expect(store().runs.a?.status).toBe("changed"))

    await store().accept("a")
    expect(acceptTestQuote).not.toHaveBeenCalled()

    finish()
    await running
    const pipeline = useGraphStore.getState().structuralFingerprint
    useGraphStore.setState({ structuralFingerprint: "edited" })
    await store().accept("a")
    expect(acceptTestQuote).not.toHaveBeenCalled()
    expect(store().error).toMatch(/run again before accepting/)
    useGraphStore.setState({ structuralFingerprint: pipeline })
  })

  it("starts no run while an accept is under way", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({
      number: 3, digest: "compared-3", changes: [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    })
    await store().run()
    let finish: () => void = () => {}
    vi.mocked(acceptTestQuote).mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(written) }))

    const accepting = store().accept("a")
    await store().run()
    expect(openTestQuote).toHaveBeenCalledTimes(2)

    finish()
    await accepting
    expect(store().runs.a).toMatchObject({ status: "unchanged", number: 4 })
    expect(store().accepting).toBe(false)
  })

  it("forgets a run once the pipeline's document is adopted anew, and an accept answering after is nobody's", async () => {
    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({
      number: 3, digest: "compared-3", changes: [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    })
    await store().run()
    let finish: () => void = () => {}
    vi.mocked(acceptTestQuote).mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(written) }))
    const accepting = store().accept("a")

    // A branch switched by hand: the run was read on the other branch's test quotes.
    useDocumentStatusStore.setState({ executionGeneration: useDocumentStatusStore.getState().executionGeneration + 1 })
    expect(store().runs).toEqual({})
    expect(store().runBasis).toBeNull()

    finish()
    await accepting
    expect(store().runs).toEqual({})
  })

  it("reports each write's capture, and captures again on the next project save what the ledger did not take", async () => {
    typeSample({ t1: [{ c1: "250000" }] })
    vi.mocked(saveTestQuote).mockResolvedValueOnce({
      ...written,
      identity_required: true,
      warnings: ["Changes saved, but version capture needs a git identity."],
    })
    vi.mocked(captureTestQuotes).mockResolvedValue({ ...written, git_sha: "abc123" })

    expect(await store().save("Northern")).toBe(true)
    expect(store().uncaptured).toBe(true)

    await store().flush()
    expect(captureTestQuotes).toHaveBeenCalledTimes(1)
    expect(saveTestQuote).toHaveBeenCalledTimes(1)
    expect(store().uncaptured).toBe(false)

    // Nothing waits on the ledger now: a save captures nothing again.
    await store().flush()
    expect(captureTestQuotes).toHaveBeenCalledTimes(1)
  })

  it("saves nothing into a branch adopted while the quote was priced, and a project save waits for a save under way", async () => {
    typeSample({ t1: [{ c1: "250000" }] })
    let finish: () => void = () => {}
    pricer.mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(priced(500)) }))

    const saving = store().save("Northern")
    let flushed = false
    const flushing = store().flush().then(() => { flushed = true })
    await vi.waitFor(() => expect(pricer).toHaveBeenCalled())
    expect(flushed).toBe(false)

    useDocumentStatusStore.setState({ executionGeneration: useDocumentStatusStore.getState().executionGeneration + 1 })
    finish()
    expect(await saving).toBe(false)
    await flushing
    expect(flushed).toBe(true)
    expect(saveTestQuote).not.toHaveBeenCalled()
    expect(store().error).toMatch(/branch changed while the quote was priced/)
  })

  it("saves past a list that refuses a file an older Haute wrote: the save brings it up to date", async () => {
    vi.mocked(fetchTestQuotes).mockRejectedValueOnce(
      new ApiError("HTTP 409", 409, "test_quote_invalid: tests/quotes/test_quotes.sqlite was written by another version of Haute.", null, null),
    )
    await store().load()
    expect(store().error).toMatch(/another version of Haute/)
    typeSample({ t1: [{ c1: "250000" }] })

    expect(await store().save("Northern")).toBe(true)
    expect(saveTestQuote).toHaveBeenCalledWith("Northern", { ...form, sample: { t1: [{ c1: "250000" }] } }, { t1: [{ c1: "250000" }] }, priced(500).tables, "work")
  })

  it("says why a save, an open, a run, an accept, a delete or a capture did not happen", async () => {
    typeSample({ t1: [{ c1: "250000" }] })
    vi.mocked(saveTestQuote).mockRejectedValueOnce(new ApiError("HTTP 409", 409, "quote_store_invalid: locked", null, null))
    expect(await store().save("Northern")).toBe(false)
    expect(store().error).toBe("Not saved: quote_store_invalid: locked")

    vi.mocked(openTestQuote).mockRejectedValueOnce(new ApiError("HTTP 409", 409, "quote_not_found: gone", null, null))
    await store().openQuote("a")
    expect(store().error).toBe("quote_not_found: gone")

    useWorkbenchPricingStore.setState({ snapshot: null })
    await store().run()
    expect(store().error).toBe("Open the workbench's sheets to price test quotes.")
    expect(store().running).toBe(false)

    vi.mocked(deleteTestQuote).mockRejectedValueOnce(new ApiError("HTTP 409", 409, "quote_not_found: gone", null, null))
    await store().remove("a")
    expect(store().error).toBe("Not deleted: quote_not_found: gone")

    vi.mocked(saveTestQuote).mockResolvedValueOnce({ ...written, identity_required: true })
    useWorkbenchPricingStore.setState({ snapshot: () => pricer })
    expect(await store().save("Southern")).toBe(true)
    vi.mocked(captureTestQuotes).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "disk full", null, null))
    await store().flush()
    expect(store().error).toBe("The test quotes were not captured: disk full")
    expect(store().uncaptured).toBe(true)
  })

  it("accepts nothing for a quote the run has no result for, and words a failure that is not a stale one", async () => {
    await store().accept("nowhere")
    useWorkbenchTestQuotesStore.setState({
      runForm: form,
      runs: { a: { status: "failed", number: 3, digest: "read-3", values: null, dropped: [], tables: null, changes: [], error: "x" } },
    })
    await store().accept("a")
    expect(acceptTestQuote).not.toHaveBeenCalled()

    await store().load()
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({
      number: 3, digest: "compared-3", changes: [{ path: "pricing_output.premium", kind: "changed", accepted: 1, priced: 500 }],
    })
    await store().run()
    vi.mocked(acceptTestQuote).mockRejectedValueOnce(new ApiError("HTTP 500", 500, "disk full", null, null))
    await store().accept("a")
    expect(store().error).toBe("Not accepted: disk full")
  })

  it("leaves a run forgotten part-way unfinished, its later quotes unpriced, and lists the quotes again when the panel is open", async () => {
    await store().load()
    useWorkbenchTestQuotesStore.setState({ open: true })
    vi.mocked(openTestQuote).mockImplementation(async (id) => opened(id, "1"))
    vi.mocked(compareTestQuote).mockResolvedValue({ number: 3, digest: "compared-3", changes: [] })
    let finish: () => void = () => {}
    pricer.mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve(priced(500)) }))
    const running = store().run()
    await vi.waitFor(() => expect(pricer).toHaveBeenCalledTimes(1))
    vi.mocked(fetchTestQuotes).mockClear()

    useDocumentStatusStore.setState({ executionGeneration: useDocumentStatusStore.getState().executionGeneration + 1 })
    finish()
    await running

    expect(fetchTestQuotes).toHaveBeenCalledTimes(1)
    expect(store().runs).toEqual({})
    expect(store().running).toBe(false)
    // The second quote was never opened: the run stopped where it was forgotten.
    expect(openTestQuote).toHaveBeenCalledTimes(1)
  })

  it("ticks and unticks a quote, and opens and closes the panel", () => {
    store().toggle("a")
    store().toggle("b")
    store().toggle("a")
    expect(store().selected).toEqual(["b"])
    store().setOpen(true)
    expect(store().open).toBe(true)
    store().setOpen(false)
    expect(store().open).toBe(false)
  })

  it("deletes a test quote and forgets its run", async () => {
    await store().load()
    store().toggle("a")
    useWorkbenchTestQuotesStore.setState({ runs: { a: { status: "changed", number: 1, digest: null, values: null, dropped: [], tables: null, changes: [], error: null } } })

    await store().remove("a")

    expect(deleteTestQuote).toHaveBeenCalledWith("a")
    expect(store().runs).toEqual({})
    expect(store().selected).toEqual([])
  })
})
