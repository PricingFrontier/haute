import type { Connection, Node } from "@xyflow/react"
import type { PipelineEdge } from "../types/node"
import {
  applyCanonicalSubmodelBoundaryConnection,
  connectCanonicalSubmodelInputFromParentConnection,
  reconcileCanonicalSubmodelBoundaryState,
  removeCanonicalSubmodelBoundaryEdges,
  removeCanonicalSubmodelInputPort,
  type CanonicalSubmodelInputConnectionState,
} from "./canonicalSubmodelBoundaryEditing"

export interface SubmodelBoundaryEditState {
  submodelName: string
  instanceId: string
  definitionId: string
  viewNodes: Node[]
  viewEdges: PipelineEdge[]
  parentNodes: Node[]
  parentEdges: PipelineEdge[]
  submodels: Record<string, unknown>
}

export type SubmodelBoundaryEditResult = SubmodelBoundaryEditState

export const applySubmodelBoundaryConnection = (state: SubmodelBoundaryEditState, connection: Connection) =>
  applyCanonicalSubmodelBoundaryConnection(state, connection)
export const connectSubmodelInputFromParentConnection = (
  state: CanonicalSubmodelInputConnectionState,
  connection: Connection,
) => connectCanonicalSubmodelInputFromParentConnection(state, connection)
export const removeSubmodelBoundaryEdges = (state: SubmodelBoundaryEditState, edgeIds: string[]) =>
  removeCanonicalSubmodelBoundaryEdges(state, edgeIds)
export const removeSubmodelInputPort = (state: SubmodelBoundaryEditState, portName: string) =>
  removeCanonicalSubmodelInputPort(state, portName)
export const reconcileSubmodelBoundaryState = (state: SubmodelBoundaryEditState) =>
  reconcileCanonicalSubmodelBoundaryState(state)
