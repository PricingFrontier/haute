import { useCallback, useEffect, useRef, useState } from "react"
import { ArrowDown, ArrowUp, Check, GitFork, Upload } from "lucide-react"

import { getGitRemotes, gitPush } from "../api/client"
import type { GitRemote } from "../api/types"
import useToastStore from "../stores/useToastStore"
import ModalShell from "./ModalShell"
import Tooltip from "./Tooltip"

interface RemotePushControlProps {
  /** Out-of-version saves on the ledger — drives the pre-push integrity prompt
   *  (S16: an overridable warning, never a hard stop). */
  pendingSaveCount: number
  /** Bumped by the panel after a save / commit so ahead/behind re-fetches. */
  refreshNonce?: number
}

/**
 * Deliberate push of the working/ledger pair to an existing remote (S16/S33):
 * a remote dropdown defaulting to no selection, the working branch's ahead/behind
 * vs the selected remote, and a push button. Nothing leaves the machine except
 * through this button — there is no auto-push and no add-remote here. When the
 * ledger holds out-of-version saves, pushing first asks for confirmation (the
 * push still proceeds if the user says so — it is a warning, not a block).
 */
export default function RemotePushControl({
  pendingSaveCount,
  refreshNonce = 0,
}: RemotePushControlProps) {
  const addToast = useToastStore((s) => s.addToast)
  const [remotes, setRemotes] = useState<GitRemote[]>([])
  const [selected, setSelected] = useState<string>("")
  const [loaded, setLoaded] = useState(false)
  const [pushing, setPushing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  // Monotonic request id: a slower fetch must not clobber a newer one (e.g. a
  // post-push reload landing after the user has already changed selection).
  const reqId = useRef(0)

  const load = useCallback(async () => {
    const id = ++reqId.current
    try {
      const res = await getGitRemotes()
      if (id !== reqId.current) return // superseded by a newer load
      setRemotes(res.remotes)
      // Keep a still-valid selection; otherwise default to the sole remote (a
      // common case) or to no selection so a push is always deliberate.
      setSelected((prev) =>
        prev && res.remotes.some((r) => r.name === prev)
          ? prev
          : res.remotes.length === 1
            ? res.remotes[0].name
            : "",
      )
    } catch {
      // Remotes are best-effort chrome; a failure just leaves the control empty.
      if (id === reqId.current) setRemotes([])
    } finally {
      if (id === reqId.current) setLoaded(true)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load, refreshNonce])

  const selectedRemote = remotes.find((r) => r.name === selected) ?? null

  const doPush = useCallback(async () => {
    if (!selected) return
    setConfirming(false)
    setPushing(true)
    try {
      const res = await gitPush(selected)
      const n = res.pushed_refs.length
      addToast("success", `Pushed ${n} branch${n === 1 ? "" : "es"} to ${res.remote}`)
      await load() // ahead/behind now reflect the synced state
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Push failed: ${detail}`)
    } finally {
      setPushing(false)
    }
  }, [selected, addToast, load])

  const onPushClick = () => {
    if (!selected) return
    if (pendingSaveCount > 0) setConfirming(true)
    else void doPush()
  }

  // Fully offline (no remotes): say so plainly rather than show an empty dropdown.
  if (loaded && remotes.length === 0) {
    return (
      <div
        data-testid="git-push-no-remotes"
        className="px-3 py-2 text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        No remotes configured — add one with{" "}
        <span className="font-mono">git remote add</span> to push.
      </div>
    )
  }

  return (
    <div data-testid="git-push-control" className="px-2 pt-2 flex items-center gap-2">
      <select
        data-testid="git-push-remote"
        aria-label="Remote to push to"
        value={selected}
        onChange={(e) => setSelected(e.target.value)}
        className="flex-1 min-w-0 text-[12px] rounded-md px-2 py-1 focus:outline-none focus:ring-2"
        style={{
          background: "var(--bg-input)",
          border: "1px solid var(--border)",
          color: "var(--text-primary)",
        }}
      >
        <option value="">Select a remote…</option>
        {remotes.map((r) => (
          <option key={r.name} value={r.name}>
            {r.url ? `${r.name} — ${r.url}` : r.name}
          </option>
        ))}
      </select>

      {selectedRemote && <AheadBehind remote={selectedRemote} />}
      {selectedRemote && <LedgerStatus remote={selectedRemote} />}

      <Tooltip
        label={
          pendingSaveCount > 0
            ? "Push the working + ledger branches (you have out-of-version saves)"
            : "Push the working + ledger branches to the remote"
        }
        side="bottom"
      >
        <button
          data-testid="git-push-button"
          onClick={onPushClick}
          disabled={!selected || pushing}
          className="shrink-0 inline-flex items-center gap-1 text-[12px] font-medium rounded-md px-2.5 py-1 transition-colors disabled:opacity-40"
          style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
        >
          <Upload size={12} /> {pushing ? "Pushing…" : "Push"}
        </button>
      </Tooltip>

      {confirming && (
        <PushConfirm
          count={pendingSaveCount}
          remote={selected}
          onCancel={() => setConfirming(false)}
          onConfirm={() => void doPush()}
        />
      )}
    </div>
  )
}

/** The working branch's divergence from the selected remote — ahead (local-only)
 *  and behind (remote-only), read from local refs only (no fetch). */
function AheadBehind({ remote }: { remote: GitRemote }) {
  if (remote.ahead === null || remote.behind === null) {
    // F2 honesty: distinguish "never pushed here" (—) from "couldn't read the
    // remote" (?) so the user never mistakes "can't tell" for "in sync".
    const unknown = remote.working?.status === "unknown"
    return (
      <Tooltip
        label={
          unknown
            ? `Can't tell — couldn't read ${remote.name}`
            : "Not pushed to this remote yet — divergence is unknown until you push"
        }
        side="bottom"
      >
        <span
          data-testid="git-push-aheadbehind"
          className="text-[11px] font-mono shrink-0"
          style={{ color: "var(--text-muted)" }}
        >
          {unknown ? "?" : "—"}
        </span>
      </Tooltip>
    )
  }
  if (remote.ahead === 0 && remote.behind === 0) {
    return (
      <Tooltip label={`In sync with ${remote.name}`} side="bottom">
        <span
          data-testid="git-push-aheadbehind"
          className="inline-flex items-center gap-0.5 text-[10px] shrink-0"
          style={{ color: "var(--success)" }}
        >
          <Check size={11} /> synced
        </span>
      </Tooltip>
    )
  }
  return (
    <Tooltip label={`${remote.ahead} ahead, ${remote.behind} behind ${remote.name}`} side="bottom">
      <span
        data-testid="git-push-aheadbehind"
        className="inline-flex items-center gap-1.5 text-[10px] font-mono shrink-0"
        style={{ color: "var(--text-secondary)" }}
      >
        <span className="inline-flex items-center gap-0.5">
          <ArrowUp size={10} />
          {remote.ahead}
        </span>
        <span className="inline-flex items-center gap-0.5">
          <ArrowDown size={10} />
          {remote.behind}
        </span>
      </span>
    </Tooltip>
  )
}

/** The ledger (save-history) leg's divergence from the selected remote — the P7
 *  surface that makes the two-machine save accident visible. Renders only the
 *  notable states: "behind" (the shared save history moved on — D2) and
 *  "diverged" (your saves and the remote's have both forked — D3). Ahead-only
 *  (you simply have unpushed saves) and synced/untracked stay silent; the working
 *  leg's AheadBehind already carries the common case. */
function LedgerStatus({ remote }: { remote: GitRemote }) {
  const leg = remote.ledger
  if (!leg || (leg.status !== "behind" && leg.status !== "diverged")) {
    return null
  }
  if (leg.status === "diverged") {
    return (
      <Tooltip
        label={`Save history has forked — your saves and ${remote.name}'s have both moved on. Reconcile before pushing.`}
        side="bottom"
      >
        <span
          data-testid="git-push-ledger-status"
          className="inline-flex items-center gap-0.5 text-[10px] shrink-0"
          style={{ color: "var(--danger)" }}
        >
          <GitFork size={11} /> saves forked
        </span>
      </Tooltip>
    )
  }
  return (
    <Tooltip
      label={`${leg.behind} newer save${leg.behind === 1 ? "" : "s"} on ${remote.name} you don't have yet`}
      side="bottom"
    >
      <span
        data-testid="git-push-ledger-status"
        className="inline-flex items-center gap-0.5 text-[10px] font-mono shrink-0"
        style={{ color: "var(--warning)" }}
      >
        <ArrowDown size={10} /> {leg.behind} saves
      </span>
    </Tooltip>
  )
}

/** Pre-push integrity prompt (S16): out-of-version saves aren't in a milestone
 *  yet. Overridable — "Push anyway" proceeds; this is a warning, not a block. */
function PushConfirm({
  count,
  remote,
  onCancel,
  onConfirm,
}: {
  count: number
  remote: string
  onCancel: () => void
  onConfirm: () => void
}) {
  return (
    <ModalShell ariaLabel="Confirm push with out-of-version saves" onClose={onCancel} testId="git-push-confirm">
      <div className="p-4 flex flex-col gap-3">
        <span className="text-[13px] font-semibold" style={{ color: "var(--text-primary)" }}>
          Push with out-of-version saves?
        </span>
        <span className="text-[12px]" style={{ color: "var(--text-secondary)" }}>
          {count} save{count === 1 ? "" : "s"} on the ledger {count === 1 ? "isn't" : "aren't"}{" "}
          folded into a milestone yet. {count === 1 ? "It" : "They"}&rsquo;ll be pushed on the
          ledger branch, but won&rsquo;t appear as a milestone on{" "}
          <span className="font-mono">{remote}</span>.
        </span>
        <div className="flex justify-end gap-2 pt-1">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-[12px] font-medium rounded-md transition-colors"
            style={{ color: "var(--text-secondary)" }}
          >
            Cancel
          </button>
          <button
            data-testid="git-push-confirm-go"
            onClick={onConfirm}
            className="px-3 py-1.5 text-[12px] font-semibold rounded-md transition-colors hover:bg-[var(--structure-action-hover)]"
            style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          >
            Push anyway
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
