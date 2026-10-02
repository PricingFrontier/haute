import type { CompletionContext, CompletionResult } from "@codemirror/autocomplete"
import { nameProblem, type GlobalConstantDraft } from "../../utils/globalConstants"

/** One constant as completion offers it: its name, type and value for the active source. */
export interface ConstantCompletion {
  name: string
  type: string
  value: string
}

/** The constants to complete after `global_constants.`, valued for *source*. */
export function constantCompletions(drafts: readonly GlobalConstantDraft[], source: string): ConstantCompletion[] {
  return drafts
    .filter((draft) => nameProblem(draft.name) === null)
    .map((draft) => {
      const value = draft.split ? (draft.bySource[source] ?? "") : draft.value
      return { name: draft.name, type: draft.type, value: value === "" ? "missing" : value }
    })
}

export function constantCompletionSource(constants: readonly ConstantCompletion[]) {
  return (context: CompletionContext): CompletionResult | null => {
    const match = context.matchBefore(/\bglobal_constants\.[A-Za-z0-9_]*$/)
    if (!match) return null
    const from = match.from + "global_constants.".length
    const partial = context.state.sliceDoc(from, context.pos).toLowerCase()
    const options = constants
      .filter((constant) => constant.name.toLowerCase().startsWith(partial))
      .map((constant) => ({
        label: constant.name,
        type: "constant" as const,
        detail: `${constant.type} = ${constant.value}`,
      }))
    if (options.length === 0) return null
    return { from, to: context.pos, options, filter: false }
  }
}

