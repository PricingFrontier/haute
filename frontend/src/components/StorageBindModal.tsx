import { useState } from "react"

import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitErrorMessage } from "../utils/gitError"
import ModalShell from "./ModalShell"

interface StorageBindModalProps {
  onClose: () => void
}

/**
 * Durable-storage bind dialog: the "unbound" affordance opens this to attach
 * an HTTPS remote so saves survive a container replacement. A successful bind
 * either takes effect immediately ("adopted") or requires an app restart to
 * load the bound project ("restart-required") — the two are shown distinctly,
 * since a restart-required bind must not be presented as already durable.
 */
export default function StorageBindModal({ onClose }: StorageBindModalProps) {
  const bindStorage = useGitStore((s) => s.bindStorage)
  const addToast = useToastStore((s) => s.addToast)

  const [remoteUrl, setRemoteUrl] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [restartMessage, setRestartMessage] = useState<string | null>(null)

  const canSubmit = remoteUrl.trim() !== "" && !busy

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      const result = await bindStorage(remoteUrl.trim())
      if (result.outcome === "restart-required") {
        setRestartMessage(result.message)
      } else {
        addToast("success", result.message)
        onClose()
      }
    } catch (err: unknown) {
      setError(gitErrorMessage(err, "Could not bind storage"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      ariaLabel="Save project to a repository"
      onClose={onClose}
      width="w-[440px]"
      testId="storage-bind-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Save this project to a repository
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Without a bound repository, saves are lost if this app restarts. Binding to a
          repository makes them durable.
        </p>
      </div>

      {restartMessage ? (
        <div className="p-4 flex flex-col gap-3">
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
            data-testid="storage-bind-restart-message"
          >
            {restartMessage}
          </p>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              Close
            </button>
          </div>
        </div>
      ) : (
        <form
          className="p-4 flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          {error && (
            <p
              className="text-[12px] px-2.5 py-1.5 rounded-md"
              style={{ background: "var(--bg-input)", color: "var(--danger)" }}
              data-testid="storage-bind-error"
            >
              {error}
            </p>
          )}

          <div>
            <label
              htmlFor="storage-bind-url"
              className="text-[11px] font-medium block mb-1"
              style={{ color: "var(--text-muted)" }}
            >
              Repository URL
            </label>
            <input
              id="storage-bind-url"
              data-testid="storage-bind-url"
              value={remoteUrl}
              onChange={(e) => setRemoteUrl(e.target.value)}
              autoFocus
              placeholder="https://github.com/org/repo.git"
              className="w-full px-3 py-1.5 text-[13px] rounded-md focus:outline-none focus:ring-2"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                caretColor: "var(--accent)",
              }}
            />
          </div>

          <div className="flex justify-end gap-2 pt-1">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              Cancel
            </button>
            <button
              type="submit"
              data-testid="storage-bind-confirm"
              disabled={!canSubmit}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Binding…" : "Bind repository"}
            </button>
          </div>
        </form>
      )}
    </ModalShell>
  )
}
