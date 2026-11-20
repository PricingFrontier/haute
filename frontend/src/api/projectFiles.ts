/**
 * The Files panel's endpoints, /api/project-files (specs/server-api).
 *
 * Lives outside api/client.ts on purpose: the only consumer is the lazy-loaded
 * Files panel, so keeping these functions and their validators in their own
 * module keeps them out of the initial JS bundle, whose gzip budget is in
 * scripts/check-bundle-size.mjs. They share client.ts's fetch machinery, session
 * bootstrap and error mapping.
 */

import {
  validateProjectFileDeleteResponse,
  validateProjectFileWriteResponse,
  validateProjectFolderResponse,
} from "../generated/api-contracts.session.validators.mjs"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { bootstrapHauteSession, request, throwApiError } from "./client"
import { sendUpload, type UploadSend } from "./clientUpload"
import type {
  ProjectFileDeleteResponse,
  ProjectFilePlace,
  ProjectFileWriteResponse,
  ProjectFolderResponse,
} from "./types"

function projectFileParams(place: ProjectFilePlace, path: string, extra: Record<string, string> = {}): string {
  return new URLSearchParams({ place, path, ...extra }).toString()
}

/** One folder of a place, relative to it ("" is the place's root). */
export function listProjectFiles(
  place: ProjectFilePlace,
  path: string,
  options?: { signal?: AbortSignal },
): Promise<ProjectFolderResponse> {
  return request<unknown>(`/api/project-files?${projectFileParams(place, path)}`, options).then((data) =>
    expectGeneratedContract("ProjectFolderResponse", validateProjectFolderResponse, data),
  )
}

/** Where a file downloads from: a same-origin attachment, so the session cookie goes with it. */
export function projectFileDownloadUrl(place: ProjectFilePlace, path: string): string {
  return `/api/project-files/content?${projectFileParams(place, path)}`
}

export interface ProjectFileUpload {
  promise: Promise<ProjectFileWriteResponse>
  /**
   * Stop sending. Once the last byte is sent it does nothing: the server publishes and
   * captures a whole body, so its answer, warnings and capture included, is still
   * reported.
   */
  abort: () => void
}

/**
 * Send one file to a place as the raw body, through the client's `sendUpload`, whose
 * upload reports progress, once, as every write is sent: the answer to a write can be lost
 * after the write landed. `replaceRevision` names the revision of the file it replaces;
 * without it an existing file is refused.
 */
export function uploadProjectFile(
  place: ProjectFilePlace,
  path: string,
  file: Blob,
  options: { replaceRevision?: string | null; onProgress?: (loaded: number, total: number) => void } = {},
): ProjectFileUpload {
  const extra: Record<string, string> = options.replaceRevision ? { replace_revision: options.replaceRevision } : {}
  let sending: UploadSend | null = null
  let aborted = false
  const promise = (async () => {
    await bootstrapHauteSession()
    if (aborted) throw new DOMException("Aborted", "AbortError")
    sending = sendUpload("PUT", `/api/project-files/content?${projectFileParams(place, path, extra)}`, file, {
      contentType: "application/octet-stream",
      onProgress: options.onProgress,
    })
    const response = await sending.response
    if (!response.ok) await throwApiError(response)
    const data: unknown = await response.json()
    return expectGeneratedContract("ProjectFileWriteResponse", validateProjectFileWriteResponse, data)
  })()
  return {
    promise,
    abort: () => {
      if (sending === null) aborted = true
      else sending.abort()
    },
  }
}

/** Delete one file if it is still `revision`, once, as every write is sent. */
export function deleteProjectFile(
  place: ProjectFilePlace,
  path: string,
  revision: string,
): Promise<ProjectFileDeleteResponse> {
  return request<unknown>(`/api/project-files/content?${projectFileParams(place, path, { revision })}`, {
    method: "DELETE",
  }).then((data) => expectGeneratedContract("ProjectFileDeleteResponse", validateProjectFileDeleteResponse, data))
}
