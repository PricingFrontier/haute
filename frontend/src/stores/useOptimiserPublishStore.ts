/**
 * Publishing an optimiser result: saving the artifact file and logging the
 * MLflow run.
 *
 * One state per node, shared by every surface that publishes (the Export pane
 * and the frontier detail card), so a save started on one is visible on the
 * other and survives pane switches. Each state belongs to one solve job: a new
 * job's state starts empty rather than showing the previous job's receipts.
 */

import { create } from "zustand"
import { logOptimiserToMlflow, saveOptimiser } from "../api/client"
import { apiErrorCode, apiErrorMessage } from "../api/errors"
import type { MlflowDestinationKey, MlflowLogResponse, SaveOptimiserResponse } from "../api/types"

/** What a publish acts on: the job's own solve, or one frontier point. */
export type PublishTarget = { pointIndex: number | null }

export type SaveReceipt = SaveOptimiserResponse & { pointIndex: number | null }
export type LogReceipt = MlflowLogResponse & { pointIndex: number | null }

export type OptimiserPublishState = {
  jobId: string
  saving: boolean
  logging: boolean
  saveReceipt: SaveReceipt | null
  logReceipt: LogReceipt | null
  saveError: string | null
  logError: string | null
  logErrorCode: string | null
  /**
   * The server refused to replace an existing file: its message and the exact
   * request refused. Replacing retries that request, never the current form.
   */
  overwritePrompt: OverwritePrompt | null
}

export type OverwritePrompt = { message: string; request: SaveArgs }

export type SaveArgs = {
  nodeId: string
  jobId: string
  pointIndex: number | null
  outputPath: string
  version: string
  stale: boolean
  overwrite?: boolean
}

export type LogArgs = {
  nodeId: string
  jobId: string
  pointIndex: number | null
  destination: string
  experimentName: string
  stale: boolean
}

/** The /save refusal for an existing destination, answered with a Replace confirmation. */
const RESULT_EXISTS_CODE = "optimiser_result_exists"

const EMPTY: Omit<OptimiserPublishState, "jobId"> = {
  saving: false,
  logging: false,
  saveReceipt: null,
  logReceipt: null,
  saveError: null,
  logError: null,
  logErrorCode: null,
  overwritePrompt: null,
}

type Store = {
  byNode: Record<string, OptimiserPublishState>
  save: (args: SaveArgs) => Promise<void>
  log: (args: LogArgs) => Promise<void>
  dismissOverwrite: (nodeId: string, jobId: string) => void
}

function stateFor(byNode: Record<string, OptimiserPublishState>, nodeId: string, jobId: string): OptimiserPublishState {
  const current = byNode[nodeId]
  return current && current.jobId === jobId ? current : { jobId, ...EMPTY }
}

const useOptimiserPublishStore = create<Store>((set, get) => {
  const patch = (nodeId: string, jobId: string, update: Partial<OptimiserPublishState>) => {
    set((store) => {
      // A reply for a job the node has moved past is dropped.
      const current = store.byNode[nodeId]
      if (current && current.jobId !== jobId) return store
      return { byNode: { ...store.byNode, [nodeId]: { ...stateFor(store.byNode, nodeId, jobId), ...update } } }
    })
  }

  return {
    byNode: {},

    save: async ({ nodeId, jobId, pointIndex, outputPath, version, stale, overwrite = false }) => {
      set((store) => ({
        byNode: {
          ...store.byNode,
          [nodeId]: { ...stateFor(store.byNode, nodeId, jobId), saving: true, saveError: null, overwritePrompt: null },
        },
      }))
      try {
        const response = await saveOptimiser({
          job_id: jobId,
          output_path: outputPath,
          point_index: pointIndex ?? undefined,
          version,
          overwrite,
          stale,
        })
        patch(nodeId, jobId, { saving: false, saveReceipt: { ...response, pointIndex } })
      } catch (error) {
        if (apiErrorCode(error) === RESULT_EXISTS_CODE && !overwrite) {
          patch(nodeId, jobId, {
            saving: false,
            overwritePrompt: { message: apiErrorMessage(error), request: { nodeId, jobId, pointIndex, outputPath, version, stale } },
          })
          return
        }
        patch(nodeId, jobId, { saving: false, saveError: apiErrorMessage(error) })
      }
    },

    log: async ({ nodeId, jobId, pointIndex, destination, experimentName, stale }) => {
      set((store) => ({
        byNode: {
          ...store.byNode,
          [nodeId]: { ...stateFor(store.byNode, nodeId, jobId), logging: true, logError: null, logErrorCode: null },
        },
      }))
      try {
        const response = await logOptimiserToMlflow({
          job_id: jobId,
          point_index: pointIndex ?? undefined,
          // Read at click time, so a switch back to Local folder after the solve sends "".
          destination: destination as "" | MlflowDestinationKey,
          // The node's current experiment; blank logs to the default it shows.
          experiment_name: experimentName || null,
          stale,
        })
        patch(nodeId, jobId, { logging: false, logReceipt: { ...response, pointIndex } })
      } catch (error) {
        patch(nodeId, jobId, {
          logging: false,
          logError: apiErrorMessage(error),
          logErrorCode: apiErrorCode(error) ?? null,
        })
      }
    },

    dismissOverwrite: (nodeId, jobId) => {
      if (get().byNode[nodeId]?.jobId !== jobId) return
      patch(nodeId, jobId, { overwritePrompt: null })
    },
  }
})

/** The node's publish state for a job; empty when it belongs to another job. */
export function usePublishState(nodeId: string, jobId: string | null): OptimiserPublishState | null {
  return useOptimiserPublishStore((store) => {
    if (!jobId) return null
    const current = store.byNode[nodeId]
    return current && current.jobId === jobId ? current : null
  })
}

export default useOptimiserPublishStore
