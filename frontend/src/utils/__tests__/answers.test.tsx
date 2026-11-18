/**
 * The late-answer helper (specs/frontend-shared low-level, "Late answers"): for each part an
 * answer belongs to, an answer held while that part moves is dropped, or handed to `stale`,
 * and the same answer let go while nothing moved reaches `ok` or `failed`.
 */
import { act, render } from "@testing-library/react"
import { useEffect } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import { create } from "zustand"
import { draftPart, useCommittedDraft } from "../../components/form/useCommittedDraft"
import { useRequestKind } from "../../hooks/useRequestKind"
import useDocumentStatusStore, { documentPart, executionPart } from "../../stores/useDocumentStatusStore"
import useGraphStore, { editPart, unsavedEditsPart } from "../../stores/useGraphStore"
import { makeIdentifiedNode } from "../../test-utils/factories"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { answer, ask, newest, requestKind, under, watch, type BelongsTo, type RequestKind } from "../answers"
import { rootAddress } from "../nodeAddress"
import { nodePart } from "../requestGateway"

/** A request whose answer the test lets go. */
function held<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

function handlers() {
  return { ok: vi.fn(), failed: vi.fn(), stale: vi.fn() }
}

const loadDocument = (sourceFile = "main.py") =>
  useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({ source_file: sourceFile }))

function loadGraph() {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [makeIdentifiedNode("rates", "banding"), makeIdentifiedNode("premium", "ratingStep")],
    edges: [],
    preamble: "",
    submodels: {},
  })
}

const formStore = create<{ form: { notes: string } }>()(() => ({ form: { notes: "kept" } }))

beforeEach(() => {
  useGraphStore.getState().resetForTests()
  useDocumentStatusStore.getState().reset()
  formStore.setState({ form: { notes: "kept" } })
  loadDocument()
  loadGraph()
})

/** Each part an answer can belong to, and what moves it. */
const PARTS: Array<{ name: string; belongsTo: () => BelongsTo; move: () => void }> = (() => {
  const kind = requestKind()
  return [
    { name: "a newer request of its kind", belongsTo: () => [newest(kind)], move: () => ask([newest(kind)]) },
    { name: "its kind bumped", belongsTo: () => [under(kind)], move: () => kind.bump() },
    { name: "another document adopted", belongsTo: () => [documentPart()], move: () => loadDocument("other.py") },
    {
      name: "a live-sync frame of the same file",
      belongsTo: () => [documentPart()],
      move: () => loadDocument("main.py"),
    },
    {
      name: "the execution fence moved",
      belongsTo: () => [executionPart()],
      move: () => useDocumentStatusStore.getState().setGraphSynchronized(false),
    },
    {
      name: "an edit",
      belongsTo: () => [editPart()],
      move: () => useGraphStore.getState().setNodes((nodes) => nodes.map((node) => ({ ...node, data: { ...node.data, label: "renamed" } }))),
    },
    {
      name: "an Undo",
      belongsTo: () => {
        useGraphStore.getState().setNodes((nodes) => nodes.slice(0, 1))
        return [editPart()]
      },
      move: () => useGraphStore.getState().undo(),
    },
    {
      name: "its node removed",
      belongsTo: () => [nodePart(rootAddress("premium"))],
      move: () => useGraphStore.getState().setNodes((nodes) => nodes.filter((node) => node.id !== "premium")),
    },
    {
      name: "a watched store changed",
      belongsTo: () => [watch(formStore, (state) => state.form)],
      move: () => formStore.setState({ form: { notes: "typed" } }),
    },
  ]
})()

