import { useState, useEffect, useRef } from "react"
import { X, Folder, FileText, ChevronLeft, Check, Table2, Loader2, AlertTriangle } from "lucide-react"
import type { ColumnInfo } from "../../types/node"
import { listFiles } from "../../api/client"
import ColumnTable from "../../components/ColumnTable"
import useSettingsStore, { useMlflowStatus } from "../../stores/useSettingsStore"
import { EditorView, placeholder as cmPlaceholder, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, rectangularSelection } from "@codemirror/view"
import { EditorState, Compartment } from "@codemirror/state"
import { python } from "@codemirror/lang-python"
import { syntaxHighlighting, indentOnInput, bracketMatching, foldGutter, foldKeymap, HighlightStyle, indentUnit, syntaxTree } from "@codemirror/language"
import { defaultKeymap, indentWithTab, history, historyKeymap } from "@codemirror/commands"
import { closeBrackets, closeBracketsKeymap, autocompletion, completionKeymap, type CompletionContext, type CompletionResult } from "@codemirror/autocomplete"
import { searchKeymap, highlightSelectionMatches } from "@codemirror/search"
import { lintGutter, setDiagnostics } from "@codemirror/lint"
import { tags } from "@lezer/highlight"

// ─── Shared Styles ───────────────────────────────────────────────
export const INPUT_STYLE = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
} as const

export const SELECT_STYLE = INPUT_STYLE

// ─── Shared Types ─────────────────────────────────────────────────

export type OnUpdateConfig = (keyOrUpdates: string | Record<string, unknown>, value?: unknown) => void

export type FileItem = {
  name: string
  path: string
  type: "file" | "directory"
  size?: number
}

export type InputSource = {
  varName: string
  sourceLabel: string
  edgeId: string
}

export type SchemaInfo = {
  path: string
  columns: ColumnInfo[]
  row_count: number | null
  row_count_estimated?: boolean
  column_count: number
  preview: Record<string, unknown>[]
} | null

export type SimpleNode = {
  id: string
  type?: string
  data: {
    label: string
    description: string
    nodeType: string
    config?: Record<string, unknown>
    [key: string]: unknown
  }
}

export type SimpleEdge = {
  id: string
  source: string
  target: string
}

// ─── MlflowStatusBadge ───────────────────────────────────────────

export function MlflowStatusBadge() {
  const { mlflowStatus, mlflowBackend } = useMlflowStatus()
  return (
    <div className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[11px]" style={{
      background: mlflowStatus === "connected" ? "var(--success-soft-subtle)" : mlflowStatus === "error" ? "var(--danger-soft-faint)" : "var(--bg-surface)",
      border: `1px solid ${mlflowStatus === "connected" ? "var(--success-border)" : mlflowStatus === "error" ? "var(--danger-border)" : "var(--border)"}`,
    }}>
      {mlflowStatus === "loading" ? (
        <><Loader2 size={11} className="animate-spin" style={{ color: "var(--text-muted)" }} /><span style={{ color: "var(--text-muted)" }}>Connecting to MLflow...</span></>
      ) : mlflowStatus === "connected" ? (
        <><Check size={11} style={{ color: "var(--success)" }} /><span style={{ color: "var(--text-secondary)" }}>MLflow ({mlflowBackend})</span></>
      ) : (
        <><AlertTriangle size={11} style={{ color: "var(--danger)" }} /><span style={{ color: "var(--danger)" }}>MLflow not available</span></>
      )}
    </div>
  )
}

// ─── FileBrowser ──────────────────────────────────────────────────

