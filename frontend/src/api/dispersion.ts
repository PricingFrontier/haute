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

import {
  validateDispersionEstimateResponse,
  validateDispersionEstimateStatusResponse,
} from "../generated/api-contracts.modelling.validators.mjs"
import { expectGeneratedContract } from "../types/generatedContractValidation"
import { ApiError, post, request } from "./client"
import { TERMINAL_JOB_STATUSES } from "./types"
import type {
  DispersionEstimateStart,
  DispersionEstimateStatus,
  DispersionParam,
  GraphPayload,
} from "./types"

// The generated validators own both responses' structure (API-R03).
function parseDispersionEstimateResponse(value: unknown): DispersionEstimateStart {
  return expectGeneratedContract("DispersionEstimateResponse", validateDispersionEstimateResponse, value)
}

function parseDispersionStatusResponse(value: unknown): DispersionEstimateStatus {
  return expectGeneratedContract("DispersionEstimateStatusResponse", validateDispersionEstimateStatusResponse, value)
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
