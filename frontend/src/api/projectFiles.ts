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
import { ApiError, bootstrapHauteSession, request, throwApiError } from "./client"
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
 * Send one file to a place as the raw body, through XMLHttpRequest, whose upload reports
 * progress. Never retried: the answer to a write can be lost after the write landed.
 * `replaceRevision` names the revision of the file it replaces; without it an existing
 * file is refused.
 */
export function uploadProjectFile(
  place: ProjectFilePlace,
  path: string,
  file: Blob,
  options: { replaceRevision?: string | null; onProgress?: (loaded: number, total: number) => void } = {},
): ProjectFileUpload {
  const extra: Record<string, string> = options.replaceRevision ? { replace_revision: options.replaceRevision } : {}
  const xhr = new XMLHttpRequest()
  let aborted = false
  let sent = false
  const promise = (async () => {
    await bootstrapHauteSession()
    const response = await new Promise<Response>((resolve, reject) => {
      xhr.open("PUT", `/api/project-files/content?${projectFileParams(place, path, extra)}`)
      xhr.setRequestHeader("Content-Type", "application/octet-stream")
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) options.onProgress?.(event.loaded, event.total)
      }
      xhr.upload.onload = () => {
        sent = true
      }
      xhr.onload = () => resolve(new Response(xhr.responseText, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers: { "Content-Type": xhr.getResponseHeader("Content-Type") ?? "application/json" },
      }))
      xhr.onerror = () => reject(new ApiError("The upload could not reach the server.", 0, "The upload could not reach the server."))
      xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"))
      if (aborted) {
        reject(new DOMException("Aborted", "AbortError"))
        return
      }
      xhr.send(file)
    })
    if (!response.ok) await throwApiError(response)
    const data: unknown = await response.json()
    return expectGeneratedContract("ProjectFileWriteResponse", validateProjectFileWriteResponse, data)
  })()
  return {
    promise,
    abort: () => {
      if (sent) return
      aborted = true
      xhr.abort()
    },
  }
}

/** Delete one file if it is still `revision`. Never retried, as a write is not. */
export function deleteProjectFile(
  place: ProjectFilePlace,
  path: string,
  revision: string,
): Promise<ProjectFileDeleteResponse> {
  return request<unknown>(`/api/project-files/content?${projectFileParams(place, path, { revision })}`, {
    method: "DELETE",
    retry: { maxRetries: 0, baseDelayMs: 100 },
  }).then((data) => expectGeneratedContract("ProjectFileDeleteResponse", validateProjectFileDeleteResponse, data))
}
