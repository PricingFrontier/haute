import { useState } from "react"
import type { StageState } from "../types/trainGuards"
import { STAGE_LABELS } from "./stageSettings"

export function ModelCheckpoints({ checkpoints, disabled, onAction }: {
  checkpoints: StageState["checkpoints"]; disabled: boolean
  onAction: (action: "save" | "restore" | "delete", value: string) => Promise<boolean>
}) {
  const [name, setName] = useState("")
  const [confirm, setConfirm] = useState<string | null>(null)
  return <details className="rounded-xl border p-3 text-[11px]" style={{ borderColor: "var(--border)" }}>
    <summary className="cursor-pointer font-medium">Saved checkpoints{checkpoints.length > 0 ? ` · ${checkpoints.length}` : ""}</summary>
    <div className="space-y-3 pt-3">
      <p style={{ color: "var(--text-muted)" }}>Keep the applied model before experimenting. Restore brings back its fitted result and model settings.</p>
      <form className="flex gap-2" onSubmit={(event) => { event.preventDefault(); void onAction("save", name.trim()).then((ok) => { if (ok) setName("") }) }}>
        <input aria-label="Checkpoint name" value={name} maxLength={80} placeholder="e.g. Baseline" onChange={(event) => setName(event.target.value)} className="min-w-0 flex-1 rounded border px-2 py-1.5" style={{ borderColor: "var(--border)", background: "var(--bg-input)" }} />
        <button disabled={disabled || !name.trim() || checkpoints.length >= 10} className="hover-chrome focus-ring rounded px-2 disabled:opacity-35">Save</button>
      </form>
      {checkpoints.map((checkpoint) => <div key={checkpoint.id} className="space-y-1 border-t pt-2" style={{ borderColor: "var(--border)" }}>
        <p className="break-words font-medium">{checkpoint.name}</p>
        <p style={{ color: "var(--text-muted)" }}>{STAGE_LABELS[checkpoint.output_stage]} · {new Date(checkpoint.created_at).toLocaleString()}</p>
        <div className="flex gap-3">
          <button disabled={disabled} className="hover-chrome focus-ring rounded py-1 disabled:opacity-35" onClick={() => setConfirm(`restore:${checkpoint.id}`)}>Restore</button>
          <button disabled={disabled} className="hover-chrome focus-ring rounded py-1 disabled:opacity-35" onClick={() => setConfirm(`delete:${checkpoint.id}`)}>Delete</button>
        </div>
        {confirm?.endsWith(`:${checkpoint.id}`) && <div className="space-y-2">
          <p>{confirm.startsWith("restore:") ? "Replace the applied model and its settings? Save it as a checkpoint first if you want to keep it." : "Delete this saved checkpoint? The applied model stays available."}</p>
          <div className="flex gap-3"><button disabled={disabled} className="hover-chrome focus-ring rounded py-1 disabled:opacity-35" onClick={() => void onAction(confirm.startsWith("restore:") ? "restore" : "delete", checkpoint.id).then((ok) => { if (ok) setConfirm(null) })}>Confirm</button><button onClick={() => setConfirm(null)}>Cancel</button></div>
        </div>}
      </div>)}
    </div>
  </details>
}
