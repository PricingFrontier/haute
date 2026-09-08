import { validateRecoveryDraft } from "../generated/api-contracts.recovery-draft.validators.mjs"
import { validateRecoveryDraftList } from "../generated/api-contracts.recovery-draft-list.validators.mjs"
import { validateRecoveryDraftPreview } from "../generated/api-contracts.recovery-draft-preview.validators.mjs"
import { formatGeneratedContractError } from "./generatedContractValidation"
import { parsePipelineEditorDocument, type PipelineEditorDocument } from "./pipelineDocument"

import type {
  JsonValue,
  RecoveryDraft,
  RecoveryDraftList,
  RecoveryDraftPreview,
} from "../generated/api-contracts.recovery.generated"

export type {
  JsonValue,
  RecoveryDraft,
  RecoveryDraftList,
  RecoveryDraftNode,
  RecoveryDraftPreview,
  RecoveryFieldChange,
  RecoveryIssue,
} from "../generated/api-contracts.recovery.generated"
export type RecoveryConfigContracts = {
  fingerprint: string
  schemas: Record<string, Record<string, unknown>>
}

export type RecoveryDraftCreate = {
  source_file: string
  source_revision: string
  targets: { source_file: string; recovery_id: string }[]
  mode: "recover" | "reset"
}

export type RecoveryDraftPatch = {
  draft_revision: string
  configs: Record<string, Record<string, JsonValue>>
  reviewed: boolean
}

export type RecoveryDraftApply = {
  draft_revision: string
  source_revision: string
  plan_hash: string
  operation_id: string
}

export type RecoveryDraftApplyResponse = {
  draft: RecoveryDraft
  document: PipelineEditorDocument
  applied_artifacts: string[]
}

function contractError(
  name: string,
  errors:
    | readonly {
        readonly instancePath: string
        readonly schemaPath?: string
        readonly keyword: string
        readonly params: Record<string, unknown>
        readonly message?: string
      }[]
    | null,
): Error {
  return new Error(formatGeneratedContractError(name, errors))
}

export function parseRecoveryDraft(value: unknown): RecoveryDraft {
  if (!validateRecoveryDraft(value))
    throw contractError("RecoveryDraft", validateRecoveryDraft.errors)
  return value as RecoveryDraft
}

export function parseRecoveryDraftList(value: unknown): RecoveryDraftList {
  if (!validateRecoveryDraftList(value))
    throw contractError("RecoveryDraftList", validateRecoveryDraftList.errors)
  return value as RecoveryDraftList
}

export function parseRecoveryDraftPreview(value: unknown): RecoveryDraftPreview {
  if (!validateRecoveryDraftPreview(value))
    throw contractError("RecoveryDraftPreview", validateRecoveryDraftPreview.errors)
  return value as RecoveryDraftPreview
}

export function parseRecoveryDraftApplyResponse(value: unknown): RecoveryDraftApplyResponse {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("RecoveryDraftApplyResponse: expected object")
  }
  const object = value as Record<string, unknown>
  const keys = Object.keys(object).sort()
  if (keys.join(",") !== "applied_artifacts,document,draft") {
    throw new Error("RecoveryDraftApplyResponse: unexpected or missing fields")
  }
  if (
    !Array.isArray(object.applied_artifacts) ||
    object.applied_artifacts.some((item) => typeof item !== "string" || item.length === 0)
  ) {
    throw new Error("RecoveryDraftApplyResponse: applied_artifacts must be non-empty strings")
  }
  return {
    draft: parseRecoveryDraft(object.draft),
    document: parsePipelineEditorDocument(object.document),
    applied_artifacts: object.applied_artifacts,
  }
}
