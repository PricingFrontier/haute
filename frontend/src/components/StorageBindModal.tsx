import { useEffect, useState } from "react"

import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitErrorMessage } from "../utils/gitError"
import ModalShell from "./ModalShell"

interface StorageBindModalProps {
  onClose: () => void
}

/**
 * Durable-storage bind dialog: the "unbound" affordance opens this to attach
 * an HTTPS remote or a uc:// volume location so saves survive a container
 * replacement.
 *
 * Binding is asynchronous. Only the instant checks (a malformed URL, an
 * already-bound project) answer here; a bind publishes the whole project, so
 * the dialog closes as soon as the request is accepted and the session stays
 * usable. The outcome arrives on the polled readiness state, which reopens
 * this dialog on failure — so what the dialog shows is DERIVED from that
 * state rather than copied into local state, and the server-side result is
 * cleared only once the user has acted on it.
 *
 * A uc:// location someone else actively holds fails with the holder named;
 * the dialog then steers rather than stonewalls — pick a different location,
 * or fork the held one into a new location and bind that.
 */
export default function StorageBindModal({ onClose }: StorageBindModalProps) {
  const bindStorage = useGitStore((s) => s.bindStorage)
  const forkStorage = useGitStore((s) => s.forkStorage)
  const acknowledgeBind = useGitStore((s) => s.acknowledgeBind)
  const bind = useGitStore((s) => s.status?.storage_bind ?? null)
  const addToast = useToastStore((s) => s.addToast)

  const failed = bind?.state === "failed" ? bind : null
  const heldBy = failed?.claim ?? null
  const needsRestart = bind?.state === "succeeded" && bind.outcome === "restart-required"

  // Reopened after a failure, the URL is already known — seed the field from
  // it rather than writing to state from an effect.
  const [remoteUrl, setRemoteUrl] = useState(() => failed?.remote_url ?? "")
  const [forkUrl, setForkUrl] = useState("")
  const [busy, setBusy] = useState(false)
  const [localError, setLocalError] = useState<string | null>(null)
  // Set when the user chooses to bind elsewhere instead of forking, so the
  // held-location panel gives way to the ordinary form.
  const [dismissedHold, setDismissedHold] = useState(false)

  const showHold = heldBy !== null && !dismissedHold
  const error = localError ?? (failed && !heldBy ? failed.message : null)

  // Adopting is the only outcome with nothing to ask the user: report it and
  // get out of the way. The other outcomes are rendered, not acted on.
  useEffect(() => {
    if (bind?.state === "succeeded" && bind.outcome === "adopted") {
      addToast("success", "This project is now saved to storage — saves publish automatically.")
      void acknowledgeBind()
      onClose()
    }
  }, [bind, acknowledgeBind, addToast, onClose])

  const dismiss = () => {
    // Clear the server-side result so a stale outcome cannot reopen this
    // dialog after the user has dealt with it.
    if (bind && bind.state !== "idle" && bind.state !== "running") void acknowledgeBind()
    onClose()
  }

  const canSubmit = remoteUrl.trim() !== "" && !busy
  const canFork = forkUrl.trim() !== "" && !busy

  const submit = async () => {
    if (!canSubmit) return
    setBusy(true)
    setLocalError(null)
    try {
      if (failed) await acknowledgeBind()
      await bindStorage(remoteUrl.trim())
      // Accepted, not finished: let the user carry on. The dialog reopens by
      // itself if the background bind fails.
      addToast("info", "Saving this project to storage — you can keep working.")
      onClose()
    } catch (err: unknown) {
      setLocalError(gitErrorMessage(err, "Could not bind storage"))
    } finally {
      setBusy(false)
    }
  }

  const forkAndBind = async () => {
    if (!canFork || !failed?.remote_url) return
    setBusy(true)
    setLocalError(null)
    try {
      const forked = await forkStorage(failed.remote_url, forkUrl.trim())
      await acknowledgeBind()
      await bindStorage(forked.target_url)
      addToast("info", "Forked, and saving the copy to storage — you can keep working.")
      onClose()
    } catch (err: unknown) {
      setLocalError(gitErrorMessage(err, "Could not fork the storage location"))
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell
      ariaLabel="Save project to a repository"
      onClose={dismiss}
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

      {needsRestart ? (
        <div className="p-4 flex flex-col gap-3">
          <p
            className="text-[12px] px-2.5 py-1.5 rounded-md"
            style={{ background: "var(--bg-input)", color: "var(--text-primary)" }}
            data-testid="storage-bind-restart-message"
          >
            Binding saved. That location already holds a project, so restart the app to load
            it — this session&apos;s project is not published.
          </p>
          <div className="flex justify-end">
            <button
              type="button"
              onClick={dismiss}
              className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
              style={{ color: "var(--text-secondary)" }}
            >
              Close
            </button>
          </div>
        </div>
      ) : showHold ? (
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
            {heldBy.message}
          </p>

          {localError && (
            <p
              className="text-[12px] px-2.5 py-1.5 rounded-md"
              style={{ background: "var(--bg-input)", color: "var(--danger)" }}
              data-testid="storage-bind-error"
            >
              {localError}
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
                setDismissedHold(true)
                setLocalError(null)
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
              onClick={dismiss}
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
              {busy ? "Starting…" : "Bind repository"}
            </button>
          </div>
        </form>
      )}
    </ModalShell>
  )
}
