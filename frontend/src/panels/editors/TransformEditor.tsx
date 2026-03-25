import { useState, useRef, useMemo, useCallback } from "react"
import { ChevronDown, ChevronUp, Plus } from "lucide-react"
import { InputSourcesBar, CodeEditor } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { getDtypeColor } from "../../utils/dtypeColors"
import { syntaxTree } from "@codemirror/language"
import type { EditorView } from "@codemirror/view"

const API_INPUT_PLACEHOLDER = `# Clean up dot-notation columns:
#
# df = haute.clean_columns(quotes)
#
# Then add derived columns with Polars:
# df = df.with_columns(
#     ((cover_start - pl.col("dob").str.to_date("%Y-%m-%d")).dt.total_days() / 365.25)
#         .floor().cast(pl.Int64).alias("age"),
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
  const editorViewRef = useRef<EditorView | null>(null)
  const [columnsExpanded, setColumnsExpanded] = useState(false)

  const cols = useMemo(() => upstreamColumns ?? [], [upstreamColumns])
  const columnNames = useMemo(() => cols.map((c) => c.name), [cols])

  // Contextual placeholder: show hint when code is empty and upstream is api_input
  const placeholder = hasApiInputUpstream ? API_INPUT_PLACEHOLDER : ""

  const handleEditorView = useCallback((view: EditorView | null) => {
    editorViewRef.current = view
  }, [])

  const insertColumnAtCursor = useCallback((colName: string) => {
    const view = editorViewRef.current
    if (!view) return

    const { state } = view
    const pos = state.selection.main.head

    // Use syntax tree for reliable string detection (matches autocomplete approach)
    const node = syntaxTree(state).resolveInner(pos, -1)
    const isInString = node.name === "String" || node.name === "FormatString"

    // Escape special characters in column name for safe insertion
    const escaped = colName.replace(/\\/g, "\\\\").replace(/"/g, '\\"')
    const text = isInString ? colName : `pl.col("${escaped}")`

    view.dispatch({
      changes: { from: pos, to: pos, insert: text },
      selection: { anchor: pos + text.length },
    })
    view.focus()
  }, [])

  return (
    <div className="flex-1 flex flex-col min-h-0 px-3 py-2 gap-2">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />
      <div className="flex items-center justify-between shrink-0">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          Polars Code
        </label>
        <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
          {hasInput ? "use input names" : <>start with <code className="px-0.5 rounded" style={{ background: 'var(--bg-hover)' }}>.</code> to chain</>}
        </span>
      </div>
      <CodeEditor
        defaultValue={defaultCode}
        onChange={(val) => onUpdate("code", val)}
        errorLine={errorLine}
        placeholder={placeholder}
        availableColumns={columnNames}
        onEditorView={handleEditorView}
      />

      {/* Click-to-insert column sidebar */}
      {cols.length > 0 && (
        <div className="shrink-0 rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
          <button
            onClick={() => setColumnsExpanded(!columnsExpanded)}
            className="w-full px-3 py-1.5 flex items-center justify-between text-left transition-colors"
            style={{ background: 'var(--bg-elevated)' }}
            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-hover)' }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          >
            <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              Available Columns
            </span>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
                {cols.length}
              </span>
              {columnsExpanded
                ? <ChevronUp size={12} style={{ color: 'var(--text-muted)' }} />
                : <ChevronDown size={12} style={{ color: 'var(--text-muted)' }} />
              }
            </div>
          </button>

          {columnsExpanded && (
            <div className="max-h-48 overflow-y-auto" style={{ borderTop: '1px solid var(--border)' }}>
              {cols.map((col) => (
                <div
                  key={col.name}
                  className="flex items-center justify-between px-3 py-1 transition-colors"
                  style={{ borderBottom: '1px solid var(--border)' }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-hover)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-xs font-mono truncate" style={{ color: 'var(--text-primary)' }}>
                      {col.name}
                    </span>
                    <span className={`text-[10px] shrink-0 ${getDtypeColor(col.dtype)}`}>
                      {col.dtype}
                    </span>
                  </div>
                  <button
                    onClick={() => insertColumnAtCursor(col.name)}
                    className="p-0.5 rounded transition-colors shrink-0 ml-2"
                    style={{ color: 'var(--text-muted)' }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--accent)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)' }}
                    title={`Insert pl.col("${col.name}")`}
                  >
                    <Plus size={12} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
