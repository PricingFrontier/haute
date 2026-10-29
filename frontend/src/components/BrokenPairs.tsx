import { useState } from "react"
import { AlertTriangle } from "lucide-react"

import type { GitWorkingBranchResponse } from "../api/types"
import useGitStore from "../stores/useGitStore"
import { brokenPairText, repairBrokenPair } from "../utils/brokenPairs"

/** The branch pairs readiness reports broken, each with its Repair (GIT-R16). `onRepaired`
 *  receives the readiness read after a repair. */
export default function BrokenPairs({
  onRepaired,
}: { onRepaired?: (status: GitWorkingBranchResponse | null) => void } = {}) {
  const pairs = useGitStore((s) => s.status?.broken_pairs)
  const [repairing, setRepairing] = useState<string | null>(null)
  if (!pairs || pairs.length === 0) return null

  return (
    <div data-testid="git-broken-pairs" className="flex flex-col gap-1">
      {pairs.map((pair) => (
        <div
          key={pair.ledger}
          data-testid="git-broken-pair"
          className="flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[12px]"
          style={{ background: "var(--warning-soft, var(--accent-soft-faint))", border: "1px solid var(--warning-border)" }}
        >
          <AlertTriangle size={12} className="shrink-0" style={{ color: "var(--warning-strong)" }} />
          <span className="flex-1" style={{ color: "var(--text-primary)" }}>{brokenPairText(pair)}</span>
          <button
            type="button"
            data-testid="git-broken-pair-repair"
            disabled={repairing !== null}
            onClick={() => {
              setRepairing(pair.ledger)
              void repairBrokenPair(pair)
                .then((status) => onRepaired?.(status))
                .finally(() => setRepairing(null))
            }}
            className="px-2 py-0.5 rounded text-[12px] font-semibold disabled:opacity-50"
            style={{ color: "var(--text-accent)" }}
          >
            {repairing === pair.ledger ? "Repairing…" : "Repair"}
          </button>
        </div>
      ))}
    </div>
  )
}
