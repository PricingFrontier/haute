import { useEffect, useState } from "react"
import { fetchModellingGpuStatus } from "../../api/client"
import type { GpuFamilyStatus } from "../../api/types"

type Props = {
  checked: boolean
  onToggle: (enabled: boolean) => void
}

/** The Train pane's GPU training checkbox; *device* names the CUDA device when one is known. */
export function GpuTrainingCheckbox({
  checked,
  onToggle,
  disabled = false,
  device = null,
}: Props & { disabled?: boolean; device?: string | null }) {
  return (
    <label className="flex cursor-pointer select-none items-center gap-2">
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onToggle(event.target.checked)}
        className="accent-purple-500"
      />
      <span className="text-[11px]" style={{ color: "var(--text-primary)" }}>
        GPU training
      </span>
      <span className="text-[10px]" style={{ color: "var(--text-muted)" }}>
        {device ? `(CUDA, ${device})` : "(CUDA)"}
      </span>
    </label>
  )
}

/**
 * XGBoost GPU training. The server probes its XGBoost build and device once;
 * the toggle is offered only when a CUDA fit works there, and otherwise says
 * why (usually: run ``haute gpu-setup``). A node already set to GPU can always
 * be switched back to CPU.
 */
export function XGBoostGpuToggle({ checked, onToggle }: Props) {
  const [status, setStatus] = useState<GpuFamilyStatus | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    fetchModellingGpuStatus({ signal: controller.signal })
      .then((response) => setStatus(response.xgboost))
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return
        setError(reason instanceof Error ? reason.message : String(reason))
      })
    return () => controller.abort()
  }, [])

  const available = status?.available === true
  const detail = error !== null
    ? `Could not check GPU support: ${error}`
    : status !== null && !available ? status.detail : null

  return (
    <div className="space-y-1">
      <GpuTrainingCheckbox
        checked={checked}
        disabled={!checked && !available}
        device={available ? status?.device : null}
        onToggle={onToggle}
      />
      {detail !== null && (
        <p className="text-[10px]" style={{ color: checked ? "var(--danger-text)" : "var(--text-muted)" }}>
          {detail}
        </p>
      )}
    </div>
  )
}
