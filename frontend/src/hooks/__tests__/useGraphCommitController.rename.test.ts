import { act, cleanup, renderHook } from "@testing-library/react"
import type { Edge, Node } from "@xyflow/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import useGraphCommitController from "../useGraphCommitController"
import { makeNode } from "../../test-utils/factories"

function createController(
  nodes: Node[],
  edges: Edge[],
  submodels: Record<string, unknown>,
  resolveNodeIdentities: (nodes: readonly Node[]) => Promise<Node[]>,
) {
  const commitGraph = vi.fn()
  const setSelectedNode = vi.fn()
  const addToast = vi.fn()
  const graphRef = { current: { nodes, edges } }
  const submodelsRef = { current: submodels }

  const hook = renderHook(() =>
    useGraphCommitController({
      graphRef,
      submodelsRef,
      readDocumentIdentity: () => "doc-1",
      readOnly: false,
      reservedApiInputFrameLabels: new Set<string>(),
      resolveNodeIdentities,
      commitGraph,
      setSelectedNode,
      addToast,
    }),
  )

  return {
    hook,
    graphRef,
    commitGraph,
  }
}

describe("useGraphCommitController submodel occurrence rename", () => {
  it.each(["document", "readOnly"] as const)("cancels a pending rename after a %s change", async (change) => {
    const original = makeNode("source", "polars", { data: { label: "source", nodeType: "polars", config: { code: "df = source_data" } } })
    const graphRef = { current: { nodes: [original], edges: [] as Edge[] } }
    const commitGraph = vi.fn()
    let finish!: (nodes: Node[]) => void
    let candidates: Node[] = []
    const resolveNodeIdentities = vi.fn((nodes: readonly Node[]) => {
      candidates = [...nodes]
      return new Promise<Node[]>((resolve) => { finish = resolve })
    })
    const context = { document: "a.py:1", readOnly: false }
    const hook = renderHook(() => useGraphCommitController({
      graphRef, submodelsRef: { current: {} },
      readDocumentIdentity: () => context.document, readOnly: context.readOnly,
      reservedApiInputFrameLabels: new Set(), resolveNodeIdentities,
      commitGraph, setSelectedNode: vi.fn(), addToast: vi.fn(),
    }))
    let pending!: ReturnType<typeof hook.result.current.onRenameNode>
    act(() => { pending = hook.result.current.onRenameNode("source", "renamed") })
    if (change === "document") context.document = "b.py:1"
    else context.readOnly = true
    hook.rerender()
    await act(async () => {
      finish(candidates)
      // Let an incorrect retry finish too, so the test detects a commit rather than timing out.
      await Promise.resolve()
      finish(candidates)
      await pending
    })
    expect(commitGraph).not.toHaveBeenCalled()
    expect(graphRef.current.nodes[0]).toBe(original)
    expect(resolveNodeIdentities).toHaveBeenCalledOnce()
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it("renames an occurrence, sending alias in identity request, committing label and alias, and rebinding downstream inputMapping without editing code", async () => {
    const submodelNode: Node = {
      id: "sub_1",
      type: "submodel",
      position: { x: 0, y: 0 },
      data: {
        label: "pricing",
        nodeType: "submodel",
        config: { definitionId: "def_pricing", alias: "pricing" },
        _functionName: "pricing",
        _sourceHandleInputNames: { out__rates: "pricing" },
      },
    }

    const transformNode: Node = {
      id: "tx_1",
      type: "polars",
      position: { x: 200, y: 0 },
      data: {
        label: "calculator",
        nodeType: "polars",
        config: {
          code: "df = pricing.with_columns()",
          inputMapping: { pricing: "pricing" },
        },
        _functionName: "calculator",
      },
    }

    const edge: Edge = {
      id: "e1",
      source: "sub_1",
      target: "tx_1",
      sourceHandle: "out__rates",
      targetHandle: null,
      data: { _inputName: "pricing" },
    }

    const submodels = {
      def_pricing: {
        definitionId: "def_pricing",
        file: "modules/pricing.py",
        graph: { nodes: [], edges: [] },
        inputPorts: [],
        outputPorts: [{ name: "rates" }],
      },
    }

    const resolveNodeIdentities = vi.fn(async (candidateNodes: readonly Node[]) => {
      return candidateNodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          _functionName: String(n.data.label),
          _sourceHandleInputNames: { out__rates: String(n.data.label) },
        },
      }))
    })

    const { hook, commitGraph } = createController(
      [submodelNode, transformNode],
      [edge],
      submodels,
      resolveNodeIdentities,
    )

    let result: unknown
    await act(async () => {
      result = await hook.result.current.onRenameNode("sub_1", "pricing_v2")
    })

    expect(result).toEqual({ ok: true })
    expect(resolveNodeIdentities).toHaveBeenCalledOnce()
    const resolvedCandidate = resolveNodeIdentities.mock.calls[0][0][0]
    expect(resolvedCandidate.data.label).toBe("pricing_v2")
    expect((resolvedCandidate.data.config as { alias: string }).alias).toBe("pricing_v2")

    expect(commitGraph).toHaveBeenCalledOnce()
    const [committedNodes, committedEdges] = commitGraph.mock.calls[0]
    const updatedSubmodel = committedNodes.find((n: Node) => n.id === "sub_1")
    expect(updatedSubmodel.data.label).toBe("pricing_v2")
    expect(updatedSubmodel.data.config.alias).toBe("pricing_v2")

    const updatedTransform = committedNodes.find((n: Node) => n.id === "tx_1")
    expect(updatedTransform.data.config.inputMapping).toEqual({ pricing: "pricing_v2" })
    expect(updatedTransform.data.config.code).toBe("df = pricing.with_columns()")

    const updatedEdge = committedEdges.find((e: Edge) => e.id === "e1")
    expect(updatedEdge.data._inputName).toBe("pricing_v2")
  })

  it("refuses a non-identifier name when identity resolution returns a different function_name", async () => {
    const submodelNode: Node = {
      id: "sub_1",
      type: "submodel",
      position: { x: 0, y: 0 },
      data: {
        label: "pricing",
        nodeType: "submodel",
        config: { definitionId: "def_pricing", alias: "pricing" },
        _functionName: "pricing",
        _sourceHandleInputNames: { out__rates: "pricing" },
      },
    }

    const resolveNodeIdentities = vi.fn(async (candidateNodes: readonly Node[]) => {
      return candidateNodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          _functionName: "pricing_2",
          _sourceHandleInputNames: { out__rates: "pricing_2" },
        },
      }))
    })

    const { hook, commitGraph } = createController(
      [submodelNode],
      [],
      {},
      resolveNodeIdentities,
    )

    let result: unknown
    await act(async () => {
      result = await hook.result.current.onRenameNode("sub_1", "pricing 2")
    })

    expect(result).toEqual({
      ok: false,
      error: 'Occurrence names must be identifiers; use "pricing_2".',
    })
    expect(commitGraph).not.toHaveBeenCalled()
  })

  it("refuses a name equal to another node's label, id, or submodel alias", async () => {
    const submodelNode: Node = {
      id: "sub_1",
      type: "submodel",
      position: { x: 0, y: 0 },
      data: {
        label: "pricing",
        nodeType: "submodel",
        config: { definitionId: "def_pricing", alias: "pricing" },
        _functionName: "pricing",
        _sourceHandleInputNames: { out__rates: "pricing" },
      },
    }

    const otherNode = makeNode("other_node", "polars", {
      data: {
        label: "existing_label",
        nodeType: "polars",
      },
    })

    const siblingSubmodel: Node = {
      id: "sub_2",
      type: "submodel",
      position: { x: 0, y: 100 },
      data: {
        label: "scoring",
        nodeType: "submodel",
        config: { definitionId: "def_scoring", alias: "scoring" },
        _functionName: "scoring",
      },
    }

    const resolveNodeIdentities = vi.fn(async (candidateNodes: readonly Node[]) => {
      return candidateNodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          _functionName: String(n.data.label),
          _sourceHandleInputNames: {},
        },
      }))
    })

    const { hook, commitGraph } = createController(
      [submodelNode, otherNode, siblingSubmodel],
      [],
      {},
      resolveNodeIdentities,
    )

    let labelCollisionResult: unknown
    await act(async () => {
      labelCollisionResult = await hook.result.current.onRenameNode("sub_1", "existing_label")
    })
    expect(labelCollisionResult).toEqual({
      ok: false,
      error: '"existing_label" is already used by another node.',
    })

    let idCollisionResult: unknown
    await act(async () => {
      idCollisionResult = await hook.result.current.onRenameNode("sub_1", "other_node")
    })
    expect(idCollisionResult).toEqual({
      ok: false,
      error: '"other_node" is already used by another node.',
    })

    let aliasCollisionResult: unknown
    await act(async () => {
      aliasCollisionResult = await hook.result.current.onRenameNode("sub_1", "scoring")
    })
    expect(aliasCollisionResult).toEqual({
      ok: false,
      error: '"scoring" is already used by another node.',
    })

    expect(commitGraph).not.toHaveBeenCalled()
  })
  function ordinarySource(): Node {
    return {
      id: "src_1",
      type: "polars",
      position: { x: 0, y: 0 },
      data: {
        label: "source",
        nodeType: "polars",
        config: { code: "df = pl.DataFrame()" },
        _functionName: "source",
        _defaultInputName: "source",
      },
    }
  }

  it("does not resolve again when only preview metadata changed during identity resolution", async () => {
    const resolveNodeIdentities = vi.fn(async (candidateNodes: readonly Node[]) => candidateNodes.map((n) => ({
      ...n,
      data: { ...n.data, _functionName: String(n.data.label), _defaultInputName: String(n.data.label) },
    })))
    const { hook, graphRef, commitGraph } = createController([ordinarySource()], [], {}, resolveNodeIdentities)
    // A preview response lands while the request is in flight: only metadata changes.
    resolveNodeIdentities.mockImplementationOnce(async (candidateNodes: readonly Node[]) => {
      graphRef.current = {
        nodes: graphRef.current.nodes.map((node) => ({ ...node, data: { ...node.data, _status: "ok" } })),
        edges: graphRef.current.edges,
      }
      return candidateNodes.map((n) => ({
        ...n,
        data: { ...n.data, _functionName: String(n.data.label), _defaultInputName: String(n.data.label) },
      }))
    })

    let result: unknown
    await act(async () => {
      result = await hook.result.current.onRenameNode("src_1", "renamed_source")
    })

    expect(result).toEqual({ ok: true })
    expect(resolveNodeIdentities).toHaveBeenCalledOnce()
    expect(commitGraph).toHaveBeenCalledOnce()
    const [committedNodes] = commitGraph.mock.calls[0]
    const renamed = committedNodes.find((n: Node) => n.id === "src_1")
    expect(renamed.data.label).toBe("renamed_source")
  })

  it("resolves again and keeps the newer config when the node itself changed during identity resolution", async () => {
    const resolveNodeIdentities = vi.fn(async (candidateNodes: readonly Node[]) => candidateNodes.map((n) => ({
      ...n,
      data: { ...n.data, _functionName: String(n.data.label), _defaultInputName: String(n.data.label) },
    })))
    const { hook, graphRef, commitGraph } = createController([ordinarySource()], [], {}, resolveNodeIdentities)
    resolveNodeIdentities.mockImplementationOnce(async (candidateNodes: readonly Node[]) => {
      graphRef.current = {
        nodes: graphRef.current.nodes.map((node) => (
          node.id === "src_1"
            ? { ...node, data: { ...node.data, config: { code: "df = pl.DataFrame().head(1)" } } }
            : node
        )),
        edges: graphRef.current.edges,
      }
      return candidateNodes.map((n) => ({
        ...n,
        data: { ...n.data, _functionName: String(n.data.label), _defaultInputName: String(n.data.label) },
      }))
    })

    let result: unknown
    await act(async () => {
      result = await hook.result.current.onRenameNode("src_1", "renamed_source")
    })

    expect(result).toEqual({ ok: true })
    expect(resolveNodeIdentities).toHaveBeenCalledTimes(2)
    expect(commitGraph).toHaveBeenCalledOnce()
    const [committedNodes] = commitGraph.mock.calls[0]
    const renamed = committedNodes.find((n: Node) => n.id === "src_1")
    expect(renamed.data.label).toBe("renamed_source")
    expect(renamed.data.config).toEqual({ code: "df = pl.DataFrame().head(1)" })
  })
})
