import { useMemo, type ReactNode } from "react"

import { configField } from "../../../utils/configField"
import { CodeEditor } from "../CodeEditor"
import { InputSourcesBar } from "../_shared"
import type { InputSource, OnUpdateConfig } from "../_shared"

type PolarsCodePanelProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  errorLine?: number | null
  upstreamColumns?: { name: string; dtype: string }[]
  hint: ReactNode
  placeholder?: string
}

export default function PolarsCodePanel({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  errorLine,
  upstreamColumns,
  hint,
  placeholder = "",
}: PolarsCodePanelProps) {
  const defaultCode = configField(config, "code", "")
  const columnNames = useMemo(() => (upstreamColumns ?? []).map((c) => c.name), [upstreamColumns])

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-2">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />
      <div className="flex items-center justify-between shrink-0">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>
          Polars Code
        </label>
        <span className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
          {hint}
        </span>
      </div>
      <CodeEditor
        defaultValue={defaultCode}
        onChange={(val) => onUpdate("code", val)}
        errorLine={errorLine}
        placeholder={placeholder}
        availableColumns={columnNames}
      />
      <div className="text-[11px] font-mono shrink-0" style={{ color: "var(--text-muted)", opacity: 0.6 }}>
        return df
      </div>
    </div>
  )
}
