import { useEffect, useState } from "react"
import { readStages, inspectStagePath, type TrainModelArgs } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { TrainResponse } from "../api/types"
import { captureDocumentExecutionFence, isDocumentExecutionFenceCurrent } from "../stores/useDocumentStatusStore"
import type { AvailablePath } from "./pathSelection"
import { stagedRequest } from "./useStagedTraining"

/** Dense table diagnostics are fetched only by the mounted Tables pane. */
export function useStageTableResult({ request, viewKey, currentRevision, selection, enabled }: {
  request: () => TrainModelArgs; viewKey: string; currentRevision?: string; enabled: boolean
  selection?: { available: AvailablePath; pointId: string }
}): { response?: TrainResponse; error?: string } {
  const artifactRevision = selection?.available.artifactRevision
  const pathId = selection?.available.path.id
  const pointId = selection?.pointId
  const identity = JSON.stringify([viewKey, currentRevision, artifactRevision, pathId, pointId])
  const [value, setValue] = useState<{ identity: string; response?: TrainResponse; error?: string }>()
  useEffect(() => {
    if (!enabled || !currentRevision) return
    const controller = new AbortController()
    const fence = captureDocumentExecutionFence()
    const load = async () => {
      try {
        const args = { ...stagedRequest(request()), diagnostics: "full" as const, signal: controller.signal }
        let response: TrainResponse
        if (artifactRevision && pathId && pointId) {
          const result = await inspectStagePath({ ...args, current_revision: currentRevision,
            artifact_revision: artifactRevision, path_id: pathId, point_id: pointId })
          if (result.current_revision !== currentRevision || result.artifact_revision !== artifactRevision
            || result.path_id !== pathId || result.point_id !== pointId) throw new Error("The selected table result changed. Select it again.")
          response = result.response
        } else {
          const state = await readStages(args)
          if (state.current?.revision !== currentRevision) throw new Error("The applied model changed. Reopen Tables.")
          if (state.stale_reason) throw new Error(state.stale_reason)
          response = state.current.result.response
        }
        if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) setValue({ identity, response })
      } catch (error) {
        if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) setValue({ identity, error: apiErrorMessage(error) })
      }
    }
    void load()
    return () => controller.abort()
  }, [request, identity, enabled, currentRevision, artifactRevision, pathId, pointId])
  return value?.identity === identity ? value : {}
}

