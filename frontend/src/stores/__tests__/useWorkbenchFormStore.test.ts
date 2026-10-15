/**
 * The workbench's form store (specs/workbench): the form read with its revision, edits
 * with undo and redo on the graph store's history rule, saves against the revision the
 * form was read at with their capture reported, the project save's flush, and the file
 * read again when the pipeline's document is adopted anew.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FormSpec } from "../../api/types"
import { makeGitWorkingBranch } from "../../test-utils/factories"
import { addWidget, BLANK_THEME, createWidget, withSampleCell, withSampleRows } from "../../utils/workbenchForm"
import { resetIdentityPromptForTests } from "../identityPrompt"
import type { SaveCapture } from "../saveCapture"
import useDocumentStatusStore from "../useDocumentStatusStore"
import useGitStore from "../useGitStore"
import { MAX_HISTORY } from "../useGraphStore"
import useToastStore from "../useToastStore"
import useWorkbenchFormStore from "../useWorkbenchFormStore"
import useWorkbenchStore from "../useWorkbenchStore"

const blank: FormSpec = {
  version: 1,
  name: "motor",
  theme: { ...BLANK_THEME },
  schema: { tables: [] },
  pages: [
    {
      id: "page_1",
      title: "Sheet 1",
      width: 1280,
      show_when: { match: "all", conditions: [] },
      sections: [{ id: "section_1", title: "Section 1", height: 560, show_when: { match: "all", conditions: [] }, widgets: [] }],
    },
  ],
  sample: {},
}

const named = (name: string): FormSpec => ({ ...blank, name })

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } })

/** A save's answer: the form at its new revision, captured as `capture` says (nowhere by default). */
const saved = (form: FormSpec, revision: string | null, capture: SaveCapture = {}) =>
  json({ form, revision, warnings: [], git_sha: null, identity_required: false, uncaptured: [], ...capture })

/**
 * Answers GET with the file as it is, and PUT with the saved form at the next revision,
 * which the file then holds.
 */
function server(initial: { form: FormSpec; revision: string | null }) {
  let file = initial
  const puts: Array<{ form: FormSpec; base_revision: string | null }> = []
  let saves = 0
  let answerPut: ((body: { form: FormSpec; base_revision: string | null }) => Response) | null = null
  let capture: SaveCapture = {}
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)) as { form: FormSpec; base_revision: string | null }
      puts.push(body)
      if (answerPut) return answerPut(body)
      saves += 1
      file = { form: body.form, revision: `rev-${saves}` }
      return saved(file.form, file.revision, capture)
    }
    return json(file)
  })
  return {
    puts,
    refusePut: (response: () => Response) => {
      answerPut = response
    },
    /** What the saves from now on answer about their capture. */
    captureWith: (fields: SaveCapture) => {
      capture = fields
    },
    /** The file changed behind the view's back: by hand, or by a branch switch. */
    writeFile: (form: FormSpec, revision: string) => {
      file = { form, revision }
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
    reads: 0,
  })
  useWorkbenchStore.setState({ enabled: true, formPath: "forms/form.json", formDirty: false, refreshTables: vi.fn(() => 1) })
  useGitStore.setState({ status: makeGitWorkingBranch({ state: "ready", last_save_sha: null }), modal: null, historyNonce: 0, uncaptured: [] })
  useToastStore.setState({ toasts: [], _toastCounter: 0 })
  resetIdentityPromptForTests()
}

const toasts = () => useToastStore.getState().toasts.map((toast) => [toast.type, toast.text])

