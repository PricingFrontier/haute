import { useState } from "react"

import { ApiError } from "../api/client"
import type { GitStorageClaim } from "../api/types"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitStorageClaimFromDetail } from "../types/guards"
import { gitErrorMessage } from "../utils/gitError"
import ModalShell from "./ModalShell"

interface StorageBindModalProps {
  onClose: () => void
}

/**
 * Durable-storage bind dialog: the "unbound" affordance opens this to attach
 * an HTTPS remote or a uc:// volume location so saves survive a container
 * replacement. A successful bind either takes effect immediately ("adopted")
 * or requires an app restart to load the bound project ("restart-required") —
 * the two are shown distinctly, since a restart-required bind must not be
 * presented as already durable.
 *
 * A uc:// location someone else actively holds comes back as a structured 409
 * naming the holder; the dialog then steers rather than stonewalls — pick a
 * different location, or fork the held one into a new location and bind that.
 */
export default function StorageBindModal({ onClose }: StorageBindModalProps) {
  const bindStorage = useGitStore((s) => s.bindStorage)
  const forkStorage = useGitStore((s) => s.forkStorage)
  const addToast = useToastStore((s) => s.addToast)

  const [remoteUrl, setRemoteUrl] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [restartMessage, setRestartMessage] = useState<string | null>(null)
  // The refusal from binding a location another app holds, plus the URL that
  // was refused (the fork source) and the user's chosen fork target.
  const [claim, setClaim] = useState<GitStorageClaim | null>(null)
  const [claimedUrl, setClaimedUrl] = useState<string | null>(null)
  const [forkUrl, setForkUrl] = useState("")

  const canSubmit = remoteUrl.trim() !== "" && !busy
  const canFork = forkUrl.trim() !== "" && !busy

  const bind = async (url: string) => {
    const result = await bindStorage(url)
    if (result.outcome === "restart-required") {
      setClaim(null)
      setRestartMessage(result.message)
    } else {
      addToast("success", result.message)
      onClose()
    }
  }

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    setError(null)
    try {
      await bind(remoteUrl.trim())
    } catch (err: unknown) {
      const claimed =
        err instanceof ApiError && err.status === 409 ? gitStorageClaimFromDetail(err.rawDetail) : null
      if (claimed) {
        setClaim(claimed)
        setClaimedUrl(remoteUrl.trim())
      } else {
        setError(gitErrorMessage(err, "Could not bind storage"))
      }
    } finally {
      setBusy(false)
    }
  }

  const forkAndBind = async () => {
    if (!canFork || claimedUrl === null) return
    setBusy(true)
    setError(null)
    try {
      const forked = await forkStorage(claimedUrl, forkUrl.trim())
      // Binding the fork lifts the copied project at the next boot, so this
      // lands in the restart-required state with the fork's own message.
      await bind(forked.target_url)
    } catch (err: unknown) {
      setError(gitErrorMessage(err, "Could not fork the storage location"))
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
      ) : claim ? (
        <form
          className="p-4 flex flex-col gap-3"
          onSubmit={(e) => {
            e.preventDefault()
            void forkAndBind()
          }}
        >
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
            data-testid="storage-bind-claimed-message"
          >
            {claim.message}
          </p>

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
              htmlFor="storage-fork-url"
              className="text-[11px] font-medium block mb-1"
              style={{ color: "var(--text-muted)" }}
            >
              Fork into a new location
            </label>
            <input
              id="storage-fork-url"
              data-testid="storage-fork-url"
              value={forkUrl}
              onChange={(e) => setForkUrl(e.target.value)}
              autoFocus
              placeholder="uc://catalog.schema.volume/path/to/copy"
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
              data-testid="storage-bind-back"
              onClick={() => {
                setClaim(null)
                setError(null)
              }}
              className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              Choose another location
            </button>
            <button
              type="submit"
              data-testid="storage-fork-confirm"
              disabled={!canFork}
              className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
              style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
            >
              {busy ? "Forking…" : "Fork and bind"}
            </button>
          </div>
        </form>
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
              placeholder="https://github.com/org/repo.git or uc://catalog.schema.volume/path"
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
