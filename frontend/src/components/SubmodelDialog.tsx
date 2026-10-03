import { useState } from "react"

import ModalShell from "./ModalShell"

interface SubmodelDialogProps {
  nodeCount: number
  onClose: () => void
  /** Creates the submodel; a refusal keeps the dialog open with the typed name. */
  onSubmit: (name: string) => Promise<{ ok: true } | { ok: false; error: string }>
}

export default function SubmodelDialog({ nodeCount, onClose, onSubmit }: SubmodelDialogProps) {
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (name: string) => {
    setPending(true)
    setError(null)
    try {
      const result = await onSubmit(name)
      if (!result.ok) setError(result.error)
    } finally {
      setPending(false)
    }
  }

  return (
    <ModalShell ariaLabel="Create submodel" onClose={pending ? () => {} : onClose} width="w-[400px]" testId="submodel-dialog">
      <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
        <h2 className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>Create Submodel</h2>
        <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
          Group {nodeCount} selected nodes into a submodel
        </p>
      </div>
      <form
        className="p-4 flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          const formData = new FormData(e.currentTarget)
          const name = (formData.get("name") as string || "").trim()
          if (name && !pending) void submit(name)
        }}
      >
        <div>
          <label className="text-[11px] font-medium block mb-1" style={{ color: 'var(--text-muted)' }}>Submodel name</label>
          <input
            name="name"
            type="text"
            autoFocus
            disabled={pending}
            placeholder="e.g. model_scoring"
            aria-invalid={error !== null}
            aria-describedby={error ? "submodel-name-error" : undefined}
            className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
            style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)', caretColor: 'var(--accent)' }}
          />
          {error && (
            <p id="submodel-name-error" role="alert" className="mt-1 text-[11px]" style={{ color: 'var(--danger)' }}>
              {error}
            </p>
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
            style={{ color: 'var(--text-secondary)' }}
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={pending}
            className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors hover:bg-[var(--structure-action-hover)]"
            style={{ background: 'var(--structure-action)', color: 'var(--text-on-accent)' }}
          >
            Create
          </button>
        </div>
      </form>
    </ModalShell>
  )
}
