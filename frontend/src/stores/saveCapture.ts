/**
 * What a save answered about its version capture (specs/git-integration), reported the
 * same way whether the pipeline or the workbench's form was saved: the ledger commit to
 * the toolbar's branch indicator, an open Git panel told to fetch its history again, each
 * warning in a toast of its own, and the git-identity prompt when the capture was skipped
 * for want of one, unless it was waved away this session.
 */
import { isIdentityPromptDismissed } from "./identityPrompt"
import useGitStore from "./useGitStore"
import useToastStore from "./useToastStore"

/** The capture fields of a save's response, the pipeline's and the form's alike. */
export interface SaveCapture {
  /** The ledger commit the save produced; null when the clone has no working branch. */
  git_sha?: string | null
  /** Non-fatal: the save landed on disk, but something is left to see to. */
  warnings?: string[]
  /** The capture was skipped because git has no commit identity. */
  identity_required?: boolean
}

export function reportSaveCapture(capture: SaveCapture): void {
  const git = useGitStore.getState()
  // Reflect the new ledger commit in the toolbar indicator (P2). null when no working
  // branch is configured — the indicator stays as-is.
  if (capture.git_sha !== undefined) git.setLastSaveSha(capture.git_sha)
  // Let an open Git panel re-fetch its history (S38).
  git.notifyHistoryChanged()
  // The save succeeded but the backend flagged something unfinished (a transform with no
  // code, a capture that failed). These are deliberately non-blocking, so they'd be
  // invisible without their own toast — and the user would only discover the problem
  // on the next run.
  const { addToast } = useToastStore.getState()
  for (const warning of capture.warnings ?? []) addToast("warning", warning)
  // A restored container has no git commit identity, so the save landed on disk but was
  // never version-captured. Prompt once per session — the warning above keeps saying so
  // after the user waves it away.
  if (capture.identity_required && !isIdentityPromptDismissed() && git.modal !== "identity") {
    git.openModal("identity")
  }
}