describe("an answer", () => {
  it.each(PARTS)("is dropped once $name", async ({ belongsTo, move }) => {
    const request = held<string>()
    const done = vi.fn()
    const asked = ask(belongsTo())
    const handled = { ok: vi.fn(), failed: vi.fn() }
    const outcome = asked.answer(request.promise, handled).then(done)
    move()
    request.resolve("late")
    await outcome
    expect(handled.ok).not.toHaveBeenCalled()
    expect(handled.failed).not.toHaveBeenCalled()
    expect(done).toHaveBeenCalledWith("stale")
  })

  it.each(PARTS)("is handed to stale once $name", async ({ belongsTo, move }) => {
    const request = held<string>()
    const handled = handlers()
    const outcome = answer(belongsTo(), request.promise, handled)
    move()
    request.resolve("late")
    expect(await outcome).toBe("stale")
    expect(handled.stale).toHaveBeenCalledWith({ value: "late" })
    expect(handled.ok).not.toHaveBeenCalled()
  })

  it.each(PARTS)("is applied while nothing moved ($name)", async ({ belongsTo }) => {
    const request = held<string>()
    const handled = handlers()
    const outcome = answer(belongsTo(), request.promise, handled)
    request.resolve("current")
    expect(await outcome).toBe("ok")
    expect(handled.ok).toHaveBeenCalledWith("current", expect.anything())
    expect(handled.stale).not.toHaveBeenCalled()
  })

  it.each(PARTS)("fails while nothing moved, and a late failure is dropped ($name)", async ({ belongsTo, move }) => {
    const current = held<string>()
    const handled = handlers()
    const failed = answer(belongsTo(), current.promise, handled)
    current.reject(new Error("refused"))
    expect(await failed).toBe("failed")
    expect(handled.failed).toHaveBeenCalledWith(expect.objectContaining({ message: "refused" }), expect.anything())

    const late = held<string>()
    const unhandled = { ok: vi.fn() }
    const dropped = answer(belongsTo(), late.promise, unhandled)
    move()
    late.reject(new Error("too late"))
    expect(await dropped).toBe("stale")
  })

  it("takes what ok throws using the answer as the request's failure", async () => {
    const request = held<string>()
    const failed = vi.fn()
    const outcome = answer([documentPart()], request.promise, {
      ok: () => {
        throw new Error("an answer the caller cannot use")
      },
      failed,
    })
    request.resolve("unusable")
    expect(await outcome).toBe("failed")
    expect(failed).toHaveBeenCalledWith(expect.objectContaining({ message: "an answer the caller cannot use" }), expect.anything())

    const unhandled = held<string>()
    const rejected = answer([documentPart()], unhandled.promise, { ok: () => { throw new Error("thrown") } })
    unhandled.resolve("unusable")
    await expect(rejected).rejects.toThrow("thrown")
  })

  it("rejects with a current failure no handler takes", async () => {
    const request = held<string>()
    const outcome = answer([documentPart()], request.promise, { ok: vi.fn() })
    request.reject(new Error("refused"))
    await expect(outcome).rejects.toThrow("refused")
  })

  it("is dropped once a field is typed in and not committed", async () => {
    function Field() {
      const draft = useCommittedDraft("kept", () => undefined)
      return <input aria-label="notes" value={draft.shown} onChange={(event) => draft.edit(event.target.value)} onBlur={() => draft.commit()} />
    }
    const view = render(<Field />)
    const request = held<string>()
    const handled = handlers()
    const outcome = answer([draftPart()], request.promise, handled)
    const input = view.getByLabelText("notes")
    act(() => {
      input.focus()
    })
    await act(async () => {
      const { fireEvent } = await import("@testing-library/react")
      fireEvent.change(input, { target: { value: "typed during open" } })
    })
    request.resolve("opened")
    expect(await outcome).toBe("stale")
    expect(handled.ok).not.toHaveBeenCalled()
  })

  it("asked while a field is typed in is never current", () => {
    function Field() {
      const draft = useCommittedDraft("kept", () => undefined)
      useEffect(() => draft.edit("typing"), [])  // eslint-disable-line react-hooks/exhaustive-deps
      return <span>{draft.shown}</span>
    }
    render(<Field />)
    expect(ask([draftPart()]).current()).toBe(false)
  })

  it("names what it belongs to", () => {
    expect(() => ask([] as unknown as BelongsTo)).toThrow("An answer must say what it belongs to")
  })

  it("runs only stale once let go, to settle a write that landed or failed", async () => {
    for (const settle of ["resolve", "reject"] as const) {
      const request = held<string>()
      const handled = handlers()
      const asked = ask([documentPart()])
      const outcome = asked.answer(request.promise, handled)
      asked.release()
      if (settle === "resolve") request.resolve("saved")
      else request.reject(new Error("refused"))
      expect(await outcome).toBe("stale")
      expect(handled.ok).not.toHaveBeenCalled()
      expect(handled.failed).not.toHaveBeenCalled()
      expect(handled.stale).toHaveBeenCalledWith(settle === "resolve" ? { value: "saved" } : { error: expect.objectContaining({ message: "refused" }) })
    }
  })

  it("stays late once a part has moved, even when it moves back", async () => {
    const branch = create<{ name: string }>()(() => ({ name: "main" }))
    const request = held<string>()
    const handled = handlers()
    const outcome = answer([watch(branch, (state) => state.name)], request.promise, handled)
    branch.setState({ name: "feature" })
    branch.setState({ name: "main" })
    request.resolve("main's list")
    expect(await outcome).toBe("stale")
    expect(handled.ok).not.toHaveBeenCalled()
  })

  it("holds an edit made elsewhere apart from the canvas taking a document while as saved", async () => {
    // As saved: the answer's own change arriving by live sync is no edit made here.
    const clean = held<string>()
    const kept = handlers()
    const first = answer([unsavedEditsPart()], clean.promise, kept)
    loadGraph()
    clean.resolve("caught up")
    expect(await first).toBe("ok")
    // With unsaved edits: another edit since is one made here.
    useGraphStore.getState().setNodes((nodes) => nodes.slice(0, 1))
    const dirty = held<string>()
    const dropped = handlers()
    const second = answer([unsavedEditsPart()], dirty.promise, dropped)
    useGraphStore.getState().setNodes((nodes) => nodes.map((node) => ({ ...node, data: { ...node.data, label: "1.5" } })))
    dirty.resolve("caught up")
    expect(await second).toBe("stale")
    expect(dropped.ok).not.toHaveBeenCalled()
  })
})

