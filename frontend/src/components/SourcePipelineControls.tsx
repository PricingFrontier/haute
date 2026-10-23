import { Suspense, lazy, useState, useRef, useCallback } from "react"
import { ChevronDown, Plus, Trash2 } from "lucide-react"
import type { WsStatus } from "../hooks/useWebSocketSync"
import useSettingsStore from "../stores/useSettingsStore"
import useGraphStore from "../stores/useGraphStore"
import { confirmed } from "../stores/useQuestionStore"
import { constantsWithSourceValue, withoutSource } from "../utils/globalConstants"
import useClickOutside from "../hooks/useClickOutside"

const PipelineSettingsModal = lazy(() => import("./PipelineSettingsModal"))

// Only these statuses mean the server is known to be unreachable; idle and
// connecting are not evidence, so a page load never flashes "Offline".
const OFFLINE_TITLES: Partial<Record<WsStatus, string>> = {
  reconnecting: "Server offline - reconnecting…",
  disconnected: "Server offline - reload the page once haute serve is running",
}

const PIPELINE_SETTINGS_TITLE = "Pipeline settings - preview rows, chunk rows and cached data"

/**
 * Remove *source*, first removing its values from split global constants. When
 * some constant holds a value for it, ask before discarding them.
 */
async function removeSourceWithConstants(source: string, removeSource: (source: string) => void): Promise<void> {
  const holders = constantsWithSourceValue(useGraphStore.getState().globalConstants, source)
  if (holders.length > 0) {
    const text = `Its values for ${holders.join(", ")} will be deleted.`
    if (!(await confirmed({ title: `Remove source "${source}"?`, text, confirm: "Remove", danger: true }))) return
  }
  // The constants as they are once answered.
  const graph = useGraphStore.getState()
  if (graph.globalConstants.some((draft) => draft.split && source in draft.bySource)) {
    graph.setGlobalConstantsRaw(withoutSource(graph.globalConstants, source))
  }
  removeSource(source)
}

interface SourcePipelineControlsProps {
  wsStatus: WsStatus
  editingDisabled?: boolean
  sourceSelectionTrusted?: boolean
}

/**
 * The Source selector over the Pipeline button, the column both the pipeline
 * toolbar and the workbench's put straight after the brand, so the controls
 * after it stand in the same places in either view.  Its fragment keeps the
 * column a direct child of the toolbar.
 */
export default function SourcePipelineControls({
  wsStatus,
  editingDisabled = false,
  sourceSelectionTrusted = true,
}: SourcePipelineControlsProps) {
  const sources = useSettingsStore((s) => s.sources)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const setActiveSource = useSettingsStore((s) => s.setActiveSource)
  const addSource = useSettingsStore((s) => s.addSource)
  const removeSource = useSettingsStore((s) => s.removeSource)
  // Local, not in the UI store: this column is the only thing that opens the
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
  const offlineTitle = OFFLINE_TITLES[wsStatus]

  return (
    <>
      {/* Source and Pipeline column — the Source selector sits on the top row, in
          line with Timing and Undo, and the Pipeline control underneath it.
          The two rows are one grid column, which takes the width of the wider
          control, so both buttons come out identical whatever the active
          source is named. */}
      <div className="grid grid-cols-[auto] items-center gap-y-1 w-fit" data-testid="toolbar-source-pipeline">
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
                    onClick={() => { void removeSourceWithConstants(activeSource, removeSource); setSourceOpen(false) }}
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
        {/* The button reads "Pipeline" whatever the calculation mode, and
            "Offline" while the server is away, as nothing calculates then.
            "Offline" is no wider than "Pipeline", so going offline never
            widens the column. */}
        {/* Spelled as the Source selector over it is, whose chevron the kit's
            centred action would not take, so the two texts line up. */}
        <button
          data-testid="toolbar-pipeline-settings"
          onClick={() => setPipelineSettingsOpen(true)}
          aria-haspopup="dialog"
          aria-expanded={pipelineSettingsOpen}
          className="toolbar-btn w-full px-2.5 py-1 text-[12px] font-medium rounded-md flex items-center justify-center"
          style={offlineTitle ? { color: 'var(--danger)' } : undefined}
          title={offlineTitle ?? PIPELINE_SETTINGS_TITLE}
        >
          {offlineTitle ? "Offline" : "Pipeline"}
        </button>
      </div>
      {pipelineSettingsOpen && (
        <Suspense fallback={null}>
          <PipelineSettingsModal onClose={closePipelineSettings} />
        </Suspense>
      )}
    </>
  )
}
