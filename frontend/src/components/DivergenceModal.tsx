import { useState } from "react"

import { setWorkingBranch } from "../api/client"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"
import { gitErrorMessage } from "../utils/gitError"
import useUIStore from "../stores/useUIStore"
import ModalShell from "./ModalShell"

interface DivergenceModalProps {
  onConfirmed: () => void
  onClose: () => void
}

type Choice = "home" | "stay" | "manager"

/**
 * Divergence-at-open modal (S14): the recorded working branch and HEAD
 * disagree because the repo was moved outside haute. Adopt-with-choice —
 *   - go home: return to the recorded working branch (re-checkout its ledger)
 *   - stay here: adopt the current branch as the working branch (only when
 *     the current branch is itself eligible)
 *   - branch manager: open the Git panel and decide there
 */
export default function DivergenceModal({ onConfirmed, onClose }: DivergenceModalProps) {
  const status = useGitStore((s) => s.status)
  const loadStatus = useGitStore((s) => s.loadStatus)
  const setGitOpen = useUIStore((s) => s.setGitOpen)
  const addToast = useToastStore((s) => s.addToast)
  const [busy, setBusy] = useState(false)

  const recorded = status?.working_branch ?? "(unknown)"
  const current = status?.current_branch ?? "(unknown)"
  // "Stay here" is only valid when the current branch is itself choosable.
  const stayEligible = status ? status.eligible_branches.includes(current) : false

  const [choice, setChoice] = useState<Choice>("home")

  const apply = async () => {
    if (busy) return
    if (choice === "manager") {
      setGitOpen(true)
      onClose()
      return
    }
    const target = choice === "home" ? recorded : current
    setBusy(true)
    try {
      await setWorkingBranch(target, false)
      await loadStatus()
      addToast("success", `Working branch set to ${target}`)
      onConfirmed()
    } catch (err: unknown) {
      const detail = gitErrorMessage(err, "unknown error")
      addToast("error", `Could not switch working branch: ${detail}`)
    } finally {
      setBusy(false)
    }
  }

  const option = (value: Choice, title: string, desc: string, disabled = false) => (
    <label
      className="flex items-start gap-2 px-2.5 py-2 rounded-md cursor-pointer"
      style={{
        background: choice === value ? "var(--bg-input)" : "transparent",
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? "not-allowed" : "pointer",
      }}
    >
      <input
        type="radio"
        name="divergence-choice"
        value={value}
        checked={choice === value}
        disabled={disabled}
        onChange={() => setChoice(value)}
        style={{ accentColor: "var(--accent)", marginTop: 2 }}
      />
      <span className="flex flex-col">
        <span className="text-[13px] font-medium" style={{ color: "var(--text-primary)" }}>
          {title}
        </span>
        <span className="text-[12px]" style={{ color: "var(--text-muted)" }}>
          {desc}
        </span>
      </span>
    </label>
  )

  return (
    <ModalShell
      ariaLabel="Resolve branch divergence"
      onClose={onClose}
      width="w-[460px]"
      testId="divergence-modal"
    >
      <div className="px-4 py-3" style={{ borderBottom: "1px solid var(--border)" }}>
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          You've moved off your working branch
        </h2>
        <p className="text-[12px] mt-0.5" style={{ color: "var(--text-muted)" }}>
          Your working branch is <span className="font-mono">{recorded}</span>, but the repo is
          currently on <span className="font-mono">{current}</span>.
        </p>
      </div>

      <form
        className="p-4 flex flex-col gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          void apply()
        }}
      >
        {option("home", `Go back to ${recorded}`, "Return to your working branch and keep going.")}
        {option(
          "stay",
          `Make ${current} my working branch`,
          stayEligible
            ? "Adopt the branch you're currently on."
            : "This branch can't be a working branch (it's protected or a save ledger).",
          !stayEligible,
        )}
        {option("manager", "Open the branch manager", "Decide in the Git panel.")}

        <div className="flex justify-end gap-2 pt-2">
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
            data-testid="divergence-confirm"
            disabled={busy || (choice === "stay" && !stayEligible)}
            className="px-4 py-1.5 text-[12px] font-semibold rounded-md transition-colors disabled:opacity-50 disabled:cursor-not-allowed hover:bg-[var(--structure-action-hover)] disabled:hover:bg-[var(--structure-action)]"
            style={{ background: "var(--structure-action)", color: "var(--text-on-accent)" }}
          >
            {busy ? "Working…" : "Continue"}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}
