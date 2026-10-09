import { describe, expect, it } from "vitest"
import type { Edge } from "@xyflow/react"
import {
  isPipelineConnectionValid,
  validatePipelineConnection,
} from "../connectionValidation"
import type { SimpleNode } from "../../panels/editors/_shared"
import { NODE_TYPES, type NodeTypeValue } from "../nodeTypes"
import { SUBMODEL_INPUT_HANDLE } from "../flowHandles"

const node = (
  id: string,
  label: string,
  nodeType: NodeTypeValue = NODE_TYPES.POLARS,
): SimpleNode => ({
  id,
  type: nodeType,
  data: {
    label,
    description: "",
    nodeType,
    config: {},
    _defaultInputName: id,
    _sourceHandleInputNames: {},
  },
})

const apiInput = (id: string, frame = "quotes"): SimpleNode => ({
  ...node(id, id, NODE_TYPES.API_INPUT),
  data: {
    label: id,
    description: "",
    nodeType: NODE_TYPES.API_INPUT,
    config: {
      tables: [{
        path: `$[:].${frame}`,
        label: frame,
        emit: true,
        columns: [{ name: "id", selected: true }],
      }],
    },
    _defaultInputName: null,
    _sourceHandleInputNames: { [frame]: frame },
  },
})

const definition = (child: SimpleNode) => ({
  definitionId: "definition_pricing",
  file: "modules/pricing.py",
  graph: { nodes: [child], edges: [] },
  inputPorts: [{
    name: "policy",
    targets: [{ nodeId: child.id, handleId: null }],
  }],
  outputPorts: [],
})

const occurrence = (copy = false) => {
  const alias = copy ? "pricing_copy" : "pricing"
  return {
    ...node(alias, alias, NODE_TYPES.SUBMODEL),
    data: {
      label: alias,
      description: "",
      nodeType: NODE_TYPES.SUBMODEL,
      config: {
        definitionId: "definition_pricing",
        alias,
        ...(copy ? { instanceOf: "pricing" } : {}),
      },
    },
  }
}

