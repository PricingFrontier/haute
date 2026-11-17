/**
 * Publishing an optimiser result: saving the artifact file and logging the
 * MLflow run.
 *
 * One state per node, kept under its result key (`resultKey`: an occurrence's own for a
 * child of a submodel), shared by every surface that publishes (the Export pane and the
 * frontier detail card), so a save started on one is visible on the other and survives
 * pane switches. Each state belongs to one solve job: a new job's state starts empty
 * rather than showing the previous job's receipts.
 * The states belong to the document too: loading another one (or accepting a
 * change on disk) forgets them, and a reply that arrives after it is dropped.
 */

import { create } from "zustand"
import { logOptimiserToMlflow, saveOptimiser } from "../api/client"
import { apiErrorCode, apiErrorMessage } from "../api/errors"
import type { MlflowDestinationKey, MlflowLogResponse, SaveOptimiserResponse } from "../api/types"
import useDocumentStatusStore, {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
  type DocumentExecutionFence,
} from "./useDocumentStatusStore"

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
  /** The key the node's results are kept under. */
  resultKey: string
  jobId: string
  pointIndex: number | null
  outputPath: string
  version: string
  stale: boolean
  overwrite?: boolean
}

export type LogArgs = {
  /** The key the node's results are kept under. */
  resultKey: string
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
  /** Each node's state, by the key its results are kept under. */
  byResultKey: Record<string, OptimiserPublishState>
  save: (args: SaveArgs) => Promise<void>
  log: (args: LogArgs) => Promise<void>
  dismissOverwrite: (resultKey: string, jobId: string) => void
}

function stateFor(byResultKey: Record<string, OptimiserPublishState>, resultKey: string, jobId: string): OptimiserPublishState {
  const current = byResultKey[resultKey]
  return current && current.jobId === jobId ? current : { jobId, ...EMPTY }
}

const useOptimiserPublishStore = create<Store>((set, get) => {
  const patch = (
    resultKey: string,
    jobId: string,
    fence: DocumentExecutionFence,
    update: Partial<OptimiserPublishState>,
  ) => {
    // A reply for a document the editor has moved past is nobody's now.
    if (!isDocumentExecutionFenceCurrent(fence)) return
    set((store) => {
      // A reply for a job the node has moved past is dropped.
      const current = store.byResultKey[resultKey]
      if (current && current.jobId !== jobId) return store
      return { byResultKey: { ...store.byResultKey, [resultKey]: { ...stateFor(store.byResultKey, resultKey, jobId), ...update } } }
    })
  }

  return {
    byResultKey: {},

    save: async ({ resultKey, jobId, pointIndex, outputPath, version, stale, overwrite = false }) => {
      const fence = captureDocumentExecutionFence()
      set((store) => ({
        byResultKey: {
          ...store.byResultKey,
          [resultKey]: { ...stateFor(store.byResultKey, resultKey, jobId), saving: true, saveError: null, overwritePrompt: null },
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
        patch(resultKey, jobId, fence, { saving: false, saveReceipt: { ...response, pointIndex } })
      } catch (error) {
        if (apiErrorCode(error) === RESULT_EXISTS_CODE && !overwrite) {
          patch(resultKey, jobId, fence, {
            saving: false,
            overwritePrompt: { message: apiErrorMessage(error), request: { resultKey, jobId, pointIndex, outputPath, version, stale } },
          })
          return
        }
        patch(resultKey, jobId, fence, { saving: false, saveError: apiErrorMessage(error) })
      }
    },

    log: async ({ resultKey, jobId, pointIndex, destination, experimentName, stale }) => {
      const fence = captureDocumentExecutionFence()
      set((store) => ({
        byResultKey: {
          ...store.byResultKey,
          [resultKey]: { ...stateFor(store.byResultKey, resultKey, jobId), logging: true, logError: null, logErrorCode: null },
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
        patch(resultKey, jobId, fence, { logging: false, logReceipt: { ...response, pointIndex } })
      } catch (error) {
        patch(resultKey, jobId, fence, {
          logging: false,
          logError: apiErrorMessage(error),
          logErrorCode: apiErrorCode(error) ?? null,
        })
      }
    },

    dismissOverwrite: (resultKey, jobId) => {
      if (get().byResultKey[resultKey]?.jobId !== jobId) return
      patch(resultKey, jobId, captureDocumentExecutionFence(), { overwritePrompt: null })
    },
  }
})

/** The publish state kept under `resultKey` for a job; empty when it belongs to another job. */
export function usePublishState(resultKey: string, jobId: string | null): OptimiserPublishState | null {
  return useOptimiserPublishStore((store) => {
    if (!jobId) return null
    const current = store.byResultKey[resultKey]
    return current && current.jobId === jobId ? current : null
  })
}

useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useOptimiserPublishStore.setState({ byResultKey: {} })
})

export default useOptimiserPublishStore
