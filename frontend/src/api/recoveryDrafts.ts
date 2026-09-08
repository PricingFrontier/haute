import { post, request } from "./client"
import {
  parseRecoveryDraft,
  parseRecoveryDraftApplyResponse,
  parseRecoveryDraftList,
  parseRecoveryDraftPreview,
  type RecoveryConfigContracts,
  type RecoveryDraft,
  type RecoveryDraftApply,
  type RecoveryDraftApplyResponse,
  type RecoveryDraftCreate,
  type RecoveryDraftList,
  type RecoveryDraftPatch,
  type RecoveryDraftPreview,
} from "../types/recoveryDraft"

const BASE = "/api/pipeline/repair/drafts"

export function listRecoveryDrafts(
  sourceFile: string,
  options?: { signal?: AbortSignal },
): Promise<RecoveryDraftList> {
  return request<unknown>(
    `${BASE}?${new URLSearchParams({ source_file: sourceFile })}`,
    options,
  ).then(parseRecoveryDraftList)
}

export function createRecoveryDraft(body: RecoveryDraftCreate): Promise<RecoveryDraft> {
  return post<unknown>(BASE, body).then(parseRecoveryDraft)
}

export function getRecoveryDraft(
  draftId: string,
  options?: { signal?: AbortSignal },
): Promise<RecoveryDraft> {
  return request<unknown>(`${BASE}/${encodeURIComponent(draftId)}`, options).then(
    parseRecoveryDraft,
  )
}

export function editRecoveryDraft(
  draftId: string,
  body: RecoveryDraftPatch,
): Promise<RecoveryDraft> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/edit`, body).then(parseRecoveryDraft)
}

export function previewRecoveryDraft(
  draftId: string,
  draftRevision: string,
): Promise<RecoveryDraftPreview> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/preview`, {
    draft_revision: draftRevision,
  }).then(parseRecoveryDraftPreview)
}

export function applyRecoveryDraft(
  draftId: string,
  body: RecoveryDraftApply,
): Promise<RecoveryDraftApplyResponse> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/apply`, body).then(
    parseRecoveryDraftApplyResponse,
  )
}

export function discardRecoveryDraft(
  draftId: string,
  draftRevision: string,
): Promise<RecoveryDraft> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/discard`, {
    draft_revision: draftRevision,
  }).then(parseRecoveryDraft)
}

export function restorePreviewRecoveryDraft(
  draftId: string,
  draftRevision: string,
): Promise<RecoveryDraftPreview> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/restore-preview`, {
    draft_revision: draftRevision,
  }).then(parseRecoveryDraftPreview)
}

export function restoreRecoveryDraft(
  draftId: string,
  body: RecoveryDraftApply,
): Promise<RecoveryDraftApplyResponse> {
  return post<unknown>(`${BASE}/${encodeURIComponent(draftId)}/restore`, body).then(
    parseRecoveryDraftApplyResponse,
  )
}

export function getRecoveryConfigContracts(options?: {
  signal?: AbortSignal
}): Promise<RecoveryConfigContracts> {
  return request<RecoveryConfigContracts>("/api/pipeline/repair/contracts", options)
}
