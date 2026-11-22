/**
 * Where each model of an interactive build stands (specs/frontend-modelling-optimiser-ui):
 * its training and its latest run, kept under the model's own key.
 */
import { useState } from "react"
import { advanceRunProgress, EMPTY_RUN_PROGRESS } from "./runProgress"
import { useResultKey } from "../panels/useGraph"
import { hasTrainResult } from "../stores/nodeResultsSelectors"
import useNodeResultsStore, { type CachedTrainResult } from "../stores/useNodeResultsStore"
import { useStagedViewKey, useStagedViews } from "./useStagedTraining"
import { modelResultKey } from "./interactiveModels"

/** Where a model's last run stands, as the list and the model's entry name it. */
export type RunState = "Not trained" | "Training" | "Trained" | "Failed"

export function runState(training: boolean, cached: CachedTrainResult | undefined): RunState {
  if (training) return "Training"
  if (cached === undefined) return "Not trained"
  return hasTrainResult(cached) ? "Trained" : "Failed"
}

/** The key one of a node's models keeps its training and run under. */
export function useModelResultKey(nodeId: string, modelId: string): string {
  return modelResultKey(useResultKey(nodeId), modelId)
}

/** A model's run state, read from its own key. */
export function useModelRunState(nodeId: string, modelId: string): { state: RunState; progress: number } {
  const key = useModelResultKey(nodeId, modelId)
  const job = useNodeResultsStore((s) => s.trainJobs[key])
  const cached = useNodeResultsStore((s) => s.trainResults[key])
  const stageKey = useStagedViewKey(key)
  const saved = useStagedViews((s) => s.models[stageKey]?.state)
  const [previous, setPrevious] = useState({ key: stageKey, value: EMPTY_RUN_PROGRESS })
  const stageJob = saved?.job
  const matches = !job || job.jobId === stageJob?.id
  // Status and stage reads are independent. A completed staged job must stay
  // visible until a stage read confirms whether it queued automatic work.
  const confirmed = stageJob?.id === cached?.jobId
    || [saved?.current, saved?.preview, saved?.final].some((record) => record?.result.response.job_id === cached?.jobId)
  const awaitingStage = !job && previous.key === stageKey && previous.value.active
    && cached?.jobId === previous.value.id && Boolean(cached?.result?.staged) && !confirmed
  const failed = !awaitingStage && matches && (
    (stageJob != null && ["failed", "cancelled", "interrupted"].includes(stageJob.status))
    || (saved?.next_preview != null && ["failed", "interrupted"].includes(saved.next_preview.status))
  )
  const next = advanceRunProgress(previous.key === stageKey ? previous.value : EMPTY_RUN_PROGRESS, {
    id: awaitingStage ? cached?.jobId : job?.jobId ?? stageJob?.id,
    progress: job?.progress?.progress,
    automatic: matches && stageJob?.automatic,
    queued: awaitingStage || saved?.next_preview?.status === "queued",
    running: Boolean(job) || stageJob?.status === "running",
    failed: failed || (!job && cached !== undefined && !hasTrainResult(cached)),
  })
  if (previous.key !== stageKey || previous.value.id !== next.id || previous.value.active !== next.active || previous.value.progress !== next.progress) {
    setPrevious({ key: stageKey, value: next })
  }
  return { state: next.active ? "Training" : failed ? "Failed"
    : !cached && saved?.current ? "Trained" : runState(false, cached), progress: next.progress }
}
