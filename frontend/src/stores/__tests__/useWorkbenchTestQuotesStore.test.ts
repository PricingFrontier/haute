/**
 * The test quotes' panel's store (specs/workbench, Test quotes; Control flow 37): a dirty
 * project saved before anything is priced, a save bound to the form revision saved, an Open
 * applied only while it is the last one asked and the form is the one it sent, results
 * forgotten when the branch changes, and an accept naming the revisions and the basis its
 * results were made on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("../../api/workbench", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/workbench")>()),
  fetchTestQuotes: vi.fn(),
  saveTestQuote: vi.fn(),
  openTestQuote: vi.fn(),
  runTestQuote: vi.fn(),
  acceptTestQuotes: vi.fn(),
  deleteTestQuote: vi.fn(),
}))

import type { FormSpec, WorkbenchTestQuoteRunResponse, WorkbenchTestQuoteSummary } from "../../api/types"
import {
  acceptTestQuotes,
  deleteTestQuote,
  fetchTestQuotes,
  openTestQuote,
  runTestQuote,
  saveTestQuote,
} from "../../api/workbench"
import { BLANK_THEME } from "../../utils/workbenchForm"
import { sheet } from "../../workbench/__tests__/fixtures"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGraphStore from "../useGraphStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchTestQuotesStore from "../useWorkbenchTestQuotesStore"

const form: FormSpec = {
  version: 1,
  name: "t",
  theme: { ...BLANK_THEME },
  schema: { tables: [] },
  pages: [sheet("p", "Sheet 1")],
  sample: {},
}
const summary = (file: string, problem: string | null = null): WorkbenchTestQuoteSummary => ({
  name: file.replace(/\.json$/, ""),
  file,
  revision: `rev-${file}`,
  problem,
})
const written = { git_sha: null, warnings: [], identity_required: false, uncaptured: [], quote: null }
const result = (file: string, outcome: WorkbenchTestQuoteRunResponse["outcome"]): WorkbenchTestQuoteRunResponse => ({
  name: file,
  file,
  outcome,
  moves: outcome === "moved" ? [{ table: "pricing", row: null, column: "premium", before: 100, after: 200 }] : [],
  removed: [],
  why: null,
  revision: `rev-${file}`,
  basis: "basis-1",
  branch: "work",
})

const store = useWorkbenchTestQuotesStore.getState

/** A promise and the function that settles it, for an answer kept back. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((settle) => {
    resolve = settle
  })
  return { promise, resolve }
}

describe("useWorkbenchTestQuotesStore", () => {
  let saveProject: ReturnType<typeof vi.fn<() => Promise<boolean>>>

  beforeEach(async () => {
    vi.mocked(fetchTestQuotes).mockResolvedValue({ branch: "work", quotes: [summary("a.json"), summary("b.json")] })
    vi.mocked(saveTestQuote).mockResolvedValue(written)
    vi.mocked(deleteTestQuote).mockResolvedValue(written)
    vi.mocked(runTestQuote).mockImplementation((file) => Promise.resolve(result(file, "moved")))
    vi.mocked(acceptTestQuotes).mockResolvedValue({ ...written, accepted: [summary("a.json")], refused: [] })
    useGraphStore.setState({ dirty: false })
    useWorkbenchFormStore.setState({ form, status: "ready", dirty: false, revision: "form-1", undoStack: [], redoStack: [] })
    saveProject = vi.fn(() => {
      useGraphStore.setState({ dirty: false })
      useWorkbenchFormStore.setState({ dirty: false, revision: "form-2" })
      return Promise.resolve(true)
    })
    store().forget()
    useWorkbenchTestQuotesStore.setState({ branch: null, quotes: [], ticked: [], error: null, notice: null, busy: false })
    store().setSaveProject(saveProject)
    await store().load()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it("saves a dirty project before it saves a test quote, bound to the form revision saved", async () => {
    useWorkbenchFormStore.setState({ dirty: true })

    expect(await store().save({ name: "Fleet" })).toBe(true)

    expect(saveProject).toHaveBeenCalledOnce()
    expect(saveTestQuote).toHaveBeenCalledWith({ name: "Fleet", replace: undefined, branch: "work", formRevision: "form-2" })
  })

  it("saves on the branch the panel read on, whatever the branch is now", async () => {
    vi.mocked(fetchTestQuotes).mockResolvedValue({ branch: "elsewhere", quotes: [] })

    await store().save({ name: "Fleet" })

    expect(vi.mocked(saveTestQuote).mock.calls[0][0].branch).toBe("work")
  })

  it("prices nothing when the project's save does not complete", async () => {
    useGraphStore.setState({ dirty: true })
    saveProject.mockResolvedValueOnce(false)

    await store().run()

    expect(runTestQuote).not.toHaveBeenCalled()
    expect(store().error).toMatch(/Save the project first/)
  })

  it("runs every quote it can open, one at a time, keeping each result", async () => {
    useWorkbenchTestQuotesStore.setState({ quotes: [summary("a.json"), summary("b.json"), summary("deploy.json", "it holds deploy's test quotes")] })

    await store().run()

    expect(vi.mocked(runTestQuote).mock.calls.map(([file]) => file)).toEqual(["a.json", "b.json"])
    expect(store().running).toBeNull()
    expect(Object.keys(store().runs)).toEqual(["a.json", "b.json"])
  })

  it("stops between quotes when asked, keeping what it priced", async () => {
    const first = deferred<WorkbenchTestQuoteRunResponse>()
    vi.mocked(runTestQuote).mockImplementationOnce(() => first.promise)

    const running = store().run()
    // Stop shows once the run is under way: its first quote asked for.
    await vi.waitFor(() => expect(runTestQuote).toHaveBeenCalledOnce())
    store().stop()
    first.resolve(result("a.json", "unchanged"))
    await running

    expect(runTestQuote).toHaveBeenCalledOnce()
    expect(Object.keys(store().runs)).toEqual(["a.json"])
  })

  it("accepts with the revisions and the basis the results were made on", async () => {
    await store().run()

    await store().acceptAll()

    expect(acceptTestQuotes).toHaveBeenCalledWith({
      branch: "work",
      basis: "basis-1",
      quotes: [
        { file: "a.json", revision: "rev-a.json" },
        { file: "b.json", revision: "rev-b.json" },
      ],
    })
    const accepted = store().runs["a.json"]
    expect(accepted.status === "done" && accepted.outcome).toBe("unchanged")
  })

  it("saves a dirty project before it accepts", async () => {
    await store().run()
    useGraphStore.setState({ dirty: true })

    await store().accept("a.json")

    expect(saveProject).toHaveBeenCalledOnce()
    expect(acceptTestQuotes).toHaveBeenCalledOnce()
    expect(saveProject.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(acceptTestQuotes).mock.invocationCallOrder[0])
  })

  it("names what the server did not accept", async () => {
    await store().run()
    vi.mocked(acceptTestQuotes).mockResolvedValueOnce({ ...written, accepted: [], refused: [{ file: "a.json", why: "it changed since the run" }] })

    await store().accept("a.json")

    expect(store().notice).toBe("Not accepted: a.json (it changed since the run).")
  })

  it("puts an opened quote's values in the sample's place, one undo step", async () => {
    vi.mocked(openTestQuote).mockResolvedValue({ values: { t1: [{ c1: "10" }] }, not_opened: ["policy.state"] })

    await store().openQuote("a.json")

    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "10" }] })
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
    expect(store().opened).toBe("a.json")
    expect(store().notice).toBe("Opened a.json. Not opened: policy.state.")
  })

  it("drops an Open answered after typing on the sheets", async () => {
    const answer = deferred<{ values: Record<string, never[]>; not_opened: string[] }>()
    vi.mocked(openTestQuote).mockImplementationOnce(() => answer.promise)

    const opening = store().openQuote("a.json")
    useWorkbenchFormStore.setState({ form: { ...form, sample: { t1: [{ c1: "typed" }] } } })
    answer.resolve({ values: {}, not_opened: [] })
    await opening

    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "typed" }] })
    expect(store().opened).toBeNull()
  })

  it("drops an earlier Open answered after a later one", async () => {
    const earlier = deferred<{ values: Record<string, { c1: string }[]>; not_opened: string[] }>()
    vi.mocked(openTestQuote)
      .mockImplementationOnce(() => earlier.promise)
      .mockResolvedValueOnce({ values: { t1: [{ c1: "later" }] }, not_opened: [] })

    const first = store().openQuote("a.json")
    await store().openQuote("b.json")
    earlier.resolve({ values: { t1: [{ c1: "earlier" }] }, not_opened: [] })
    await first

    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({ t1: [{ c1: "later" }] })
    expect(store().opened).toBe("b.json")
  })

  it("drops an Open and forgets results when the pipeline's document is adopted anew", async () => {
    await store().run()
    const answer = deferred<{ values: Record<string, { c1: string }[]>; not_opened: string[] }>()
    vi.mocked(openTestQuote).mockImplementationOnce(() => answer.promise)

    const opening = store().openQuote("a.json")
    useDocumentStatusStore.setState((s) => ({ executionGeneration: s.executionGeneration + 1 }))
    answer.resolve({ values: { t1: [{ c1: "other branch" }] }, not_opened: [] })
    await opening

    expect(store().runs).toEqual({})
    expect(useWorkbenchFormStore.getState().form?.sample).toEqual({})
  })

  it("forgets results when the list is read on another branch", async () => {
    await store().run()
    vi.mocked(fetchTestQuotes).mockResolvedValueOnce({ branch: "elsewhere", quotes: [summary("a.json")] })

    await store().load()

    expect(store().runs).toEqual({})
    expect(store().branch).toBe("elsewhere")
  })

  it("deletes a quote at the revision listed, on the branch listed", async () => {
    await store().remove("a.json")

    expect(deleteTestQuote).toHaveBeenCalledWith("a.json", "rev-a.json", "work")
  })
})

describe("the test quotes' calls", () => {
  const original = globalThis.fetch

  afterEach(() => {
    globalThis.fetch = original
    vi.useRealTimers()
  })

  it("take an answer that comes after 30 seconds, never giving up while it is under way", async () => {
    const api = await vi.importActual<typeof import("../../api/workbench")>("../../api/workbench")
    vi.useFakeTimers()
    const answers: Record<string, unknown> = {
      "/api/workbench/test-quotes": { git_sha: null, warnings: [], identity_required: false, uncaptured: [], quote: null },
      "/api/workbench/test-quotes/a.json/run": result("a.json", "moved"),
      "/api/workbench/test-quotes/accept": { git_sha: null, warnings: [], identity_required: false, uncaptured: [], accepted: [], refused: [] },
    }
    globalThis.fetch = vi.fn((url: string, options?: RequestInit) =>
      new Promise((resolve, reject) => {
        options?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true })
        setTimeout(() => {
          resolve(new Response(JSON.stringify(answers[url]), { status: 200, headers: { "Content-Type": "application/json" } }))
        }, 45_000)
      }),
    ) as unknown as typeof fetch

    const saving = api.saveTestQuote({ name: "Fleet", branch: "work", formRevision: "form-1" })
    const running = api.runTestQuote("a.json")
    const accepting = api.acceptTestQuotes({ branch: "work", basis: "basis-1", quotes: [{ file: "a.json", revision: "r" }] })
    await vi.advanceTimersByTimeAsync(45_000)

    await expect(saving).resolves.toMatchObject({ quote: null })
    await expect(running).resolves.toMatchObject({ outcome: "moved" })
    await expect(accepting).resolves.toMatchObject({ refused: [] })
  })
})