describe("connection validation", () => {
  it("rejects self loops", () => {
    const item = node("one", "One")
    expect(isPipelineConnectionValid({
      source: item.id,
      target: item.id,
      sourceHandle: null,
      targetHandle: null,
    }, [item], [])).toBe(false)
  })

  it("allows a canonical public input", () => {
    const api = apiInput("api")
    const submodel = occurrence()
    const child = node("prepare", "Prepare")
    expect(validatePipelineConnection({
      source: api.id,
      target: submodel.id,
      sourceHandle: "quotes",
      targetHandle: "in__policy",
    }, [api, submodel], [], { definition_pricing: definition(child) }))
      .toEqual({ ok: true })
  })

  it("maps a matching frame through the one generic socket on owners and copies", () => {
    const api = apiInput("api", "policy")
    const owner = occurrence()
    const copy = occurrence(true)
    const child = node("prepare", "Prepare")
    const submodels = { definition_pricing: definition(child) }
    const connection = {
      source: api.id,
      sourceHandle: "policy",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    }

    expect(validatePipelineConnection({
      ...connection,
      target: owner.id,
    }, [api, owner], [], submodels)).toEqual({ ok: true })
    expect(validatePipelineConnection({
      ...connection,
      target: copy.id,
    }, [api, copy], [], submodels)).toEqual({ ok: true })
  })

  it("allows a new authoritative frame only on the owner generic socket", () => {
    const api = apiInput("api", "quotes")
    const owner = occurrence()
    const copy = occurrence(true)
    const child = node("prepare", "Prepare")
    const submodels = { definition_pricing: definition(child) }
    const connection = {
      source: api.id,
      sourceHandle: "quotes",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    }

    expect(validatePipelineConnection({
      ...connection,
      target: owner.id,
    }, [api, owner], [], submodels)).toEqual({ ok: true })
    expect(validatePipelineConnection({
      ...connection,
      target: copy.id,
    }, [api, copy], [], submodels)).toMatchObject({ ok: false })
  })

  it("rejects a second generic binding to the same declared frame", () => {
    const api = apiInput("api", "policy")
    const upstream = node("upstream", "Upstream")
    const submodel = occurrence()
    const child = node("prepare", "Prepare")
    const edges: Edge[] = [{
      id: "existing",
      source: upstream.id,
      target: submodel.id,
      sourceHandle: null,
      targetHandle: "in__policy",
    }]
    expect(validatePipelineConnection({
      source: api.id,
      target: submodel.id,
      sourceHandle: "policy",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    }, [api, upstream, submodel], edges, {
      definition_pricing: definition(child),
    })).toEqual({
      ok: false,
      reason: { kind: "duplicate-input-name", inputName: "policy" },
    })
  })

  it("rejects invalid submodel definitions with duplicate port names", () => {
    const api = apiInput("api", "policy")
    const submodel = occurrence()
    const child = node("prepare", "Prepare")
    const base = definition(child)
    const duplicate = {
      ...base,
      inputPorts: [
        ...base.inputPorts,
        {
          name: "policy",
          targets: [{ nodeId: child.id, handleId: "copy" }],
        },
      ],
    }
    expect(validatePipelineConnection({
      source: api.id,
      target: submodel.id,
      sourceHandle: "policy",
      targetHandle: SUBMODEL_INPUT_HANDLE,
    }, [api, submodel], [], { definition_pricing: duplicate })).toEqual({
      ok: false,
      reason: {
        kind: "invalid-connection",
        message: "Canonical submodel definition is unavailable",
      },
    })
  })

  it("rejects a second binding to the same canonical public input", () => {
    const api = apiInput("api")
    const upstream = node("upstream", "Upstream")
    const submodel = occurrence()
    const child = node("prepare", "Prepare")
    const edges: Edge[] = [{
      id: "existing",
      source: upstream.id,
      target: submodel.id,
      sourceHandle: null,
      targetHandle: "in__policy",
    }]
    expect(validatePipelineConnection({
      source: api.id,
      target: submodel.id,
      sourceHandle: "quotes",
      targetHandle: "in__policy",
    }, [api, upstream, submodel], edges, {
      definition_pricing: definition(child),
    })).toEqual({
      ok: false,
      reason: { kind: "duplicate-input-name", inputName: "policy" },
    })
  })

  it("rejects an undeclared canonical public handle", () => {
    const api = apiInput("api")
    const submodel = occurrence()
    const child = node("prepare", "Prepare")
    expect(validatePipelineConnection({
      source: api.id,
      target: submodel.id,
      sourceHandle: "quotes",
      targetHandle: "in__unknown",
    }, [api, submodel], [], { definition_pricing: definition(child) }))
      .toMatchObject({ ok: false })
  })
})

describe("a Workbench Output's tables", () => {
  const workbenchOutput: SimpleNode = {
    ...node("response", "response", NODE_TYPES.WORKBENCH_OUTPUT),
    data: {
      label: "response",
      description: "",
      nodeType: NODE_TYPES.WORKBENCH_OUTPUT,
      config: {
        tables: [
          { path: "$[:]", label: "pricing_output", emit: true, columns: [] },
          { path: "$[:].layers[:]", label: "layers", emit: true, columns: [] },
        ],
      },
    },
  }
  const priced = node("priced", "priced")
  const layered = node("layered", "layered")
  const nodes = [workbenchOutput, priced, layered]
  const filled: Edge[] = [{
    id: "filled",
    source: priced.id,
    target: workbenchOutput.id,
    sourceHandle: null,
    targetHandle: "pricing_output",
  }]
  const connect = (source: string, targetHandle: string | null, edges: Edge[] = []) =>
    validatePipelineConnection({ source, target: workbenchOutput.id, sourceHandle: null, targetHandle }, nodes, edges)
  const refused = (message: string) => ({ ok: false, reason: { kind: "invalid-connection", message } })

  it("takes a connection on a table's port", () => {
    expect(connect(priced.id, "pricing_output")).toEqual({ ok: true })
    expect(connect(layered.id, "layers", filled)).toEqual({ ok: true })
  })

  it("refuses a connection on no table, on a filled table, or from a node that fills another", () => {
    expect(connect(priced.id, null)).toEqual(refused("Connect to one of response's tables"))
    expect(connect(priced.id, "renamed")).toEqual(refused("Connect to one of response's tables"))
    expect(connect(layered.id, "pricing_output", filled)).toEqual(
      refused("pricing_output is already filled by priced"),
    )
    expect(connect(priced.id, "layers", filled)).toEqual(
      refused("priced already fills pricing_output; a node fills one table"),
    )
  })
})
