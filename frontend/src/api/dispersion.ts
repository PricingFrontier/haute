/**
 * GLM dispersion-estimation endpoints (NB theta / Tweedie var_power).
 *
 * Lives outside api/client.ts on purpose: the only consumer is the
 * lazy-loaded modelling config panel, so keeping these functions (and their
 * runtime parsers) in their own module keeps them out of the initial JS
 * bundle — the initial-gzip budget in scripts/check-bundle-size.mjs is the
 * gate this layout exists to respect. They share client.ts's fetch machinery
 * via its exported `request`/`post`.
 */

import { ApiError, post, request } from "./client"
import { JOB_STATUS_VALUES, TERMINAL_JOB_STATUSES } from "./types"
import type {
  DispersionEstimateStart,
  DispersionEstimateStatus,
  DispersionParam,
  GraphPayload,
  JobStatus,
} from "./types"

function asRecord(value: unknown, parser: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${parser}: expected an object`)
  }
  return value as Record<string, unknown>
}

function numberOrNull(obj: Record<string, unknown>, key: string): number | null {
  const value = obj[key]
  return typeof value === "number" ? value : null
}

function stringOrNull(obj: Record<string, unknown>, key: string): string | null {
  const value = obj[key]
  return typeof value === "string" ? value : null
}

function parseDispersionEstimateResponse(value: unknown): DispersionEstimateStart {
  const obj = asRecord(value, "parseDispersionEstimateResponse")
  if (obj.status !== "started" || typeof obj.job_id !== "string") {
    throw new Error(
      `parseDispersionEstimateResponse: unexpected payload (status ${String(obj.status)})`,
    )
  }
  return { status: "started", job_id: obj.job_id }
}

function parseDispersionStatusResponse(value: unknown): DispersionEstimateStatus {
  const obj = asRecord(value, "parseDispersionStatusResponse")
  if (
    typeof obj.status !== "string"
    || !JOB_STATUS_VALUES.includes(obj.status as JobStatus)
  ) {
    throw new Error(`parseDispersionStatusResponse: invalid \`status\` ${String(obj.status)}`)
  }
  return {
    status: obj.status as JobStatus,
    progress: typeof obj.progress === "number" ? obj.progress : 0,
    message: typeof obj.message === "string" ? obj.message : "",
    elapsed_seconds: typeof obj.elapsed_seconds === "number" ? obj.elapsed_seconds : 0,
    param: stringOrNull(obj, "param"),
    value: numberOrNull(obj, "value"),
    llf: numberOrNull(obj, "llf"),
    n_fits: numberOrNull(obj, "n_fits"),
    error: stringOrNull(obj, "error"),
    terminal_reason: stringOrNull(obj, "terminal_reason"),
  }
}

export interface EstimateDispersionArgs {
  graph: GraphPayload
  node_id: string
  param: DispersionParam
  source?: string
  signal?: AbortSignal
}

export function estimateGlmDispersion(args: EstimateDispersionArgs): Promise<DispersionEstimateStart> {
  const { signal, ...payload } = args
  // Pipeline execution can take minutes for large datasets — match /train.
  return post<unknown>(
    "/api/modelling/dispersion/estimate",
    { ...payload, source: payload.source ?? "live" },
    { signal, timeout: 600_000 },
  ).then(parseDispersionEstimateResponse)
}

export function getDispersionStatus(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<DispersionEstimateStatus> {
  return request<unknown>(
    `/api/modelling/dispersion/status/${encodeURIComponent(jobId)}`,
    options,
  ).then(parseDispersionStatusResponse)
}

export function cancelDispersion(
  jobId: string,
  options?: { signal?: AbortSignal },
): Promise<DispersionEstimateStatus> {
  return post<unknown>(
    `/api/modelling/dispersion/cancel/${encodeURIComponent(jobId)}`,
    {},
    options,
  ).then(parseDispersionStatusResponse)
}

/** Start a dispersion estimation and poll it to completion.
 *
 * Resolves with the estimated value; rejects with the job's message on any
 * non-completed terminal status. The value is returned to the caller (the
 * config panel) for the user to accept into the config — the estimate is an
 * explicit user choice, never a silently applied default.
 */
export async function runDispersionEstimate(
  args: EstimateDispersionArgs,
  options?: { signal?: AbortSignal; pollIntervalMs?: number },
): Promise<number> {
  const { job_id } = await estimateGlmDispersion({ ...args, signal: options?.signal })
  const pollInterval = options?.pollIntervalMs ?? 500
  try {
    for (;;) {
      if (options?.signal?.aborted) {
        throw new DOMException("Dispersion estimation aborted", "AbortError")
      }
      const status = await getDispersionStatus(job_id, { signal: options?.signal })
      if (status.status === "completed") {
        if (status.value === null) {
          throw new Error("Dispersion estimation completed without a value")
        }
        return status.value
      }
      if (TERMINAL_JOB_STATUSES.has(status.status)) {
        throw new ApiError(status.error || status.message || `Dispersion estimation ${status.status}`, 500)
      }
      await new Promise((resolve) => setTimeout(resolve, pollInterval))
    }
  } catch (error) {
    if (options?.signal?.aborted) {
      await cancelDispersion(job_id)
    }
    throw error
  }
}
