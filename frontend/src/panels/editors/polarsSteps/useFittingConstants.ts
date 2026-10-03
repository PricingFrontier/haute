import { useMemo } from "react"
import useGraphStore from "../../../stores/useGraphStore"
import { GLOBAL_CONSTANT_TYPES, nameProblem, type GlobalConstantType } from "../../../utils/globalConstants"

/** Every constant type: what a comparison, fill, branch or formula value takes. */
export const ALL_CONSTANT_TYPES: readonly GlobalConstantType[] = GLOBAL_CONSTANT_TYPES

/**
 * The pipeline's global constants a slot taking *types* can read: each valid
 * name whose constant has one of those types, in the Constants pane's order.
 */
export function useFittingConstants(types: readonly GlobalConstantType[]): string[] {
  const drafts = useGraphStore((s) => s.globalConstants)
  return useMemo(
    () => drafts
      .filter((draft) => nameProblem(draft.name) === null && types.includes(draft.type))
      .map((draft) => draft.name),
    [drafts, types],
  )
}
