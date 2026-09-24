import { useRef, useState } from "react"
import {
  applyRecoverUnavailableNode,
  applyRemoveUnavailableNode,
} from "../api/client"
import type { PipelineEditorDocument } from "../types/pipelineDocument"
import { apiErrorMessage } from "../api/errors"
import ModalShell from "./ModalShell"
import { useRecoverySummaryStore } from "../stores/useRecoverySummaryStore"

export interface PipelineRepairTarget {
  sourceFile: string
  recoveryId: string
  action?: "remove" | "update" | "reset" | "recover"
}

interface PipelineRepairDialogProps {
  target: PipelineRepairTarget
  sourceFile: string
  sourceRevision: string
  onClose: () => void
  onApplied: (document: PipelineEditorDocument) => void
}

function PipelineRepairDialogContent({
  target,
  sourceFile,
  sourceRevision,
  onClose,
  onApplied,
}: PipelineRepairDialogProps) {
  const action = (target.action ?? "remove") as "remove" | "update" | "reset" | "recover"
  const isRemoval = action === "remove"
  const isRecover = action === "recover"
  const actionTitle = action === "update"
    ? "Update to current format"
    : action === "reset" ? "Reset node"
    : isRecover ? "Recover settings" : "Remove unavailable node"
  const applyLabel = action === "update"
    ? "Update to current format"
    : action === "reset" ? "Reset node"
    : isRecover ? "Recover settings" : "Remove node"
  const [deleteConfig, setDeleteConfig] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const applyingRef = useRef(false)

  const apply = async () => {
    if (applyingRef.current) return
    applyingRef.current = true
    setApplying(true)
    setError(null)
    try {
      const response = isRemoval
        ? await applyRemoveUnavailableNode({
            sourceFile,
            sourceRevision,
            targetSourceFile: target.sourceFile,
            targetRecoveryId: target.recoveryId,
            deleteConfig,
          })
        : await applyRecoverUnavailableNode({
            sourceFile,
            sourceRevision,
            targetSourceFile: target.sourceFile,
            targetRecoveryId: target.recoveryId,
            action,
          })
      if (isRecover) {
        // Transient session record for the panel's dismissible summary.
        useRecoverySummaryStore.getState().recordSummary({
          sourceFile,
          recoveryId: target.recoveryId,
          fieldChanges: response.field_changes,
          completeness: response.completeness,
          previousConfig: response.previous_config,
          changes: response.changes,
        })
      }
      onApplied(response.document)
    } catch (err) {
      setError(apiErrorMessage(err, "Could not apply this repair."))
    } finally {
      applyingRef.current = false
      setApplying(false)
    }
  }

  const requestClose = () => {
    if (!applyingRef.current) onClose()
  }

  return (
    <ModalShell ariaLabel={actionTitle} onClose={requestClose} width="w-[680px]" testId="pipeline-repair-dialog">
      <div className="border-b px-5 py-4" style={{ borderColor: "var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{actionTitle}</h2>
        <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          {action === "reset"
            ? "Replace this node's settings and code while preserving its identity and connections. Configuration may be needed before running."
            : action === "update"
              ? `Update ${target.recoveryId} to the current submodel format while preserving its authored connections. If other occurrences share this child definition, the update affects every one of them.`
              : isRecover
                ? `Rebuild ${target.recoveryId} against the current definitions. Valid settings and code are retained; anything missing stays highlighted in the normal editor afterwards.`
                : `Remove ${target.recoveryId} and its connection declarations.`}
        </p>
      </div>
      <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4 text-xs">
        {error && <div role="alert" className="rounded p-3" style={{ color: "var(--danger-text)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}>{error}</div>}
        {isRemoval && <section aria-label="Config file">
          <label className="flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <input type="checkbox" checked={deleteConfig} disabled={applying} onChange={(event) => setDeleteConfig(event.target.checked)} />
            Also delete config
          </label>
          <p className="mt-1" style={{ color: "var(--text-secondary)" }}>
            Deletes the config file this node references, if it has one; otherwise it is kept. A config file shared with another node is never deleted.
          </p>
        </section>}
      </div>
      <div className="flex justify-end gap-2 border-t px-5 py-3" style={{ borderColor: "var(--border)" }}>
        <button type="button" onClick={requestClose} disabled={applying} className="rounded px-3 py-1.5" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
        <button
          type="button"
          onClick={() => void apply()}
          disabled={applying}
          className="rounded px-3 py-1.5"
          style={{ color: "var(--text-on-accent)", background: isRemoval ? "var(--danger)" : "var(--accent)" }}
        >
          {applying ? "Applying…" : applyLabel}
        </button>
      </div>
    </ModalShell>
  )
}

export default function PipelineRepairDialog(props: PipelineRepairDialogProps) {
  return <PipelineRepairDialogContent {...props} />
}
