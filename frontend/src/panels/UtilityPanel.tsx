import { useState, useEffect, useCallback, useRef } from "react"
import { Plus, Trash2, FileCode2, ChevronDown } from "lucide-react"
import { CodeEditor } from "./editors/CodeEditor"
import PanelShell from "./PanelShell"
import useClickOutside from "../hooks/useClickOutside"
import useToastStore from "../stores/useToastStore"
import {
  ApiError,
  listUtilityFiles,
  readUtilityFile,
  createUtilityFile,
  updateUtilityFile,
  deleteUtilityFile,
} from "../api/client"
import type { UtilityFile } from "../api/types"

/**
 * Extract syntax error info from an ApiError's flat string detail.
 *
 * Server responses are ``{"detail": "Syntax error on line N: <msg>"}``.
 * The line number is extracted via a ``/line (\d+)/`` regex for the editor's
 * gutter; the raw message is shown to the user.
 */
function parseSyntaxError(err: unknown): { error: string; error_line: number | null } | null {
  if (!(err instanceof ApiError) || err.status !== 400) return null
  const raw = err.detail
  if (!raw) return null

  const message = String(raw)
  const match = /\bline\s+(\d+)\b/i.exec(message)
  return {
    error: message,
    error_line: match ? Number.parseInt(match[1], 10) : null,
  }
}

interface UtilityPanelProps {
  onClose: () => void
  onImportAdded: (importLine: string) => void
}

