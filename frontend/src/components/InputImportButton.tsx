import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react"
import { Download, Loader2 } from "lucide-react"

import { getInputCacheStatus } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { InputCacheSnapshotResponse } from "../api/types"
import type { SimpleNode } from "../panels/editors"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "../panels/previewPanelLayout"
import {
  captureDocumentExecutionFence,
  isDocumentExecutionFenceCurrent,
} from "../stores/useDocumentStatusStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import useNodeWorkStore, { registerNodeStop } from "../stores/useNodeWorkStore"
import useToastStore from "../stores/useToastStore"
import { importedTitle } from "../utils/importedTitle"
import { inputSnapshotSource } from "../utils/inputSnapshotSource"
import { instanceOriginal } from "../utils/instanceOriginal"

export interface InputImportButtonProps {
  node: SimpleNode
  allNodes: SimpleNode[]
  /** Called after an import that completed and was not stopped. */
  onImported: () => void
}

/**
 * Import: re-read a Data Input's or structured Quote Input's source and publish
 * it as a new snapshot, whether or not the source is known to have changed.
 * Refresh keeps its freshness rules; this is the one action that re-reads a
 * source whose changes cannot be detected (a database, a Databricks table).
 *
 * While it runs, it is this node's work, so the frame's Stop stops it. Every
 * outcome, including a failed or stopped import that published some tables,
 * raises the node-data epoch so downstream previews go out of date.
 */
export default function InputImportButton({ node, allNodes, onImported }: InputImportButtonProps) {
  const addToast = useToastStore((s) => s.addToast)
  const setWorkRunning = useNodeWorkStore((s) => s.setRunning)
  const workKey = useId()
  // An instance reads its original's input, so it imports that config.
  const effective = useMemo(
    () => instanceOriginal(node, new Map(allNodes.map((candidate) => [candidate.id, candidate]))),
    [allNodes, node],
  )
  const source = useMemo(() => inputSnapshotSource(effective), [effective])
  const sourceKey = source ? JSON.stringify(source) : null

  const [importing, setImporting] = useState(false)
  const [rows, setRows] = useState<number | null>(null)
  // Kept with the source it describes, so a changed config never shows the
  // previous source's import time.
  const [answer, setAnswer] = useState<{
    sourceKey: string
    status: InputCacheSnapshotResponse | null
    error: string | null
  } | null>(null)
  const status = answer?.sourceKey === sourceKey ? answer.status : null
  const statusError = answer?.sourceKey === sourceKey ? answer.error : null
  const controller = useRef<AbortController | null>(null)
  // Set while a stopped import's build refused to stop: Stop cancels it again.
  const stopRetry = useRef<(() => Promise<void>) | null>(null)

  // Read again after an import, whatever its outcome.
  const readStatus = useCallback(async () => {
    if (!source || !sourceKey) return
    try {
      setAnswer({ sourceKey, status: await getInputCacheStatus(source), error: null })
    } catch (err) {
      setAnswer({ sourceKey, status: null, error: apiErrorMessage(err) })
    }
  }, [source, sourceKey])

  useEffect(() => {
    if (!source || !sourceKey) return
    const reading = new AbortController()
    getInputCacheStatus(source, { signal: reading.signal }).then(
      (read) => setAnswer({ sourceKey, status: read, error: null }),
      (err: unknown) => {
        if (!reading.signal.aborted) setAnswer({ sourceKey, status: null, error: apiErrorMessage(err) })
      },
    )
    return () => reading.abort()
    // The source's content, not its object identity, decides what to read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey])

  useEffect(() => {
    setWorkRunning(workKey, importing ? node.id : null)
  }, [importing, node.id, setWorkRunning, workKey])
  useEffect(() => () => setWorkRunning(workKey, null), [setWorkRunning, workKey])

  const settle = useCallback(
    (fence: ReturnType<typeof captureDocumentExecutionFence>, completed: boolean) => {
      const stopped = controller.current?.signal.aborted ?? true
      controller.current = null
      stopRetry.current = null
      setImporting(false)
      setRows(null)
      void readStatus()
      if (!isDocumentExecutionFenceCurrent(fence)) return
      // Whatever the outcome, the snapshot may have changed.
      useNodeDataStore.getState().bumpEpoch()
      if (completed && !stopped) onImported()
    },
    [onImported, readStatus],
  )

  const runImport = useCallback(async () => {
    if (importing || !source) return
    const fence = captureDocumentExecutionFence()
    const running = new AbortController()
    controller.current = running
    setImporting(true)
    setRows(null)
    const { cancelInputSnapshotBuild, ensureInputSnapshots } = await import("../hooks/ensureInputSnapshots")
    try {
      await ensureInputSnapshots([effective as never], {
        force: true,
        signal: running.signal,
        onBuildProgress: (progress) => {
          if (!running.signal.aborted) setRows(progress.rows)
        },
      })
    } catch (err) {
      const failed = err as { name?: unknown; jobId?: unknown } | null
      if (failed?.name === "CancellationFailed" && typeof failed.jobId === "string") {
        // The build may still be running: the import stays running and Stop
        // cancels that build again.
        const jobId = failed.jobId
        addToast("error", `Stopping the import failed: ${apiErrorMessage(err)} Press Stop to try again.`)
        stopRetry.current = async () => {
          try {
            await cancelInputSnapshotBuild(jobId)
          } catch (retried) {
            addToast("error", `Stopping the import failed: ${apiErrorMessage(retried)} Press Stop to try again.`)
            return
          }
          settle(fence, false)
        }
        return
      }
      if (failed?.name !== "AbortError") addToast("error", `Import failed: ${apiErrorMessage(err)}`)
      settle(fence, false)
      return
    }
    settle(fence, true)
  }, [addToast, effective, importing, settle, source])

  const stop = useCallback(() => {
    if (stopRetry.current) {
      void stopRetry.current()
      return
    }
    controller.current?.abort()
  }, [])

  useEffect(() => registerNodeStop(node.id, stop), [node.id, stop])

  if (!source) return null
  const title = importing
    ? "Importing this input; Stop stops it"
    : `${statusError ? `Could not read when this was last imported: ${statusError}` : status ? importedTitle(status, new Date()) : "Checking when this was last imported"}. Import re-reads the source and caches it again.`
  return (
    <button
      type="button"
      onClick={() => void runImport()}
      disabled={importing}
      className={`${PREVIEW_PANEL_ACTION_BUTTON_CLASS} shrink-0 transition-opacity hover:opacity-[0.85] disabled:opacity-70`}
      style={{ border: "1px solid var(--accent)", color: "var(--accent)", background: "transparent" }}
      title={title}
      data-testid="input-import"
    >
      {importing ? (
        <Loader2 size={11} className="animate-spin" aria-hidden="true" />
      ) : (
        <Download size={11} aria-hidden="true" />
      )}
      {importing ? (rows === null ? "Importing…" : `Importing · ${rows.toLocaleString()} rows`) : "Import"}
    </button>
  )
}
