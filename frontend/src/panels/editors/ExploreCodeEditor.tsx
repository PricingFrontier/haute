import PolarsCodePanel from "./shared/PolarsCodePanel"
import type { InputSource, OnUpdateConfig } from "./_shared"

export default function ExploreCodeEditor(props: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  onRenameInput?: (edgeId: string, alias: string | null) => void
  errorLine?: number | null
  upstreamColumns?: { name: string; dtype: string }[]
}) {
  return <PolarsCodePanel {...props} hint="assign to df" />
}