describe("what a request sets while it waits", () => {
  it("is reset the moment the request stops being current, not when its answer comes", async () => {
    const kind = requestKind()
    const pending = vi.fn()
    const page = held<string>()
    const outcome = answer([under(kind)], page.promise, { ok: vi.fn() }, { pending })
    expect(pending.mock.calls).toEqual([[true]])
    kind.bump()
    expect(pending.mock.calls).toEqual([[true], [false]])
    page.resolve("late")
    await outcome
    expect(pending.mock.calls).toEqual([[true], [false]])
  })

  it("is set for a newer request after the older one's reset", () => {
    const kind = requestKind()
    let loading = false
    const pending = (waiting: boolean) => {
      loading = waiting
    }
    void answer([newest(kind)], held<string>().promise, {}, { pending })
    expect(loading).toBe(true)
    void answer([newest(kind)], held<string>().promise, {}, { pending })
    expect(loading).toBe(true)
  })

  it("is reset once the answer is applied", async () => {
    const pending = vi.fn()
    const request = held<string>()
    const outcome = answer([documentPart()], request.promise, { ok: () => expect(pending).toHaveBeenLastCalledWith(true) }, { pending })
    request.resolve("current")
    await outcome
    expect(pending.mock.calls).toEqual([[true], [false]])
  })

  it("is reset at once for a request never current", () => {
    const kind = requestKind()
    const pending = vi.fn()
    ask([under(kind), newest(kind)], { pending })
    expect(pending.mock.calls).toEqual([[true], [false]])
  })
})

describe("the newest of a kind", () => {
  it("leaves only the request a reset asked current", () => {
    const kind = requestKind()
    let nested: ReturnType<typeof ask> | null = null
    ask([newest(kind)], { pending: (waiting) => {
      if (!waiting && nested === null) nested = ask([newest(kind)])
    } })
    const outer = ask([newest(kind)])
    expect(nested!.current()).toBe(true)
    expect(outer.current()).toBe(false)
  })
})

