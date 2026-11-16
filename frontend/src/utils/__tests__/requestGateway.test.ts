import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import type { GraphPayload, PreparedGraph } from "../../api/types"
import { childAddress, nodeFrame, rootAddress } from "../nodeAddress"
import {
  RequestScopeError,
  addressOfRuntimeId,
  answerNodeNamer,
  canvasTarget,
  documentRequestScope,
  prepareNodeRequest,
  preparePipelineRequest,
  runtimeNodeId,
} from "../requestGateway"

// Occurrence "br rating" has a space, so its runtime id is encoded.
const OCCURRENCE = "br rating"
const DEFINITION_ID = "br_rating"

function load() {
  const definition: SubmodelDefinition = {
    definitionId: DEFINITION_ID,
    file: "modules/br_rating.py",
    graph: {
      nodes: [makeIdentifiedNode("bands", "banding"), makeIdentifiedNode("tables", "ratingStep")],
      edges: [{ id: "e1", source: "bands", target: "tables", data: { _inputName: "bands" } }],
    },
    inputPorts: [{ name: "policy", targets: [{ nodeId: "bands", handleId: null }] }],
    outputPorts: [{ name: "premium", source: { nodeId: "tables", handleId: "rated" } }],
  }
  const occurrence: Node = makeNode(OCCURRENCE, "submodel", {
    data: { label: OCCURRENCE, nodeType: "submodel", config: { definition_id: DEFINITION_ID, alias: "br_rating" } },
  })
  const edges: PipelineEdge[] = [
    { id: "feed", source: "workbench_input", sourceHandle: "policy_frame", target: OCCURRENCE, targetHandle: "in__policy" },
  ]
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [makeIdentifiedNode("workbench_input", "workbenchInput"), occurrence],
    edges,
    preamble: "import polars as pl",
    submodels: { [DEFINITION_ID]: definition },
  })
}

const open = () => useGraphStore.getState().enterSubmodelView({ instanceId: OCCURRENCE, definitionId: DEFINITION_ID })
const card = (direction: "input" | "output") =>
  useGraphStore.getState().nodes.find((node) => node.type === "submodelPort" && node.data.portDirection === direction)!

describe("the request gateway", () => {
  beforeEach(() => {
    useGraphStore.getState().resetForTests()
    useSettingsStore.setState({ activeSource: "live" })
    load()
  })

  it("prepares a request about a node of the pipeline: the whole document, the node's id, the source and generations", () => {
    useDocumentStatusStore.setState({ sourceFile: "rating/main.py", executionGeneration: 7 })
    const generation = useGraphStore.getState().semanticGeneration

    const request = prepareNodeRequest(documentRequestScope, canvasTarget("workbench_input"))

    expect(request.graph.nodes.map((node) => node.id)).toEqual(["workbench_input", OCCURRENCE])
    expect(Object.keys(request.graph.submodels ?? {})).toEqual([DEFINITION_ID])
    expect(request.graph.preamble).toBe("import polars as pl")
    expect(request.nodeId).toBe("workbench_input")
    expect(request.portLabel).toBeUndefined()
    expect(request.target).toEqual(nodeFrame(rootAddress("workbench_input")))
    expect(request.source).toBe("live")
    expect(request.document).toEqual({ sourceFile: "rating/main.py", executionGeneration: 7 })
    expect(request.semanticGeneration).toBe(generation)
  })

  it("sends the whole pipeline and a child's runtime id from inside an open submodel", () => {
    open()

    const request = prepareNodeRequest(documentRequestScope, canvasTarget("bands"))

    expect(request.graph.nodes.map((node) => node.id)).toEqual(["workbench_input", OCCURRENCE])
    expect(request.nodeId).toBe("submodel_runtime/br%20rating/bands")
    expect(request.target).toEqual(nodeFrame(childAddress(OCCURRENCE, "bands")))
    // An answer names a node on screen by its runtime id, and one outside the submodel by its own.
    expect(request.nodeNamed("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
    expect(request.nodeNamed("workbench_input")?.id).toBe("workbench_input")
    expect(request.nodeNamed("submodel_runtime/other/tables")).toBeNull()
  })

  it("reads the Input card's port at the pipeline's node feeding it, from the document", () => {
    open()
    const input = card("input")

    const target = canvasTarget(input.id, "policy")
    const request = prepareNodeRequest(documentRequestScope, target)

    expect(target).toEqual({ kind: "occurrenceInput", occurrence: OCCURRENCE, port: "policy" })
    expect(request.nodeId).toBe("workbench_input")
    expect(request.portLabel).toBe("policy_frame")
    // A card with one port reads it without a handle.
    expect(canvasTarget(input.id)).toEqual(target)
  })

  it("reads the Output card's port at the child that produces it", () => {
    open()

    const request = prepareNodeRequest(documentRequestScope, canvasTarget(card("output").id, "premium"))

    expect(request.nodeId).toBe("submodel_runtime/br%20rating/tables")
    expect(request.portLabel).toBe("rated")
  })

  it("lets no caller send a graph it built itself", () => {
    const built: GraphPayload = { nodes: [], edges: [] }
    // @ts-expect-error a graph a caller built is not one the gateway prepared
    const sent: PreparedGraph = built

    expect(sent).toBe(built)
  })

  it("reads a card as itself when its port is not named or nothing feeds it yet", () => {
    useGraphStore.getState().setEdges([])
    open()
    const input = card("input")

    expect(canvasTarget(input.id, "policy")).toEqual(nodeFrame(childAddress(OCCURRENCE, input.id), "policy"))
    expect(prepareNodeRequest(documentRequestScope, canvasTarget(input.id, "policy")).nodeId)
      .toBe(`submodel_runtime/br%20rating/${encodeURIComponent(input.id)}`)
  })

  it("names an answer's nodes by the view the request was prepared in, after the view changed", () => {
    open()
    const request = prepareNodeRequest(documentRequestScope, canvasTarget("bands"))
    useGraphStore.getState().exitSubmodelView()

    expect(request.nodeNamed("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
  })

  it("refuses a request where there is no document to run, sending nothing", () => {
    expect(() => prepareNodeRequest(null, canvasTarget("workbench_input"))).toThrow(RequestScopeError)
    expect(() => preparePipelineRequest(null)).toThrow(RequestScopeError)
  })

  it("prepares a request about the whole pipeline, also from inside a submodel", () => {
    open()

    const request = preparePipelineRequest(documentRequestScope)

    expect(request.graph.nodes.map((node) => node.id)).toEqual(["workbench_input", OCCURRENCE])
    expect(request.source).toBe("live")
  })

  it("turns addresses into runtime ids and reads answers' ids back, and names nodes without preparing", () => {
    expect(runtimeNodeId(rootAddress("workbench_input"))).toBe("workbench_input")
    expect(runtimeNodeId(childAddress(OCCURRENCE, "bands"))).toBe("submodel_runtime/br%20rating/bands")
    open()
    expect(addressOfRuntimeId("submodel_runtime/br%20rating/bands")).toEqual(childAddress(OCCURRENCE, "bands"))
    expect(addressOfRuntimeId("workbench_input")).toEqual(rootAddress("workbench_input"))
    expect(answerNodeNamer()("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
  })
})
