import { useEffect, useRef, useState } from "react"
import {
  ApiError,
  applyRemoveUnavailableNode,
  dryRunRemoveUnavailableNode,
} from "../api/client"
import type { PipelineEditorDocument } from "../types/pipelineDocument"
import type { RemoveUnavailableNodeDryRunResponse } from "../types/pipelineRepair"
import ModalShell from "./ModalShell"

export interface PipelineRepairTarget {
  sourceFile: string
  recoveryId: string
}

interface PipelineRepairDialogProps {
  target: PipelineRepairTarget
  sourceFile: string
  sourceRevision: string
  onClose: () => void
  onApplied: (document: PipelineEditorDocument) => void
}

function errorDetail(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = error.rawDetail
    if (typeof detail === "object" && detail !== null && !Array.isArray(detail)) {
      const { code, message } = detail as { code?: unknown; message?: unknown }
      if (typeof code === "string" && typeof message === "string") return `${code}: ${message}`
    }
    if (error.detail) return error.detail
  }
  return error instanceof Error ? error.message : String(error)
}

export default function PipelineRepairDialog({
  target,
  sourceFile,
  sourceRevision,
  onClose,
  onApplied,
}: PipelineRepairDialogProps) {
  const [deleteConfig, setDeleteConfig] = useState(false)
  const [plan, setPlan] = useState<RemoveUnavailableNodeDryRunResponse | null>(null)
  const [planning, setPlanning] = useState(true)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retainedArtifactPaths, setRetainedArtifactPaths] = useState<string[]>([])
  const sequence = useRef(0)
  const applyingRef = useRef(false)

  useEffect(() => {
    const controller = new AbortController()
    const current = ++sequence.current
    // The request identity changed; clear the prior confirmation before the
    // replacement plan can arrive so its hash can never be applied.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setPlanning(true)
    setPlan(null)
    setError(null)
    void dryRunRemoveUnavailableNode({
      sourceFile,
      sourceRevision,
      targetSourceFile: target.sourceFile,
      targetRecoveryId: target.recoveryId,
      deleteConfig,
    }, { signal: controller.signal })
      .then((next) => {
        if (sequence.current === current) {
          setPlan(next)
          if (!next.delete_config) setRetainedArtifactPaths(next.retained_artifacts)
        }
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted || sequence.current !== current) return
        setError(errorDetail(err))
      })
      .finally(() => {
        if (sequence.current === current) setPlanning(false)
      })
    return () => controller.abort()
  }, [deleteConfig, sourceFile, sourceRevision, target.recoveryId, target.sourceFile])

  const apply = async () => {
    if (!plan || planning || applyingRef.current) return
    applyingRef.current = true
    setApplying(true)
    setError(null)
    try {
      const response = await applyRemoveUnavailableNode({
        sourceFile: plan.source_file,
        sourceRevision: plan.source_revision,
        targetSourceFile: plan.target_source_file,
        targetRecoveryId: plan.target_recovery_id,
        deleteConfig: plan.delete_config,
        planHash: plan.plan_hash,
      })
      onApplied(response.document)
    } catch (err) {
      setError(errorDetail(err))
    } finally {
      applyingRef.current = false
      setApplying(false)
    }
  }

  const updateDeleteConfig = (next: boolean) => {
    // A changed option makes the displayed hash inapplicable immediately.
    setPlan(null)
    setError(null)
    setPlanning(true)
    setDeleteConfig(next)
  }

  const requestClose = () => {
    if (!applyingRef.current) onClose()
  }

  const canChooseConfigDeletion = retainedArtifactPaths.length > 0 || deleteConfig
  return (
    <ModalShell ariaLabel="Remove unavailable node" onClose={requestClose} width="w-[680px]" testId="pipeline-repair-dialog">
      <div className="border-b px-5 py-4" style={{ borderColor: "var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Remove unavailable node</h2>
        <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          Remove {plan?.target_authored_id ?? target.recoveryId} and the listed artifacts only.
        </p>
      </div>
      <div className="max-h-[60vh] space-y-4 overflow-y-auto px-5 py-4 text-xs">
        {planning && <p style={{ color: "var(--text-secondary)" }}>Preparing repair plan…</p>}
        {error && <div role="alert" className="rounded p-3" style={{ color: "var(--danger-text)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}>{error}</div>}
        {plan && <>
          <p style={{ color: "var(--text-secondary)" }}>Predicted document status: <strong>{plan.predicted_load_status}</strong></p>
          <section aria-label="Changed artifacts">
            <h3 className="font-semibold" style={{ color: "var(--text-primary)" }}>Changed artifacts</h3>
            <ul className="mt-2 space-y-3">
              {plan.changes.map((change) => <li key={change.path} className="rounded p-3" style={{ border: "1px solid var(--border)" }}>
                <div className="font-mono" style={{ color: "var(--text-primary)" }}>{change.operation}: {change.path}</div>
                <p className="mt-1" style={{ color: "var(--text-secondary)" }}>{change.description}</p>
                {change.diff && <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded p-2 text-[11px]" style={{ background: "var(--bg-elevated)", color: "var(--text-secondary)" }}>{change.diff}</pre>}
                {change.diff_truncated && <p className="mt-1" style={{ color: "var(--warning)" }}>Diff truncated.</p>}
              </li>)}
            </ul>
          </section>
          {plan.warnings.length > 0 && <section aria-label="Repair warnings"><h3 className="font-semibold" style={{ color: "var(--text-primary)" }}>Warnings</h3><ul className="mt-1 list-disc pl-5" style={{ color: "var(--warning)" }}>{plan.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></section>}
        </>}
        {canChooseConfigDeletion && <section aria-label={deleteConfig ? "Config deletion" : "Retained config"}>
          {retainedArtifactPaths.length > 0 && <p className="mb-2" style={{ color: "var(--text-secondary)" }}>
            {deleteConfig ? "Config selected for deletion" : "Retained config"}: {retainedArtifactPaths.join(", ")}
          </p>}
          <label className="flex items-center gap-2" style={{ color: "var(--text-primary)" }}>
            <input type="checkbox" checked={deleteConfig} disabled={planning || applying} onChange={(event) => updateDeleteConfig(event.target.checked)} />
            Also delete config
          </label>
        </section>}
      </div>
      <div className="flex justify-end gap-2 border-t px-5 py-3" style={{ borderColor: "var(--border)" }}>
        <button type="button" onClick={requestClose} disabled={applying} className="rounded px-3 py-1.5" style={{ color: "var(--text-secondary)", border: "1px solid var(--border)" }}>Cancel</button>
        <button type="button" onClick={() => void apply()} disabled={!plan || planning || applying} className="rounded px-3 py-1.5" style={{ color: "var(--text-on-accent)", background: "var(--danger)" }}>{applying ? "Removing…" : "Remove node"}</button>
      </div>
    </ModalShell>
  )
}