describe("an edit made since", () => {
  it("is a node moved on a canvas with unsaved edits", async () => {
    useGraphStore.getState().setNodes((nodes) => nodes.slice(0, 1))
    const request = held<string>()
    const handled = handlers()
    const outcome = answer([unsavedEditsPart()], request.promise, handled)
    // A drag records its history entry, then moves the node step by step.
    useGraphStore.getState().pushSnapshot()
    useGraphStore.getState().setNodesRaw((nodes) => nodes.map((node) => ({ ...node, position: { x: node.position.x + 40, y: node.position.y } })))
    request.resolve("caught up")
    expect(await outcome).toBe("stale")
  })

  it("is a node dragged inside an open submodel, before the drag ends", async () => {
    const definition = {
      definitionId: "br",
      file: "modules/br.py",
      graph: { nodes: [makeIdentifiedNode("bands", "banding")], edges: [] },
      inputPorts: [],
      outputPorts: [],
    }
    useGraphStore.getState().loadGraphSnapshot({
      nodes: [{ ...makeIdentifiedNode("occ", "submodel"), data: { label: "occ", nodeType: "submodel", config: { definition_id: "br", alias: "br" } } }],
      edges: [],
      preamble: "",
      submodels: { br: definition },
    })
    useGraphStore.getState().enterSubmodelView({ instanceId: "occ", definitionId: "br" })
    useGraphStore.getState().setPreamble("import math")
    expect(useGraphStore.getState().dirty).toBe(true)
    const request = held<string>()
    const handled = handlers()
    const outcome = answer([unsavedEditsPart()], request.promise, handled)
    useGraphStore.getState().pushSnapshot()
    useGraphStore.getState().setNodesRaw((nodes) => nodes.map((node) => node.id === "bands"
      ? { ...node, dragging: true, position: { x: node.position.x + 40, y: node.position.y } }
      : node))
    request.resolve("caught up")
    expect(await outcome).toBe("stale")
  })

  it("is a step of a form gesture, and not the form read anew", async () => {
    const { default: useWorkbenchFormStore } = await import("../../stores/useWorkbenchFormStore")
    const { formEditsPart } = await import("../../stores/useWorkbenchStore")
    const form = { version: 1, name: "t", theme: {}, schema: { tables: [] }, pages: [], sample: {} } as never
    useWorkbenchFormStore.setState({ form, status: "ready", dirty: false, savedForm: JSON.stringify(form) })
    // The form read anew (its file changed on disk) is no edit made here.
    const read = held<string>()
    const kept = handlers()
    const first = answer([formEditsPart()], read.promise, kept)
    useWorkbenchFormStore.setState((state) => ({ form: { ...(state.form as object) } as never, reads: state.reads + 1 }))
    read.resolve("caught up")
    expect(await first).toBe("ok")

    // A gesture's step leaves `dirty` as it was, and is an edit.
    const stepped = held<string>()
    const dropped = handlers()
    const second = answer([formEditsPart()], stepped.promise, dropped)
    const before = useWorkbenchFormStore.getState().form!
    useWorkbenchFormStore.getState().beginGesture(before, () => undefined)
    useWorkbenchFormStore.getState().setFormRaw({ ...before, name: "dragged" })
    expect(useWorkbenchFormStore.getState().dirty).toBe(false)
    stepped.resolve("caught up")
    expect(await second).toBe("stale")
    useWorkbenchFormStore.getState().endGesture()
  })
})

describe("a write's exclusion", () => {
  it("lasts until the answer has settled and been handled, current or not", async () => {
    const kind = requestKind()
    const inFlight = vi.fn()
    const pending = vi.fn()
    const write = held<string>()
    const settled = answer([under(kind)], write.promise, { stale: () => expect(inFlight).toHaveBeenLastCalledWith(true) }, { pending, inFlight })
    kind.bump()
    expect(pending.mock.calls).toEqual([[true], [false]])
    expect(inFlight.mock.calls).toEqual([[true]])
    write.resolve("landed")
    expect(await settled).toBe("stale")
    expect(inFlight.mock.calls).toEqual([[true], [false]])
  })
})

describe("a request's signal", () => {
  it("aborts a read the moment its document is replaced", () => {
    const asked = ask([documentPart()])
    const signal = asked.signal
    expect(signal.aborted).toBe(false)
    loadDocument("other.py")
    expect(signal.aborted).toBe(true)
  })

  it("is given to a request sent through answer", async () => {
    const kind = requestKind()
    let seen: AbortSignal | null = null
    const first = answer([newest(kind)], (signal) => {
      seen = signal
      return new Promise<string>((_, reject) => signal.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))))
    }, { ok: vi.fn(), failed: vi.fn() })
    void answer([newest(kind)], held<string>().promise, {})
    expect(seen!.aborted).toBe(true)
    expect(await first).toBe("stale")
  })
})

describe("a component's kind", () => {
  it("drops the answers of a component that unmounted", async () => {
    let kind: RequestKind | null = null
    function Lookup() {
      kind = useRequestKind()
      return null
    }
    const view = render(<Lookup />)
    const request = held<string>()
    const handled = handlers()
    const outcome = answer([under(kind!)], request.promise, { ok: handled.ok })
    view.unmount()
    request.resolve("late")
    expect(await outcome).toBe("stale")
    expect(handled.ok).not.toHaveBeenCalled()
  })

  it("drops an effect's answer once the effect runs again", async () => {
    const requests: Array<ReturnType<typeof held<string>>> = []
    const shown = vi.fn()
    function Reader({ path }: { path: string }) {
      useEffect(() => {
        const asked = ask([documentPart()])
        const request = held<string>()
        requests.push(request)
        void asked.answer(request.promise, { ok: (value) => shown(path, value) })
        return () => asked.release()
      }, [path])
      return null
    }
    const view = render(<Reader path="a.csv" />)
    view.rerender(<Reader path="b.csv" />)
    await act(async () => {
      requests[0].resolve("a's rows")
      requests[1].resolve("b's rows")
    })
    expect(shown.mock.calls).toEqual([["b.csv", "b's rows"]])
  })
})