export function FileBrowser({ currentPath, onSelect, extensions }: { currentPath?: string; onSelect: (path: string) => void; extensions?: string }) {
  const [dir, setDir] = useState(() => {
    if (!currentPath) return "."
    const lastSlash = currentPath.lastIndexOf("/")
    return lastSlash > 0 ? currentPath.substring(0, lastSlash) : "."
  })
  const [items, setItems] = useState<FileItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selectedPath, setSelectedPath] = useState<string | undefined>(currentPath)
  const getFileListCache = useSettingsStore((s) => s.getFileListCache)
  const setFileListCache = useSettingsStore((s) => s.setFileListCache)

  useEffect(() => {
    let cancelled = false
    const cacheKey = `${dir}|${extensions || ""}`
    const cached = getFileListCache(cacheKey)
    if (cached) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- cache-hit fast path: show cached file list immediately without async fetch
      setItems(cached as FileItem[])
      setLoading(false)
      return
    }
    setError(null)
    listFiles(dir, extensions)
      .then((data) => {
        if (cancelled) return
        const fileItems = data.items || []
        setItems(fileItems)
        setFileListCache(cacheKey, fileItems)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (cancelled) return
        setError(e instanceof Error ? e.message : "Failed to load files")
        setItems([])
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [dir, extensions, getFileListCache, setFileListCache])

  const goUp = () => {
    if (dir === ".") return
    const parts = dir.split("/")
    parts.pop()
    setLoading(true)
    setDir(parts.length > 0 ? parts.join("/") : ".")
  }

  const handleFileClick = (path: string) => {
    setSelectedPath(path)
    onSelect(path)
  }

  const formatSize = (bytes: number) => {
    if (bytes > 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return (
    <div>
      {selectedPath && (
        <div className="mb-2 px-2.5 py-2 rounded-lg flex items-center gap-2" style={{ background: 'var(--success-soft)', border: '1px solid var(--success-border)' }}>
          <Check size={14} style={{ color: 'var(--success)' }} className="shrink-0" />
          <span className="text-xs font-mono truncate" style={{ color: 'var(--success-hover)' }}>{selectedPath}</span>
        </div>
      )}

      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div className="px-2 py-1.5 flex items-center gap-1.5" style={{ background: 'var(--bg-elevated)', borderBottom: '1px solid var(--border)' }}>
          <button
            onClick={goUp}
            disabled={dir === "."}
            className="p-0.5 rounded disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            style={{ color: 'var(--text-secondary)' }}
          >
            <ChevronLeft size={14} />
          </button>
          <span className="text-xs font-mono truncate" style={{ color: 'var(--text-muted)' }}>{dir === "." ? "/" : dir}</span>
        </div>

        <div className="max-h-40 overflow-y-auto" style={{ background: 'var(--bg-input)' }}>
          {loading ? (
            <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-muted)' }}>Loading...</div>
          ) : error ? (
            <div className="px-3 py-2 text-xs" style={{ color: 'var(--danger-text)' }}>{error}</div>
          ) : items.length === 0 ? (
            <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-muted)' }}>No matching files</div>
          ) : (
            items.map((item) => {
              const isSelected = item.type === "file" && item.path === selectedPath
              return (
                <button
                  key={item.path}
                  onClick={() => {
                    if (item.type === "directory") {
                      setLoading(true)
                      setDir(item.path)
                    } else {
                      handleFileClick(item.path)
                    }
                  }}
                  className="file-browser-row w-full px-3 py-2 flex items-center gap-2 text-left"
                  data-selected={isSelected ? "true" : "false"}
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  {item.type === "directory" ? (
                    <Folder size={14} style={{ color: 'var(--warning-strong)' }} className="shrink-0" />
                  ) : isSelected ? (
                    <Check size={14} style={{ color: 'var(--accent)' }} className="shrink-0" />
                  ) : (
                    <FileText size={14} style={{ color: 'var(--text-muted)' }} className="shrink-0" />
                  )}
                  <span className="text-xs truncate" style={{ color: isSelected ? 'var(--accent)' : 'var(--text-secondary)', fontWeight: isSelected ? 500 : 400 }}>
                    {item.name}
                  </span>
                  {item.size !== undefined && (
                    <span className="text-[11px] ml-auto shrink-0" style={{ color: 'var(--text-muted)' }}>
                      {formatSize(item.size)}
                    </span>
                  )}
                </button>
              )
            })
          )}
        </div>
      </div>
    </div>
  )
}

// ─── SchemaPreview ────────────────────────────────────────────────

export function SchemaPreview({ schema }: { schema: SchemaInfo }) {
  const [showPreview, setShowPreview] = useState(false)

  if (!schema || !schema.columns) return null

  return (
    <div style={{ borderTop: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
      <div className="px-4 py-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Table2 size={14} style={{ color: 'var(--text-muted)' }} />
          <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>Schema</span>
          <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
            {schema.column_count ?? 0} cols / {schema.row_count != null ? `${schema.row_count_estimated ? "~" : ""}${schema.row_count.toLocaleString()} rows` : "? rows"}
          </span>
        </div>
        <button
          onClick={() => setShowPreview(!showPreview)}
          className="text-[11px] font-medium" style={{ color: 'var(--accent)' }}
        >
          {showPreview ? "Hide preview" : "Show preview"}
        </button>
      </div>

      <div className="px-4 pb-3">
        <ColumnTable columns={schema.columns} />

        {showPreview && schema.preview.length > 0 && (
          <div className="mt-2 rounded-lg overflow-x-auto" style={{ border: '1px solid var(--border)', background: 'var(--bg-input)' }}>
            <table className="w-full text-[11px]">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-elevated)' }}>
                  {schema.columns.map((col) => (
                    <th key={col.name} className="text-left px-2 py-1 font-semibold whitespace-nowrap" style={{ color: 'var(--text-muted)' }}>
                      {col.name}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {schema.preview.map((row, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
                    {schema.columns.map((col) => (
                      <td key={col.name} className="px-2 py-1 font-mono whitespace-nowrap" style={{ color: 'var(--text-secondary)' }}>
                        {String(row[col.name] ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}

// ─── CodeEditor (CodeMirror 6) ────────────────────────────────────

// Dark theme matching Haute's CSS variables
const hauteTheme = EditorView.theme({
  "&": {
    backgroundColor: "var(--bg-input)",
    color: "var(--text-primary)",
    fontSize: "12px",
    fontFamily: "ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace",
    flex: "1",
    minHeight: "120px",
  },
  "&.cm-focused": {
    outline: "none",
  },
  ".cm-scroller": {
    fontFamily: "inherit",
    lineHeight: "1.625",
    overflow: "auto",
  },
  ".cm-content": {
    caretColor: "var(--accent)",
    padding: "10px 0",
  },
  ".cm-line": {
    padding: "0 12px 0 4px",
  },
  ".cm-gutters": {
    backgroundColor: "var(--bg-elevated)",
    color: "var(--text-muted)",
    border: "none",
    borderRight: "1px solid var(--border)",
  },
  ".cm-gutter.cm-lineNumbers .cm-gutterElement": {
    padding: "0 8px 0 4px",
    minWidth: "28px",
  },
  ".cm-activeLineGutter": {
    backgroundColor: "transparent",
    color: "var(--text-secondary)",
  },
  ".cm-activeLine": {
    backgroundColor: "rgba(255,255,255,.03)",
  },
  ".cm-selectionBackground": {
    backgroundColor: "var(--accent-selection) !important",
  },
  "&.cm-focused .cm-selectionBackground": {
    backgroundColor: "var(--accent-ring) !important",
  },
  ".cm-cursor": {
    borderLeftColor: "var(--accent)",
  },
  ".cm-matchingBracket": {
    backgroundColor: "var(--accent-selection)",
    outline: "1px solid var(--accent-outline)",
  },
  ".cm-selectionMatch": {
    backgroundColor: "var(--accent-soft-strong)",
  },
  ".cm-searchMatch": {
    backgroundColor: "var(--warning-search)",
    outline: "1px solid var(--warning-search-outline)",
  },
  ".cm-searchMatch.cm-searchMatch-selected": {
    backgroundColor: "var(--warning-search-selected)",
  },
  ".cm-foldGutter .cm-gutterElement": {
    color: "var(--text-muted)",
    padding: "0 4px",
  },
  ".cm-tooltip": {
    backgroundColor: "var(--bg-elevated)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li": {
    padding: "4px 8px",
  },
  ".cm-tooltip.cm-tooltip-autocomplete > ul > li[aria-selected]": {
    backgroundColor: "var(--accent-soft)",
    color: "var(--text-primary)",
  },
  ".cm-panels": {
    backgroundColor: "var(--bg-elevated)",
    color: "var(--text-primary)",
    borderTop: "1px solid var(--border)",
  },
  ".cm-panels.cm-panels-bottom": {
    borderTop: "1px solid var(--border)",
  },
  ".cm-panel input": {
    backgroundColor: "var(--bg-input)",
    color: "var(--text-primary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 6px",
    fontSize: "12px",
  },
  ".cm-panel button": {
    backgroundColor: "var(--bg-hover)",
    color: "var(--text-secondary)",
    border: "1px solid var(--border)",
    borderRadius: "4px",
    padding: "2px 8px",
    fontSize: "12px",
  },
  ".cm-placeholder": {
    color: "var(--text-muted)",
    fontStyle: "italic",
  },
  // Lint diagnostics
  ".cm-lintRange-error": {
    backgroundImage: "none",
    backgroundColor: "var(--danger-soft-strong)",
    borderBottom: "2px solid var(--danger)",
  },
  ".cm-gutter-lint": {
    width: "14px",
  },
  ".cm-gutter-lint .cm-gutterElement": {
    padding: "0 2px",
  },
  ".cm-lint-marker-error": {
    width: "8px !important",
    height: "8px !important",
    borderRadius: "50%",
    backgroundColor: "var(--danger)",
    display: "inline-block",
    marginTop: "6px",
  },
  ".cm-tooltip-lint": {
    backgroundColor: "var(--bg-elevated)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  },
}, { dark: true })

// Syntax highlighting colours for Python
const hauteHighlighting = HighlightStyle.define([
  { tag: tags.keyword, color: "#c084fc" },             // purple — def, return, if, for, import
  { tag: tags.controlKeyword, color: "#c084fc" },
  { tag: tags.definitionKeyword, color: "#c084fc" },
  { tag: tags.operatorKeyword, color: "#c084fc" },      // and, or, not, in, is
  { tag: tags.modifier, color: "#c084fc" },
  { tag: tags.self, color: "#f472b6" },                  // self
  { tag: tags.bool, color: "#fb923c" },                  // True, False
  { tag: tags.null, color: "#fb923c" },                  // None
  { tag: tags.number, color: "#fb923c" },                // numeric literals
  { tag: tags.string, color: "#86efac" },                // strings
  { tag: tags.special(tags.string), color: "#86efac" },  // f-strings
  { tag: tags.regexp, color: "var(--syntax-warning)" },
  { tag: tags.comment, color: "var(--text-muted)", fontStyle: "italic" },
  { tag: tags.function(tags.definition(tags.variableName)), color: "var(--syntax-accent)" },  // function defs
  { tag: tags.function(tags.variableName), color: "#93c5fd" },  // function calls
  { tag: tags.className, color: "var(--syntax-warning)" },
  { tag: tags.definition(tags.className), color: "var(--syntax-warning)" },
  { tag: tags.propertyName, color: "#67e8f9" },          // .method / .attr
  { tag: tags.operator, color: "#94a3b8" },
  { tag: tags.punctuation, color: "#94a3b8" },
  { tag: tags.bracket, color: "#cbd5e1" },
  { tag: tags.meta, color: "#a78bfa" },                  // decorators
  { tag: tags.variableName, color: "var(--text-primary)" },
  { tag: tags.typeName, color: "var(--syntax-warning)" },
])

// Focus ring: border change on focus/blur
const focusRingTheme = EditorView.theme({
  "&": {
    borderRadius: "8px",
    border: "1px solid var(--border)",
    overflow: "hidden",
    transition: "border-color 0.15s, box-shadow 0.15s",
  },
  "&.cm-focused": {
    borderColor: "var(--accent-ring)",
    boxShadow: "0 0 0 2px var(--accent-soft)",
  },
})

// ─── Column-aware autocomplete source ─────────────────────────────

function columnCompletionSource(columns: string[]) {
  return (context: CompletionContext): CompletionResult | null => {
    if (columns.length === 0) return null

    // Walk the syntax tree to check if cursor is inside a string literal
    const node = syntaxTree(context.state).resolveInner(context.pos, -1)
    const isString = node.name === "String" || node.name === "FormatString"

    if (!isString) return null

    // Find the opening quote and extract the partial text after it
    const nodeText = context.state.sliceDoc(node.from, context.pos)
    // Match opening quote(s): single, double, triple-single, triple-double, with optional f/r/b prefix
    const quoteMatch = nodeText.match(/^[fFrRbBuU]{0,2}("""|'''|"|')/)
    if (!quoteMatch) return null

    const quoteLen = quoteMatch[0].length
    const partialFrom = node.from + quoteLen
    const partial = context.state.sliceDoc(partialFrom, context.pos)

    // Filter columns by case-insensitive prefix match
    const lower = partial.toLowerCase()
    const options = columns
      .filter((col) => col.toLowerCase().startsWith(lower))
      .map((col) => ({ label: col, type: "variable" as const }))

    if (options.length === 0) return null

    return {
      from: partialFrom,
      to: context.pos,
      options,
      filter: false, // we already filtered
    }
  }
}

export function CodeEditor({
  defaultValue,
  onChange,
  placeholder,
  errorLine,
  availableColumns,
  onEditorView,
}: {
  defaultValue: string
  onChange: (value: string) => void
  placeholder?: string
  errorLine?: number | null
  /** Column names for in-string autocomplete */
  availableColumns?: string[]
  /** Callback to expose the EditorView for external operations (e.g. text insertion) */
  onEditorView?: (view: EditorView | null) => void
}) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined)
  const placeholderCompartment = useRef(new Compartment())
  const columnCompartment = useRef(new Compartment())
  const isFocusedRef = useRef(false)

  // Keep onChange ref fresh without recreating the editor
  useEffect(() => { onChangeRef.current = onChange }, [onChange])

  // Sync external value changes to the editor when not focused
  // (avoids overwriting user typing)
  useEffect(() => {
    const view = viewRef.current
    if (!view || isFocusedRef.current) return
    const currentDoc = view.state.doc.toString()
    if (defaultValue !== currentDoc) {
      view.dispatch({
        changes: { from: 0, to: currentDoc.length, insert: defaultValue },
      })
    }
  }, [defaultValue])

  // Create the editor once on mount
  useEffect(() => {
    if (!containerRef.current) return

    const updateListener = EditorView.updateListener.of((update) => {
      if (update.docChanged) {
        const value = update.state.doc.toString()
        if (debounceRef.current) clearTimeout(debounceRef.current)
        debounceRef.current = setTimeout(() => onChangeRef.current(value), 150)
        // Clear diagnostics when the user edits — they're fixing the code
        if (update.view) {
          update.view.dispatch(setDiagnostics(update.state, []))
        }
      }
    })

    const state = EditorState.create({
      doc: defaultValue,
      extensions: [
        // Core editing
        history(),
        drawSelection(),
        rectangularSelection(),
        indentOnInput(),
        bracketMatching(),
        closeBrackets(),
        // Column-aware completions (reconfigurable via compartment)
        columnCompartment.current.of(
          availableColumns?.length
            ? autocompletion({ override: [columnCompletionSource(availableColumns)] })
            : autocompletion(),
        ),
        highlightActiveLine(),
        highlightActiveLineGutter(),
        highlightSelectionMatches(),
        indentUnit.of("    "),

        // Gutters
        lineNumbers(),
        foldGutter(),
        lintGutter(),

        // Python language
        python(),
        syntaxHighlighting(hauteHighlighting),

        // Theme
        hauteTheme,
        focusRingTheme,

        // Placeholder
        placeholderCompartment.current.of(
          placeholder ? cmPlaceholder(placeholder) : [],
        ),

        // Keymaps — order matters: specific before general
        keymap.of([
          ...closeBracketsKeymap,
          ...searchKeymap,
          ...historyKeymap,
          ...foldKeymap,
          ...completionKeymap,
          indentWithTab,
          ...defaultKeymap,
        ]),

        // Focus tracking for external sync
        EditorView.domEventHandlers({
          focus: () => { isFocusedRef.current = true },
          blur: () => { isFocusedRef.current = false },
        }),

        // Change listener
        updateListener,

        // Prevent the editor from growing wider than the panel
        EditorView.lineWrapping,
      ],
    })

    const view = new EditorView({
      state,
      parent: containerRef.current,
    })

    viewRef.current = view
    onEditorView?.(view)

    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current)
      onEditorView?.(null)
      view.destroy()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount once: defaultValue is initial content only, onChange is tracked via ref
  }, [])

  // Update column completions when availableColumns changes
  useEffect(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: columnCompartment.current.reconfigure(
        availableColumns?.length
          ? autocompletion({ override: [columnCompletionSource(availableColumns)] })
          : autocompletion(),
      ),
    })
  }, [availableColumns])

  // Push error diagnostics when errorLine changes
  useEffect(() => {
    const view = viewRef.current
    if (!view) return

    if (errorLine != null && errorLine >= 1) {
      const doc = view.state.doc
      const lineNum = Math.min(errorLine, doc.lines)
      const line = doc.line(lineNum)
      view.dispatch(setDiagnostics(view.state, [{
        from: line.from,
        to: line.to,
        severity: "error",
        message: `Error on line ${errorLine}`,
      }]))
    } else {
      view.dispatch(setDiagnostics(view.state, []))
    }
  }, [errorLine])

  // Update placeholder if it changes
  useEffect(() => {
    if (!viewRef.current) return
    viewRef.current.dispatch({
      effects: placeholderCompartment.current.reconfigure(
        placeholder ? cmPlaceholder(placeholder) : [],
      ),
    })
  }, [placeholder])

  return <div ref={containerRef} data-testid="code-editor-wrapper" className="flex-1 min-h-[120px]" />
}

// ─── InputSourcesBar ──────────────────────────────────────────────

export function InputSourcesBar({
  inputSources,
  onDeleteInput,
}: {
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
}) {
  if (inputSources.length === 0) return null
  return (
    <div className="rounded-lg px-3 py-1.5 shrink-0" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          {inputSources.length > 1 ? "Inputs" : "Input"}
        </span>
        {inputSources.map((src) => (
          <span key={src.varName} className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded" style={{ background: 'var(--accent-soft)' }}>
            <code className="text-[11px] font-semibold" style={{ color: 'var(--accent)' }}>{src.varName}</code>
            {onDeleteInput && (
              <button
                onClick={() => onDeleteInput(src.edgeId)}
                className="icon-danger-btn p-0 rounded"
                title={`Remove connection from ${src.sourceLabel}`}
              >
                <X size={10} />
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  )
}
