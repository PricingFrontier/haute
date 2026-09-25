import { useEffect, useMemo, useState } from "react"
import { Download, Loader2 } from "lucide-react"

import { getInputCacheStatus } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { InputCacheSnapshotResponse } from "../api/types"
import type { SimpleNode } from "../panels/editors"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "../panels/previewPanelLayout"
import useInputImportStore, { startInputImport } from "../stores/useInputImportStore"
import useNodeDataStore from "../stores/useNodeDataStore"
import { apiInputHasEmittingTable } from "../utils/apiInputPorts"
import { importedTitle } from "../utils/importedTitle"
import { inputSnapshotSource } from "../utils/inputSnapshotSource"
import { instanceOriginal } from "../utils/instanceOriginal"

export interface InputImportButtonProps {
  node: SimpleNode
  allNodes: SimpleNode[]
  /** Called with the node id after an import that completed and was not stopped. */
  onImported: (nodeId: string) => void
}

/**
 * Import: re-read a Data Input's or structured Quote Input's source and publish
 * it as a new snapshot, whether or not the source is known to have changed.
 * Refresh keeps its freshness rules; this is the one action that re-reads a
 * source whose changes cannot be detected (a database, a Databricks table).
 *
 * The import itself belongs to the node that started it (see
 * `useInputImportStore`), so this button only shows and starts it.
 */
export default function InputImportButton({ node, allNodes, onImported }: InputImportButtonProps) {
  // An instance reads its original's input, so it imports that config.
  const effective = useMemo(
    () => instanceOriginal(node, new Map(allNodes.map((candidate) => [candidate.id, candidate]))),
    [allNodes, node],
  )
  const source = useMemo(() => {
    const found = inputSnapshotSource(effective)
    // A Quote Input without an emitting table has nothing to import.
    return found && (found.node_type !== "apiInput" || apiInputHasEmittingTable(found.config))
      ? found
      : null
  }, [effective])
  const sourceKey = source ? JSON.stringify(source) : null
  const run = useInputImportStore((s) => s.runs[node.id])
  // Every import's outcome raises the epoch, so the last-imported time is
  // read again after each one.
  const epoch = useNodeDataStore((s) => s.epoch)

  // Kept with the source it describes, so a changed config never shows the
  // previous source's import time.
  const [answer, setAnswer] = useState<{
    sourceKey: string
    status: InputCacheSnapshotResponse | null
    error: string | null
  } | null>(null)
  const status = answer?.sourceKey === sourceKey ? answer.status : null
  const statusError = answer?.sourceKey === sourceKey ? answer.error : null

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
  }, [sourceKey, epoch])

  if (!source) return null
  const importing = run !== undefined
  const title = importing
    ? "Importing this input; Stop stops it"
    : `${statusError ? `Could not read when this was last imported: ${statusError}` : status ? importedTitle(status, new Date()) : "Checking when this was last imported"}. Import re-reads the source and caches it again.`
  return (
    <button
      type="button"
      onClick={() => startInputImport(node.id, effective as never, onImported)}
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
      {importing
        ? run.rows === null
          ? "Importing…"
          : `Importing · ${run.rows.toLocaleString()} rows`
        : "Import"}
    </button>
  )
}
