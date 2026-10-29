/**
 * Branch pairs a crash left in two parts (readiness's `broken_pairs`, GIT-R16): what
 * each reads as, and the Repair that makes it whole. Shared by the startup report and
 * the branch manager.
 */

import { repairBranchPair } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { GitBrokenPair, GitWorkingBranchResponse } from "../api/types"
import useGitStore from "../stores/useGitStore"
import useToastStore from "../stores/useToastStore"

/** What went wrong with *pair*, in the analyst's words. */
export function brokenPairText(pair: GitBrokenPair): string {
  return pair.kind === "orphan-ledger"
    ? `Branch '${pair.working}' is missing, though its saves are kept.`
    : `Archiving '${pair.working}' stopped part way: its saves were not archived with it.`
}

/** Startup's report of the pairs readiness found broken: each a toast that stays until it is
 *  dismissed, with its Repair. */
export function reportBrokenPairs(pairs: GitBrokenPair[]): void {
  const { addToast } = useToastStore.getState()
  for (const pair of pairs) {
    addToast("error", brokenPairText(pair), { label: "Repair", run: () => void repairBrokenPair(pair) })
  }
}

/** Repair *pair*, toast the outcome, and read the branches and readiness again: the readiness
 *  read after the repair is the answer. */
export async function repairBrokenPair(pair: GitBrokenPair): Promise<GitWorkingBranchResponse | null> {
  const { addToast } = useToastStore.getState()
  try {
    const repaired = await repairBranchPair(pair.ledger)
    addToast("success", `Repaired ${repaired.working}`)
  } catch (err) {
    addToast("error", `Could not repair ${pair.working}: ${apiErrorMessage(err, "unknown error")}`)
  }
  const git = useGitStore.getState()
  const status = await git.loadStatus({ refresh: true }) // not a read begun before the repair
  git.notifyHistoryChanged()
  return status
}
