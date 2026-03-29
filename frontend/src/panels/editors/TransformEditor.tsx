import { useMemo } from "react"
import { InputSourcesBar, CodeEditor } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"

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
  const defaultCode = configField(config, "code", "")
  const hasInput = inputSources.length > 0

  const columnNames = useMemo(() => (upstreamColumns ?? []).map((c) => c.name), [upstreamColumns])

  // Contextual placeholder: show hint when code is empty and upstream is api_input
  const placeholder = hasApiInputUpstream ? API_INPUT_PLACEHOLDER : ""

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-2">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />
      <div className="flex items-center justify-between shrink-0">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          Polars Code
        </label>
        <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
          {hasInput ? "use input names" : "assign to df"}
        </span>
      </div>
      <CodeEditor
        defaultValue={defaultCode}
        onChange={(val) => onUpdate("code", val)}
        errorLine={errorLine}
        placeholder={placeholder}
        availableColumns={columnNames}
      />
      <div className="text-[11px] font-mono shrink-0" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
        return df
      </div>
    </div>
  )
}
