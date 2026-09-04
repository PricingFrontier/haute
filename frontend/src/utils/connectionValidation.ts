import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import { isSubmodelDefinition, isSubmodelInstanceConfig } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"
import { SUBMODEL_INPUT_HANDLE } from "./flowHandles"
import {
  edgeInputName,
  incomingEdgeInputNames,
  resolveSubmodelBoundaryNode,
  resolveSubmodelBoundaryNodes,
  submodelInputPortIdForName,
} from "./apiInputPorts"

type ConnectionLike = {
  source: string | null | undefined
  target: string | null | undefined
  sourceHandle?: string | null
  targetHandle?: string | null
  sourceHandleType?: string
  targetHandleType?: string
  fromHandle?: { type?: string } | null
  toHandle?: { type?: string } | null
}

type SubmodelsLike = Record<string, unknown> | undefined

function nodeMap(nodes: readonly SimpleNode[]): Map<string, SimpleNode> {
  return new Map(nodes.map((node) => [node.id, node]))
}

export type ConnectionValidationFailure =
  | { kind: "duplicate-input-name"; inputName: string }
  | { kind: "invalid-connection"; message: string }

export type ConnectionValidationResult =
  | { ok: true }
  | { ok: false; reason: ConnectionValidationFailure }

/**
 * React Flow's Loose mode can report the node at either end as `source`
 * depending on which handle the user started dragging from. Normalize the
 * API-input endpoint before applying source-frame rules and input-name
 * uniqueness, so both gesture directions have exactly the same validation.
 */
function actualEdge(
  connection: ConnectionLike,
  nodesById: Map<string, SimpleNode>,
): {
  source: string
  target: string
  sourceHandle: string | null | undefined
  targetHandle: string | null | undefined
} {
  const sourceNode = nodesById.get(connection.source as string)
  const targetNode = nodesById.get(connection.target as string)
  if (
    (targetNode?.data.nodeType === NODE_TYPES.API_INPUT
      && sourceNode?.data.nodeType !== NODE_TYPES.API_INPUT)
    || (sourceNode?.data.nodeType === NODE_TYPES.SUBMODEL
      && (connection.sourceHandle?.startsWith("in__")
        || connection.sourceHandle === SUBMODEL_INPUT_HANDLE))
    || (connection.sourceHandleType === "target"
      && connection.targetHandleType === "source")
  ) {
    return {
      source: connection.target as string,
      target: connection.source as string,
      sourceHandle: connection.targetHandle,
      targetHandle: connection.sourceHandle,
    }
  }
  return {
    source: connection.source as string,
    target: connection.target as string,
    sourceHandle: connection.sourceHandle,
    targetHandle: connection.targetHandle,
  }
}

function handleType(value: unknown): string | undefined {
  return typeof value === "object" && value !== null && "type" in value
    && typeof (value as { type?: unknown }).type === "string"
    ? (value as { type: string }).type
    : undefined
}

function targetEndpointIsSource(connection: ConnectionLike): boolean {
  return connection.targetHandleType === "source"
    || handleType(connection.targetHandle) === "source"
    || handleType(connection.toHandle) === "source"
}

function resolvedTarget(
  targetNode: SimpleNode,
  targetHandle: string | null | undefined,
  submodels: SubmodelsLike,
): SimpleNode {
  return resolveSubmodelBoundaryNode(targetNode, targetHandle, "in", submodels) ?? targetNode
}

/**
 * Validate a pipeline connection before React Flow commits it.
 *
 * The one name derivation here is also used by panel/editor surfaces. A
 * candidate is rejected when its derived input name already belongs to an
 * incoming edge of the target, or when an API source has no labelled frame
 * handle. The latter deliberately rejects the zero-frame default handle in
 * both Loose-mode gesture directions.
 */
