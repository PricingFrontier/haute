import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import useExtensionsStore, { PIPELINE_VIEW } from "../useExtensionsStore"
import useToastStore from "../useToastStore"

const obverse = {
  name: "obverse",
  label: "Obverse",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
  quote_tables: false,
  response_tables: false,
}

function respond(body: unknown, status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }),
  )
}

describe("useExtensionsStore", () => {
  beforeEach(() => {
    useExtensionsStore.setState({
      extensions: [],
      activeView: PIPELINE_VIEW,
      toolbarSlot: null,
      viewSave: null,
      quoteTables: null,
    })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("loads the installed extensions from GET /api/extensions", async () => {
    const fetchSpy = respond({ extensions: [obverse] })

    await useExtensionsStore.getState().load()

    expect(String(fetchSpy.mock.calls[0][0])).toContain("/api/extensions")
    expect(useExtensionsStore.getState().extensions).toEqual([obverse])
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("leaves the store untouched when nothing is installed, so the editor does not re-render", async () => {
    respond({ extensions: [] })
    const listener = vi.fn()
    const unsubscribe = useExtensionsStore.subscribe(listener)

    await useExtensionsStore.getState().load()
    unsubscribe()

    expect(listener).not.toHaveBeenCalled()
    expect(useExtensionsStore.getState().extensions).toEqual([])
  })

  it("reports a failed list as one error toast and keeps the list empty", async () => {
    respond({ detail: "No such route: /api/extensions" }, 404)

    await useExtensionsStore.getState().load()

    expect(useExtensionsStore.getState().extensions).toEqual([])
    expect(useToastStore.getState().toasts).toEqual([
      expect.objectContaining({
        type: "error",
        text: "Could not list Haute extensions: No such route: /api/extensions",
      }),
    ])
  })

  it("rejects a list that breaks the generated contract", async () => {
    respond({ extensions: [{ ...obverse, ready: "yes" }] })

    await useExtensionsStore.getState().load()

    expect(useExtensionsStore.getState().extensions).toEqual([])
    expect(useToastStore.getState().toasts[0].text).toContain("ExtensionsResponse: invalid contract")
  })

  it("shows an installed extension's view and the pipeline again, and rejects anything else", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    const { showView } = useExtensionsStore.getState()

    showView("obverse")
    expect(useExtensionsStore.getState().activeView).toBe("obverse")
    showView(PIPELINE_VIEW)
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)

    expect(() => showView("forms")).toThrow('No installed extension is called "forms"')
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)
  })

  it("holds the toolbar element the active extension renders its controls into", () => {
    const slot = document.createElement("div")

    useExtensionsStore.getState().setToolbarSlot(slot)
    expect(useExtensionsStore.getState().toolbarSlot).toBe(slot)
    useExtensionsStore.getState().setToolbarSlot(null)
    expect(useExtensionsStore.getState().toolbarSlot).toBeNull()
  })

  it("lets an extension's view take the toolbar's Save, and says how it went", async () => {
    const store = useExtensionsStore.getState
    store().setViewSave(vi.fn().mockResolvedValue(true))
    expect(store().saveView()).toBe(false) // the pipeline editor shows: Save saves the pipeline

    useExtensionsStore.setState({ extensions: [obverse], activeView: "obverse" })
    expect(store().saveView()).toBe(true)
    await vi.waitFor(() => expect(useToastStore.getState().toasts.map((t) => t.text)).toEqual(["Saved Obverse"]))

    store().setViewSave(vi.fn().mockResolvedValue(false))
    expect(store().saveView()).toBe(true)
    await vi.waitFor(() =>
      expect(useToastStore.getState().toasts.at(-1)).toMatchObject({
        type: "error",
        text: "Obverse could not be saved; its toolbar says why",
      }),
    )

    store().setViewSave(null)
    expect(store().saveView()).toBe(false) // a view that can't save leaves Save to the pipeline
  })
})

describe("refreshQuoteTables", () => {
  const workbench = { ...obverse, label: "Workbench", quote_tables: true }
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
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW, quoteTables: null })
    useToastStore.setState({ toasts: [], _toastCounter: 0 })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("fetches nothing when no installed extension supplies the Quote Input's tables", () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")
    useExtensionsStore.setState({ extensions: [obverse] })

    expect(useExtensionsStore.getState().refreshQuoteTables()).toBeNull()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("publishes the newest fetch, dropping an older one that settles later without a toast", async () => {
    const held = heldResponses()
    useExtensionsStore.setState({ extensions: [workbench] })
    const store = useExtensionsStore.getState

    const older = store().refreshQuoteTables()
    const newer = store().refreshQuoteTables()
    held.answer(1, { extension: "obverse", tables: tables("newer"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().quoteTables?.fetch).toBe(newer))
    held.answer(0, { extension: "obverse", tables: tables("older"), sample: {}, response_tables: [] })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(older).toBeLessThan(newer!)
    expect(store().quoteTables).toEqual({
      extension: "obverse",
      tables: tables("newer"),
      sample: {},
      responseTables: [],
      fetch: newer,
    })

    const failing = store().refreshQuoteTables()
    const newest = store().refreshQuoteTables()
    held.answer(3, { extension: "obverse", tables: tables("newest"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().quoteTables?.fetch).toBe(newest))
    held.answer(2, { detail: "v2 table label 'class' must be an ASCII Python identifier", type: "ApiInputSchemaError" }, 422)
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(failing).toBeLessThan(newest!)
    expect(useToastStore.getState().toasts).toEqual([])
  })

  it("keeps the sample quote a fetch brings beside the tables", async () => {
    const held = heldResponses()
    useExtensionsStore.setState({ extensions: [workbench] })
    const store = useExtensionsStore.getState
    const sample = { policy: { state: "NY" } }

    const fetch = store().refreshQuoteTables()
    held.answer(0, { extension: "obverse", tables: tables("policy"), sample, response_tables: [] })

    await vi.waitFor(() => expect(store().quoteTables?.fetch).toBe(fetch))
    expect(store().quoteTables?.sample).toEqual(sample)
  })

  it("keeps the tables it has when the newest fetch fails, and says so once", async () => {
    const held = heldResponses()
    useExtensionsStore.setState({ extensions: [workbench] })
    const store = useExtensionsStore.getState
    store().refreshQuoteTables()
    held.answer(0, { extension: "obverse", tables: tables("policy"), sample: {}, response_tables: [] })
    await vi.waitFor(() => expect(store().quoteTables).not.toBeNull())
    const kept = store().quoteTables

    store().refreshQuoteTables()
    held.answer(1, { detail: "v2 table label 'class' must be an ASCII Python identifier", type: "ApiInputSchemaError" }, 422)

    await vi.waitFor(() => expect(useToastStore.getState().toasts).toHaveLength(1))
    expect(useToastStore.getState().toasts[0]).toMatchObject({
      type: "error",
      text: "Could not fetch the Quote Input's tables from Workbench: v2 table label 'class' must be an ASCII Python identifier",
    })
    expect(store().quoteTables).toBe(kept)
  })

  it("fetches again when the pipeline view shows after the extension's", () => {
    const held = heldResponses()
    useExtensionsStore.setState({ extensions: [workbench] })
    const { showView } = useExtensionsStore.getState()

    showView("obverse")
    expect(held.count()).toBe(0)
    showView(PIPELINE_VIEW)
    expect(held.count()).toBe(1)
    showView(PIPELINE_VIEW)
    expect(held.count()).toBe(1)
  })
})
