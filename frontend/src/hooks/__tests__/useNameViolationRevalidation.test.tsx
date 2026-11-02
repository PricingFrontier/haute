/**
 * A document loaded with name violations: the parser reads them,
 * the status store fences save/run/preview until renames clear them, the
 * banner lists them, and each graph edit revalidates through the editor
 * identity request, a newer edit superseding an answer still in flight.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"

import NameViolationsBanner from "../../components/NameViolationsBanner"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { parsePipelineEditorDocument } from "../../types/pipelineDocument"
import type { PipelineNameViolation } from "../../types/pipelineDocument"
import { useNameViolationRevalidation } from "../useNameViolationRevalidation"

const resolveEditorNodeIdentities = vi.fn()
vi.mock("../../api/client", () => ({
  resolveEditorNodeIdentities: (...args: unknown[]) => resolveEditorNodeIdentities(...args),
}))

const RESERVED: PipelineNameViolation = {
  kind: "reserved",
  name: "pl",
  message: "Node 'pl' (the pipeline) takes the name `pl`, which the generated module binds to polars.",
  parties: [{ node_id: "pl", label: "pl", submodel: null }],
  binding: null,
}
const DUPLICATE: PipelineNameViolation = {
  kind: "duplicate",
  name: "transform",
  message: "Nodes 'transform' (the pipeline) and 'transform' (submodel 'rates') take one name, `transform`.",
  parties: [
    { node_id: "transform", label: "transform", submodel: null },
    { node_id: "transform", label: "transform", submodel: "rates" },
  ],
  binding: null,
}
const FRAME_HELPER: PipelineNameViolation = {
  kind: "support_input",
  name: "rate_lookup",
  message: "Node 'rated' (the pipeline) receives an input named `rate_lookup`, which a definition in the preamble binds. Rename the input or the helper.",
  parties: [{ node_id: "rated", label: "rated", submodel: null }],
  binding: { kind: "frame", node_id: "quotes", handle: "rate_lookup", submodel: null },
}
const PORT_HELPER: PipelineNameViolation = {
  kind: "support_input",
  name: "band",
  message: "Node 'child' (submodel 'rates') receives an input named `band`, which `band` from `utility.bands` binds. Rename the input or the helper.",
  parties: [{ node_id: "child", label: "child", submodel: "rates" }],
  binding: { kind: "submodel_input", submodel: "rates", port: "band" },
}

function fencedDocument(violations: PipelineNameViolation[] = [RESERVED, DUPLICATE]) {
  const document = makePipelineEditorDocument({ name_violations: violations })
  return {
    ...document,
    capabilities: { ...document.capabilities, can_save: false, can_execute: false, can_preview: false },
  }
}

function node(id: string, label: string): Node {
  return { id, type: "polars", position: { x: 0, y: 0 }, data: { label, nodeType: "polars", config: {} } }
}

afterEach(() => {
  cleanup()
  resolveEditorNodeIdentities.mockReset()
  useDocumentStatusStore.getState().reset()
})

describe("the document's name violations", () => {
  it("are parsed from the wire, and an unknown kind is refused", () => {
    const wire = JSON.parse(JSON.stringify(fencedDocument()))
    expect(parsePipelineEditorDocument(wire).name_violations).toEqual([RESERVED, DUPLICATE])
    wire.name_violations[0].kind = "shadowed"
    expect(() => parsePipelineEditorDocument(wire)).toThrow(/name_violations\[0\]\.kind/)
  })

  it("carry the frame or public input port that names their input, which loading keeps for its editor", () => {
    const wire = JSON.parse(JSON.stringify(fencedDocument([RESERVED, FRAME_HELPER, PORT_HELPER])))
    const document = parsePipelineEditorDocument(wire)
    expect(document.name_violations).toEqual([RESERVED, FRAME_HELPER, PORT_HELPER])
    useDocumentStatusStore.getState().loadDocumentStatus(document)
    expect(useDocumentStatusStore.getState().inputBindingViolations).toEqual([FRAME_HELPER, PORT_HELPER])

    wire.name_violations[1].binding = { kind: "frame", node_id: "quotes", handle: "rate_lookup" }
    expect(() => parsePipelineEditorDocument(wire)).toThrow(/name_violations\[1\]\.binding/)
    wire.name_violations[1].binding = { kind: "submodel_input", submodel: "rates", port: "" }
    expect(() => parsePipelineEditorDocument(wire)).toThrow(/name_violations\[1\]\.binding\.port/)
  })

  it("fence save, run and preview until renames clear them, and again if they return", () => {
    const store = useDocumentStatusStore.getState()
    store.loadDocumentStatus(fencedDocument())
    expect(useDocumentStatusStore.getState().capabilities).toMatchObject({
      can_mutate: true, can_save: false, can_execute: false, can_preview: false,
    })

    store.setNameViolations([RESERVED])
    expect(useDocumentStatusStore.getState().capabilities?.can_save).toBe(false)
    store.setNameViolations([])
    expect(useDocumentStatusStore.getState().capabilities).toMatchObject({
      can_save: true, can_execute: true, can_preview: true,
    })
    store.setNameViolations([DUPLICATE])
    expect(useDocumentStatusStore.getState().capabilities?.can_save).toBe(false)
  })

  it("leave a document loaded without them alone", () => {
    const store = useDocumentStatusStore.getState()
    store.loadDocumentStatus(makePipelineEditorDocument())
    store.setNameViolations([RESERVED])
    expect(useDocumentStatusStore.getState().nameViolations).toEqual([])
    expect(useDocumentStatusStore.getState().capabilities?.can_save).toBe(true)
  })

  it("are listed in a banner whose rows select their nodes", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(fencedDocument())
    const onSelect = vi.fn()
    render(<NameViolationsBanner onSelectViolation={onSelect} />)

    expect(screen.getByTestId("name-violations-banner")).toHaveTextContent("these 2 name issues before saving")
    fireEvent.click(screen.getByText(DUPLICATE.message))
    expect(onSelect).toHaveBeenCalledWith(DUPLICATE)

    act(() => useDocumentStatusStore.getState().setNameViolations([]))
    expect(screen.queryByTestId("name-violations-banner")).toBeNull()
  })
})

describe("useNameViolationRevalidation", () => {
  const edges: Edge[] = []
  const preamble = ""
  const buildContextGraph = () => ({ nodes: [], edges: [], submodels: undefined, preamble: undefined })

  it("sends the graph after each edit and follows the server's answer", async () => {
    useDocumentStatusStore.getState().loadDocumentStatus(fencedDocument())
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [DUPLICATE] })

    const { rerender } = renderHook(
      ({ nodes }) => useNameViolationRevalidation({ nodes, edges, submodels: {}, preamble, buildContextGraph }),
      { initialProps: { nodes: [node("pl", "pl")] } },
    )
    await waitFor(() => expect(useDocumentStatusStore.getState().nameViolations).toEqual([DUPLICATE]))
    expect(resolveEditorNodeIdentities).toHaveBeenCalledWith({ nodes: [], graph: buildContextGraph() })

    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [] })
    rerender({ nodes: [node("pl", "polars rows")] })
    await waitFor(() => expect(useDocumentStatusStore.getState().capabilities?.can_save).toBe(true))
  })

  it("lets a newer edit supersede an answer still in flight", async () => {
    useDocumentStatusStore.getState().loadDocumentStatus(fencedDocument())
    let answerFirst: (value: unknown) => void = () => {}
    resolveEditorNodeIdentities
      .mockImplementationOnce(() => new Promise((resolve) => { answerFirst = resolve }))
      .mockResolvedValueOnce({ identities: [], violations: [RESERVED] })

    const { rerender } = renderHook(
      ({ nodes }) => useNameViolationRevalidation({ nodes, edges, submodels: {}, preamble, buildContextGraph }),
      { initialProps: { nodes: [node("pl", "pl")] } },
    )
    rerender({ nodes: [node("pl", "pl"), node("transform", "root transform")] })
    await waitFor(() => expect(useDocumentStatusStore.getState().nameViolations).toEqual([RESERVED]))

    await act(async () => answerFirst({ identities: [], violations: [] }))
    expect(useDocumentStatusStore.getState().nameViolations).toEqual([RESERVED])
  })

  it("rechecks after a preamble edit, which can remove a colliding helper", async () => {
    useDocumentStatusStore.getState().loadDocumentStatus(fencedDocument())
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [RESERVED] })
    const nodes = [node("pl", "pl")]

    const { rerender } = renderHook(
      ({ code }) => useNameViolationRevalidation({
        nodes, edges, submodels: {}, preamble: code, buildContextGraph,
      }),
      { initialProps: { code: "def pl():\n    pass" } },
    )
    await waitFor(() => expect(resolveEditorNodeIdentities).toHaveBeenCalledTimes(1))
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [] })
    rerender({ code: "" })

    await waitFor(() => expect(resolveEditorNodeIdentities).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(useDocumentStatusStore.getState().nameViolations).toEqual([]))
  })

  it("asks nothing of a document loaded without violations", () => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument())
    renderHook(() => useNameViolationRevalidation({
      nodes: [node("pl", "pl")], edges, submodels: {}, preamble, buildContextGraph,
    }))
    expect(resolveEditorNodeIdentities).not.toHaveBeenCalled()
  })
})

describe("useNameViolationRevalidation after an input binding edit", () => {
  const edges: Edge[] = []
  const buildContextGraph = () => ({ nodes: [], edges: [], submodels: undefined, preamble: undefined })

  function quoteInput(...labels: string[]): Node {
    return {
      id: "quotes",
      type: "apiInput",
      position: { x: 0, y: 0 },
      data: { label: "quotes", nodeType: "apiInput", config: { tables: labels.map((label) => ({ label })) } },
    }
  }

  function rates(...ports: string[]) {
    return { rates: { definitionId: "rates", file: "modules/rates.py", inputPorts: ports.map((name) => ({ name, targets: [] })), outputPorts: [] } }
  }

  it("asks after a frame label is committed, and clears what fixing it clears, in a document without a fence", async () => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument())
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [FRAME_HELPER] })
    const { rerender } = renderHook(
      ({ nodes, code }) => useNameViolationRevalidation({ nodes, edges, submodels: {}, preamble: code, buildContextGraph }),
      { initialProps: { nodes: [quoteInput("policy")], code: "" } },
    )
    expect(resolveEditorNodeIdentities).not.toHaveBeenCalled()

    rerender({ nodes: [quoteInput("rate_lookup")], code: "" })
    await waitFor(() => expect(useDocumentStatusStore.getState().inputBindingViolations).toEqual([FRAME_HELPER]))
    expect(resolveEditorNodeIdentities).toHaveBeenCalledWith({ nodes: [], graph: buildContextGraph() })
    // No fence follows: save itself refuses the name.
    expect(useDocumentStatusStore.getState().nameViolations).toEqual([])
    expect(useDocumentStatusStore.getState().capabilities?.can_save).toBe(true)

    // While one shows, removing the helper from the preamble asks again.
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [] })
    rerender({ nodes: [quoteInput("rate_lookup")], code: "# helper removed" })
    await waitFor(() => expect(useDocumentStatusStore.getState().inputBindingViolations).toEqual([]))
    expect(resolveEditorNodeIdentities).toHaveBeenCalledTimes(2)

    // With nothing showing, a preamble edit or a new request input asks nothing.
    rerender({ nodes: [quoteInput("rate_lookup")], code: "" })
    rerender({ nodes: [quoteInput("rate_lookup"), node("transform", "transform")], code: "" })
    expect(resolveEditorNodeIdentities).toHaveBeenCalledTimes(2)
  })

  it("asks after a definition gains a public input port", async () => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument())
    resolveEditorNodeIdentities.mockResolvedValue({ identities: [], violations: [RESERVED, PORT_HELPER] })
    const { rerender } = renderHook(
      ({ submodels }) => useNameViolationRevalidation({ nodes: [], edges, submodels, preamble: "", buildContextGraph }),
      { initialProps: { submodels: rates("policy") } },
    )
    rerender({ submodels: rates("policy", "band") })
    await waitFor(() => expect(useDocumentStatusStore.getState().inputBindingViolations).toEqual([PORT_HELPER]))
  })
})
