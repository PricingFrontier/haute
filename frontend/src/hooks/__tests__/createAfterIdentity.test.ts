import type { Edge, Node } from "@xyflow/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import useDocumentStatusStore from "../../stores/useDocumentStatusStore"
import useGraphStore from "../../stores/useGraphStore"
import { makeNode } from "../../test-utils/factories"
import { makePipelineEditorDocument } from "../../testSupport/pipelineDocumentFixture"
import { createAfterIdentity } from "../createAfterIdentity"

type Graph = { nodes: Node[]; edges: Edge[] }

const graphOf = (): Graph => ({
  nodes: [
    makeNode("quotes", "dataSource", { data: { label: "quotes", nodeType: "dataSource" } }),
    makeNode("price", "polars", { data: { label: "price", nodeType: "polars" } }),
  ],
  edges: [{ id: "e-quotes-price", source: "quotes", target: "price" }],
})

/** A creation whose identities resolve when the test says so. */
function pendingCreation(graphRef: { current: Graph }, dependsOnNodeIds?: string[]) {
  let answer!: (id: string) => void
  const apply = vi.fn()
  const addToast = vi.fn()
  const done = createAfterIdentity({
    graphRef,
    serialRef: { current: 0 },
    dependsOnNodeIds,
    resolve: () => new Promise<string>((resolve) => { answer = resolve }),
    apply,
    addToast,
    action: "Node creation",
    failure: "Create node failed",
  })
  return { apply, addToast, finish: async () => { answer("polars_3"); await done } }
}

const refusal = ["error", "Node creation was not applied because the graph changed while identity resolution was running."]

describe("createAfterIdentity", () => {
  beforeEach(() => {
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({ source_file: "rating/main.py" }))
    useGraphStore.setState({ submodelView: null })
  })

  it("applies onto the live graph after a selection or a drag", async () => {
    const graphRef = { current: graphOf() }
    const creation = pendingCreation(graphRef, ["price"])
    const moved = { ...graphRef.current, nodes: graphRef.current.nodes.map((node) => ({ ...node, selected: true, position: { x: 99, y: 99 } })) }
    graphRef.current = moved

    await creation.finish()

    expect(creation.addToast).not.toHaveBeenCalled()
    expect(creation.apply).toHaveBeenCalledWith("polars_3", moved)
  })

  it("is refused when the document is reloaded with the same structure", async () => {
    const graphRef = { current: graphOf() }
    const creation = pendingCreation(graphRef)
    graphRef.current = graphOf()
    useDocumentStatusStore.getState().loadDocumentStatus(makePipelineEditorDocument({ source_file: "rating/main.py" }))

    await creation.finish()

    expect(creation.apply).not.toHaveBeenCalled()
    expect(creation.addToast).toHaveBeenCalledWith(...refusal)
  })

  it("is refused when the canvas moves into a submodel view", async () => {
    const graphRef = { current: graphOf() }
    const creation = pendingCreation(graphRef)
    useGraphStore.getState().enterSubmodelView(graphOf())
    graphRef.current = graphOf()

    await creation.finish()

    expect(creation.apply).not.toHaveBeenCalled()
    expect(creation.addToast).toHaveBeenCalledWith(...refusal)
  })

  it("is refused when a node is renamed, which may take the name it was given or leave an edge's input name stale", async () => {
    const graphRef = { current: graphOf() }
    const creation = pendingCreation(graphRef, ["price"])
    graphRef.current = {
      ...graphRef.current,
      nodes: graphRef.current.nodes.map((node) => node.id === "quotes" ? { ...node, data: { ...node.data, label: "polars_3" } } : node),
    }

    await creation.finish()

    expect(creation.apply).not.toHaveBeenCalled()
    expect(creation.addToast).toHaveBeenCalledWith(...refusal)
  })
})
