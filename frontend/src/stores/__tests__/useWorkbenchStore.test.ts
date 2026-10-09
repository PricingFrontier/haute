import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import useWorkbenchStore from "../useWorkbenchStore"
import useToastStore from "../useToastStore"

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  )
}

describe("useWorkbenchStore", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: false, tables: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("reads whether the project's workbench is enabled from GET /api/workbench", async () => {
    const fetchSpy = respond({ enabled: true, form: "forms/form.json" })

    await useWorkbenchStore.getState().load()

    expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/workbench")
    expect(useWorkbenchStore.getState().enabled).toBe(true)
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("leaves the store untouched while the workbench is not enabled, so the editor does not re-render", async () => {
    respond({ enabled: false, form: null })
    const listener = vi.fn()
    const unsubscribe = useWorkbenchStore.subscribe(listener)

    await useWorkbenchStore.getState().load()
    unsubscribe()

    expect(listener).not.toHaveBeenCalled()
    expect(useWorkbenchStore.getState().enabled).toBe(false)
  })

  it("reports a failed status as one error toast and leaves the workbench not enabled", async () => {
    respond({ detail: "[workbench].enabled must be true or false" }, 409)

    await useWorkbenchStore.getState().load()

    expect(useWorkbenchStore.getState().enabled).toBe(false)
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Could not read the workbench's status: [workbench].enabled must be true or false",
      }),
    ])
  })

  it("rejects a status that breaks the generated contract", async () => {
    respond({ enabled: "yes", form: null })

    await useWorkbenchStore.getState().load()

    expect(useWorkbenchStore.getState().enabled).toBe(false)
    expect(useToastStore.getState().toasts[0].text).toContain("WorkbenchStatusResponse: invalid contract")
  })
})

describe("refreshTables", () => {
  const tables = (label: string) => [{ path: "$[:]", label, emit: true, row_id_column: null, columns: [] }]

  /** Answers each fetch when the test says so, in whatever order it says. */
  function heldResponses() {
    const pending: Array<(response: Response) => void> = []
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () => new Promise<Response>((resolve) => pending.push(resolve)),
    )
    const json = (body: unknown, status = 200) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })
    return {
      answer: (index: number, body: unknown, status = 200) => pending[index](json(body, status)),
      count: () => pending.length,
    }
  }

  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true, tables: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("fetches nothing while the workbench is not enabled", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    useWorkbenchStore.setState({ enabled: false })

    expect(useWorkbenchStore.getState().refreshTables()).toBeNull()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("publishes the newest fetch, dropping an older one that settles later without a toast", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState

    const older = store().refreshTables()
    const newer = store().refreshTables()
    held.answer(1, { tables: tables("newer"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().tables?.fetch).toBe(newer))
    held.answer(0, { tables: tables("older"), sample: {}, response_tables: [] })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(older).toBeLessThan(newer!)
    expect(store().tables).toEqual({ tables: tables("newer"), sample: {}, responseTables: [], fetch: newer })

    const failing = store().refreshTables()
    const newest = store().refreshTables()
    held.answer(3, { tables: tables("newest"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().tables?.fetch).toBe(newest))
    held.answer(2, { detail: "v2 table label 'class' must be an ASCII Python identifier", type: "ApiInputSchemaError" }, 422)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(failing).toBeLessThan(newest!)
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("keeps the sample quote and the response tables a fetch brings beside the tables", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState
    const sample = { policy: { state: "NY" } }

    const fetch = store().refreshTables()
    held.answer(0, { tables: tables("policy"), sample, response_tables: tables("pricing_output") })

    await vi.waitFor(() => expect(store().tables?.fetch).toBe(fetch))
    expect(store().tables?.sample).toEqual(sample)
    expect(store().tables?.responseTables).toEqual(tables("pricing_output"))
  })

  it("keeps the tables it has when the newest fetch fails, and says so once", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState
    store().refreshTables()
    held.answer(0, { tables: tables("policy"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().tables).not.toBeNull())
    const kept = store().tables

    store().refreshTables()
    const missing = "forms/form.json does not exist: create it, point [workbench].form at the form, or set [workbench] enabled = false in haute.toml."
    held.answer(1, { detail: missing }, 409)

    await vi.waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1))
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      type: "error",
      text: `Could not fetch the workbench's tables: ${missing}`,
    })
    expect(store().tables).toBe(kept)
  })
})
