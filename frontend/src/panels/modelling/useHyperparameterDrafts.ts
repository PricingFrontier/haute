import { useCommittedDraft, type CommittedDraft } from "../../components/form/useCommittedDraft"
import type { OnUpdateConfig } from "../editors/_shared"
import {
  formatHyperparameters,
  formatTuningSearchSpace,
  mergeReservedKeys,
  parseHyperparameters,
  parseTuningSearchSpace,
} from "./hyperparameters"

export type HyperparameterDrafts = {
  /** The Parameters JSON being typed: the node's fixed parameters, less its reserved keys. */
  params: CommittedDraft<string>
  /** The search space JSON being typed, while tuning is on. */
  searchSpace: CommittedDraft<string>
}

/**
 * The Parameters pane's two JSON drafts, held by the node's whole modelling config so they
 * outlive a pane switch, and owned by the node, so another node starts from its own. Each
 * commits on blur, as one undo step; text that does not parse, or names a reserved key, is
 * refused and kept, and the caller makes it a Train-guard issue. An Undo, a Redo or any other
 * change of the stored value lets go of the draft (FEM-03: the box shows what training uses).
 */
export function useHyperparameterDrafts({
  nodeId,
  params,
  tuning,
  reservedKeys,
  reservedKeysHelp,
  onUpdate,
}: {
  nodeId: string
  params: Record<string, unknown>
  tuning: Record<string, unknown> | null
  reservedKeys: readonly string[]
  reservedKeysHelp: string
  onUpdate: OnUpdateConfig
}): HyperparameterDrafts {
  const searchSpace = (
    tuning?.search_space !== null
    && typeof tuning?.search_space === "object"
    && !Array.isArray(tuning.search_space)
  )
    ? tuning.search_space as Record<string, unknown>
    : {}
  const paramsDraft = useCommittedDraft(
    formatHyperparameters(params, reservedKeys),
    (text) => {
      let parsed: Record<string, unknown>
      try {
        parsed = parseHyperparameters(text, reservedKeys, reservedKeysHelp)
      } catch {
        // Kept on screen with its reason, and a Train-guard issue.
        return { ok: false }
      }
      onUpdate("params", mergeReservedKeys(params, parsed, reservedKeys))
    },
    { owner: nodeId },
  )
  const searchSpaceDraft = useCommittedDraft(
    formatTuningSearchSpace(searchSpace),
    (text) => {
      if (!tuning) return { ok: false }
      let parsed: Record<string, unknown>
      try {
        parsed = parseTuningSearchSpace(text)
      } catch {
        return { ok: false }
      }
      onUpdate("tuning", { ...tuning, search_space: parsed })
    },
    { owner: nodeId },
  )
  return { params: paramsDraft, searchSpace: searchSpaceDraft }
}
