import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import useWorkbenchStore from "../useWorkbenchStore"
import useGraphStore from "../useGraphStore"
import useNodeDataStore from "../useNodeDataStore"
import useToastStore from "../useToastStore"

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  )
}

describe("useWorkbenchStore", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: false, formPath: null, activeView: "pipeline", tables: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("reads whether the project's workbench is enabled, and where its form is, from GET /api/workbench", async () => {
    const fetchSpy = respond({ enabled: true, form: "rating/workbench" })

    await useWorkbenchStore.getState().load()

    expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/workbench")
    expect(useWorkbenchStore.getState()).toMatchObject({ enabled: true, formPath: "rating/workbench" })
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("shows the workbench's view only while the workbench is enabled", () => {
    expect(useWorkbenchStore.getState().activeView).toBe("pipeline")
    expect(() => useWorkbenchStore.getState().showView("workbench")).toThrow("The workbench is not enabled")

    useWorkbenchStore.setState({ enabled: true })
    useWorkbenchStore.getState().showView("workbench")
    expect(useWorkbenchStore.getState().activeView).toBe("workbench")
    useWorkbenchStore.getState().showView("pipeline")
    expect(useWorkbenchStore.getState().activeView).toBe("pipeline")
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
  const tables = (name: string) => [{ name, rows: "one" as const, columns: [] }]

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
    useWorkbenchStore.setState({ enabled: true, tables: null, storeColumns: {} })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("settles awaitTables with whether the newest fetch published, a superseded fetch taking the newer one's outcome", async () => {
    await expect(useWorkbenchStore.getState().awaitTables()).resolves.toBe(true)
    const server = heldResponses()
    useWorkbenchStore.getState().refreshTables()
    const first = useWorkbenchStore.getState().awaitTables()
    useWorkbenchStore.getState().refreshTables()
    const second = useWorkbenchStore.getState().awaitTables()
    let settled = false
    void second.then(() => {
      settled = true
    })
    await Promise.resolve()
    expect(settled).toBe(false)

    server.answer(1, { tables: tables("policy"), sample: {}, response_tables: [], store_columns: {} })
    await expect(second).resolves.toBe(true)
    server.answer(0, { tables: tables("old"), sample: {}, response_tables: [], store_columns: {} })
    await expect(first).resolves.toBe(true)
    expect(useWorkbenchStore.getState().tables?.tables).toEqual(tables("policy"))

    useWorkbenchStore.getState().refreshTables()
    server.answer(2, { detail: "boom" }, 409)
    await expect(useWorkbenchStore.getState().awaitTables()).resolves.toBe(false)
    expect(useWorkbenchStore.getState().tables?.tables).toEqual(tables("policy"))
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
    held.answer(1, { tables: tables("newer"), sample: {}, response_tables: [], store_columns: {} })
    await vi.waitFor(() => expect(store().tables?.fetch).toBe(newer))
    held.answer(0, { tables: tables("older"), sample: {}, response_tables: [], store_columns: {} })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(older).toBeLessThan(newer!)
    expect(store().tables).toEqual({ tables: tables("newer"), sample: {}, responseTables: [], fetch: newer })

    const failing = store().refreshTables()
    const newest = store().refreshTables()
    held.answer(3, { tables: tables("newest"), sample: {}, response_tables: [], store_columns: {} })
    await vi.waitFor(() => expect(store().tables?.fetch).toBe(newest))
    held.answer(2, { detail: "v2 table label 'class' must be an ASCII Python identifier", type: "ApiInputSchemaError" }, 422)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(failing).toBeLessThan(newest!)
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("keeps the sample quote, the response tables and the columns the quote store keeps a fetch brings beside the tables", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState
    const sample = { policy: { state: "NY" } }
    const kept = { quote_details: [{ name: "quote_version", kind: "int" }, { name: "saved_at", kind: "datetime" }], t_items: [{ name: "quote_row", kind: "int" }] }

    const fetch = store().refreshTables()
    held.answer(0, { tables: tables("policy"), sample, response_tables: tables("pricing_output"), store_columns: kept })

    await vi.waitFor(() => expect(store().tables?.fetch).toBe(fetch))
    expect(store().tables?.sample).toEqual(sample)
    expect(store().tables?.responseTables).toEqual(tables("pricing_output"))
    expect(store().storeColumns).toEqual(kept)
  })

  it("asks every preview and reader of node data again after a fetch that changes what the workbench nodes are given, and not after one that brings the same", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState
    const epoch = () => useNodeDataStore.getState().epoch
    const structure = () => useGraphStore.getState().structuralVersion
    const fetchedAs = async (index: number, body: unknown) => {
      const fetch = store().refreshTables()
      held.answer(index, body)
      await vi.waitFor(() => expect(store().tables?.fetch).toBe(fetch))
    }
    const columns = [{ name: "state", type: "str" }, { name: "exposure", type: "float" }]
    await fetchedAs(0, { tables: [{ name: "policy", rows: "one", columns }], sample: {}, response_tables: [], store_columns: {} })
    const first = epoch()
    const structural = structure()

    // The same, its keys written in another order.
    await fetchedAs(1, { store_columns: {}, response_tables: [], sample: {}, tables: [{ columns, rows: "one", name: "policy" }] })
    expect(epoch()).toBe(first)
    expect(structure()).toBe(structural)

    await fetchedAs(2, { tables: [{ name: "policy", rows: "one", columns }], sample: { policy: { state: "NY" } }, response_tables: [], store_columns: {} })
    expect(epoch()).toBe(first + 1)
    await fetchedAs(3, { tables: [{ name: "policy", rows: "one", columns }], sample: { policy: { state: "NY" } }, response_tables: tables("premiums"), store_columns: {} })
    expect(epoch()).toBe(first + 2)
    await fetchedAs(4, { tables: [{ name: "policy", rows: "many", columns }], sample: { policy: { state: "NY" } }, response_tables: tables("premiums"), store_columns: {} })
    expect(epoch()).toBe(first + 3)
    // Each change is the graph's structure as every preview, recorded column and job reads it.
    expect(structure()).toBe(structural + 3)
  })

  it("keeps the tables it has when the newest fetch fails, and says so once", async () => {
    const held = heldResponses()
    const store = useWorkbenchStore.getState
    store().refreshTables()
    held.answer(0, { tables: tables("policy"), sample: {}, response_tables: [], store_columns: {} })
    await vi.waitFor(() => expect(store().tables).not.toBeNull())
    const kept = store().tables

    store().refreshTables()
    const missing = "rating/workbench does not exist: restore it from version history (a project starts its workbench with haute init --workbench)"
    held.answer(1, { detail: missing }, 409)

    await vi.waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1))
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      type: "error",
      text: `Could not fetch the workbench's tables: ${missing}`,
    })
    expect(store().tables).toBe(kept)
  })
})
