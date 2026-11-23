/**
 * The client's upload transport (specs/frontend-shared, "API client"): a raw body sent through
 * XMLHttpRequest, whose upload reports progress. Part of the client, kept apart from
 * api/client.ts so its code stays out of the initial bundle beside its only (lazy) consumer,
 * the Files panel. It sends once, as every write is sent.
 */

import { ServerUnreachableError } from "./transportErrors"

/** A raw body sent through XMLHttpRequest, for its upload progress, and how to stop it. */
export interface UploadSend {
  /** The answer, whatever its status; a request that could not reach the server rejects. */
  response: Promise<Response>
  /** Stop sending. Once the last byte is sent it does nothing: the answer is still reported. */
  abort: () => void
}

/**
 * Send `body` raw to `url` with `method` through XMLHttpRequest, whose upload reports
 * progress, once, as every write is sent. The caller bootstraps the session first.
 */
export function sendUpload(
  method: "PUT" | "POST",
  url: string,
  body: Blob,
  options: { contentType: string; onProgress?: (loaded: number, total: number) => void },
): UploadSend {
  const xhr = new XMLHttpRequest()
  let aborted = false
  let sent = false
  const response = new Promise<Response>((resolve, reject) => {
    xhr.open(method, url)
    xhr.setRequestHeader("Content-Type", options.contentType)
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
    xhr.onerror = () => reject(new ServerUnreachableError(url, "XMLHttpRequest error"))
    xhr.onabort = () => reject(new DOMException("Aborted", "AbortError"))
    if (aborted) {
      reject(new DOMException("Aborted", "AbortError"))
      return
    }
    xhr.send(body)
  })
  return {
    response,
    abort: () => {
      if (sent) return
      aborted = true
      xhr.abort()
    },
  }
}
