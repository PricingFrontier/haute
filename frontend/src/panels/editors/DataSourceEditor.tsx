import { useState, useEffect } from "react"
import { FileText, Database, Check } from "lucide-react"
import { FileBrowser, CodeEditor } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { WarehousePicker, CatalogTablePicker, DatabricksFetchButton } from "./_DatabricksSelector"
import { configField } from "../../utils/configField"

export default function DataSourceEditor({
  config,
  onUpdate,
  onRefreshPreview,
  accentColor,
  errorLine,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  onRefreshPreview?: () => void
  accentColor: string
  errorLine?: number | null
}) {
  const sourceType = configField(config, "sourceType", "flat_file")
  const currentPath = configField<string | undefined>(config, "path", undefined)
  const hasFile = sourceType === "flat_file" && !!currentPath
  const [fileExpanded, setFileExpanded] = useState(false)
  const configQuery = configField(config, "query", "") || "SELECT *"
  const [query, setQuery] = useState(configQuery)
  useEffect(() => { setQuery(configQuery) }, [configQuery])

  return (
    <>
      <div className="px-4 py-3 space-y-3">
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Source Type</label>
          <div className="mt-1">
            <ToggleButtonGroup
              value={sourceType}
              onChange={(v) => onUpdate("sourceType", v)}
              options={[
                { key: "flat_file", label: "Flat File", icon: <FileText size={12} /> },
                { key: "databricks", label: "Databricks", icon: <Database size={12} /> },
              ]}
              accentColor={accentColor}
            />
          </div>
        </div>

        {sourceType === "flat_file" && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1.5 block" style={{ color: 'var(--text-muted)' }}>
              File
            </label>
            {hasFile && (
              <div className="px-2.5 py-2 rounded-lg flex items-center gap-2" style={{ background: 'var(--success-soft)', border: '1px solid var(--success-border)' }}>
                <Check size={14} style={{ color: 'var(--success)' }} className="shrink-0" />
                <span className="text-xs font-mono truncate flex-1" style={{ color: 'var(--success-hover)' }}>{currentPath}</span>
                <button
                  data-testid="file-change-btn"
                  onClick={() => setFileExpanded(!fileExpanded)}
                  className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded transition-colors"
                  style={{ color: 'var(--success-hover)' }}
                >
                  {fileExpanded ? "close" : "change"}
                </button>
              </div>
            )}
            {(!hasFile || fileExpanded) && (
              <div className="mt-2">
                <FileBrowser
                  currentPath={undefined}
                  onSelect={(path) => {
                    onUpdate("path", path)
                    setFileExpanded(false)
                    setTimeout(() => onRefreshPreview?.(), 50)
                  }}
                />
              </div>
            )}
          </div>
        )}

        {sourceType === "databricks" && (
          <div className="space-y-3">
            <WarehousePicker
              httpPath={configField(config, "http_path", "")}
              onSelect={(hp) => onUpdate("http_path", hp || undefined)}
            />
            <CatalogTablePicker
              table={configField(config, "table", "")}
              onSelect={(fullName) => onUpdate("table", fullName || undefined)}
            />
            <div>
              <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
                SQL Query
                <span className="ml-1.5 normal-case tracking-normal font-normal" style={{ color: 'var(--text-muted)' }}>(optional)</span>
              </label>
              <textarea
                placeholder={"SELECT *\nFROM catalog.schema.table\nWHERE status = 'active'"}
                value={query}
                onChange={(e) => { setQuery(e.target.value); onUpdate("query", e.target.value || undefined) }}
                rows={3}
                className="focus-ring mt-1 w-full px-2.5 py-1.5 text-xs font-mono rounded-lg resize-y"
                style={{ background: 'var(--bg-input)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
              />
              <div className="mt-1 text-[10px]" style={{ color: 'var(--text-muted)' }}>
                Combined with table above as: query FROM table
              </div>
            </div>
            <DatabricksFetchButton
              table={configField(config, "table", "")}
              httpPath={configField(config, "http_path", "")}
              query={configField(config, "query", "")}
              onFetched={() => onRefreshPreview?.()}
            />
          </div>
        )}
      </div>

      <div className="px-3 py-2 flex flex-col gap-2" style={{ borderTop: '1px solid var(--border)' }}>
        <div className="flex items-center justify-between shrink-0">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
            Polars Code
            <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
          </label>
          <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
            use <code className="px-0.5 rounded" style={{ background: 'var(--bg-hover)' }}>df</code> for loaded data
          </span>
        </div>
        <CodeEditor
          defaultValue={configField(config, "code", "")}
          onChange={(val) => onUpdate("code", val)}
          errorLine={errorLine}
          placeholder=""
        />
        <div className="text-[11px] font-mono" style={{ color: 'var(--text-muted)', opacity: 0.6 }}>
          return df
        </div>
      </div>
    </>
  )
}
