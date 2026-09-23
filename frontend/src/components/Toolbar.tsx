import { Suspense, lazy, useState, useMemo, useRef, useCallback, useEffect } from "react"
import { Undo2, Redo2, ZoomIn, ZoomOut, Scan, Network, Timer, HardDrive, ChevronDown, Plus, Trash2, FileCode2, Package, Bot, Loader2, Group, Link2, BookOpen, CircleHelp, Keyboard, Bug } from "lucide-react"
import type { WsStatus } from "../hooks/useWebSocketSync"
import type { NodeTiming, NodeMemory } from "../api/types"
import BreakdownDropdown, { type BreakdownItem } from "./BreakdownDropdown"
import BranchIndicator from "./BranchIndicator"
import useSettingsStore from "../stores/useSettingsStore"
import useUIStore from "../stores/useUIStore"
import useClickOutside from "../hooks/useClickOutside"
import MlflowSettingsModal from "./MlflowSettingsModal"

const PipelineSettingsModal = lazy(() => import("./PipelineSettingsModal"))

declare const __APP_VERSION__: string

function formatTiming(ms: number): string {
  const rounded = Math.round(ms)
  return rounded < 1000 ? `${rounded} ms` : `${(ms / 1000).toFixed(2)} s`
}

function formatMemory(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

const DOCUMENTATION_URL = "https://pricingfrontier.github.io/haute/"
const REPORT_BUG_URL = "https://github.com/PricingFrontier/haute/issues/new"

const HELP_ITEM_CLASS =
  "w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors hover:bg-[var(--chrome-hover)] focus-visible:bg-[var(--chrome-hover)] focus:outline-none"

const WS_STATUS_CONFIG: Record<WsStatus, { color: string; title: string }> = {
  connected: { color: "var(--success)", title: "Live sync connected" },
  reconnecting: { color: "var(--warning-strong)", title: "Reconnecting to server\u2026" },
  disconnected: { color: "var(--danger)", title: "Server unreachable - restart haute serve" },
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
  sourceSelectionTrusted?: boolean
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
  sourceSelectionTrusted = true,
}: ToolbarProps) {
  const sources = useSettingsStore((s) => s.sources)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const setActiveSource = useSettingsStore((s) => s.setActiveSource)
  const addSource = useSettingsStore((s) => s.addSource)
  const removeSource = useSettingsStore((s) => s.removeSource)
  const calculationMode = useUIStore((s) => s.calculationMode)
  const assistantOpen = useUIStore((s) => s.assistantOpen)
  const setAssistantOpen = useUIStore((s) => s.setAssistantOpen)
  // Local, not in the UI store: the toolbar is the only thing that opens the
  // pipeline settings pane, so no other surface needs to read or set this.
  const [pipelineSettingsOpen, setPipelineSettingsOpen] = useState(false)
  const closePipelineSettings = useCallback(() => setPipelineSettingsOpen(false), [])
  const [addingSource, setAddingSource] = useState(false)
  const [newSourceName, setNewSourceName] = useState("")
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [sourceOpen, setSourceOpen] = useState(false)
  const sourceRef = useRef<HTMLDivElement>(null)
  const closeSource = useCallback(() => setSourceOpen(false), [])
  useClickOutside(sourceRef, closeSource, sourceOpen)
  const [helpOpen, setHelpOpen] = useState(false)
  const helpRef = useRef<HTMLDivElement>(null)
  const closeHelp = useCallback(() => setHelpOpen(false), [])
  useClickOutside(helpRef, closeHelp, helpOpen)
  const helpItems = () =>
    Array.from(helpRef.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])
  // Menu keyboard contract: focus lands on the first item when it opens, and
  // the arrow keys move through the items.
  useEffect(() => {
    if (helpOpen) helpItems()[0]?.focus()
  }, [helpOpen])
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen)
  const wsConfig = WS_STATUS_CONFIG[wsStatus]

  const mlflowSettingsOpen = useUIStore((s) => s.mlflowSettingsOpen)
  const setMlflowSettingsOpen = useUIStore((s) => s.setMlflowSettingsOpen)
  const closeMlflowSettings = useCallback(() => setMlflowSettingsOpen(false), [setMlflowSettingsOpen])

  const timingItems: BreakdownItem[] = useMemo(
    () => (timings ?? []).map((t) => ({ node_id: t.node_id, label: t.label, value: t.timing_ms })),
    [timings],
  )

  const memoryItems: BreakdownItem[] = useMemo(
    () => (memory ?? []).map((m) => ({ node_id: m.node_id, label: m.label, value: m.memory_bytes })),
    [memory],
  )

  return (
    <header role="toolbar" aria-label="Pipeline toolbar" className="min-h-11 flex flex-wrap items-center gap-y-2 px-4 py-1.5 shrink-0 [&>div]:shrink-0" style={{ background: 'var(--chrome)', borderBottom: '1px solid var(--chrome-border)' }}>
      {/* Haute brand column — lowercase "haute" heading taking ~2/3 vertical space, centered version underneath, status dots centered at the row-gap level to the right.
          Width is pinned to 165px (180px node palette + 1px border - 16px header px-4 padding) so the Source label starts at exactly x + 1 = 181px from the left edge of the page. */}
      <div className="h-[56px] w-[165px] flex items-center gap-2 select-none" data-testid="toolbar-brand">
        <div className="flex flex-col items-center justify-center">
          <h1 className="text-[24px] font-bold tracking-tight leading-none" style={{ color: 'var(--text-primary)' }}>
            haute
          </h1>
          <span className="text-[10px] font-mono tracking-tight leading-none mt-1" style={{ color: 'var(--text-muted)' }}>
            v{__APP_VERSION__}
          </span>
        </div>
        <div className="flex items-center gap-1.5" data-testid="toolbar-status-dots">
          <span
            className={`w-2 h-2 rounded-full shrink-0${wsStatus === "reconnecting" ? " animate-pulse-dot" : ""}`}
            style={{ background: wsConfig.color }}
            title={wsConfig.title}
          />
          <span
            className={`w-1.5 h-1.5 rounded-full shrink-0 ${dirty ? "bg-amber-400 animate-pulse-dot" : "invisible"}`}
            title={dirty ? "Unsaved changes" : undefined}
            aria-hidden={!dirty ? true : undefined}
          />
        </div>
      </div>
      {/* Source and Pipeline column — the Source selector sits on the top row, in
          line with Timing and Undo, and the Pipeline control underneath it.
          The two rows are one grid so the control column takes the width of the
          wider control and both buttons come out identical, whatever the active
          source is named. */}
      <div className="grid grid-cols-[auto_auto] items-center gap-x-1 gap-y-1 w-fit" data-testid="toolbar-source-pipeline">
        <label className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Source:</label>
        <div ref={sourceRef} className="relative w-full">
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
                className="w-full min-w-20 px-2.5 py-1 text-[12px] font-medium rounded-md focus:outline-none"
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
              disabled={editingDisabled || !sourceSelectionTrusted}
              className="toolbar-btn w-full flex items-center justify-between gap-1.5 px-2.5 py-1 text-[12px] font-medium rounded-md"
              /* Uses the shared toolbar button surface and type so it matches
                 every other button in the bar.  The chevron is pinned right
                 rather than trailing the name, so the control still reads as a
                 dropdown when the grid stretches it past its text. */
              style={sourceOpen ? { background: 'var(--accent-soft)', borderColor: 'var(--accent)' } : undefined}
              title="Data source"
            >
              <span className="flex items-center gap-1.5 min-w-0">
                {sourceSelectionTrusted && activeSource === "live" && (
                  <span className="w-1.5 h-1.5 rounded-full bg-green-400 shrink-0" />
                )}
                <span className="truncate">{sourceSelectionTrusted ? activeSource : "Unavailable"}</span>
              </span>
              <ChevronDown size={11} className="shrink-0" style={{ color: 'var(--text-muted)', transition: 'transform 150ms', transform: sourceOpen ? 'rotate(180deg)' : undefined }} />
            </button>
          )}
          {sourceOpen && !editingDisabled && sourceSelectionTrusted && (
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
                        color: 'var(--text-primary)',
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
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-left transition-colors hover:bg-[var(--chrome-hover)]"
                  style={{ color: 'var(--text-primary)' }}
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
        <label className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>Pipeline:</label>
        <button
          data-testid="toolbar-pipeline-settings"
          onClick={() => setPipelineSettingsOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={pipelineSettingsOpen}
          className="toolbar-btn w-full px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center"
          title="Pipeline settings - preview rows, chunk rows and cached data"
        >
          {calculationMode === "manual" ? "Manual" : "Calculating"}
        </button>
      </div>
      {/* Canvas actions (Undo/Redo through Utility/Imports) sit on the left,
          between the Source/Pipeline column and the Timing/Memory readouts. */}
      <div className="ml-2.5 flex flex-wrap items-center gap-2.5" data-testid="toolbar-canvas-actions">
        {/* Undo / Redo column — Undo at the top, Redo underneath, leading the canvas action group */}
        <div className="flex flex-col gap-1 w-fit" data-testid="toolbar-undo-redo">
          <button
            data-testid="toolbar-undo"
            onClick={onUndo}
            disabled={editingDisabled || !canUndo}
            aria-label="Undo"
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Undo (Ctrl+Z)"
          >
            <Undo2 size={13} aria-hidden="true" />
            Undo
          </button>
          <button
            data-testid="toolbar-redo"
            onClick={onRedo}
            disabled={editingDisabled || !canRedo}
            aria-label="Redo"
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Redo (Ctrl+Shift+Z)"
          >
            <Redo2 size={13} aria-hidden="true" />
            Redo
          </button>
        </div>
        {/* Zoom controls column — Zoom In on top of Zoom Out */}
        <div className="flex flex-col gap-1 w-fit">
          <button
            data-testid="toolbar-zoom-in"
            onClick={onZoomIn}
            aria-label="Zoom in"
            className="toolbar-btn px-2 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Zoom in"
          >
            <ZoomIn size={13} aria-hidden="true" />
            Zoom In
          </button>
          <button
            data-testid="toolbar-zoom-out"
            onClick={onZoomOut}
            aria-label="Zoom out"
            className="toolbar-btn px-2 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Zoom out"
          >
            <ZoomOut size={13} aria-hidden="true" />
            Zoom Out
          </button>
        </div>
        {/* Centre and Layout column — Centre on top of Layout */}
        <div className="flex flex-col gap-1 w-fit">
          <button
            data-testid="toolbar-centre"
            onClick={onCentre}
            disabled={nodeCount === 0}
            className="toolbar-btn px-2 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Fit all nodes in view"
          >
            <Scan size={13} aria-hidden="true" />
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
            className="toolbar-btn relative px-2 py-1 text-[12px] font-medium rounded-md inline-flex items-center justify-center whitespace-nowrap w-full"
            title={isAutoLayouting ? "Auto-arranging nodes" : "Auto-arrange nodes"}
          >
            {/* Keep the icon and label in flow while the spinner overlays them
                so the toolbar never reflows when auto-layout starts. */}
            <span className={`inline-flex items-center gap-1${isAutoLayouting ? " invisible" : ""}`}>
              <Network size={13} aria-hidden="true" />
              Layout
            </span>
            {isAutoLayouting && (
              <Loader2
                size={13}
                aria-hidden="true"
                className="animate-spin absolute inset-0 m-auto"
              />
            )}
          </button>
        </div>
        {/* Selection actions.  These use ``aria-disabled`` rather than the
            ``disabled`` attribute: a disabled button is removed from the tab
            order AND swallows pointer events, so its title never appears -
            precisely when the user most needs to know why it's unavailable.
            The click is NOT guarded here: the handler owns the policy and
            answers an unavailable click with the same toast Ctrl+G gives, so
            the reason reaches the keyboard and touch users a `title` cannot.
            ``can*`` therefore drives presentation only. */}
        {/* Submodel and Instance column — Submodel on top of Instance */}
        <div className="flex flex-col gap-1 w-fit">
          <button
            data-testid="toolbar-submodel"
            onClick={onCreateSubmodel}
            aria-disabled={!canCreateSubmodel}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Group the selected nodes into a submodel - select 2 or more (Ctrl+G)"
          >
            <Group size={13} aria-hidden="true" />
            Submodel
          </button>
          <button
            data-testid="toolbar-instance"
            onClick={onCreateInstance}
            aria-disabled={!canCreateInstance}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Create a linked instance of the selected node - select exactly one"
          >
            <Link2 size={13} aria-hidden="true" />
            Instance
          </button>
        </div>
        {/* Utility and Imports column — Utility on top of Imports */}
        <div className="flex flex-col gap-1 w-fit">
          <button
            data-testid="toolbar-utility"
            onClick={onOpenUtility}
            disabled={editingDisabled}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Utility scripts - reusable functions"
          >
            <FileCode2 size={13} />
            Utility
          </button>
          <button
            data-testid="toolbar-imports"
            onClick={onOpenImports}
            disabled={editingDisabled}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Pipeline imports - utility and library imports"
          >
            <Package size={13} />
            Imports
          </button>
        </div>
      </div>
      {/* Timing + memory breakdowns column — Timing at the top, Memory at the bottom */}
      <div className="ml-2.5 flex flex-col gap-1 w-fit" data-testid="toolbar-breakdowns">
        <BreakdownDropdown
          icon={Timer}
          title="Pipeline Timing"
          items={timingItems}
          formatValue={formatTiming}
        />
        <BreakdownDropdown
          icon={HardDrive}
          title="Pipeline Memory"
          items={memoryItems}
          formatValue={formatMemory}
          valueWidth="w-14"
        />
      </div>
      {/* 10px is the toolbar's one spacing value: between adjacent buttons and
          between sections alike.  Only a label and the field it names sit
          closer (4px), so they still read as one control. */}
      <div className="ml-auto flex max-w-full flex-wrap items-center justify-end gap-2.5">
        {/* Assistant and Help column — equal width, paired with branch name & save/commit */}
        <div className="flex flex-col gap-1 w-fit">
          <button
            data-testid="toolbar-assistant"
            onClick={() => setAssistantOpen(!assistantOpen)}
            disabled={editingDisabled}
            aria-label="Assistant"
            aria-pressed={assistantOpen}
            className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
            title="Pricing assistant"
          >
            <Bot size={13} />
            Assistant
          </button>
          <div
            ref={helpRef}
            className="relative w-full"
            onKeyDown={(e) => {
              if (!helpOpen) return
              if (e.key === "Escape") {
                e.stopPropagation()
                setHelpOpen(false)
                helpRef.current?.querySelector<HTMLElement>('[data-testid="toolbar-help"]')?.focus()
                return
              }
              if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return
              e.preventDefault()
              const items = helpItems()
              const index = items.indexOf(document.activeElement as HTMLElement)
              const step = e.key === "ArrowDown" ? 1 : -1
              items[(index + step + items.length) % items.length]?.focus()
            }}
          >
            <button
              data-testid="toolbar-help"
              onClick={() => setHelpOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={helpOpen}
              className="toolbar-btn px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center gap-1 w-full"
              title="Help"
            >
              <CircleHelp size={13} />
              Help
            </button>
            {helpOpen && (
              <div
                role="menu"
                aria-label="Help"
                data-testid="toolbar-help-menu"
                className="absolute top-full right-0 mt-1 rounded-lg shadow-2xl z-50 min-w-[160px] overflow-hidden py-1"
                style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)', color: 'var(--text-primary)' }}
              >
                <a
                  role="menuitem"
                  data-testid="toolbar-documentation"
                  href={DOCUMENTATION_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={closeHelp}
                  className={HELP_ITEM_CLASS}
                  title="Documentation - opens in a new tab"
                >
                  <BookOpen size={12} />
                  Documentation
                </a>
                <button
                  role="menuitem"
                  data-testid="toolbar-hotkeys"
                  onClick={() => { setShortcutsOpen(true); setHelpOpen(false) }}
                  className={HELP_ITEM_CLASS}
                  title="Keyboard shortcuts (?)"
                >
                  <Keyboard size={12} />
                  Hotkeys
                </button>
                <a
                  role="menuitem"
                  data-testid="toolbar-report-bug"
                  href={REPORT_BUG_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={closeHelp}
                  className={HELP_ITEM_CLASS}
                  title="Report a bug on GitHub - opens in a new tab"
                >
                  <Bug size={12} />
                  Report a bug
                </a>
              </div>
            )}
          </div>
        </div>
        <BranchIndicator>
          {/* Save then Commit, in the order the work happens: a save is itself a
              commit to the save branch, and Commit rolls those saves into a
              milestone.  Two filled buttons, distinguished by hue rather than by
              one being demoted. The widths expand with flex-1 while keeping a
              fixed distance apart, matching the branch name button above. */}
          <div className="flex items-center gap-1.5 w-full">
            <button
              data-testid="toolbar-save"
              onClick={onSave}
              disabled={editingDisabled}
              className="flex-1 px-2 py-1 text-[12px] font-semibold text-white rounded-md transition-colors hover:bg-[var(--accent-hover)] text-center"
              style={{ background: 'var(--accent)' }}
              title="Save - Ctrl+S"
            >
              Save
            </button>
            <button
              data-testid="toolbar-save-commit"
              onClick={onSaveCommit}
              disabled={editingDisabled}
              className="flex-1 px-2 py-1 text-[12px] font-semibold text-white rounded-md transition-colors hover:bg-[var(--success-fill-hover)] text-center"
              style={{ background: 'var(--success-fill)' }}
              title="Commit - record a milestone on your working branch"
            >
              Commit
            </button>
          </div>
        </BranchIndicator>
      </div>
      {mlflowSettingsOpen && <MlflowSettingsModal onClose={closeMlflowSettings} />}
      {pipelineSettingsOpen && (
        <Suspense fallback={null}>
          <PipelineSettingsModal onClose={closePipelineSettings} />
        </Suspense>
      )}
    </header>
  )
}
