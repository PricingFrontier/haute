import { useState, useMemo, useRef, useCallback } from "react"
import { Undo2, Redo2, ZoomIn, ZoomOut, Timer, HardDrive, ChevronDown, Plus, Trash2, FileCode2, Package, Bot, Loader2, Group, Link2 } from "lucide-react"
import type { WsStatus } from "../hooks/useWebSocketSync"
import type { NodeTiming, NodeMemory } from "../api/types"
import BreakdownDropdown, { type BreakdownItem } from "./BreakdownDropdown"
import BranchIndicator from "./BranchIndicator"
import useSettingsStore, { MAX_STREAMING_CHUNK_SIZE, MIN_STREAMING_CHUNK_SIZE } from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import useClickOutside from "../hooks/useClickOutside"

declare const __APP_VERSION__: string

function formatTiming(ms: number): string {
  return ms < 1000 ? `${ms.toFixed(1)}ms` : `${(ms / 1000).toFixed(2)}s`
}

function formatMemory(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

const WS_STATUS_CONFIG: Record<WsStatus, { color: string; title: string }> = {
  connected: { color: "var(--success)", title: "Live sync connected" },
  reconnecting: { color: "var(--warning-strong)", title: "Reconnecting to server\u2026" },
  disconnected: { color: "var(--danger)", title: "Server unreachable \u2014 restart haute serve" },
}

interface ToolbarProps {
  nodeCount: number
  dirty: boolean
  canUndo: boolean
  canRedo: boolean
  onUndo: () => void
  onRedo: () => void
  onZoomIn: () => void
  onZoomOut: () => void
  onOpenUtility: () => void
  onOpenImports: () => void
  /** Group the current selection into a submodel. Enabled only when the
   *  selection can actually be grouped — 2+ nodes, not already inside a
   *  submodel, not a read-only instance. The caller owns that policy. */
  canCreateSubmodel: boolean
  onCreateSubmodel: () => void
  /** Create a linked instance of the single selected non-singleton node (the
   *  generic `instanceOf` path, not just submodels). */
  canCreateInstance: boolean
  onCreateInstance: () => void
  onCentre: () => void
  onAutoLayout: () => void
  isAutoLayouting: boolean
  onSave: () => void
  onSaveCommit: () => void
  wsStatus: WsStatus
  timings?: NodeTiming[]
  memory?: NodeMemory[]
  editingDisabled?: boolean
}

export default function Toolbar({
  nodeCount, dirty,
  canUndo, canRedo, onUndo, onRedo,
  onZoomIn, onZoomOut,
  onOpenUtility, onOpenImports,
  canCreateSubmodel, onCreateSubmodel,
  canCreateInstance, onCreateInstance,
  onCentre, onAutoLayout,
  isAutoLayouting,
  onSave,
  onSaveCommit,
  wsStatus, timings, memory,
  editingDisabled = false,
}: ToolbarProps) {
  const rowLimit = useSettingsStore((s) => s.rowLimit)
  const setRowLimit = useSettingsStore((s) => s.setRowLimit)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const setStreamingChunkSize = useSettingsStore((s) => s.setStreamingChunkSize)
  const sources = useSettingsStore((s) => s.sources)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const setActiveSource = useSettingsStore((s) => s.setActiveSource)
  const addSource = useSettingsStore((s) => s.addSource)
  const removeSource = useSettingsStore((s) => s.removeSource)
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const setAssistantOpen = useUIStore((s) => s.setAssistantOpen)
  const rowLimitWidthCh = Math.max(4, String(rowLimit).length)
  const [addingSource, setAddingSource] = useState(false)
  const [newSourceName, setNewSourceName] = useState("")
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const sourceRef = useRef<HTMLDivElement>(null)
  const closeSource = useCallback(() => setSourceOpen(false), [])
  useClickOutside(sourceRef, closeSource, sourceOpen)
  const wsConfig = WS_STATUS_CONFIG[wsStatus]

  const timingItems: BreakdownItem[] = useMemo(
    () => (timings ?? []).map((t) => ({ node_id: t.node_id, label: t.label, value: t.timing_ms })),
    [timings],
  )

  const memoryItems: BreakdownItem[] = useMemo(
    () => (memory ?? []).map((m) => ({ node_id: m.node_id, label: m.label, value: m.memory_bytes })),
    [memory],
  )

  return (
    <header role="toolbar" aria-label="Pipeline toolbar" className="h-11 flex items-center px-4 shrink-0" style={{ background: 'var(--chrome)', borderBottom: '1px solid var(--chrome-border)' }}>
      <div className="flex items-center gap-2.5">
        <h1 className="text-sm font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>Haute</h1>
        <span className="text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>v{__APP_VERSION__}</span>
        {dirty && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse-dot" title="Unsaved changes" />}
        <span
          className={`w-2 h-2 rounded-full shrink-0${wsStatus === "reconnecting" ? " animate-pulse-dot" : ""}`}
          style={{ background: wsConfig.color }}
          title={wsConfig.title}
        />
      </div>
      {/* Source selector — custom dropdown */}
      <div ref={sourceRef} className="relative flex items-center gap-1 ml-12">
        <label className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Source</label>
        {addingSource ? (
          <form
            className="relative flex items-center gap-0.5"
            onSubmit={(e) => {
              e.preventDefault()
              const result = addSource(newSourceName)
              if (result.ok) {
                setActiveSource(result.key)
                setAddingSource(false)
                setNewSourceName("")
                setSourceError(null)
              } else if (result.reason === "empty") {
                // Keep the form open so the user can supply a name.
                setSourceError("Enter a name for the source.")
              } else {
                // A distinct label that sanitises onto an existing key — name
                // the collision so the reject is intelligible, not silent.
                setSourceError(`Matches existing source "${result.key}".`)
              }
            }}
          >
            <input
              autoFocus
              value={newSourceName}
              onChange={(e) => { setNewSourceName(e.target.value); if (sourceError) setSourceError(null) }}
              onBlur={() => requestAnimationFrame(() => { setAddingSource(false); setNewSourceName(""); setSourceError(null) })}
              placeholder="name"
              aria-invalid={sourceError ? true : undefined}
              aria-describedby={sourceError ? "source-add-error" : undefined}
              className="w-20 px-1.5 py-1 text-[11px] font-mono rounded focus:outline-none"
              style={{ background: 'var(--chrome-hover)', border: `1px solid ${sourceError ? 'var(--danger)' : 'var(--accent)'}`, color: 'var(--text-primary)' }}
            />
            {sourceError && (
              <span
                id="source-add-error"
                role="alert"
                data-testid="source-add-error"
                className="absolute top-full left-0 mt-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[10px] font-medium z-50"
                style={{ background: 'var(--danger-soft)', color: 'var(--danger-text)', border: '1px solid var(--danger-border)' }}
              >
                {sourceError}
              </span>
            )}
          </form>
        ) : (
          <button
            data-testid="source-selector"
            onClick={() => setSourceOpen((v) => !v)}
            className="flex items-center gap-1.5 px-2 py-1 text-[12px] font-mono rounded-md transition-colors"
            /* Shares the toolbar button surface so the two boxed controls in
               the bar don't sit at different lightnesses. */
            style={{
              background: sourceOpen ? 'var(--accent-soft)' : 'var(--btn-surface)',
              border: `1px solid ${sourceOpen ? 'var(--accent)' : 'var(--btn-border)'}`,
              color: 'var(--text-primary)',
            }}
            title="Data source"
          >
            {activeSource === "live" && (
              <span className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" />
            )}
            <span>{activeSource}</span>
            <ChevronDown size={11} style={{ color: 'var(--text-muted)', transition: 'transform 150ms', transform: sourceOpen ? 'rotate(180deg)' : undefined }} />
          </button>
        )}
        {sourceOpen && (
          <div
            className="absolute top-full left-0 mt-1 rounded-lg shadow-2xl z-50 min-w-[160px] overflow-hidden"
            style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}
          >
            <div className="py-1">
              {sources.map((s) => {
                const isActive = s === activeSource
                return (
                  <button
                    key={s}
                    onClick={() => { setActiveSource(s); setSourceOpen(false) }}
                    className={`w-full flex items-center gap-2 px-3 py-1.5 text-[12px] font-mono text-left transition-colors ${isActive ? "" : "hover:bg-[var(--chrome-hover)]"}`}
                    style={{
                      color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                      background: isActive ? 'var(--accent-soft)' : 'transparent',
                    }}
                  >
                    {s === "live"
                      ? <span className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" />
                      : <span className="w-1.5 shrink-0" />}
                    {s}
                  </button>
                )
              })}
            </div>
            <div className="py-1" style={{ borderTop: '1px solid var(--border)' }}>
              <button
                onClick={() => { setAddingSource(true); setSourceOpen(false) }}
                className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors hover:bg-[var(--chrome-hover)] hover:text-[var(--text-secondary)]"
                style={{ color: 'var(--text-muted)' }}
              >
                <Plus size={12} />
                Add source
              </button>
              {activeSource !== "live" && (
                <button
                  onClick={() => { removeSource(activeSource); setSourceOpen(false) }}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors hover:bg-[var(--danger-soft)]"
                  style={{ color: 'var(--danger)' }}
                >
                  <Trash2 size={12} />
                  Remove "{activeSource}"
                </button>
              )}
            </div>
          </div>
        )}
      </div>
      {/* Row limit — next to source */}
      <div className="flex items-center gap-1 ml-2.5" title="Row limit for preview (0 = no limit)">
        <label htmlFor="toolbar-rows-input" className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Rows</label>
        <input
          id="toolbar-rows-input"
          type="number"
          min={0}
          step={100}
          value={rowLimit}
          onChange={(e) => setRowLimit(Math.max(0, parseInt(e.target.value) || 0))}
          className="toolbar-number-input px-1.5 py-0.5 text-[12px] font-mono rounded text-center focus:outline-none"
          /* Four digits at minimum, growing to retain the complete configured
             value because row limits have no upper bound. */
          style={{ width: `calc(${rowLimitWidthCh}ch + 16px)`, background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)', color: 'var(--text-primary)' }}
        />
      </div>
      {/* Streaming chunk size — next to row limit */}
      <div className="flex items-center gap-1 ml-2.5" title="Streaming chunk size (rows per streaming chunk). Default 500000. Lower this if you OOM on wide schemas.">
        <label htmlFor="toolbar-chunk-input" className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Chunk</label>
        <input
          id="toolbar-chunk-input"
          type="number"
          min={MIN_STREAMING_CHUNK_SIZE}
          max={MAX_STREAMING_CHUNK_SIZE}
          step={10000}
          value={streamingChunkSize}
          onChange={(e) => {
            const raw = e.target.value.trim()
            if (raw === "") return
            const parsed = Number(raw)
            if (!Number.isFinite(parsed)) return
            setStreamingChunkSize(parsed)
          }}
          className="toolbar-number-input px-1.5 py-0.5 text-[12px] font-mono rounded text-center focus:outline-none"
          /* Eight digits cover the complete 10,000,000 backend maximum. */
          style={{ width: 'calc(8ch + 16px)', background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)', color: 'var(--text-primary)' }}
        />
      </div>
      {/* Undo / Redo.  Grouped so they take the shared 10px gap — as bare
          icons they sat flush against each other, which only became visible
          once they grew borders. */}
      <div className="flex items-center gap-2.5 ml-2.5">
        <button
          data-testid="toolbar-undo"
          onClick={onUndo}
          disabled={editingDisabled || !canUndo}
          aria-label="Undo"
          className="toolbar-btn p-1.5 rounded-md"
          title="Undo (Ctrl+Z)"
        >
          <Undo2 size={14} aria-hidden="true" />
        </button>
        <button
          data-testid="toolbar-redo"
          onClick={onRedo}
          disabled={editingDisabled || !canRedo}
          aria-label="Redo"
          className="toolbar-btn p-1.5 rounded-md"
          title="Redo (Ctrl+Shift+Z)"
        >
          <Redo2 size={14} aria-hidden="true" />
        </button>
      </div>
      {/* Timing + memory breakdowns */}
      <div className="ml-2.5">
        <BreakdownDropdown
          icon={Timer}
          title="Pipeline Timing"
          items={timingItems}
          formatValue={formatTiming}
        />
      </div>
      <BreakdownDropdown
        icon={HardDrive}
        title="Pipeline Memory"
        items={memoryItems}
        formatValue={formatMemory}
        valueWidth="w-14"
      />
      {/* 10px is the toolbar's one spacing value: between adjacent buttons and
          between sections alike.  Only a label and the field it names sit
          closer (4px), so they still read as one control. */}
      <div className="ml-auto flex items-center gap-2.5">
        {/* Selection actions.  These use ``aria-disabled`` rather than the
            ``disabled`` attribute: a disabled button is removed from the tab
            order AND swallows pointer events, so its title never appears —
            precisely when the user most needs to know why it's unavailable.
            The click is NOT guarded here: the handler owns the policy and
            answers an unavailable click with the same toast Ctrl+G gives, so
            the reason reaches the keyboard and touch users a `title` cannot.
            ``can*`` therefore drives presentation only. */}
        <button
          data-testid="toolbar-submodel"
          onClick={onCreateSubmodel}
          aria-disabled={!canCreateSubmodel}
          className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1"
          title="Group the selected nodes into a submodel — select 2 or more (Ctrl+G)"
        >
          <Group size={13} aria-hidden="true" />
          Submodel
        </button>
        <button
          data-testid="toolbar-instance"
          onClick={onCreateInstance}
          aria-disabled={!canCreateInstance}
          className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1"
          title="Create a linked instance of the selected node — select exactly one"
        >
          <Link2 size={13} aria-hidden="true" />
          Instance
        </button>
        <button
          data-testid="toolbar-utility"
          onClick={onOpenUtility}
          disabled={editingDisabled}
          className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1"
          title="Utility scripts — reusable functions"
        >
          <FileCode2 size={13} />
          Utility
        </button>
        <button
          data-testid="toolbar-imports"
          onClick={onOpenImports}
          disabled={editingDisabled}
          className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1"
          title="Pipeline imports — utility and library imports"
        >
          <Package size={13} />
          Imports
        </button>
        <button
          data-testid="toolbar-assistant"
          onClick={() => setAssistantOpen(!assistantOpen)}
          disabled={editingDisabled}
          aria-label="Assistant"
          aria-pressed={assistantOpen}
          className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1"
          title="Pricing assistant"
        >
          <Bot size={13} />
          Assistant
        </button>
        {/* View controls — zoom, centre, layout.  Grouped for reading order
            only; the divider that used to sit between zoom and Centre is gone,
            since with the container gap either side of it that one seam was
            21px wide against 10px everywhere else. */}
        <div className="flex items-center gap-2.5">
          <button
            data-testid="toolbar-zoom-out"
            onClick={onZoomOut}
            aria-label="Zoom out"
            className="toolbar-btn p-1.5 rounded-md"
            title="Zoom out"
          >
            <ZoomOut size={14} aria-hidden="true" />
          </button>
          <button
            data-testid="toolbar-zoom-in"
            onClick={onZoomIn}
            aria-label="Zoom in"
            className="toolbar-btn p-1.5 rounded-md"
            title="Zoom in"
          >
            <ZoomIn size={14} aria-hidden="true" />
          </button>
          <button
            data-testid="toolbar-centre"
            onClick={onCentre}
            disabled={nodeCount === 0}
            className="toolbar-btn px-2 py-1 text-[12px] font-medium rounded-md"
            title="Fit all nodes in view"
          >
            Centre
          </button>
          <button
            data-testid="toolbar-layout"
            onClick={onAutoLayout}
            disabled={editingDisabled || nodeCount === 0 || isAutoLayouting}
            aria-busy={isAutoLayouting}
            /* The visible label stays "Layout" in both states — swapping it to
               "Laying out" is what previously forced a fixed 104px width.  The
               running state is carried by the spinner, ``aria-busy``, the title
               and an ``aria-label`` that keeps the accessible name honest
               without costing any width. */
            aria-label={isAutoLayouting ? "Laying out" : "Layout"}
            className="toolbar-btn relative px-2 py-1 text-[12px] font-medium rounded-md inline-flex items-center justify-center whitespace-nowrap"
            title={isAutoLayouting ? "Auto-arranging nodes" : "Auto-arrange nodes"}
          >
            {/* The spinner is overlaid on the hidden label rather than laid out
                beside it, so the button is exactly as wide as "Layout" and the
                toolbar never reflows when auto-layout starts. */}
            <span className={isAutoLayouting ? "invisible" : undefined}>Layout</span>
            {isAutoLayouting && (
              <Loader2
                size={13}
                aria-hidden="true"
                className="animate-spin absolute inset-0 m-auto"
              />
            )}
          </button>
        </div>
        <BranchIndicator />
        {/* Save then Commit, in the order the work happens: a save is itself a
            commit to the save branch, and Commit rolls those saves into a
            milestone.  Two filled buttons, distinguished by hue rather than by
            one being demoted. */}
        <button
          data-testid="toolbar-save"
          onClick={onSave}
          className="px-3 py-1 text-[12px] font-semibold text-white rounded-md transition-colors hover:bg-[var(--accent-hover)]"
          style={{ background: 'var(--accent)' }}
          title="Save — Ctrl+S"
        >
          Save
        </button>
        <button
          data-testid="toolbar-save-commit"
          onClick={onSaveCommit}
          className="px-3 py-1 text-[12px] font-semibold text-white rounded-md transition-colors hover:bg-[var(--success-fill-hover)]"
          style={{ background: 'var(--success-fill)' }}
          title="Commit — record a milestone on your working branch"
        >
          Commit
        </button>
      </div>
    </header>
  )
}
