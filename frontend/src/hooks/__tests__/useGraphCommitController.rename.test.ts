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
})
