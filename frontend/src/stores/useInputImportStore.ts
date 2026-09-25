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
// A retired import's builds that refused to stop are cancelled again this many
// times, this far apart, before the failure is left to the user.
const RETIRED_CANCEL_ATTEMPTS = 5
const RETIRED_CANCEL_INTERVAL_MS = 2_000

export function startInputImport(nodeId: string, input: Node, onImported: (nodeId: string) => void): void {
  if (useInputImportStore.getState().runs[nodeId]) return
  const source = inputSnapshotSource(input)
  if (!source) return
  const sourceKey = JSON.stringify(source)
  const fence = captureDocumentExecutionFence()
  const controller = new AbortController()
  const workKey = `import:${nodeId}`
  const snapshots = import("../hooks/ensureInputSnapshots")
  // Builds that refused to stop: Stop (or, once retired, the background retry)
  // cancels them again.
  let pending: string[] | null = null
  let ended = false
  let retired = false
  const addToast = useToastStore.getState().addToast

  // Everything that shows this run on its node, released exactly once.
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
  // Cancel builds that refused to stop again. Returns whether they all stopped.
  const cancelPending = async (): Promise<boolean> => {
    const { cancelInputSnapshotBuilds } = await snapshots
    const stillRunning = await cancelInputSnapshotBuilds(pending ?? [])
    pending = stillRunning?.jobIds ?? null
    return stillRunning === null
  }
  // A retired import has no node left to press Stop on, so its builds are
  // cancelled again in the background until they stop, and a build that never
  // does is reported.
  const cancelRetired = async () => {
    for (let attempt = 1; attempt <= RETIRED_CANCEL_ATTEMPTS; attempt += 1) {
      if (await cancelPending()) return
      await new Promise((resolve) => setTimeout(resolve, RETIRED_CANCEL_INTERVAL_MS))
    }
    addToast("error", "An import from the previous pipeline could not be stopped and may still be running.")
  }
  const unregisterStop = registerNodeStop(nodeId, () => {
    if (pending === null) {
      controller.abort()
      return
    }
    void cancelPending().then((stopped) => {
      if (stopped) settle(false)
      else addToast("error", "Stopping the import failed. Press Stop to try again.")
    })
  })
  // A replaced document is another pipeline, whose node may reuse this id: the
  // run leaves its node at once, so it is never shown, stopped, or continued as
  // the new document's, while its build is still stopped in the background.
  const unsubscribeDocument = useDocumentStatusStore.subscribe(() => {
    if (isDocumentExecutionFenceCurrent(fence) || !release()) return
    retired = true
    if (pending !== null) void cancelRetired()
    else controller.abort()
  })

  setRun(nodeId, { rows: null })
  useNodeWorkStore.getState().setRunning(workKey, nodeId)
  void (async () => {
    const { ensureInputSnapshots } = await snapshots
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
        pending = failed.jobIds as string[]
        if (retired) {
          void cancelRetired()
          return
        }
        addToast("error", `Stopping the import failed: ${apiErrorMessage(err)} Press Stop to try again.`)
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
