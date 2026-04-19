import { useState, useEffect, useCallback, useRef } from "react"
import { Plus, Trash2, FileCode2, ChevronDown } from "lucide-react"
import { CodeEditor } from "./editors"
import PanelShell from "./PanelShell"
import useClickOutside from "../hooks/useClickOutside"
import useToastStore from "../stores/useToastStore"
import { hoverHandlers, hoverBg } from "../utils/hoverHandlers"
import {
  ApiError,
  listUtilityFiles,
  readUtilityFile,
  createUtilityFile,
  updateUtilityFile,
  deleteUtilityFile,
} from "../api/client"
import type { UtilityFile } from "../api/client"

const fileHover = hoverBg("var(--bg-hover)")

/** Extract syntax error info from an ApiError's detail (which may be a JSON object or string). */
function parseSyntaxError(err: unknown): { error: string; error_line: number | null } | null {
  if (!(err instanceof ApiError) || err.status !== 400) return null
  const raw = err.detail
  if (!raw) return null
  // detail may already be an object (runtime) or a JSON string
  let parsed: unknown = raw
  if (typeof raw === "string") {
    try { parsed = JSON.parse(raw) } catch { return { error: raw, error_line: null } }
  }
  if (typeof parsed === "object" && parsed !== null && "error" in parsed) {
    const obj = parsed as { error?: string; error_line?: number | null }
    return { error: obj.error ?? "Syntax error", error_line: obj.error_line ?? null }
  }
  return { error: String(raw), error_line: null }
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
  const activeModuleRef = useRef(activeModule)
  const mountedRef = useRef(true)
  useEffect(() => { activeModuleRef.current = activeModule }, [activeModule])
  useEffect(() => {
    mountedRef.current = true
    return () => { mountedRef.current = false }
  }, [])

  const autoSave = useCallback((module: string, value: string) => {
    clearTimeout(saveTimer.current)
    saveTimer.current = setTimeout(async () => {
      // Guard: module may have changed during the debounce window
      if (activeModuleRef.current !== module) return
      try {
        await updateUtilityFile(module, value)
        if (!mountedRef.current || activeModuleRef.current !== module) return
        setErrorLine(null)
        setErrorMsg(null)
      } catch (err) {
        if (!mountedRef.current || activeModuleRef.current !== module) return
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
    }, 500)
  }, [addToast])

  // Cleanup timer on unmount
  useEffect(() => () => clearTimeout(saveTimer.current), [])

  // Load file list
  const loadFiles = useCallback(async () => {
    try {
      const res = await listUtilityFiles()
      setFiles(res.files)
    } catch (err) {
      // utility/ may not exist yet
      console.warn("Failed to list utility files", err)
      setFiles([])
    }
  }, [])

  // eslint-disable-next-line react-hooks/set-state-in-effect -- fetch-on-mount pattern
  useEffect(() => { loadFiles() }, [loadFiles])

  // Load file content
  const loadFile = useCallback(async (module: string) => {
    // Flush any pending save for the previous file
    clearTimeout(saveTimer.current)
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
  }, [addToast])

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
    clearTimeout(saveTimer.current)
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
                    {files.map((f) => (
                      <button
                        key={f.module}
                        onClick={() => { setDropdownOpen(false); if (f.module !== activeModule) loadFile(f.module) }}
                        className="w-full flex items-center px-3 py-1.5 text-[12px] font-mono text-left transition-colors"
                        style={{
                          color: f.module === activeModule ? 'var(--accent)' : 'var(--text-secondary)',
                          background: f.module === activeModule ? 'var(--accent-soft)' : 'transparent',
                        }}
                        onMouseEnter={(e) => { if (f.module !== activeModule) fileHover.onMouseEnter(e) }}
                        onMouseLeave={(e) => { if (f.module !== activeModule) fileHover.onMouseLeave(e) }}
                      >
                        {f.module}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
            <button
              onClick={() => setCreating(true)}
              className="p-1.5 rounded-md transition-colors"
              style={{ color: 'var(--text-muted)' }}
              {...hoverHandlers("var(--bg-hover)", "var(--accent)", "transparent", "var(--text-muted)")}
              title="New utility file"
            >
              <Plus size={14} />
            </button>
            {activeModule && (
              <button
                onClick={handleDelete}
                className="p-1.5 rounded-md transition-colors"
                style={{ color: 'var(--text-muted)' }}
                {...hoverHandlers("rgba(239,68,68,.1)", "#ef4444", "transparent", "var(--text-muted)")}
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
              <div className="px-3 py-2 text-[11px] shrink-0" style={{ color: '#ef4444', borderTop: '1px solid var(--border)' }}>
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
                  className="mt-2 px-3 py-1 text-[12px] font-medium rounded-md transition-colors"
                  style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}
                  {...hoverBg("rgba(59,130,246,.2)", "var(--accent-soft)")}
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
