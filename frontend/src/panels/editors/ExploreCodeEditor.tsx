import SteppedCodePane from "./shared/SteppedCodePane"
import type { InputSource, OnReplaceConfig, OnUpdateConfig } from "./_shared"

/**
 * Explore's "Polars Code" pane: the shared stepped-code pane in `frame` mode.
 * Codegen binds the single input as `df`, so the steps see only that frame
 * and may not reference inputs; their list travels in the decorator beside
 * the node's pivots and charts.
 */
export default function ExploreCodeEditor(props: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onReplaceConfig?: OnReplaceConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  /** The last run's error message for this node, if it failed. */
  runError?: string | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  return <SteppedCodePane {...props} inputNames={[]} start="frame" codeHint="assign to df" />
}
