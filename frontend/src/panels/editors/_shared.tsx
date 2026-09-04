import { useState, useEffect } from "react"
import { X, Folder, FileText, ChevronLeft, Check, Table2, Loader2, AlertTriangle } from "lucide-react"
import type { ColumnInfo } from "../../types/node"
import { listFiles } from "../../api/client"
import type { FileListItem } from "../../api/types"
import ColumnTable from "../../components/ColumnTable"
import useSettingsStore, { useMlflowStatus } from "../../stores/useSettingsStore"
import { formatValue } from "../../utils/formatValue"

// ─── Shared Styles ───────────────────────────────────────────────
export const INPUT_STYLE = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
} as const

export const SELECT_STYLE = INPUT_STYLE

// ─── Shared Types ─────────────────────────────────────────────────

export type OnUpdateConfigResult =
  | { ok: true }
  | { ok: false; error: string }

export type OnUpdateConfig = (
  keyOrUpdates: string | Record<string, unknown>,
  value?: unknown,
) => OnUpdateConfigResult

/** Replace an IO configuration atomically when its provider branch changes. */
export type OnReplaceConfig = (nextConfig: Record<string, unknown>) => OnUpdateConfigResult

export type FileItem = FileListItem

export type InputSource = {
  sourceNodeId: string
  /** The executable input name derived from this edge. */
  name: string
  sourceLabel: string
  edgeId: string
  frameUnresolved?: boolean
}

// Shared by editor components; this intentional non-component export is the
// single title contract for unresolved API-input frame labels.
// eslint-disable-next-line react-refresh/only-export-components
export const unresolvedFrameTitle = (sourceLabel: string): string =>
  `No emitted frame resolves for this connection (from ${sourceLabel})`

export type SchemaInfo = {
  path: string
  columns: ColumnInfo[]
  row_count: number | null
  row_count_estimated?: boolean
  column_count: number
  preview: Record<string, unknown>[]
} | null

function formatPreviewCell(value: unknown): string {
  if (value === null || value === undefined) return ""
  return formatValue(value)
}

function previewCellExactText(value: unknown, displayValue: string): string | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined
  const exactValue = Object.is(value, -0) ? "-0" : String(value)
  return exactValue === displayValue ? undefined : exactValue
}

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
  sourceHandle?: string | null
  targetHandle?: string | null
  data?: Record<string, unknown>
}

// ─── MlflowStatusBadge ───────────────────────────────────────────

export function MlflowStatusBadge() {
  const {
    mlflowStatus,
    mlflowBackend,
    mlflowInstalled,
    mlflowImportable,
    mlflowTrackingConfigured,
    mlflowDetail,
  } = useMlflowStatus()

  const isConnected = mlflowStatus === "connected"
  const isLoading = mlflowStatus === "loading"
  const isPackageMissing = mlflowStatus === "error" && mlflowInstalled === false
  const isPackageLoadFailed =
    mlflowStatus === "error" && mlflowInstalled === true && mlflowImportable === false
  const isTrackingNotConfigured =
    mlflowStatus === "error" &&
    mlflowInstalled === true &&
    mlflowImportable !== false &&
    mlflowTrackingConfigured === false
  const tone = isConnected
    ? "success"
    : isPackageMissing || isPackageLoadFailed
      ? "danger"
      : isTrackingNotConfigured || mlflowStatus === "error"
        ? "warning"
        : "neutral"
  const background = tone === "success"
    ? "var(--editor-status-success-bg)"
    : tone === "danger"
      ? "var(--danger-soft-faint)"
      : tone === "warning"
        ? "var(--warning-soft-subtle)"
        : "var(--bg-panel)"
  const border = tone === "success"
    ? "var(--editor-status-success-border)"
    : tone === "danger"
      ? "var(--danger-border)"
      : tone === "warning"
        ? "var(--warning-border)"
        : "var(--border)"
  const iconColor = tone === "success"
    ? "var(--editor-status-success-text)"
    : tone === "danger"
      ? "var(--danger)"
      : tone === "warning"
        ? "var(--warning-strong)"
        : "var(--text-muted)"
  const labelColor = tone === "danger"
    ? "var(--danger)"
    : tone === "warning"
      ? "var(--warning-strong)"
      : "var(--text-secondary)"
  const label = isLoading
    ? "Checking MLflow..."
    : isConnected
      ? `MLflow tracking configured (${mlflowBackend || "local"})`
      : isPackageMissing
        ? "MLflow package missing"
        : isPackageLoadFailed
          ? "MLflow package failed to load"
          : isTrackingNotConfigured
            ? "MLflow tracking not configured"
          : "MLflow status unavailable"

  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-[11px]"
      role="status"
      title={mlflowDetail || label}
      style={{
        background,
        border: `1px solid ${border}`,
      }}
    >
      {isLoading ? (
        <><Loader2 size={11} className="animate-spin" style={{ color: iconColor }} /><span style={{ color: "var(--text-muted)" }}>{label}</span></>
      ) : isConnected ? (
        <><Check size={11} style={{ color: iconColor }} /><span style={{ color: labelColor }}>{label}</span></>
      ) : (
        <><AlertTriangle size={11} style={{ color: iconColor }} /><span style={{ color: labelColor }}>{label}</span></>
      )}
    </div>
  )
}

