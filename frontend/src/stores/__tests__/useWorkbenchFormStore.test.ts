/**
 * The workbench's form store (specs/workbench): the form read with its revision, edits
 * with undo and redo on the graph store's history rule, and saves against the revision
 * the form was read at.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FormSpec } from "../../api/types"
import { MAX_HISTORY } from "../useGraphStore"
import useToastStore from "../useToastStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchStore from "../useWorkbenchStore"

const blank: FormSpec = {
  version: 1,
  name: "motor",
  schema: { tables: [] },
  pages: [{ id: "page_1", title: "Sheet 1", widgets: [] }],
  sample: {},
}

const named = (name: string): FormSpec => ({ ...blank, name })

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

/** Answers GET with the form at `revision`, and PUT with the saved form at the next revision. */
function server(initial: { form: FormSpec; revision: string | null }) {
  const puts: Array<{ form: FormSpec; base_revision: string | null }> = []
  let saves = 0
  let answerPut: ((body: { form: FormSpec; base_revision: string | null }) => Response) | null = null
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as { form: FormSpec; base_revision: string | null }
      puts.push(body)
      if (answerPut) return answerPut(body)
      saves += 1
      return json({ form: body.form, revision: `rev-${saves}` })
    }
    return json(initial)
  })
  return {
    puts,
    refusePut: (response: () => Response) => {
      answerPut = response
    },
  }
}

function reset(): void {
  useWorkbenchFormStore.setState({
    form: null,
    revision: null,
    status: "idle",
    loadError: null,
    savedForm: "",
    dirty: false,
    undoStack: [],
    redoStack: [],
    stale: false,
    saving: false,
  })
  useWorkbenchStore.setState({ enabled: true, formPath: "forms/form.json", refreshTables: vi.fn(() => 1) })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
}

const toasts = () => useToastStore.getState().toasts.map((toast) => [toast.type, toast.text])

describe("useWorkbenchFormStore", () => {
  beforeEach(reset)

  afterEach(() => {
    vi.restoreAllMocks()
    useWorkbenchStore.setState({ enabled: false, formPath: null })
  })

  it("reads the form once, with the file's revision, through the generated contract", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState

    await store().load()
    await store().load()

    expect(store()).toMatchObject({ form: blank, revision: "rev-0", status: "ready", dirty: false, stale: false })
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1)
    expect(String(vi.mocked(fetch).mock.calls[0][0])).toContain("/api/workbench/form")
  })

  it("keeps a form that could not be read out, saying why", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "forms/form.json is not JSON: bad" }, 409))

    await useWorkbenchFormStore.getState().load()

    expect(useWorkbenchFormStore.getState()).toMatchObject({
      form: null,
      status: "failed",
      loadError: "forms/form.json is not JSON: bad",
    })
    expect(toasts()).toEqual([])
  })

  it("rejects a form that breaks the generated contract", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ form: { name: "x" }, revision: 3 }))

    await useWorkbenchFormStore.getState().load()

    expect(useWorkbenchFormStore.getState().status).toBe("failed")
    expect(useWorkbenchFormStore.getState().loadError).toContain("WorkbenchFormResponse: invalid contract")
  })

  it("records each edit for undo, tracking whether the form is as saved", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()

    store().change((form) => ({ ...form, name: "home" }))
    store().change((form) => ({ ...form, name: "home and motor" }))
    expect(store().form?.name).toBe("home and motor")
    expect(store().undoStack.map((form) => form.name)).toEqual(["motor", "home"])
    expect(store().dirty).toBe(true)

    store().undo()
    store().undo()
    expect(store().form?.name).toBe("motor")
    expect(store().dirty).toBe(false)
    expect(store().redoStack.map((form) => form.name)).toEqual(["home and motor", "home"])

    store().redo()
    expect(store().form?.name).toBe("home")
    expect(store().dirty).toBe(true)

    // An edit after an undo forgets what was undone; one that changes nothing is no edit.
    store().change((form) => ({ ...form, name: "boat" }))
    expect(store().redoStack).toEqual([])
    store().change((form) => form)
    expect(store().undoStack.map((form) => form.name)).toEqual(["motor", "home"])
  })

  it("keeps the newest MAX_HISTORY edits", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()

    for (let i = 1; i <= MAX_HISTORY + 5; i += 1) store().change((form) => ({ ...form, name: `v${i}` }))

    expect(store().undoStack).toHaveLength(MAX_HISTORY)
    expect(store().undoStack[0].name).toBe("v5")
  })

  it("saves the form against the revision it was read at, adopting the new one", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    await expect(store().save()).resolves.toBe(true)

    expect(api.puts).toEqual([{ form: named("home"), base_revision: "rev-0" }])
    expect(store()).toMatchObject({ revision: "rev-1", dirty: false, saving: false, stale: false })
    expect(toasts()).toEqual([["success", "Saved → forms/form.json"]])
    // The schema may have changed: the workbench nodes' copies follow.
    expect(useWorkbenchStore.getState().refreshTables).toHaveBeenCalledTimes(1)

    // The next save quotes the revision the last one gave.
    store().change((form) => ({ ...form, name: "boat" }))
    await store().save()
    expect(api.puts[1]).toEqual({ form: named("boat"), base_revision: "rev-1" })
    expect(store().revision).toBe("rev-2")
  })

  it("leaves edits made while a save ran unsaved", async () => {
    let finish: (response: Response) => void = () => {}
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method !== "PUT") return json({ form: blank, revision: "rev-0" })
      return new Promise<Response>((resolve) => {
        finish = resolve
      })
    })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    const saving = store().save()
    await vi.waitFor(() => expect(store().saving).toBe(true))
    store().change((form) => ({ ...form, name: "home and boat" }))
    finish(json({ form: named("home"), revision: "rev-1" }))

    await expect(saving).resolves.toBe(true)
    expect(store()).toMatchObject({ revision: "rev-1", dirty: true, form: named("home and boat") })
  })

  it("refuses to overwrite a form that changed on disk, keeping the edits until a reload", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    api.refusePut(() =>
      json({ detail: "stale_document_revision: The workbench's form changed on disk after the workbench read it. Reload the workbench before saving." }, 409),
    )
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    await expect(store().save()).resolves.toBe(false)

    expect(store()).toMatchObject({ stale: true, dirty: true, revision: "rev-0", form: named("home") })
    expect(toasts()).toEqual([["error", "Save rejected: the form changed on disk. Reload the workbench first."]])
    expect(useWorkbenchStore.getState().refreshTables).not.toHaveBeenCalled()

    vi.mocked(fetch).mockResolvedValue(json({ form: named("renamed on disk"), revision: "rev-9" }))
    await store().reload()
    expect(store()).toMatchObject({
      stale: false,
      dirty: false,
      revision: "rev-9",
      form: named("renamed on disk"),
      undoStack: [],
      redoStack: [],
    })
  })

  it("says why any other save failed", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    api.refusePut(() => json({ detail: "[workbench].enabled must be true or false" }, 409))
    const store = useWorkbenchFormStore.getState
    await store().load()

    await expect(store().save()).resolves.toBe(false)

    expect(store().stale).toBe(false)
    expect(toasts()).toEqual([["error", "Could not save the workbench's form: [workbench].enabled must be true or false"]])
  })

  it("saves nothing before the form is read", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")

    await expect(useWorkbenchFormStore.getState().save()).resolves.toBe(false)

    expect(fetchSpy).not.toHaveBeenCalled()
  })
})