export default function UtilityPanel({ onClose, onImportAdded }: UtilityPanelProps) {
  const addToast = useToastStore((s) => s.addToast)
  const [files, setFiles] = useState<UtilityFile[]>([])
  const [activeModule, setActiveModule] = useState<string | null>(null)
  const [content, setContent] = useState("")
  const [errorLine, setErrorLine] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  useClickOutside(dropdownRef, () => setDropdownOpen(false), dropdownOpen)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")

  // Auto-save: debounce API calls so we don't fire on every keystroke
  const saveTimer = useRef<ReturnType<typeof setTimeout>>(undefined)
  // The value awaiting the debounce window. Tracked so a file switch / unmount
  // can FLUSH it (persist immediately) instead of discarding the last edit.
  const pendingSaveRef = useRef<{ module: string; value: string } | null>(null)
  const inflightSaveRef = useRef<Promise<boolean> | null>(null)
  const activeModuleRef = useRef(activeModule)
  const mountedRef = useRef(true)
  useEffect(() => { activeModuleRef.current = activeModule }, [activeModule])
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  // Persist one edit and reconcile the error banner. Post-await state updates
  // are guarded so a stale (switched-away or unmounted) response can't clobber
  // the current file's error UI.
  const persistSave = useCallback(async (module: string, value: string): Promise<boolean> => {
    try {
      await updateUtilityFile(module, value)
      if (mountedRef.current && activeModuleRef.current === module) {
        setErrorLine(null)
        setErrorMsg(null)
      }
      return true
    } catch (err) {
      if (mountedRef.current && activeModuleRef.current === module) {
        const syntaxErr = parseSyntaxError(err)
        if (syntaxErr) {
          setErrorLine(syntaxErr.error_line)
          setErrorMsg(syntaxErr.error)
        } else {
          const detail = err instanceof Error ? err.message : "unknown error"
          addToast("error", `Failed to save utility file "${module}": ${detail}`)
          setErrorMsg("Failed to save")
        }
      }
      return false
    }
  }, [addToast])

  const queueSave = useCallback((module: string, value: string): Promise<boolean> => {
    const prior = inflightSaveRef.current ?? Promise.resolve(true)
    const queued = prior.then(() => persistSave(module, value))
    inflightSaveRef.current = queued
    void queued.then(() => {
      if (inflightSaveRef.current === queued) inflightSaveRef.current = null
    })
    return queued
  }, [persistSave])

  const autoSave = useCallback((module: string, value: string) => {
    clearTimeout(saveTimer.current)
    pendingSaveRef.current = { module, value }
    saveTimer.current = setTimeout(() => {
      pendingSaveRef.current = null
      void queueSave(module, value)
    }, 500)
  }, [queueSave])

  // Flush a pending debounced save synchronously (returns the persist promise so
  // callers can await it before switching file). No-op when nothing is pending.
  const flushSave = useCallback(async (): Promise<boolean> => {
    const pending = pendingSaveRef.current
    if (!pending) return inflightSaveRef.current ?? true
    clearTimeout(saveTimer.current)
    pendingSaveRef.current = null
    return queueSave(pending.module, pending.value)
  }, [queueSave])

  // On unmount, flush any pending edit (fire-and-forget — cleanup can't await;
  // persistSave's post-await guards skip state updates once unmounted).
  useEffect(() => () => {
    const pending = pendingSaveRef.current
    clearTimeout(saveTimer.current)
    if (pending) {
      pendingSaveRef.current = null
      void queueSave(pending.module, pending.value)
    }
  }, [queueSave])

  // Load file list.  The backend returns `{files: []}` for a missing
  // utility/ dir, so anything reaching this catch is a real failure
  // (network, 500, auth) — surface it as a toast, not a silent empty list.
  const loadFiles = useCallback(async () => {
    try {
      const res = await listUtilityFiles()
      setFiles(res.files)
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to list utility files: ${detail}`)
      setFiles([])
    }
  }, [addToast])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount pattern
  useEffect(() => { loadFiles() }, [loadFiles])

  // Load file content
  const loadFile = useCallback(async (module: string) => {
    // Flush (persist) any pending save for the previous file before switching —
    // a bare clearTimeout here would silently discard the last edit.
    if (!await flushSave()) return
    try {
      const res = await readUtilityFile(module)
      setContent(res.content)
      setActiveModule(module)
      setErrorLine(null)
      setErrorMsg(null)
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to load utility file "${module}": ${detail}`)
      setErrorMsg(`Failed to load ${module}`)
    }
  }, [addToast, flushSave])

  // Auto-select first file
  useEffect(() => {
    if (files.length > 0 && activeModule === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- auto-select on initial load
      loadFile(files[0].module)
    }
  }, [files, activeModule, loadFile])

  const handleCreate = useCallback(async () => {
    const name = newName.trim().replace(/\.py$/, "")
    if (!name) return
    setCreating(false)
    setNewName("")
    try {
      const res = await createUtilityFile({ name })
      // Auto-add import to preamble
      if (res.import_line) {
        onImportAdded(res.import_line)
      }
      await loadFiles()
      loadFile(res.module)
    } catch (err) {
      const syntaxErr = parseSyntaxError(err)
      if (syntaxErr) {
        setErrorMsg(syntaxErr.error)
      } else {
        setErrorMsg(err instanceof Error ? err.message : "Failed to create")
      }
    }
  }, [newName, loadFiles, loadFile, onImportAdded])

  const handleDelete = useCallback(async () => {
    if (!activeModule) return
    if (!confirm(`Delete ${activeModule}?`)) return
    // Discard any pending save — the file is being removed.
    clearTimeout(saveTimer.current)
    pendingSaveRef.current = null
    try {
      await deleteUtilityFile(activeModule)
      setActiveModule(null)
      setContent("")
      await loadFiles()
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to delete utility file "${activeModule}": ${detail}`)
      setErrorMsg("Failed to delete")
    }
  }, [activeModule, loadFiles, addToast])

  return (
    <PanelShell
      testId="utility-panel"
      title="Utility Scripts"
      onClose={onClose}
      icon={<FileCode2 size={14} style={{ color: 'var(--accent)' }} />}
    >
      {/* File selector */}
      <div className="px-3 py-2 flex items-center gap-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
        {creating ? (
          <form className="flex items-center gap-1 flex-1" onSubmit={(e) => { e.preventDefault(); handleCreate() }}>
            <input
              autoFocus
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onBlur={() => { setCreating(false); setNewName("") }}
              placeholder="module_name"
              className="flex-1 px-2 py-1 text-[12px] font-mono rounded focus:outline-none"
              style={{ background: 'var(--bg-input)', border: '1px solid var(--accent)', color: 'var(--text-primary)' }}
            />
            <span className="text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>.py</span>
          </form>
        ) : (
          <>
            <div className="relative flex-1" ref={dropdownRef}>
              <button
                onClick={() => setDropdownOpen((v) => !v)}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-[12px] font-mono rounded-md transition-colors"
                style={{
                  background: dropdownOpen ? 'var(--accent-soft)' : 'var(--bg-input)',
                  border: `1px solid ${dropdownOpen ? 'var(--accent)' : 'var(--border)'}`,
                  color: 'var(--text-primary)',
                }}
              >
                <span className="flex-1 text-left truncate">
                  {activeModule ?? "No files"}
                </span>
                <ChevronDown size={11} style={{ color: 'var(--text-muted)', transition: 'transform 150ms', transform: dropdownOpen ? 'rotate(180deg)' : undefined }} />
              </button>
              {dropdownOpen && files.length > 0 && (
                <div className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-2xl z-50 overflow-hidden" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}>
                  <div className="py-1">
                    {files.map((f) => {
                      const isActive = f.module === activeModule
                      return (
                        <button
                          key={f.module}
                          onClick={() => { setDropdownOpen(false); if (!isActive) loadFile(f.module) }}
                          className={`w-full flex items-center px-3 py-1.5 text-[12px] font-mono text-left transition-colors ${isActive ? "" : "hover:bg-[var(--bg-hover)]"}`}
                          style={{
                            color: isActive ? 'var(--accent)' : 'var(--text-secondary)',
                            background: isActive ? 'var(--accent-soft)' : 'transparent',
                          }}
                        >
                          {f.module}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
            <button
              onClick={() => setCreating(true)}
              className="p-1.5 rounded-md transition-colors hover:bg-[var(--bg-hover)] hover:text-[var(--accent)]"
              style={{ color: 'var(--text-muted)' }}
              title="New utility file"
            >
              <Plus size={14} />
            </button>
            {activeModule && (
              <button
                onClick={handleDelete}
                className="p-1.5 rounded-md transition-colors hover:bg-[var(--danger-soft)] hover:text-[var(--danger)]"
                style={{ color: 'var(--text-muted)' }}
                title={`Delete ${activeModule}`}
              >
                <Trash2 size={14} />
              </button>
            )}
          </>
        )}
      </div>

      {/* Editor */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {activeModule ? (
          <div className="h-full flex flex-col">
            <div className="flex-1 min-h-0">
              <CodeEditor
                defaultValue={content}
                onChange={(val) => { setContent(val); setErrorLine(null); setErrorMsg(null); if (activeModule) autoSave(activeModule, val) }}
                errorLine={errorLine}
                placeholder="# Write reusable helper functions here\n\nimport polars as pl\n\ndef my_helper(df):\n    return df"
              />
            </div>
            {errorMsg && (
              <div className="px-3 py-2 text-[11px] shrink-0" style={{ color: 'var(--danger)', borderTop: '1px solid var(--border)' }}>
                {errorMsg}
              </div>
            )}
          </div>
        ) : (
          <div className="flex items-center justify-center h-full text-[12px]" style={{ color: 'var(--text-muted)' }}>
            {files.length === 0 ? (
              <div className="text-center">
                <p>No utility files yet.</p>
                <button
                  onClick={() => setCreating(true)}
                  className="mt-2 px-3 py-1 text-[12px] font-medium rounded-md transition-colors hover:bg-[var(--accent-soft-hover)]"
                  style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}
                >
                  Create one
                </button>
              </div>
            ) : (
              "Select a file"
            )}
          </div>
        )}
      </div>

    </PanelShell>
  )
}
