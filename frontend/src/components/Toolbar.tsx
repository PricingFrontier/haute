import { useState, useMemo, useRef, useCallback } from "react"
import { Undo2, Redo2, ZoomIn, ZoomOut, Timer, HardDrive, ChevronDown, Plus, Trash2, FileCode2, Package, GitFork, Loader2 } from "lucide-react"
import type { WsStatus } from "../hooks/useWebSocketSync"
import type { NodeTiming, NodeMemory, RunMode } from "../api/types"
import BreakdownDropdown, { type BreakdownItem } from "./BreakdownDropdown"
import useSettingsStore, { MAX_STREAMING_CHUNK_SIZE, MIN_STREAMING_CHUNK_SIZE } from "../stores/useSettingsStore"
import useClickOutside from "../hooks/useClickOutside"

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
  onOpenGit: () => void
  onCentre: () => void
  onAutoLayout: () => void
  isAutoLayouting: boolean
  onSave: () => void
  onRun: (mode: RunMode) => void
  runBusy: boolean
  wsStatus: WsStatus
  timings?: NodeTiming[]
  memory?: NodeMemory[]
}

export default function Toolbar({
  nodeCount, dirty,
  canUndo, canRedo, onUndo, onRedo,
  onZoomIn, onZoomOut,
  onOpenUtility, onOpenImports, onOpenGit,
  onCentre, onAutoLayout,
  isAutoLayouting,
  onSave,
  onRun, runBusy,
  wsStatus, timings, memory,
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
  const [addingSource, setAddingSource] = useState(false)
  const [newSourceName, setNewSourceName] = useState("")
  const [sourceOpen, setSourceOpen] = useState(false)
  const sourceRef = useRef<HTMLDivElement>(null)
  const closeSource = useCallback(() => setSourceOpen(false), [])
  useClickOutside(sourceRef, closeSource, sourceOpen)
  const [runMenuOpen, setRunMenuOpen] = useState(false)
  const runMenuRef = useRef<HTMLDivElement>(null)
  const closeRunMenu = useCallback(() => setRunMenuOpen(false), [])
  useClickOutside(runMenuRef, closeRunMenu, runMenuOpen)
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
        <span className="text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>v0.1.0</span>
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
            className="flex items-center gap-0.5"
            onSubmit={(e) => {
              e.preventDefault()
              if (newSourceName.trim()) {
                const slug = addSource(newSourceName)
                if (slug) setActiveSource(slug)
              }
              setAddingSource(false)
              setNewSourceName("")
            }}
          >
            <input
              autoFocus
              value={newSourceName}
              onChange={(e) => setNewSourceName(e.target.value)}
              onBlur={() => requestAnimationFrame(() => { setAddingSource(false); setNewSourceName("") })}
              placeholder="name"
              className="w-20 px-1.5 py-1 text-[11px] font-mono rounded focus:outline-none"
              style={{ background: 'var(--chrome-hover)', border: '1px solid var(--accent)', color: 'var(--text-primary)' }}
            />
          </form>
        ) : (
          <button
            data-testid="source-selector"
            onClick={() => setSourceOpen((v) => !v)}
            className="flex items-center gap-1.5 px-2 py-1 text-[12px] font-mono rounded-md transition-colors"
            style={{
              background: sourceOpen ? 'var(--accent-soft)' : 'var(--chrome-hover)',
              border: `1px solid ${sourceOpen ? 'var(--accent)' : 'var(--chrome-border)'}`,
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
      <div className="flex items-center gap-1 ml-3" title="Row limit for preview (0 = no limit)">
        <label htmlFor="toolbar-rows-input" className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Rows</label>
        <input
          id="toolbar-rows-input"
          type="number"
          min={0}
          step={100}
          value={rowLimit}
          onChange={(e) => setRowLimit(Math.max(0, parseInt(e.target.value) || 0))}
          className="w-16 px-1.5 py-0.5 text-[12px] font-mono rounded text-center focus:outline-none"
          style={{ background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)', color: 'var(--text-primary)' }}
        />
      </div>
      {/* Streaming chunk size — next to row limit */}
      <div className="flex items-center gap-1 ml-3" title="Streaming chunk size (rows per streaming chunk). Default 500000. Lower this if you OOM on wide schemas.">
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
          className="w-28 px-1.5 py-0.5 text-[12px] font-mono rounded text-center focus:outline-none"
          style={{ background: 'var(--chrome-hover)', border: '1px solid var(--chrome-border)', color: 'var(--text-primary)' }}
        />
      </div>
      {/* Undo / Redo */}
      <button
        data-testid="toolbar-undo"
        onClick={onUndo}
        disabled={!canUndo}
        aria-label="Undo"
        className="p-1.5 rounded-md transition-colors disabled:opacity-20 ml-3 hover-chrome"
        title="Undo (Ctrl+Z)"
      >
        <Undo2 size={14} aria-hidden="true" />
      </button>
      <button
        data-testid="toolbar-redo"
        onClick={onRedo}
        disabled={!canRedo}
        aria-label="Redo"
        className="p-1.5 rounded-md transition-colors disabled:opacity-20 hover-chrome"
        title="Redo (Ctrl+Shift+Z)"
      >
        <Redo2 size={14} aria-hidden="true" />
      </button>
      {/* Timing + memory breakdowns */}
      <div className="ml-3">
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
      <div className="ml-auto flex items-center gap-1.5">
        <button
          data-testid="toolbar-utility"
          onClick={onOpenUtility}
          className="px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1 hover-chrome"
          title="Utility scripts — reusable functions"
        >
          <FileCode2 size={13} />
          Utility
        </button>
        <button
          data-testid="toolbar-imports"
          onClick={onOpenImports}
          className="px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center gap-1 hover-chrome"
          title="Pipeline imports — utility and library imports"
        >
          <Package size={13} />
          Imports
        </button>
        {/* Zoom */}
        <button
          data-testid="toolbar-zoom-out"
          onClick={onZoomOut}
          aria-label="Zoom out"
          className="p-1.5 rounded-md hover-chrome"
          title="Zoom out"
        >
          <ZoomOut size={14} aria-hidden="true" />
        </button>
        <button
          data-testid="toolbar-zoom-in"
          onClick={onZoomIn}
          aria-label="Zoom in"
          className="p-1.5 rounded-md hover-chrome"
          title="Zoom in"
        >
          <ZoomIn size={14} aria-hidden="true" />
        </button>
        <div className="w-px h-4 mx-0.5" style={{ background: 'var(--chrome-border)' }} />
        <button
          data-testid="toolbar-centre"
          onClick={onCentre}
          disabled={nodeCount === 0}
          className="px-2.5 py-1 text-[12px] font-medium rounded-md disabled:opacity-30 hover-chrome"
          title="Fit all nodes in view"
        >
          Centre
        </button>
        <button
          data-testid="toolbar-layout"
          onClick={onAutoLayout}
          disabled={nodeCount === 0 || isAutoLayouting}
          aria-busy={isAutoLayouting}
          className="w-[104px] px-2.5 py-1 text-[12px] font-medium rounded-md disabled:opacity-30 hover-chrome inline-flex items-center justify-center gap-1.5"
          title={isAutoLayouting ? "Auto-arranging nodes" : "Auto-arrange nodes"}
        >
          {isAutoLayouting && <Loader2 size={13} aria-hidden="true" className="animate-spin" />}
          {isAutoLayouting ? "Laying out" : "Layout"}
        </button>
        <div ref={runMenuRef} className="relative flex items-stretch">
          <button
            data-testid="toolbar-run-button"
            onClick={() => onRun("default")}
            disabled={runBusy || nodeCount === 0}
            aria-busy={runBusy}
            className="px-2.5 py-1 text-[12px] font-semibold text-white rounded-l-md disabled:opacity-40 inline-flex items-center gap-1 hover:opacity-90"
            style={{ background: "var(--accent)" }}
            title="Run the selection (+ upstream) and write any selected data sinks"
          >
            {runBusy && <Loader2 size={12} aria-hidden="true" className="animate-spin" />}
            Run
          </button>
          <button
            data-testid="toolbar-run-menu"
            onClick={() => setRunMenuOpen((o) => !o)}
            disabled={runBusy || nodeCount === 0}
            aria-label="Run options"
            className="px-0.5 rounded-r-md text-white disabled:opacity-40 hover:opacity-90"
            style={{ background: "var(--accent)", borderLeft: "1px solid rgba(255,255,255,0.25)" }}
          >
            <ChevronDown size={13} />
          </button>
          {runMenuOpen && (
            <div
              className="absolute right-0 top-full mt-1 z-50 min-w-[190px] rounded-md py-1"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)", boxShadow: "0 4px 16px rgba(0,0,0,0.3)" }}
            >
              {([
                ["selected-no-export", "Run selection (no write)"],
                ["all-no-export", "Run all (no write)"],
                ["all-export", "Run all & write sinks"],
              ] as [RunMode, string][]).map(([mode, label]) => (
                <button
                  key={mode}
                  data-testid={`toolbar-run-${mode}`}
                  onClick={() => { setRunMenuOpen(false); onRun(mode) }}
                  className="w-full text-left px-3 py-1.5 text-[12px] hover-bg"
                  style={{ color: "var(--text-primary)" }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
        <button
          data-testid="toolbar-save"
          data-dirty={dirty ? "true" : "false"}
          onClick={onSave}
          className={`px-3 py-1 text-[12px] font-semibold text-white rounded-md transition-[background-color,opacity] hover:bg-[var(--accent-hover)] hover:opacity-100${dirty ? "" : " opacity-40"}`}
          style={{ background: 'var(--accent)' }}
          title="Ctrl+S"
        >
          Save
        </button>
        <button
          data-testid="toolbar-git"
          onClick={onOpenGit}
          className="px-3 py-1 text-[12px] font-semibold text-white rounded-md transition-colors flex items-center gap-1 hover:bg-[var(--success-hover)]"
          style={{ background: 'var(--success)' }}
          title="Git — branch management and version control"
        >
          <GitFork size={13} />
          Git
        </button>
      </div>
    </header>
  )
}
