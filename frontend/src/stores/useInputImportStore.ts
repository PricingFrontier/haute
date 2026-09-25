import { create } from "zustand"

import type { Node } from "@xyflow/react"

import { apiErrorMessage } from "../api/errors"
import { inputSnapshotSource } from "../utils/inputSnapshotSource"
import { instanceOriginal } from "../utils/instanceOriginal"
import useDocumentStatusStore, {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "./useDocumentStatusStore"
import useGraphStore from "./useGraphStore"
import useNodeDataStore from "./useNodeDataStore"
import useNodeWorkStore, { registerNodeStop } from "./useNodeWorkStore"
import useToastStore from "./useToastStore"

/** A running Import: rows read so far, or null while none are reported. */
export interface InputImportRun {
  rows: number | null
}

interface InputImportState {
  runs: Record<string, InputImportRun>
}

/**
 * Imports in flight, by the node that started them. An Import belongs to that
 * node, not to whichever node's panel is open: its progress, its Stop, and its
 * continuation stay with it when the user moves to another node.
 */
const useInputImportStore = create<InputImportState>(() => ({ runs: {} }))

/** The snapshot source *nodeId* reads now, as a comparable key, or null. */
function currentSourceKey(nodeId: string): string | null {
  const nodes = useGraphStore.getState().nodes
  const node = nodes.find((candidate) => candidate.id === nodeId)
  if (!node) return null
  const effective = instanceOriginal(node, new Map(nodes.map((candidate) => [candidate.id, candidate])))
  const source = inputSnapshotSource(effective)
  return source ? JSON.stringify(source) : null
}

function setRun(nodeId: string, run: InputImportRun | null): void {
  useInputImportStore.setState((state) => {
    const { [nodeId]: _previous, ...rest } = state.runs
    void _previous
    return { runs: run === null ? rest : { ...rest, [nodeId]: run } }
  })
}

/**
 * Re-read *input*'s source (the node's own input, or its original's for an
 * instance) and publish it as a new snapshot, on behalf of *nodeId*.
 *
 * Every outcome raises the node-data epoch, since a failed or stopped import
 * can publish part of the data. *onImported* runs only after a completed,
 * unstopped import whose node still reads the same source.
 */
export function startInputImport(nodeId: string, input: Node, onImported: (nodeId: string) => void): void {
  if (useInputImportStore.getState().runs[nodeId]) return
  const source = inputSnapshotSource(input)
  if (!source) return
  const sourceKey = JSON.stringify(source)
  const fence = captureDocumentExecutionFence()
  const controller = new AbortController()
  const workKey = `import:${nodeId}`
  // Set while builds refused to stop: Stop cancels them again.
  let retry: (() => Promise<void>) | null = null
  let ended = false
  const addToast = useToastStore.getState().addToast

  // Everything that ties this run to its node, released exactly once.
  const release = () => {
    if (ended) return false
    ended = true
    setRun(nodeId, null)
    useNodeWorkStore.getState().setRunning(workKey, null)
    unregisterStop()
    unsubscribeDocument()
    return true
  }
  const settle = (completed: boolean) => {
    if (!release()) return
    if (!isDocumentExecutionFenceCurrent(fence)) return
    useNodeDataStore.getState().bumpEpoch()
    if (completed && !controller.signal.aborted && currentSourceKey(nodeId) === sourceKey) {
      onImported(nodeId)
    }
  }
  const unregisterStop = registerNodeStop(nodeId, () => {
    if (retry) {
      void retry()
      return
    }
    controller.abort()
  })
  // A replaced document is another pipeline, whose node may reuse this id: the
  // run retires at once, stopping its build, so it is never shown, stopped, or
  // continued as the new document's.
  const unsubscribeDocument = useDocumentStatusStore.subscribe(() => {
    if (isDocumentExecutionFenceCurrent(fence)) return
    if (release()) controller.abort()
  })

  setRun(nodeId, { rows: null })
  useNodeWorkStore.getState().setRunning(workKey, nodeId)
  void (async () => {
    const { cancelInputSnapshotBuilds, ensureInputSnapshots } = await import("../hooks/ensureInputSnapshots")
    try {
      await ensureInputSnapshots([input], {
        force: true,
        signal: controller.signal,
        onBuildProgress: (job) => {
          if (controller.signal.aborted) return
          // Only a bounded build streams its rows; an eager one has no count yet.
          setRun(nodeId, { rows: job.build_class === "bounded" ? job.progress.rows : null })
        },
      })
    } catch (err) {
      const failed = err as { name?: unknown; jobIds?: unknown } | null
      if (failed?.name === "CancellationFailed" && Array.isArray(failed.jobIds)) {
        let pending = failed.jobIds as string[]
        addToast("error", `Stopping the import failed: ${apiErrorMessage(err)} Press Stop to try again.`)
        retry = async () => {
          try {
            await cancelInputSnapshotBuilds(pending)
          } catch (retried) {
            const still = (retried as { jobIds?: unknown }).jobIds
            if (Array.isArray(still)) pending = still as string[]
            addToast("error", `Stopping the import failed: ${apiErrorMessage(retried)} Press Stop to try again.`)
            return
          }
          settle(false)
        }
        return
      }
      if (failed?.name !== "AbortError") addToast("error", `Import failed: ${apiErrorMessage(err)}`)
      settle(false)
      return
    }
    settle(true)
  })()
}

export default useInputImportStore