// ─── FileBrowser ──────────────────────────────────────────────────

export function FileBrowser({
  currentPath,
  onSelect,
  extensions,
  showSelectionSummary = true,
}: {
  currentPath?: string
  onSelect: (path: string) => void
  extensions?: string
  showSelectionSummary?: boolean
}) {
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
      {showSelectionSummary && selectedPath && (
        <div className="mb-2 px-2.5 py-2 rounded-lg flex items-center gap-2" style={{ background: 'var(--banner-success-bg)', border: '1px solid var(--banner-success-border)' }}>
          <Check size={14} style={{ color: 'var(--banner-success-text)' }} className="shrink-0" />
          <span className="text-xs font-mono truncate" style={{ color: 'var(--banner-success-data)' }}>{selectedPath}</span>
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
                  {typeof item.size === "number" && (
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
                    {schema.columns.map((col) => {
                      const cellValue = row[col.name]
                      const displayValue = formatPreviewCell(cellValue)
                      const exactText = previewCellExactText(cellValue, displayValue)
                      return (
                        <td
                          key={col.name}
                          className="px-2 py-1 font-mono whitespace-nowrap"
                          style={{ color: 'var(--text-secondary)' }}
                          title={exactText}
                          tabIndex={exactText ? 0 : undefined}
                          aria-label={exactText ? `${displayValue}; exact value ${exactText}` : undefined}
                        >
                          {displayValue}
                        </td>
                      )
                    })}
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

// ─── InputSourcesBar ──────────────────────────────────────────────

export function InputSourcesBar({
  inputSources,
  onDeleteInput,
  deleteTitle = (name) => `Remove connection from ${name}`,
}: {
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  /**
   * Wording for the remove control. Ordinary nodes drop one incoming edge, but
   * the submodel Input boundary retires a shared public port, so a caller whose
   * removal reaches further than this chip must say so.
   */
  deleteTitle?: (name: string) => string
}) {
  if (inputSources.length === 0) return null
  return (
    <div className="rounded-lg px-3 py-1.5 shrink-0" style={{ background: 'var(--bg-input)', border: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          {inputSources.length > 1 ? "Inputs" : "Input"}
        </span>
        {inputSources.map((src) => {
          const unresolvedTitle = unresolvedFrameTitle(src.sourceLabel)
          return (
            <span
              key={src.edgeId}
              data-testid={`input-source-${src.edgeId}`}
              data-unresolved={src.frameUnresolved ? "true" : undefined}
              aria-label={src.frameUnresolved ? "Unresolved frame" : undefined}
              title={src.frameUnresolved ? unresolvedTitle : `from ${src.name}`}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded"
              style={{
                background: src.frameUnresolved ? 'var(--warning-soft-subtle)' : 'var(--accent-soft)',
                border: src.frameUnresolved ? '1px solid var(--warning)' : undefined,
              }}
            >
              {src.frameUnresolved && (
                <AlertTriangle
                  size={11}
                  aria-hidden="true"
                  style={{ color: 'var(--warning)' }}
                />
              )}
              <code className="min-w-0 overflow-hidden text-ellipsis whitespace-pre text-[11px] font-semibold" style={{ color: src.frameUnresolved ? 'var(--warning)' : 'var(--accent)', whiteSpace: "pre" }}>
                {src.name}
              </code>
              {onDeleteInput && (
                <button
                  onClick={() => onDeleteInput(src.edgeId)}
                  className="icon-danger-btn p-0 rounded"
                  title={deleteTitle(src.name)}
                >
                  <X size={10} />
                </button>
              )}
            </span>
          )
        })}
      </div>
    </div>
  )
}
