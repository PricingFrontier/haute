import {
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectNonBlankString,
  expectPlainObject,
  expectString,
  expectStringLiteral,
} from "./guards"
import { parsePipelineEditorDocument, type PipelineEditorDocument } from "./pipelineDocument"

const PARSER = "parsePipelineRepairResponse"
const PLAN_HASH = /^[0-9a-f]{64}$/

export interface RemoveUnavailableNodeRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetRecoveryId: string
  deleteConfig: boolean
}

export interface ApplyRemoveUnavailableNodeRequest extends RemoveUnavailableNodeRequest {
  planHash: string
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

export function parseApplyRemoveUnavailableNodeRequest(value: unknown): ApplyRemoveUnavailableNodeRequest {
  const object = expectPlainObject(PARSER, value, "request")
  expectExactKeys(PARSER, object, "request", ["sourceFile", "sourceRevision", "targetSourceFile", "targetRecoveryId", "deleteConfig", "planHash"])
  return { ...parseRequestBase({
    sourceFile: object.sourceFile,
    sourceRevision: object.sourceRevision,
    targetSourceFile: object.targetSourceFile,
    targetRecoveryId: object.targetRecoveryId,
    deleteConfig: object.deleteConfig,
  }, "request"), planHash: planHash(object.planHash, "request.planHash") }
}

export interface PipelineRepairChange {
  path: string
  operation: "update" | "delete"
  description: string
  diff: string
  diff_truncated: boolean
}

export interface RemoveUnavailableNodeDryRunResponse {
  repair_kind: "remove_unavailable_node"
  source_file: string
  source_revision: string
  target_source_file: string
  target_recovery_id: string
  target_authored_id: string
  delete_config: boolean
  plan_hash: string
  changes: PipelineRepairChange[]
  retained_artifacts: string[]
  warnings: string[]
  predicted_load_status: "ready" | "degraded"
}

export interface RemoveUnavailableNodeApplyResponse {
  repair_kind: "remove_unavailable_node"
  plan_hash: string
  applied_artifacts: string[]
  document: PipelineEditorDocument
}

function planHash(value: unknown, field: string): string {
  const hash = expectNonBlankString(PARSER, value, field)
  if (!PLAN_HASH.test(hash)) throw new Error(`${PARSER}: ${field} must be a 64-character lowercase hex hash`)
  return hash
}

function uniqueStrings(value: unknown, field: string, nonEmpty: boolean): string[] {
  const values = expectArray(PARSER, value, field).map((item, index) =>
    nonEmpty ? expectNonBlankString(PARSER, item, `${field}[${index}]`) : expectString(PARSER, item, `${field}[${index}]`),
  )
  if (new Set(values).size !== values.length) throw new Error(`${PARSER}: ${field} contains duplicate values`)
  return values
}

function parseChange(value: unknown, field: string): PipelineRepairChange {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["path", "operation", "description", "diff", "diff_truncated"])
  const description = expectNonBlankString(PARSER, object.description, `${field}.description`)
  if (Array.from(description).length > 1024) {
    throw new Error(`${PARSER}: ${field}.description exceeds 1024 characters`)
  }
  const diff = expectString(PARSER, object.diff, `${field}.diff`)
  if (Array.from(diff).length > 131_072) {
    throw new Error(`${PARSER}: ${field}.diff exceeds 131072 characters`)
  }
  return {
    path: expectNonBlankString(PARSER, object.path, `${field}.path`),
    operation: expectStringLiteral(PARSER, object.operation, `${field}.operation`, ["update", "delete"]),
    description,
    diff,
    diff_truncated: expectBoolean(PARSER, object.diff_truncated, `${field}.diff_truncated`),
  }
}

export function parseRemoveUnavailableNodeDryRunResponse(value: unknown): RemoveUnavailableNodeDryRunResponse {
  const object = expectPlainObject(PARSER, value, "response")
  expectExactKeys(PARSER, object, "response", [
    "repair_kind", "source_file", "source_revision", "target_source_file", "target_recovery_id",
    "target_authored_id", "delete_config", "plan_hash", "changes", "retained_artifacts", "warnings",
    "predicted_load_status",
  ])
  const changes = expectArray(PARSER, object.changes, "response.changes").map((item, index) =>
    parseChange(item, `response.changes[${index}]`),
  )
  if (changes.length === 0) throw new Error(`${PARSER}: response.changes must not be empty`)
  const changePaths = changes.map((change) => change.path)
  if (new Set(changePaths).size !== changePaths.length) throw new Error(`${PARSER}: response.changes contains duplicate paths`)
  return {
    repair_kind: expectStringLiteral(PARSER, object.repair_kind, "response.repair_kind", ["remove_unavailable_node"]),
    source_file: expectNonBlankString(PARSER, object.source_file, "response.source_file"),
    source_revision: expectNonBlankString(PARSER, object.source_revision, "response.source_revision"),
    target_source_file: expectNonBlankString(PARSER, object.target_source_file, "response.target_source_file"),
    target_recovery_id: expectNonBlankString(PARSER, object.target_recovery_id, "response.target_recovery_id"),
    target_authored_id: expectNonBlankString(PARSER, object.target_authored_id, "response.target_authored_id"),
    delete_config: expectBoolean(PARSER, object.delete_config, "response.delete_config"),
    plan_hash: planHash(object.plan_hash, "response.plan_hash"),
    changes,
    retained_artifacts: uniqueStrings(object.retained_artifacts, "response.retained_artifacts", true),
    warnings: uniqueStrings(object.warnings, "response.warnings", false),
    predicted_load_status: expectStringLiteral(PARSER, object.predicted_load_status, "response.predicted_load_status", ["ready", "degraded"]),
  }
}

export function parseRemoveUnavailableNodeApplyResponse(value: unknown): RemoveUnavailableNodeApplyResponse {
  const object = expectPlainObject(PARSER, value, "response")
  expectExactKeys(PARSER, object, "response", ["repair_kind", "plan_hash", "applied_artifacts", "document"])
  return {
    repair_kind: expectStringLiteral(PARSER, object.repair_kind, "response.repair_kind", ["remove_unavailable_node"]),
    plan_hash: planHash(object.plan_hash, "response.plan_hash"),
    applied_artifacts: uniqueStrings(object.applied_artifacts, "response.applied_artifacts", true),
    document: parsePipelineEditorDocument(object.document),
  }
}
