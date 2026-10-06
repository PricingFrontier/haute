import {
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectNonBlankString,
  expectPlainObject,
  expectString,
  expectStringLiteral,
} from "./guards"
import {
  parseNodeCompleteness,
  parsePipelineEditorDocument,
  type PipelineEditorDocument,
  type PipelineNodeCompleteness,
} from "./pipelineDocument"

const PARSER = "parsePipelineRepairResponse"

export interface RemoveUnavailableNodeRequest {
  sourceFile: string
  sourceRevision: string
  targetSourceFile: string
  targetRecoveryId: string
  deleteConfig: boolean
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

export interface PipelineRepairChange {
  path: string
  operation: "update" | "delete"
  description: string
  diff: string
  diff_truncated: boolean
}

export interface PipelineRepairFieldChange {
  path: string
  outcome: "retained" | "defaulted" | "needs_input" | "needs_review" | "removed" | "blocked"
  reason: string
}

export interface RemoveUnavailableNodeApplyResponse {
  repair_kind: "remove_unavailable_node"
  applied_artifacts: string[]
  /** Bounded display diffs of what the server wrote. */
  changes: PipelineRepairChange[]
  document: PipelineEditorDocument
  field_changes: PipelineRepairFieldChange[]
  completeness: PipelineNodeCompleteness[]
  previous_config: Record<string, unknown> | null
}

export interface RecoverUnavailableNodeApplyResponse extends Omit<RemoveUnavailableNodeApplyResponse, "repair_kind"> {
  repair_kind: "reset_node" | "recover_node"
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

const FIELD_OUTCOMES = ["retained", "defaulted", "needs_input", "needs_review", "removed", "blocked"] as const

function parseFieldChange(value: unknown, field: string): PipelineRepairFieldChange {
  const object = expectPlainObject(PARSER, value, field)
  expectExactKeys(PARSER, object, field, ["path", "outcome", "reason"])
  return {
    path: expectString(PARSER, object.path, `${field}.path`),
    outcome: expectStringLiteral(PARSER, object.outcome, `${field}.outcome`, FIELD_OUTCOMES),
    reason: expectNonBlankString(PARSER, object.reason, `${field}.reason`),
  }
}

function parseRepairExtras(object: Record<string, unknown>): {
  field_changes: PipelineRepairFieldChange[]
  completeness: PipelineNodeCompleteness[]
  previous_config: Record<string, unknown> | null
} {
  return {
    field_changes: expectArray(PARSER, object.field_changes, "response.field_changes").map(
      (item, index) => parseFieldChange(item, `response.field_changes[${index}]`),
    ),
    completeness: expectArray(PARSER, object.completeness, "response.completeness").map(
      (item, index) => parseNodeCompleteness(item, `response.completeness[${index}]`),
    ),
    previous_config:
      object.previous_config === null
        ? null
        : { ...expectPlainObject(PARSER, object.previous_config, "response.previous_config") },
  }
}

function parseChanges(object: Record<string, unknown>): PipelineRepairChange[] {
  const changes = expectArray(PARSER, object.changes, "response.changes").map((item, index) => parseChange(item, `response.changes[${index}]`))
  if (changes.length === 0) throw new Error(`${PARSER}: response.changes must not be empty`)
  if (new Set(changes.map((change) => change.path)).size !== changes.length) throw new Error(`${PARSER}: response.changes contains duplicate paths`)
  return changes
}

const APPLY_RESPONSE_KEYS = ["repair_kind", "applied_artifacts", "changes", "document", "field_changes", "completeness", "previous_config"]

export function parseRemoveUnavailableNodeApplyResponse(value: unknown): RemoveUnavailableNodeApplyResponse {
  const object = expectPlainObject(PARSER, value, "response")
  expectExactKeys(PARSER, object, "response", APPLY_RESPONSE_KEYS)
  return {
    repair_kind: expectStringLiteral(PARSER, object.repair_kind, "response.repair_kind", ["remove_unavailable_node"]),
    applied_artifacts: uniqueStrings(object.applied_artifacts, "response.applied_artifacts", true),
    changes: parseChanges(object),
    document: parsePipelineEditorDocument(object.document),
    ...parseRepairExtras(object),
  }
}

export function parseRecoverUnavailableNodeApplyResponse(value: unknown): RecoverUnavailableNodeApplyResponse {
  const object = expectPlainObject(PARSER, value, "response")
  expectExactKeys(PARSER, object, "response", APPLY_RESPONSE_KEYS)
  return {
    repair_kind: expectStringLiteral(PARSER, object.repair_kind, "response.repair_kind", ["reset_node", "recover_node"]),
    applied_artifacts: uniqueStrings(object.applied_artifacts, "response.applied_artifacts", true),
    changes: parseChanges(object),
    document: parsePipelineEditorDocument(object.document),
    ...parseRepairExtras(object),
  }
}
