/**
 * Test quotes in Preview (specs/workbench): the quote on the sheets saved in full, opened
 * back onto the sheets, run one request each on a pipeline fixed when the run starts, a
 * change accepted after the revision the run read, and a quote deleted.
 */
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchWorkbenchFormTables: vi.fn(),
  fetchTestQuotes: vi.fn(),
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
  fetchWorkbenchFormTables,
  openTestQuote,
  saveTestQuote,
} from "../../api/workbench"
import type { PricedSample } from "../../utils/workbenchTables"
import { sheet } from "../../workbench/__tests__/fixtures"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGraphStore from "../useGraphStore"
import useSettingsStore from "../useSettingsStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchPreviewStore from "../useWorkbenchPreviewStore"
import useWorkbenchPricingStore, { type Pricer } from "../useWorkbenchPricingStore"
import useWorkbenchTestQuotesStore, { runIsStale } from "../useWorkbenchTestQuotesStore"

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
const pricing: SchemaTable = { id: "t2", name: "pricing_output", role: "output", rows: "one", columns: [column("c2", "premium", { type: "float" })] }
const form: FormSpec = { version: 1, name: "t", schema: { tables: [policy, pricing] }, pages: [sheet("p", "Sheet 1")], sample: {} }
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

describe("useWorkbenchTestQuotesStore", () => {
  let pricer: Mock<Pricer>
  let snapshots: number

  beforeEach(() => {
    snapshots = 0
    pricer = vi.fn<Pricer>((_workbench: WorkbenchTablesResponse) => Promise.resolve(priced(500)))
    vi.mocked(fetchWorkbenchFormTables).mockResolvedValue(workbench)
    vi.mocked(fetchTestQuotes).mockResolvedValue({
      quotes: [summary("a", "Northern"), summary("b", "Southern")],
      branch: "work",
    })
    vi.mocked(saveTestQuote).mockResolvedValue(written)
    vi.mocked(acceptTestQuote).mockResolvedValue(written)
    vi.mocked(deleteTestQuote).mockResolvedValue(written)
    useWorkbenchFormStore.setState({ form, status: "ready" })
    useWorkbenchPricingStore.setState({
      snapshot: () => {
        snapshots += 1
        return pricer
      },
    })
    useWorkbenchPreviewStore.setState({ quote: {}, checked: false, price: null, error: null, pricing: false })
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
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "250000" }] } })

    expect(await store().save("Northern")).toBe(true)

    expect(snapshots).toBe(1)
    expect(fetchWorkbenchFormTables).toHaveBeenCalledWith({ ...form, sample: { t1: [{ c1: "250000" }] } })
    // On the branch the list named as the save started.
    expect(saveTestQuote).toHaveBeenCalledWith("Northern", form, { t1: [{ c1: "250000" }] }, priced(500).tables, "work")
    expect(store().quotes.map((quote) => quote.name)).toEqual(["Northern", "Southern"])
  })

  it("refuses to save a quote Price would refuse, saying why", async () => {
    expect(await store().save("Blank")).toBe(false)
    expect(store().error).toBe("Type the quote first")

    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "-5" }] } })
    expect(await store().save("Negative")).toBe(false)
    expect(store().error).toBe("1 cell needs attention")
    expect(saveTestQuote).not.toHaveBeenCalled()
  })

  it("opens a test quote onto the sheets, naming what has no place there", async () => {
    vi.mocked(openTestQuote).mockResolvedValue(opened("a", "125000", ["policy_details.cover"]))
    useWorkbenchPreviewStore.setState({ quote: { t9: [{ x: "old" }] }, checked: true })

    await store().openQuote("a")

    expect(openTestQuote).toHaveBeenCalledWith("a", form)
    expect(useWorkbenchPreviewStore.getState()).toMatchObject({ quote: { t1: [{ c1: "125000" }] }, checked: false })
    expect(store().notice).toBe("Opened 'a'. No place on the sheets for: policy_details.cover.")
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

  it("is stale once the pipeline, the source or the form changes after the run started", async () => {
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
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "250000" }] } })
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
    useWorkbenchPreviewStore.setState({ quote: { t1: [{ c1: "250000" }] } })
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
