import PolarsCodePanel from "./shared/PolarsCodePanel"
import type { InputSource, OnUpdateConfig } from "./_shared"

const API_INPUT_PLACEHOLDER = `# Clean up dot-notation columns:
#
# df = clean_columns(quotes)
#
# Then add derived columns with Polars:
# df = df.with_columns(
#     years_between(to_date("date_of_birth"), to_date("cover_start_date")).alias("age"),
# )`

export default function TransformEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  errorLine,
  upstreamColumns,
  hasApiInputUpstream,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  upstreamColumns?: { name: string; dtype: string }[]
  /** Whether any upstream node is an api_input type (for contextual placeholder) */
  hasApiInputUpstream?: boolean
}) {
  const hasInput = inputSources.length > 0
  return (
    <PolarsCodePanel
      config={config}
      onUpdate={onUpdate}
      inputSources={inputSources}
      onDeleteInput={onDeleteInput}
      errorLine={errorLine}
      upstreamColumns={upstreamColumns}
      hint={hasInput ? "use input names" : "assign to df"}
      placeholder={hasApiInputUpstream ? API_INPUT_PLACEHOLDER : ""}
    />
  )
}
