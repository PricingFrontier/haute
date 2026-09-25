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
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../../stores/useDocumentStatusStore"
import useNodeResultsStore, { hashConfig, type SolveProgress } from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import { buildGraph } from "../../utils/buildGraph"
import {
  executionJobStatusFromReason,
  executionMetricsFromError,
  executionTerminalReasonFromError,
} from "../../utils/executionDiagnostics"
import { solveIdentityConfig } from "../../utils/modellingExportConfig"
import type { SimpleEdge, SimpleNode } from "../editors"

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

export type StartSolveArgs = {
  nodeId: string
  /** The node's current config: what the job is keyed by and what it solves. */
  config: Record<string, unknown>
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
}

/**
 * Submit a solve for an optimiser node and register it for background polling.
 *
 * Resolves once the job is registered (or its start failure recorded); the
 * background poller owns progress and completion.
 */
export async function startOptimiserSolve({ nodeId, config, allNodes, edges, submodels }: StartSolveArgs): Promise<void> {
  const documentFence = captureDocumentExecutionFence()
  if (!isDocumentExecutionFenceCurrent(documentFence)) return
  const nodeLabel = allNodes.find((candidate) => candidate.id === nodeId)?.data.label || "Optimiser"
  const constraints = (config.constraints ?? {}) as Constraints
  const configHash = solveConfigHash(config)
  const source = useSettingsStore.getState().activeSource
  const structuralVersion = useGraphStore.getState().structuralVersion
  const { startSolveJob, failSolveJob } = useNodeResultsStore.getState()
  const startupFailureId = `startup-failure:${nodeId}`
  try {
    const result = await solveOptimiser({ graph: buildGraph(allNodes, edges, submodels), node_id: nodeId })
    if (!isDocumentExecutionFenceCurrent(documentFence)) return
    if (result.status === "started" && result.job_id) {
      startSolveJob(nodeId, result.job_id, nodeLabel, constraints, configHash, source, structuralVersion)
    } else if (result.status === "error") {
      startSolveJob(nodeId, startupFailureId, nodeLabel, constraints, configHash, source, structuralVersion)
      failSolveJob(nodeId, result.error || "Unknown error")
    }
  } catch (error) {
    if (!isDocumentExecutionFenceCurrent(documentFence)) return
    const message = apiErrorMessage(error)
    startSolveJob(nodeId, startupFailureId, nodeLabel, constraints, configHash, source, structuralVersion)
    failSolveJob(nodeId, message, solveFailureStatus(error, message))
  }
}

/**
 * Stop the node's running solve and record the server's answer.
 *
 * Mirrors modelling's Cancel: a job that finished first keeps its result, a
 * stopped job becomes a terminal failure, anything else is progress.
 */
export async function stopOptimiserSolve(nodeId: string): Promise<void> {
  const job = useNodeResultsStore.getState().solveJobs[nodeId]
  if (!job) return
  const documentFence = captureDocumentExecutionFence()
  const status = await cancelOptimiserSolve(job.jobId)
  // The reply belongs to the job it cancelled: if polling finished that job and
  // another started meanwhile, or the document changed, it must not touch them.
  if (!isDocumentExecutionFenceCurrent(documentFence)) return
  if (useNodeResultsStore.getState().solveJobs[nodeId]?.jobId !== job.jobId) return
  const { completeSolveJob, failSolveJob, updateSolveProgress } = useNodeResultsStore.getState()
  if (status.status === "completed" && status.result) completeSolveJob(nodeId, status.result, status)
  else if (FAILED_JOB_STATUSES.has(status.status)) failSolveJob(nodeId, status.message || "Optimisation stopped", status)
  else updateSolveProgress(nodeId, status)
}
