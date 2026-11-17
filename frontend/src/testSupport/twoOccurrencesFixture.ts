/**
 * A pipeline holding two occurrences of one submodel definition: `pricing`, which owns
 * it, and `pricing_2`, a read-only copy of it. Their children share the definition's
 * local ids, so a result kept under a child's bare id would show in both.
 */
import type { Node } from "@xyflow/react"

import useGraphStore from "../stores/useGraphStore"
import { makeNode } from "../test-utils/factories"
import type { SubmodelDefinition } from "../types/node"
import { addressKey, childAddress } from "../utils/nodeAddress"

export const DEFINITION_ID = "definition_pricing"

export interface Occurrence {
  readonly instanceId: string
  readonly definitionId: string
}

/** The occurrence that owns the definition. */
export const OWNER: Occurrence = { instanceId: "pricing", definitionId: DEFINITION_ID }
/** A read-only copy of the owner. */
export const COPY: Occurrence = { instanceId: "pricing_2", definitionId: DEFINITION_ID }

function occurrenceNode(id: string, instanceOf?: string): Node {
  return makeNode(id, "submodel", {
    data: {
      label: id,
      nodeType: "submodel",
      config: { definition_id: DEFINITION_ID, alias: id, ...(instanceOf ? { instance_of: instanceOf } : {}) },
    },
  })
}

/** Load the pipeline, the definition holding `children`, with neither occurrence open. */
export function loadTwoOccurrences(children: Node[]): void {
  const definition: SubmodelDefinition = {
    definitionId: DEFINITION_ID,
    file: "modules/pricing.py",
    graph: { nodes: children, edges: [] },
    inputPorts: [],
    outputPorts: [],
  }
  useGraphStore.getState().loadGraphSnapshot({
    nodes: [occurrenceNode(OWNER.instanceId), occurrenceNode(COPY.instanceId, OWNER.instanceId)],
    edges: [],
    preamble: "",
    submodels: { [DEFINITION_ID]: definition },
  })
}

/** Show `occurrence`, leaving the one open first. */
export function openOccurrence(occurrence: Occurrence): void {
  if (useGraphStore.getState().focus) useGraphStore.getState().exitSubmodelView()
  useGraphStore.getState().enterSubmodelView(occurrence)
}

/** A child as the open occurrence's canvas shows it. */
export function onCanvas(id: string): Node {
  const node = useGraphStore.getState().nodes.find((candidate) => candidate.id === id)
  if (!node) throw new Error(`${id} is not on the canvas`)
  return node
}

/** The key a child's results are kept under in `occurrence`. */
export function childKey(occurrence: Occurrence, nodeId: string): string {
  return addressKey(childAddress(occurrence.instanceId, nodeId))
}
