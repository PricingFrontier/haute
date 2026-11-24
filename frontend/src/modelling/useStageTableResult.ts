import { useEffect, useState } from "react"
import { readStages, inspectStagePath, type TrainModelArgs } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { TrainResponse } from "../api/types"
import { captureDocumentExecutionFence, isDocumentExecutionFenceCurrent } from "../stores/useDocumentStatusStore"
import type { AvailablePath } from "./pathSelection"
import { stagedRequest } from "./useStagedTraining"

/**
 * The results last read, by what was read: a revision's (or path point's) result never
 * changes, so a pane mounted again shows it at once instead of reading it again. The least
 * recently used goes first. Full results hold every table bank, so fewer of them are kept.
 */
const loaded: Record<"tables" | "panels", { entries: Map<string, TrainResponse>; limit: number }> = {
  tables: { entries: new Map(), limit: 2 },
  panels: { entries: new Map(), limit: 4 },
}
function remember(kind: "tables" | "panels", identity: string, response: TrainResponse) {
  const { entries, limit } = loaded[kind]
  entries.delete(identity)
  entries.set(identity, response)
  if (entries.size > limit) entries.delete(entries.keys().next().value!)
}

/**
 * What a pane reads beyond the compact state: `tables`, the applied (or a path point's)
 * result in full; `panels`, the applied result's compact view with its SHAP, PDP and
 * scatter, which never loads the table bank. Neither reads the preview or checkpoints.
 */
export type StageRead = "tables" | "panels"

/** Dense diagnostics (tables, SHAP, PDP, the scatter) are fetched only by a mounted pane that draws them. */
export function useStageTableResult({ request, viewKey, currentRevision, selection, enabled, read = "tables" }: {
  request: () => TrainModelArgs; viewKey: string; currentRevision?: string; enabled: boolean
  /** A path point to read instead of the applied result; for `tables` only. */
  selection?: { available: AvailablePath; pointId: string }
  read?: StageRead
}): { response?: TrainResponse; error?: string } {
  const artifactRevision = selection?.available.artifactRevision
  const pathId = selection?.available.path.id
  const pointId = selection?.pointId
  const identityOf = (kind: StageRead) => JSON.stringify([kind, viewKey, currentRevision, artifactRevision, pathId, pointId])
  const identity = identityOf(read)
  const tablesIdentity = identityOf("tables")
  // A full result already read holds every panel's diagnostics too.
  const known = (): [StageRead, TrainResponse] | undefined => {
    const own = loaded[read].entries.get(identity)
    if (own) return [read, own]
    const full = read === "panels" ? loaded.tables.entries.get(tablesIdentity) : undefined
    return full ? ["tables", full] : undefined
  }
  const [value, setValue] = useState<{ identity: string; response?: TrainResponse; error?: string }>()
  const found = enabled && currentRevision ? known() : undefined
  const hit = found?.[1]
  const hitKind = found?.[0]
  useEffect(() => {
    if (hit && hitKind) {
      remember(hitKind, hitKind === read ? identity : tablesIdentity, hit)
      return
    }
    if (!enabled || !currentRevision) return
    const controller = new AbortController()
    const fence = captureDocumentExecutionFence()
    const load = async () => {
      try {
        const args = { ...stagedRequest(request()), signal: controller.signal }
        let response: TrainResponse
        if (artifactRevision && pathId && pointId) {
          const result = await inspectStagePath({ ...args, diagnostics: "full", current_revision: currentRevision,
            artifact_revision: artifactRevision, path_id: pathId, point_id: pointId })
          if (result.current_revision !== currentRevision || result.artifact_revision !== artifactRevision
            || result.path_id !== pathId || result.point_id !== pointId) throw new Error("The selected table result changed. Select it again.")
          response = result.response
        } else {
          const state = await readStages({ ...args, diagnostics: read === "panels" ? "panels" : "current" })
          if (state.current?.revision !== currentRevision) throw new Error("The applied model changed. Reopen the pane.")
          if (state.stale_reason) throw new Error(state.stale_reason)
          response = state.current.result.response
        }
        if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) {
          remember(read, identity, response)
          setValue({ identity, response })
        }
      } catch (error) {
        if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) setValue({ identity, error: apiErrorMessage(error) })
      }
    }
    void load()
    return () => controller.abort()
  }, [request, identity, enabled, currentRevision, artifactRevision, pathId, pointId, hit, hitKind, read, tablesIdentity])
  if (hit) return { response: hit }
  return value?.identity === identity ? value : {}
}

