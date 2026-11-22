/**
 * What a save answered about its version capture (specs/git-integration), reported the
 * same way whichever write it was (the pipeline's save, the form's save, a Pipeline file
 * written or deleted): the ledger commit to the toolbar's branch
 * indicator, an open Git panel told to fetch its history again, each warning in a toast of
 * its own, the files the capture skipped or failed on kept until a capture takes them, and
 * the git-identity prompt when the capture was skipped for want of one, unless it was
 * waved away this session.
 */
import { captureFiles } from "../api/client"
import { apiErrorCode, apiErrorMessage } from "../api/errors"
import { isIdentityPromptDismissed } from "./identityPrompt"
import useGitStore, { gitBranchMayHaveChanged, shownBranch } from "./useGitStore"
import useToastStore from "./useToastStore"
import { documentPart } from "./useDocumentStatusStore"
import { answer } from "../utils/answers"

/** The capture fields of a write's response, every writer's alike. */
export interface SaveCapture {
  /** The ledger commit the save produced; null when the clone has no working branch. */
  git_sha?: string | null
  /** Non-fatal: the save landed on disk, but something is left to see to. */
  warnings?: string[]
  /** The capture was skipped because git has no commit identity. */
  identity_required?: boolean
  /** The files, relative to the project, the capture skipped or failed on. */
  uncaptured?: string[]
}

export function reportSaveCapture(capture: SaveCapture): void {
  const git = useGitStore.getState()
  // Reflect the new ledger commit in the toolbar indicator. null when no working
  // branch is configured — the indicator stays as-is.
  if (capture.git_sha !== undefined) git.setLastSaveSha(capture.git_sha)
  // Let an open Git panel re-fetch its history.
  git.notifyHistoryChanged()
  // The capture is pathspec-scoped, so no later write's capture takes these files: they
  // are kept until `captureUncaptured` does.
  keepUncaptured(capture.uncaptured ?? [])
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

function keepUncaptured(paths: string[]): void {
  if (paths.length === 0) return
  useGitStore.setState((state) => ({ uncaptured: [...new Set([...state.uncaptured, ...paths])] }))
}

/**
 * Capture on the ledger every file writes left uncaptured, as it is on disk now: after the
 * identity prompt records a name and email, and at each Save, naming `workingBranch` (the
 * project Save's, else the branch the tab shows). The answer is reported as a save's is, so
 * a file still uncaptured is kept again; a request that fails keeps them all and says so.
 * Nothing is sent while nothing is kept. False when the project moved to another branch
 * (`branch_changed`): the Save it is the first step of goes no further.
 */
export async function captureUncaptured(workingBranch: string | null = shownBranch()): Promise<boolean> {
  const paths = useGitStore.getState().uncaptured
  if (paths.length === 0) return true
  let moved = false
  // Taken off before the request: a write answered meanwhile keeps its own files.
  useGitStore.setState((state) => ({ uncaptured: state.uncaptured.filter((path) => !paths.includes(path)) }))
  const failed = (error: unknown) => {
    keepUncaptured(paths)
    useToastStore.getState().addToast("error", `Version capture failed: ${apiErrorMessage(error)}`)
    if (apiErrorCode(error) === "branch_changed") {
      moved = true
      void gitBranchMayHaveChanged()
    }
  }
  // The capture is the ledger's whatever the editor shows by its answer: it is settled either way.
  await answer([documentPart()], captureFiles(paths, workingBranch), {
    ok: reportSaveCapture,
    failed,
    stale: (settled) => ("value" in settled ? reportSaveCapture(settled.value) : failed(settled.error)),
  })
  return !moved
}
