import { beforeEach, describe, expect, it } from "vitest"
import type { Node } from "@xyflow/react"
import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { makeIdentifiedNode, makeNode } from "../../test-utils/factories"
import type { PipelineEdge, SubmodelDefinition } from "../../types/node"
import type { GraphPayload, PreparedGraph } from "../../api/types"
import { addressKey, childAddress, nodeFrame, rootAddress } from "../nodeAddress"
import {
  RequestScopeError,
  answerNodeNamer,
  canvasTarget,
  documentRequestScope,
  prepareCanvasRequest,
  prepareNodeRequest,
  preparePipelineRequest,
  resultKey,
  resultKeysOf,
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
    // A preview sends the target by address, every field present.
    expect(request.wireTarget).toEqual({ kind: "node", occurrence: null, node_id: "workbench_input", port_label: null, port_labels: null })
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
    // A preview sends the child by its occurrence and local id, never a runtime id.
    expect(request.wireTarget).toEqual({ kind: "node", occurrence: OCCURRENCE, node_id: "bands", port_label: null, port_labels: null })
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
    expect(request.wireTarget).toEqual({ kind: "occurrence_input", occurrence: OCCURRENCE, port: "policy" })
    expect(request.nodeId).toBe("workbench_input")
    expect(request.portLabel).toBe("policy_frame")
    // A card with one port reads it without a handle.
    expect(canvasTarget(input.id)).toEqual(target)
  })

  it("reads the Output card's port at the child that produces it", () => {
    // On the main pipeline, the collapsed occurrence's output handle is the same port.
    expect(canvasTarget(OCCURRENCE, "out__premium")).toEqual({ kind: "occurrenceOutput", occurrence: OCCURRENCE, port: "premium" })
    open()

    const request = prepareNodeRequest(documentRequestScope, canvasTarget(card("output").id, "premium"))

    expect(request.wireTarget).toEqual({ kind: "occurrence_output", occurrence: OCCURRENCE, port: "premium" })
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
    expect(prepareNodeRequest(documentRequestScope, canvasTarget(input.id, "policy")).wireTarget)
      .toEqual({ kind: "node", occurrence: OCCURRENCE, node_id: input.id, port_label: "policy", port_labels: null })
    expect(prepareNodeRequest(documentRequestScope, canvasTarget(input.id, "policy")).nodeId)
      .toBe(`submodel_runtime/br%20rating/${encodeURIComponent(input.id)}`)
  })

  it("names an answer's nodes by the view the request was prepared in, after the view changed", () => {
    open()
    const request = prepareNodeRequest(documentRequestScope, canvasTarget("bands"))
    useGraphStore.getState().exitSubmodelView()

    expect(request.nodeNamed("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
  })

  it("keys results by occurrence, and gives every key a deleted node's results live under", () => {
    // A second occurrence of the same definition.
    useGraphStore.getState().setNodes((nodes) => [...nodes, makeNode("copy", "submodel", {
      data: { label: "copy", nodeType: "submodel", config: { definition_id: DEFINITION_ID, alias: "copy", instance_of: OCCURRENCE } },
    })])
    expect(resultKey("workbench_input")).toBe("workbench_input")
    // An occurrence takes its children's results with it.
    expect(resultKeysOf(OCCURRENCE)).toEqual([
      OCCURRENCE,
      addressKey(childAddress(OCCURRENCE, "bands")),
      addressKey(childAddress(OCCURRENCE, "tables")),
    ])

    open()
    expect(resultKey("bands")).toBe(addressKey(childAddress(OCCURRENCE, "bands")))
    expect(prepareCanvasRequest(documentRequestScope, "bands").resultKey).toBe(resultKey("bands"))
    // A definition's child leaves every occurrence.
    expect(resultKeysOf("bands")).toEqual([
      addressKey(childAddress(OCCURRENCE, "bands")),
      addressKey(childAddress("copy", "bands")),
    ])
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

  it("turns addresses into runtime ids for the requests still sent by id, and names nodes without preparing", () => {
    expect(runtimeNodeId(rootAddress("workbench_input"))).toBe("workbench_input")
    expect(runtimeNodeId(childAddress(OCCURRENCE, "bands"))).toBe("submodel_runtime/br%20rating/bands")
    open()
    expect(answerNodeNamer()("submodel_runtime/br%20rating/tables")?.id).toBe("tables")
  })
})