export function validatePipelineConnection(
  connection: ConnectionLike,
  nodes: readonly SimpleNode[] = [],
  edges: readonly SimpleEdge[] = [],
  submodels: SubmodelsLike = undefined,
): ConnectionValidationResult {
  if (!connection.source || !connection.target) {
    return { ok: false, reason: { kind: "invalid-connection", message: "Connection endpoints are incomplete" } }
  }
  if (connection.source === connection.target) {
    return { ok: false, reason: { kind: "invalid-connection", message: "Self-connections are not allowed" } }
  }

  // Output-to-output gestures are converted into EdgeJoin nodes by
  // useEdgeHandlers. They are not ordinary input edges, so input-name and
  // null-api-handle validation must not veto them.
  if (targetEndpointIsSource(connection)) return { ok: true }

  if (nodes.length === 0) return { ok: true }
  const nodesById = nodeMap(nodes)
  const candidate = actualEdge(connection, nodesById)
  const sourceNode = nodesById.get(candidate.source)
  const targetNode = nodesById.get(candidate.target)
  if (!sourceNode || !targetNode) {
    return { ok: false, reason: { kind: "invalid-connection", message: "Connection node is missing" } }
  }

  if (sourceNode.data.nodeType === NODE_TYPES.API_INPUT
    && (candidate.sourceHandle === null || candidate.sourceHandle === undefined)) {
    return { ok: false, reason: { kind: "invalid-connection", message: "apiInput connections require a frame handle" } }
  }

  let candidateName: string
  try {
    candidateName = edgeInputName(
      {
        id: "<candidate>",
        source: candidate.source,
        target: candidate.target,
        sourceHandle: candidate.sourceHandle,
        targetHandle: candidate.targetHandle,
      },
      sourceNode,
      submodels,
    )
  } catch (error) {
    return {
      ok: false,
      reason: {
        kind: "invalid-connection",
        message: error instanceof Error ? error.message : String(error),
      },
    }
  }

  try {
    if (
      targetNode.data.nodeType === NODE_TYPES.SUBMODEL
      && isSubmodelInstanceConfig(targetNode.data.config)
    ) {
      if (candidate.targetHandle === SUBMODEL_INPUT_HANDLE) {
        const definition = submodels?.[targetNode.data.config.definitionId]
        if (!isSubmodelDefinition(definition, targetNode.data.config.definitionId)) {
          throw new Error("Canonical submodel definition is unavailable")
        }
        const portId = submodelInputPortIdForName(definition, candidateName)
        if (portId === null) {
          if (targetNode.data.config.instanceOf !== undefined) {
            throw new Error("New public inputs can only be added through the definition owner")
          }
          return { ok: true }
        }
        if (edges.some(
          (edge) => edge.target === candidate.target
            && edge.targetHandle === `in__${portId}`,
        )) {
          return {
            ok: false,
            reason: { kind: "duplicate-input-name", inputName: candidateName },
          }
        }
        return { ok: true }
      }
      const targets = resolveSubmodelBoundaryNodes(
        targetNode,
        candidate.targetHandle,
        "in",
        submodels,
      )
      if (targets.length === 0) {
        throw new Error(
          "Canonical submodel input " + String(candidate.targetHandle)
          + " has no declared targets",
        )
      }
      const existingBinding = edges.some(
        (edge) =>
          edge.target === candidate.target
          && edge.targetHandle === candidate.targetHandle,
      )
      if (existingBinding) {
        const portId = candidate.targetHandle?.slice("in__".length)
        if (!portId) {
          throw new Error("Canonical submodel input handle is malformed")
        }
        const config = targetNode.data.config
        const definition = isSubmodelInstanceConfig(config)
          ? submodels?.[config.definitionId]
          : undefined
        if (!isSubmodelDefinition(definition, config?.definitionId)) {
          throw new Error("Canonical submodel definition is unavailable")
        }
        const inputName = definition._inputPortInputNames?.[portId]
        if (typeof inputName !== "string" || inputName.length === 0) {
          throw new Error(`Canonical submodel input ${portId} has no authoritative identity`)
        }
        return {
          ok: false,
          reason: {
            kind: "duplicate-input-name",
            inputName,
          },
        }
      }
      return { ok: true }
    }

    const candidateTarget = resolvedTarget(targetNode, candidate.targetHandle, submodels)
    const existingNames = incomingEdgeInputNames({
      targetNodeId: candidateTarget.id,
      boundaryNodeId: candidate.target,
      nodes,
      edges,
      submodels,
    })
    if (existingNames.includes(candidateName)) {
      return { ok: false, reason: { kind: "duplicate-input-name", inputName: candidateName } }
    }
  } catch (error) {
    return {
      ok: false,
      reason: {
        kind: "invalid-connection",
        message: error instanceof Error ? error.message : String(error),
      },
    }
  }
  return { ok: true }
}

export function isPipelineConnectionValid(
  connection: ConnectionLike,
  nodes: readonly SimpleNode[] = [],
  edges: readonly SimpleEdge[] = [],
  submodels: SubmodelsLike = undefined,
): boolean {
  return validatePipelineConnection(connection, nodes, edges, submodels).ok
}
