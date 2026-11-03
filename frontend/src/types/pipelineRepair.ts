import {
  expectBoolean,
  expectExactKeys,
  expectNonBlankString,
  expectPlainObject,
} from "./guards"
import type { PipelineRepairApplyResponse } from "../generated/api-contracts.generated"
import { expectGeneratedContract } from "./generatedContractValidation"
import {
  editorDocumentFromContract,
  loadEditorDocumentValidators,
  type PipelineEditorDocument,
} from "./pipelineDocument"

const PARSER = "parsePipelineRepairResponse"

export interface RemoveUnavailableNodeRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetRecoveryId: string
  deleteConfig: boolean
}

export interface RemoveBrokenConnectionRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetConnectionId: string
}

export type RecoverUnavailableNodeAction = "reset" | "recover"

export interface RecoverUnavailableNodeRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetRecoveryId: string
  action: RecoverUnavailableNodeAction
}

function parseRequestBase(value: unknown, field: string): RemoveUnavailableNodeRequest {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["sourceFile", "sourceRevision", "targetSourceFile", "targetRecoveryId", "deleteConfig"])
  return {
    sourceFile: expectNonBlankString(PARSER, object.sourceFile, `${field}.sourceFile`),
    sourceRevision: expectNonBlankString(PARSER, object.sourceRevision, `${field}.sourceRevision`),
    targetSourceFile: expectNonBlankString(PARSER, object.targetSourceFile, `${field}.targetSourceFile`),
    targetRecoveryId: expectNonBlankString(PARSER, object.targetRecoveryId, `${field}.targetRecoveryId`),
    deleteConfig: expectBoolean(PARSER, object.deleteConfig, `${field}.deleteConfig`),
  }
}

export function parseRemoveUnavailableNodeRequest(value: unknown): RemoveUnavailableNodeRequest {
  return parseRequestBase(value, "request")
}

export type {
  PipelineRepairChange,
  PipelineRepairFieldChange,
} from "../generated/api-contracts.generated"

type RepairKind = PipelineRepairApplyResponse["repair_kind"]

/** A repair's answer, its document read as the editor's and its kind the route's own. */
type RepairApplied<K extends RepairKind> = Omit<PipelineRepairApplyResponse, "repair_kind" | "document"> & {
  repair_kind: K
  document: PipelineEditorDocument
}

export type RemoveUnavailableNodeApplyResponse = RepairApplied<"remove_unavailable_node">
export type RecoverUnavailableNodeApplyResponse = RepairApplied<"reset_node" | "recover_node">
export type RemoveBrokenConnectionApplyResponse = RepairApplied<"remove_connection">

function unique(values: string[], field: string): void {
  if (new Set(values).size !== values.length) {
    throw new Error(`${PARSER}: ${field} contains duplicate values`)
  }
}

// The generated validator owns the answer's structure. Each repair route answers its
// own kinds, and names each artifact and each changed path once.
async function parseRepairApplyResponse<K extends RepairKind>(
  value: unknown,
  kinds: readonly K[],
): Promise<RepairApplied<K>> {
  const { validatePipelineRepairApplyResponse } = await loadEditorDocumentValidators()
  const response = expectGeneratedContract(
    "PipelineRepairApplyResponse",
    validatePipelineRepairApplyResponse,
    value,
  )
  const kind = kinds.find((item) => item === response.repair_kind)
  if (kind === undefined) {
    throw new Error(`${PARSER}: response.repair_kind ${response.repair_kind} is not one of ${kinds.join(", ")}`)
  }
  unique(response.applied_artifacts, "response.applied_artifacts")
  unique(response.changes.map((change) => change.path), "response.changes paths")
  return { ...response, repair_kind: kind, document: editorDocumentFromContract(response.document) }
}

export function parseRemoveUnavailableNodeApplyResponse(
  value: unknown,
): Promise<RemoveUnavailableNodeApplyResponse> {
  return parseRepairApplyResponse(value, ["remove_unavailable_node"] as const)
}

export function parseRecoverUnavailableNodeApplyResponse(
  value: unknown,
): Promise<RecoverUnavailableNodeApplyResponse> {
  return parseRepairApplyResponse(value, ["reset_node", "recover_node"] as const)
}

export function parseRemoveBrokenConnectionApplyResponse(
  value: unknown,
): Promise<RemoveBrokenConnectionApplyResponse> {
  return parseRepairApplyResponse(value, ["remove_connection"] as const)
}
