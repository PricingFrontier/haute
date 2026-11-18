import { create } from "zustand"

import type { Node } from "@xyflow/react"

import { apiErrorMessage } from "../api/errors"
import { inputSnapshotSource } from "../utils/inputSnapshotSource"
import useDocumentStatusStore, {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "./useDocumentStatusStore"
import { noteProjectInputsChanged } from "./projectInputs"
import useGraphStore from "./useGraphStore"
import useNodeDataStore from "./useNodeDataStore"
import useNodeWorkStore, { registerNodeStop } from "./useNodeWorkStore"
import useToastStore from "./useToastStore"

/** A running Import: rows read so far, or null while none are reported. */
export interface InputImportRun {
  rows: number | null
}

interface InputImportState {
  /** By the result key of the node that started each import (`resultKey`). */
  runs: Record<string, InputImportRun>
}

/**
 * Imports in flight, by the node that started them. An Import belongs to that
 * node, not to whichever node's panel is open: its progress, its Stop, and its
 * continuation stay with it when the user moves to another node, or to the same
 * child in another occurrence of its submodel.
 */
const useInputImportStore = create<InputImportState>(() => ({ runs: {} }))

/** The open view: the occurrence of a submodel shown, or null for the main pipeline. */
function openView(): string | null {
  return useGraphStore.getState().focus?.instanceId ?? null
}

/**
 * The snapshot source the canvas node *nodeId* reads now, as a comparable key,
 * while *view* is still the one open; otherwise, or for a node gone, null.
 */
function currentSourceKey(nodeId: string, view: string | null): string | null {
  if (openView() !== view) return null
  const nodes = useGraphStore.getState().nodes
  const node = nodes.find((candidate) => candidate.id === nodeId)
  if (!node) return null
  const source = inputSnapshotSource(node)
  return source ? JSON.stringify(source) : null
}

function setRun(nodeKey: string, run: InputImportRun | null): void {
  useInputImportStore.setState((state) => {
    const { [nodeKey]: _previous, ...rest } = state.runs
    void _previous
    return { runs: run === null ? rest : { ...rest, [nodeKey]: run } }
  })
}

/**
 * Re-read *input*'s source and publish it as a new snapshot, on behalf of the
 * node whose results are kept under *nodeKey* (`resultKey`, taken as it starts).
 *
 * Every outcome raises the node-data epoch, since a build can publish before a
 * stop or a failure reaches it. *onImported* runs, with *nodeKey*, only after a
 * completed, unstopped import whose node still reads the same source.
 */
// How each running import leaves its node when the node is deleted, by its result key.
const retirements = new Map<string, () => void>()

/**
 * Retire the import running for a deleted node (`resultKey`, taken before the
 * deletion): it leaves its node at once, as when its document is replaced, and
 * its build is stopped in the background. A node with no import is left alone.
 */
export function retireInputImport(nodeKey: string): void {
  retirements.get(nodeKey)?.()
}

// A retired import's builds that refused to stop are cancelled again this many
// times, this far apart, before the failure is left to the user.
const RETIRED_CANCEL_ATTEMPTS = 5
const RETIRED_CANCEL_INTERVAL_MS = 2_000

export function startInputImport(nodeKey: string, input: Pick<Node, "id" | "data">, onImported: (nodeKey: string) => void): void {
  if (useInputImportStore.getState().runs[nodeKey]) return
  const source = inputSnapshotSource(input)
  if (!source) return
  const sourceKey = JSON.stringify(source)
  const fence = captureDocumentExecutionFence()
  const controller = new AbortController()
  const workKey = `import:${nodeKey}`
  // The view the node is on: its continuation reads it only while that view is open.
  const view = openView()
  const snapshots = import("../hooks/ensureInputSnapshots")
  // Builds that refused to stop: Stop (or, once retired, the background retry)
  // cancels them again.
  let pending: string[] | null = null
  let ended = false
  let settled = false
  // Why the run left its node before its work ended: its document was replaced,
  // or its node was deleted.
  let retired: "document" | "deleted" | null = null
  const addToast = useToastStore.getState().addToast

  // Everything that shows this run on its node, released exactly once.
  const release = () => {
    if (ended) return false
    ended = true
    setRun(nodeKey, null)
    useNodeWorkStore.getState().setRunning(workKey, null)
    unregisterStop()
    unsubscribeDocument()
    if (retirements.get(nodeKey) === retireDeleted) retirements.delete(nodeKey)
    return true
  }
  // The end of the work, once. Under the same document a build may have
  // published before it stopped, so the node data is read anew even for a
  // retired run; only a completed run whose node still reads the source is
  // previewed again.
  const settle = (completed: boolean) => {
    if (settled) return
    settled = true
    release()
    if (!isDocumentExecutionFenceCurrent(fence)) return
    useNodeDataStore.getState().bumpEpoch()
    // A source imported anew is data a price, a preview or an estimate made before did not read.
    if (completed) noteProjectInputsChanged()
    if (completed && retired === null && !controller.signal.aborted && currentSourceKey(input.id, view) === sourceKey) {
      onImported(nodeKey)
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
      if (await cancelPending()) {
        settle(false)
        return
      }
      await new Promise((resolve) => setTimeout(resolve, RETIRED_CANCEL_INTERVAL_MS))
    }
    addToast(
      "error",
      retired === "deleted"
        ? "An import of a deleted node could not be stopped and may still be running."
        : "An import from the previous pipeline could not be stopped and may still be running.",
    )
  }
  // The run leaves its node at once, and its build is stopped in the background.
  const retire = (reason: "document" | "deleted") => {
    if (!release()) return
    retired = reason
    if (pending !== null) void cancelRetired()
    else controller.abort()
  }
  const retireDeleted = () => retire("deleted")
  const unregisterStop = registerNodeStop(nodeKey, () => {
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
    if (!isDocumentExecutionFenceCurrent(fence)) retire("document")
  })
  // A deleted node's run leaves it the same way (`retireInputImport`).
  retirements.set(nodeKey, retireDeleted)

  setRun(nodeKey, { rows: null })
  useNodeWorkStore.getState().setRunning(workKey, nodeKey)
  void (async () => {
    const { ensureInputSnapshots } = await snapshots
    try {
      await ensureInputSnapshots([input], {
        force: true,
        signal: controller.signal,
        onBuildProgress: (job) => {
          if (controller.signal.aborted) return
          // A count once the build has counted rows: one written in a single
          // pass (a CSV) or an API Input's tables report none until they end.
          setRun(nodeKey, { rows: job.progress.rows > 0 ? job.progress.rows : null })
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
      // A retired run's failure belongs to a node that is gone.
      if (failed?.name !== "AbortError" && retired === null) addToast("error", `Import failed: ${apiErrorMessage(err)}`)
      settle(false)
      return
    }
    settle(true)
  })()
}

export default useInputImportStore