describe("useWorkbenchFormStore", () => {
  beforeEach(reset)

  afterEach(() => {
    vi.restoreAllMocks()
    useWorkbenchStore.setState({ enabled: false, formPath: null })
    useGitStore.setState({ status: null, modal: null })
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

  it("records a burst as one undo step: a snapshot, then raw updates without history", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()

    store().pushSnapshot()
    store().setFormRaw({ ...blank, name: "a" })
    store().setFormRaw({ ...blank, name: "ab" })

    expect(store()).toMatchObject({ form: named("ab"), dirty: true })
    expect(store().undoStack.map((form) => form.name)).toEqual(["motor"])
    store().undo()
    expect(store()).toMatchObject({ form: blank, dirty: false })
    expect(store().redoStack.map((form) => form.name)).toEqual(["ab"])
  })

  it("records a gesture once it is done, as one undo step, and nothing for one that leaves the form as it found it, keeping redo", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "kept" }))
    store().undo()

    // Out and back: nothing recorded, so the redo stands.
    const pressed = store().form!
    const stop = vi.fn()
    store().beginGesture(pressed, stop)
    store().setFormRaw({ ...pressed, name: "a" })
    store().setFormRaw(structuredClone(pressed))
    store().endGesture()
    expect(stop).toHaveBeenCalledTimes(1)
    expect(store()).toMatchObject({ undoStack: [], dirty: false })
    expect(store().redoStack.map((form) => form.name)).toEqual(["kept"])

    store().beginGesture(pressed, stop)
    store().setFormRaw({ ...pressed, name: "ab" })
    // Whether it is unsaved is worked out as the gesture ends, not at each step.
    expect(store().dirty).toBe(false)
    store().endGesture()
    expect(store()).toMatchObject({ form: named("ab"), dirty: true, redoStack: [] })
    expect(store().undoStack).toEqual([pressed])
    // Ended already: ending again records nothing more.
    store().endGesture()
    expect(store().undoStack).toEqual([pressed])
  })

  it("finishes a gesture under way before an edit, an undo or a redo, so its steps never write over them and its undo step comes first", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "titled" }))

    // An undo while a gesture is under way: the gesture is recorded and stopped, then undone.
    const stop = vi.fn()
    store().beginGesture(store().form!, stop)
    store().setFormRaw({ ...store().form!, name: "dragged" })
    store().undo()
    expect(stop).toHaveBeenCalledTimes(1)
    expect(store().form?.name).toBe("titled")
    store().endGesture()
    store().undo()
    expect(store().form?.name).toBe("motor")
    store().redo()
    store().redo()
    expect(store().form?.name).toBe("dragged")

    // An edit while one is under way comes after it, and nothing is recorded for it twice.
    const pressed = store().form!
    store().beginGesture(pressed, stop)
    store().setFormRaw({ ...pressed, name: "moved" })
    store().change((form) => ({ ...form, name: `${form.name} and named` }))
    expect(stop).toHaveBeenCalledTimes(2)
    expect(store().undoStack.slice(-2).map((form) => form.name)).toEqual(["dragged", "moved"])
    store().endGesture()
    expect(store().form?.name).toBe("moved and named")
    expect(store().undoStack.slice(-2).map((form) => form.name)).toEqual(["dragged", "moved"])
  })

  it("keeps the sample typed while building as part of the form: unsaved until saved, undone like any edit", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()

    store().change((form) => withSampleCell(form, "t1", 1, "c1", "100"))
    expect(store().form?.sample).toEqual({ t1: [{}, { c1: "100" }] })
    expect(store().dirty).toBe(true)

    store().change((form) => withSampleRows(form, { t1: [{ c1: "A" }, { c1: "B" }], t2: [{ c2: "1" }, { c2: "2" }] }))
    store().undo()
    expect(store().form?.sample).toEqual({ t1: [{}, { c1: "100" }] })
    store().undo()
    expect(store().form?.sample).toEqual({})
    expect(store().dirty).toBe(false)
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
    // The schema may have changed, but the save fetches no tables itself: the project
    // save it is the form step of fetches them afresh next, and a second fetch would
    // publish a second update of each workbench node.
    expect(useWorkbenchStore.getState().refreshTables).not.toHaveBeenCalled()

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
    finish(saved(named("home"), "rev-1"))

    await expect(saving).resolves.toBe(true)
    expect(store()).toMatchObject({ revision: "rev-1", dirty: true, form: named("home and boat") })
  })

  it("reports what the save's capture gave: the ledger commit, each warning, and an identity to ask for", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    api.captureWith({ git_sha: "ledger-1", warnings: ["Changes saved; version capture failed: no such branch"] })
    await store().save()

    expect(useGitStore.getState().status?.last_save_sha).toBe("ledger-1")
    expect(useGitStore.getState().historyNonce).toBe(1)
    expect(toasts()).toEqual([
      ["success", "Saved → forms/form.json"],
      ["warning", "Changes saved; version capture failed: no such branch"],
    ])
    expect(useGitStore.getState().uncaptured).toEqual([])

    // Skipped for want of a git identity: the prompt opens, and the file is kept for a
    // capture to take; the form itself is saved, so a flush saves nothing again.
    api.captureWith({ identity_required: true, uncaptured: ["forms/form.json"] })
    store().change((form) => ({ ...form, name: "boat" }))
    await store().save()
    expect(useGitStore.getState().modal).toBe("identity")
    expect(useGitStore.getState().uncaptured).toEqual(["forms/form.json"])
    expect(store().dirty).toBe(false)
    await expect(store().flush()).resolves.toBe(true)
    expect(api.puts).toHaveLength(2)
  })

  it("mirrors whether the form holds unsaved edits onto the workbench store, for the editor's guards", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    expect(useWorkbenchStore.getState().formDirty).toBe(false)

    store().change((form) => ({ ...form, name: "home" }))
    expect(useWorkbenchStore.getState().formDirty).toBe(true)
    await store().save()
    expect(useWorkbenchStore.getState().formDirty).toBe(false)
    store().change((form) => ({ ...form, name: "boat" }))
    store().undo()
    expect(useWorkbenchStore.getState().formDirty).toBe(false)
  })

  it("flushes only what there is to save: unsaved edits", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState

    // Not read, or as saved: nothing to save, so no request.
    await expect(store().flush()).resolves.toBe(true)
    await store().load()
    await expect(store().flush()).resolves.toBe(true)
    expect(api.puts).toEqual([])

    store().change((form) => ({ ...form, name: "home" }))
    await expect(store().flush()).resolves.toBe(true)
    expect(api.puts).toEqual([{ form: named("home"), base_revision: "rev-0" }])
    expect(store().dirty).toBe(false)

    // A refused save refuses the flush: a milestone does not go on without it.
    api.refusePut(() => json({ detail: "stale_document_revision: The workbench's form changed on disk after the workbench read it." }, 409))
    store().change((form) => ({ ...form, name: "boat" }))
    await expect(store().flush()).resolves.toBe(false)
    expect(store()).toMatchObject({ stale: true, dirty: true })
  })

  it("reads the file again when the pipeline's document is adopted anew: adopting a changed file while clean, stale while dirty", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))
    store().undo()

    // The same revision: nothing changes, history included.
    await store().sync()
    expect(store()).toMatchObject({ form: blank, revision: "rev-0" })
    expect(store().redoStack).toHaveLength(1)

    // Changed on disk while the form has no unsaved edits: adopted, history dropped.
    api.writeFile(named("other branch"), "rev-7")
    useDocumentStatusStore.getState().reset()
    await vi.waitFor(() => expect(store().revision).toBe("rev-7"))
    expect(store()).toMatchObject({ form: named("other branch"), dirty: false, stale: false, undoStack: [], redoStack: [] })

    // Changed on disk while the form has unsaved edits: kept, and stale until a reload.
    store().change((form) => ({ ...form, name: "mine" }))
    api.writeFile(named("theirs"), "rev-8")
    await store().sync()
    expect(store()).toMatchObject({ form: named("mine"), revision: "rev-7", dirty: true, stale: true })

    // Not read: no request.
    const fetches = vi.mocked(fetch).mock.calls.length
    useWorkbenchFormStore.setState({ status: "idle" })
    await store().sync()
    expect(vi.mocked(fetch).mock.calls).toHaveLength(fetches)
  })

  it("syncs after a save in flight, so the file the save just wrote is never read as another's", async () => {
    let finish: (response: Response) => void = () => {}
    let gets = 0
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method !== "PUT") {
        gets += 1
        return gets === 1 ? json({ form: blank, revision: "rev-0" }) : json({ form: named("home"), revision: "rev-1" })
      }
      return new Promise<Response>((resolve) => {
        finish = resolve
      })
    })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    const saving = store().save()
    await vi.waitFor(() => expect(store().saving).toBe(true))
    const synced = store().sync()
    expect(gets).toBe(1)
    finish(saved(named("home"), "rev-1"))
    await saving
    await synced

    expect(gets).toBe(2)
    expect(store()).toMatchObject({ form: named("home"), revision: "rev-1", dirty: false, stale: false })
  })

  it("reads the file again after a read in flight when a document is adopted meanwhile, never ending on the old branch's form", async () => {
    let finish: (response: Response) => void = () => {}
    let gets = 0
    vi.spyOn(globalThis, "fetch").mockImplementation(async () => {
      gets += 1
      if (gets === 1) {
        return new Promise<Response>((resolve) => {
          finish = resolve
        })
      }
      return json({ form: named("other branch"), revision: "rev-7" })
    })
    const store = useWorkbenchFormStore.getState

    const loading = store().load()
    expect(store().status).toBe("loading")
    await vi.waitFor(() => expect(gets).toBe(1))
    // A branch switched while the file was being read: the sync waits its turn.
    useDocumentStatusStore.getState().reset()
    finish(json({ form: blank, revision: "rev-0" }))
    await loading

    await vi.waitFor(() => expect(store().revision).toBe("rev-7"))
    expect(store()).toMatchObject({ form: named("other branch"), status: "ready", dirty: false, stale: false })
    expect(gets).toBe(2)
  })

  it("is as saved after a save whatever order the file writes a component's keys in", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    // The server writes a component's fields in its own order.
    api.refusePut(() => {
      const body = JSON.parse(String(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.body)) as { form: FormSpec }
      const page = body.form.pages[0]
      const sections = page.sections.map((section) => ({
        ...section,
        widgets: section.widgets.map((widget) => Object.fromEntries(Object.entries(widget).reverse())) as typeof section.widgets,
      }))
      return saved({ ...body.form, pages: [{ ...page, sections }] }, "rev-1")
    })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => addWidget(form, "section_1", createWidget("collection", { x: 0, y: 0, w: 720, h: 120 })))
    expect(store().dirty).toBe(true)

    await expect(store().save()).resolves.toBe(true)

    expect(store()).toMatchObject({ revision: "rev-1", dirty: false })
    expect(useWorkbenchStore.getState().formDirty).toBe(false)
    // Undoing the edit makes the form differ from the file again.
    store().undo()
    expect(store().dirty).toBe(true)
  })

  it("says when the file could not be read again, leaving the form as it is", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    vi.mocked(fetch).mockResolvedValue(json({ detail: "forms/form.json is not JSON: bad" }, 409))

    await store().sync()

    expect(store()).toMatchObject({ form: blank, revision: "rev-0", status: "ready", stale: false })
    expect(toasts()).toEqual([["error", "Could not read forms/form.json again: forms/form.json is not JSON: bad"]])
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
    expect(toasts()).toEqual([["error", "Save rejected: forms/form.json changed on disk. Reload the workbench first."]])
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

  it("takes the file's revision when a stale refusal follows a save whose answer was lost and the file holds what it sent", async () => {
    // The server writes what it is sent at the revision it is given, and refuses another.
    let file: { form: FormSpec; revision: string } = { form: blank, revision: "rev-0" }
    let loseTheAnswer = false
    const puts: Array<string | null> = []
    vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
      if (init?.method !== "PUT") return json(file)
      const body = JSON.parse(String(init.body)) as { form: FormSpec; base_revision: string | null }
      puts.push(body.base_revision)
      if (body.base_revision !== file.revision) {
        return json({ detail: "stale_document_revision: forms/form.json changed on disk after the workbench read it." }, 409)
      }
      file = { form: body.form, revision: `rev-${puts.length}` }
      if (loseTheAnswer) {
        loseTheAnswer = false
        throw new TypeError("Failed to fetch")
      }
      return saved(file.form, file.revision)
    })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))
    loseTheAnswer = true
    await expect(store().save()).resolves.toBe(false)
    expect(store().revision).toBe("rev-0")

    // Typing since, then Save: refused as stale, the file read, and since it holds what the
    // lost save sent, its revision is the base and the save goes through.
    store().change((form) => ({ ...form, name: "boat" }))
    await expect(store().save()).resolves.toBe(true)

    expect(puts).toEqual(["rev-0", "rev-0", "rev-1"])
    expect(store()).toMatchObject({ revision: "rev-3", stale: false, dirty: false, form: named("boat") })
    expect(file.form).toEqual(named("boat"))
  })

  it("keeps a stale refusal stale when the file holds something else than a lost save sent", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))
    api.refusePut(() => { throw new TypeError("Failed to fetch") })
    await expect(store().save()).resolves.toBe(false)

    // The file changed by hand instead.
    api.writeFile(named("by hand"), "rev-7")
    api.refusePut(() => json({ detail: "stale_document_revision: forms/form.json changed on disk." }, 409))
    await expect(store().save()).resolves.toBe(false)

    expect(store()).toMatchObject({ stale: true, revision: "rev-0", form: named("home") })
  })

  it("says why a save found the form being read again", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    store().change((form) => ({ ...form, name: "home" }))

    // Save, then a reload asked for before the save's turn came.
    const saving = store().flush()
    const reading = store().reload()

    await expect(saving).resolves.toBe(false)
    await reading
    expect(toasts()).toContainEqual(["error", "Not saved: forms/form.json is being read again."])
  })

  it("counts each read anew, and no save or undo as one", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    expect(store().reads).toBe(1)

    store().change((form) => ({ ...form, name: "home" }))
    await store().save()
    store().undo()
    await store().save()
    expect(store().reads).toBe(1)

    api.writeFile(named("by hand"), "rev-9")
    await store().sync()
    expect(store().reads).toBe(2)
    await store().reload()
    expect(store().reads).toBe(3)
  })

  it("says why any other save failed", async () => {
    const api = server({ form: blank, revision: "rev-0" })
    api.refusePut(() => json({ detail: "[workbench].enabled must be true or false" }, 409))
    const store = useWorkbenchFormStore.getState
    await store().load()

    await expect(store().save()).resolves.toBe(false)

    expect(store().stale).toBe(false)
    expect(toasts()).toEqual([["error", "Could not save forms/form.json: [workbench].enabled must be true or false"]])
  })

  it("saves nothing before the form is read", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch")

    await expect(useWorkbenchFormStore.getState().save()).resolves.toBe(false)

    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("refuses an edit before the form is read, and undoes or redoes nothing when there is nothing to", async () => {
    const store = useWorkbenchFormStore.getState
    expect(() => store().change((form) => form)).toThrow("The workbench's form is not loaded")
    expect(() => store().pushSnapshot()).toThrow("The workbench's form is not loaded")
    store().undo()
    store().redo()
    expect(store()).toMatchObject({ form: null, undoStack: [], redoStack: [] })

    server({ form: blank, revision: "rev-0" })
    await store().load()
    store().undo()
    store().redo()
    expect(store()).toMatchObject({ form: blank, dirty: false, undoStack: [], redoStack: [] })
  })

  it("names forms/form.json until the workbench's status says where the form is", async () => {
    server({ form: blank, revision: "rev-0" })
    const store = useWorkbenchFormStore.getState
    await store().load()
    useWorkbenchStore.setState({ formPath: null })
    vi.mocked(fetch).mockImplementation(async () => json({ detail: "gone" }, 409))

    await store().sync()
    store().change((form) => ({ ...form, name: "home" }))
    await expect(store().save()).resolves.toBe(false)

    expect(toasts()).toEqual([
      ["error", "Could not read forms/form.json again: gone"],
      ["error", "Could not save forms/form.json: gone"],
    ])
  })
})
