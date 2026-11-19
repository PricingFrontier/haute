/**
 * Starting, stopping and judging the staleness of an optimiser solve.
 *
 * The Solve pane, the result preview's Re-run and Ctrl+Enter all start a solve
 * through `startOptimiserSolve`, so every entry point records the same job
 * identity: the solve-identity config hash (export settings excluded), the
 * active source and the graph's structural version.
 */

import { cancelOptimiserSolve, solveOptimiser } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import { FAILED_JOB_STATUSES } from "../../api/types"
import useGraphStore from "../../stores/useGraphStore"
import { executionPart } from "../../stores/useDocumentStatusStore"
import useNodeResultsStore, { type SolveProgress } from "../../stores/useNodeResultsStore"
import { answer, watch } from "../../utils/answers"
import { hashConfig } from "../../utils/configHash"
import useSettingsStore from "../../stores/useSettingsStore"
import { canvasTarget, prepareNodeRequest, resultKey, type RequestScope } from "../../utils/requestGateway"
import {
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import { solveIdentityConfig } from "../../utils/modellingExportConfig"
import type { SimpleNode } from "../editors/_shared"

type Constraints = Record<string, Record<string, number>>

/** The hash a solve result is keyed by: the config minus its export settings. */
export function solveConfigHash(config: Record<string, unknown>): string {
  return hashConfig(solveIdentityConfig(config))
}

/** Whether a cached result no longer matches the node's current solve inputs. */
export function isSolveResultStale(
  cached: { configHash: string; source: string; structuralVersion: number } | null | undefined,
  config: Record<string, unknown>,
  source: string,
  structuralVersion: number,
): boolean {
  return !!cached && (
    cached.configHash !== solveConfigHash(config)
    || cached.source !== source
    || cached.structuralVersion !== structuralVersion
  )
}

function solveFailureStatus(error: unknown, message: string): SolveProgress | undefined {
  const metrics = executionMetricsFromError(error)
  if (!metrics) return undefined
  const terminalReason = executionTerminalReasonFromError(error)
  return {
    status: executionJobStatusFromReason(terminalReason),
    progress: 1,
    message,
    elapsed_seconds: 0,
    terminal_reason: terminalReason,
    execution_metrics: metrics,
  }
}

type StartSolveArgs = {
  nodeId: string
  /** The node's current config: what the job is keyed by and what it solves. */
  config: Record<string, unknown>
  allNodes: SimpleNode[]
  /** The document the solve runs (`utils/requestGateway`): the panel's. */
  requestScope: RequestScope | null
}

/**
 * Submit a solve for an optimiser node and register it for background polling.
 *
 * Resolves once the job is registered (or its start failure recorded); the
 * background poller owns progress and completion. The job is kept under the
 * node's result key as the view was when the solve was asked for, so a start
 * answered after the view changed stays with the occurrence it was started in.
 */
export async function startOptimiserSolve({ nodeId, config, allNodes, requestScope }: StartSolveArgs): Promise<void> {
  const documentFence = executionPart()
  if (!documentFence.holds()) return
  // Taken before preparing, which can refuse: its failure is the node's too.
  const key = resultKey(nodeId)
  const nodeLabel = allNodes.find((candidate) => candidate.id === nodeId)?.data.label || "Optimiser"
  const constraints = (config.constraints ?? {}) as Constraints
  const configHash = solveConfigHash(config)
  const source = useSettingsStore.getState().activeSource
  const structuralVersion = useGraphStore.getState().structuralVersion
  const { startSolveJob, failSolveJob } = useNodeResultsStore.getState()
  // Its own key, so a start that fails in two occurrences records two jobs.
  const startupFailureId = `startup-failure:${key}`
  await answer([documentFence], async () => {
    const target = prepareNodeRequest(requestScope, canvasTarget(nodeId))
    return solveOptimiser({ graph: target.graph, target: target.node })
  }, {
    ok: (result) => {
      if (result.status === "started" && result.job_id) {
        startSolveJob(key, result.job_id, nodeLabel, constraints, configHash, source, structuralVersion)
      } else if (result.status === "error") {
        startSolveJob(key, startupFailureId, nodeLabel, constraints, configHash, source, structuralVersion)
        failSolveJob(key, result.error || "Unknown error")
      }
    },
    failed: (error) => {
      const message = apiErrorMessage(error)
      startSolveJob(key, startupFailureId, nodeLabel, constraints, configHash, source, structuralVersion)
      failSolveJob(key, message, solveFailureStatus(error, message))
    },
  })
}

/**
 * Stop the running solve kept under `key` (the node's result key) and record the
 * server's answer there.
 *
 * Mirrors modelling's Cancel: a job that finished first keeps its result, a
 * stopped job becomes a terminal failure, anything else is progress.
 */
export async function stopOptimiserSolve(key: string): Promise<void> {
  const job = useNodeResultsStore.getState().solveJobs[key]
  if (!job) return
  // The reply belongs to the job it cancelled: if polling finished that job and
  // another started meanwhile, or the document changed, it must not touch them.
  // A refusal is the caller's to report, late or not.
  await answer([
    executionPart(),
    watch(useNodeResultsStore, (state) => state.solveJobs[key]?.jobId, (_, now) => now === job.jobId),
  ], cancelOptimiserSolve(job.jobId), {
    ok: (status) => {
      const { completeSolveJob, failSolveJob, updateSolveProgress } = useNodeResultsStore.getState()
      if (status.status === "completed" && status.result) completeSolveJob(key, status.result, status)
      else if (FAILED_JOB_STATUSES.has(status.status)) failSolveJob(key, status.message || "Optimisation stopped", status)
      else updateSolveProgress(key, status)
    },
    stale: (settled) => {
      if ("error" in settled) throw settled.error
    },
  })
}
