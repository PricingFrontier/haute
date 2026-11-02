import { useState, useEffect, useCallback, useRef } from "react"
import type { EditorView } from "@codemirror/view"
import { Plus, Trash2, FileCode2, ChevronDown } from "lucide-react"
import { CodeEditor } from "./editors/CodeEditor"
import PanelShell from "./PanelShell"
import useClickOutside from "../hooks/useClickOutside"
import { hasModifier } from "../hooks/keyboardTargets"
import { reportSaveCapture } from "../stores/saveCapture"
import { confirmed } from "../stores/useQuestionStore"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import {
  ApiError,
  listUtilityFiles,
  readUtilityFile,
  createUtilityFile,
  updateUtilityFile,
  deleteUtilityFile,
} from "../api/client"
import { apiErrorStatus } from "../api/errors"
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
  /** The pipeline's imports (its preamble), edited through the fixed Imports entry. */
  preamble: string
  onPreambleChange: (value: string) => void
}

const IMPORTS_LABEL = "Imports"

export default function UtilityPanel({
  onClose,
  onImportAdded,
  preamble,
  onPreambleChange,
}: UtilityPanelProps) {
  const addToast = useToastStore((s) => s.addToast)
  const [files, setFiles] = useState<UtilityFile[]>([])
  // The Imports entry is first in the list; it edits the preamble and never
  // goes through the utility file routes.
  const [importsSelected, setImportsSelected] = useState(false)
  const [activeModule, setActiveModule] = useState<string | null>(null)
  const [content, setContent] = useState("")
  const [errorLine, setErrorLine] = useState<number | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)
  useClickOutside(dropdownRef, () => setDropdownOpen(false), dropdownOpen)
  const [creating, setCreating] = useState(false)
  const [newName, setNewName] = useState("")
  // A refused create, shown under the selector in every state; the typed
  // name stays until a create succeeds.
  const [createError, setCreateError] = useState<string | null>(null)

  const inflightSaveRef = useRef<Promise<boolean> | null>(null)
  // The edit not saved yet: its file and the editor's text. It is saved when the panel
  // closes, when another file opens, or on Ctrl+S, never while typing, so a project hook
  // the save runs never rewrites a file under the cursor. A refused save leaves it here.
  const draftRef = useRef<{ module: string; value: string } | null>(null)
  // The draft a save is sending, so a second trigger waits for that save, not another.
  const sendingRef = useRef<{ draft: { module: string; value: string }; result: Promise<boolean> } | null>(null)
  const unsaved = useUIStore((s) => s.utilityUnsaved)
  const setUtilityUnsaved = useUIStore((s) => s.setUtilityUnsaved)
  const leave = useUIStore((s) => s.utilityLeave)
  // Each file's revision as its read, create or last save returned it: a save or delete
  // sends it, so the server refuses one made against a file changed outside the panel.
  const revisionsRef = useRef(new Map<string, string>())
  // A file whose save was refused because it changed outside the panel (412), with the
  // server's message. Its draft stays, unsaved, until Reload or Overwrite.
  const [conflict, setConflict] = useState<{ module: string; message: string } | null>(null)
  const conflictModuleRef = useRef<string | null>(null)
  // Bumped when the open file is read again, so the editor shows what was read.
  const [readGeneration, setReadGeneration] = useState(0)
  // A Reload or Overwrite under way: one runs at a time.
  const [recovering, setRecovering] = useState(false)
  const recoveringRef = useRef(false)
  const contentRef = useRef(content)
  const editorViewRef = useRef<EditorView | null>(null)
  useEffect(() => { contentRef.current = content }, [content])
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
    if (conflictModuleRef.current === module) return false
    try {
      const revision = revisionsRef.current.get(module)
      if (revision === undefined) throw new Error(`no revision of ${module} was read`)
      const saved = await updateUtilityFile(module, value, revision)
      reportSaveCapture(saved)
      const active = mountedRef.current && activeModuleRef.current === module
      const typedSince = active && contentRef.current !== value
      if (typedSince && saved.content !== value) {
        // The file is not what was sent (a project hook rewrote it, or another editor
        // changed it meanwhile) and text was typed since: neither replaces the other unasked.
        // The old revision stays, so nothing more saves until Reload or Overwrite.
        conflictModuleRef.current = module
        setConflict({
          module,
          message: `utility/${module}.py changed while it was saved. Reload it, or overwrite it with your edit.`,
        })
        return false
      }
      revisionsRef.current.set(module, saved.revision)
      if (active) {
        setErrorLine(null)
        setErrorMsg(null)
        // The file as the save left it, a project hook's formatting included, in the editor
        // as it is, the cursor kept where the text allows.
        if (saved.content !== value) {
          contentRef.current = saved.content
          setContent(saved.content)
          const view = editorViewRef.current
          if (view) {
            view.dispatch({
              changes: { from: 0, to: view.state.doc.length, insert: saved.content },
              selection: { anchor: Math.min(view.state.selection.main.head, saved.content.length) },
            })
          } else {
            setReadGeneration((generation) => generation + 1)
          }
        }
      }
      return true
    } catch (err) {
      if (mountedRef.current && activeModuleRef.current === module) {
        const syntaxErr = parseSyntaxError(err)
        if (syntaxErr) {
          setErrorLine(syntaxErr.error_line)
          setErrorMsg(syntaxErr.error)
        } else if (apiErrorStatus(err) === 412) {
          conflictModuleRef.current = module
          setConflict({ module, message: err instanceof Error ? err.message : String(err) })
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

  const recordEdit = useCallback((module: string, value: string) => {
    if (value === contentRef.current) return
    if (draftRef.current === null) setUtilityUnsaved(true)
    draftRef.current = { module, value }
    contentRef.current = value
  }, [setUtilityUnsaved])

  // Send *draft*, which stops being the draft once saved unless text was typed since.
  const sendDraft = useCallback((draft: { module: string; value: string }): Promise<boolean> => {
    const result = queueSave(draft.module, draft.value).then((saved) => {
      // Settled: a later save of the same draft sends it again.
      if (sendingRef.current?.draft === draft) sendingRef.current = null
      if (saved && draftRef.current === draft) {
        draftRef.current = null
        useUIStore.getState().setUtilityUnsaved(false)
      }
      return saved
    })
    sendingRef.current = { draft, result }
    return result
  }, [queueSave])

  // Save the draft: the text as it is now, or with *all* until no draft is left (text typed
  // during a save is saved next), as leaving the file needs. False when a save is refused,
  // the draft then kept.
  const saveDraft = useCallback(async (all: boolean): Promise<boolean> => {
    let draft = draftRef.current
    while (draft !== null) {
      const sending = sendingRef.current
      if (!(await (sending?.draft === draft ? sending.result : sendDraft(draft)))) return false
      if (!all) return true
      draft = draftRef.current
    }
    return true
  }, [sendDraft])

  // Closing, or another panel opening in this one's place, waits (the store holds it) for
  // the draft to save; a refused save keeps the panel open showing why.
  const leavingRef = useRef(false)
  useEffect(() => {
    if (leave === null || leavingRef.current) return
    leavingRef.current = true
    void saveDraft(true).then((saved) => {
      leavingRef.current = false
      const ui = useUIStore.getState()
      if (saved) ui.completeUtilityLeave()
      else ui.cancelUtilityLeave()
    })
  }, [leave, saveDraft])

  // Close without saving, after a refused save: the draft is dropped.
  const discardDraft = () => {
    draftRef.current = null
    setUtilityUnsaved(false)
    onClose()
  }

  // A panel that goes away by another path still saves what it holds, and holds nothing back.
  const queueSaveRef = useRef(queueSave)
  useEffect(() => { queueSaveRef.current = queueSave }, [queueSave])
  useEffect(() => () => {
    const draft = draftRef.current
    if (draft !== null) void queueSaveRef.current(draft.module, draft.value)
    const ui = useUIStore.getState()
    ui.setUtilityUnsaved(false)
    ui.cancelUtilityLeave()
  }, [])

  // Closing the tab with a draft asks first.
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (draftRef.current !== null) event.preventDefault()
    }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [])

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
    // The previous file's draft saves first; a refused save keeps it open.
    if (!await saveDraft(true)) return
    try {
      const res = await readUtilityFile(module)
      revisionsRef.current.set(module, res.revision)
      setContent(res.content)
      setActiveModule(module)
      setImportsSelected(false)
      setErrorLine(null)
      setErrorMsg(null)
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to load utility file "${module}": ${detail}`)
      setErrorMsg(`Failed to load ${module}`)
    }
  }, [addToast, saveDraft])

  // Reload drops the draft for the file as it is now; Overwrite saves the draft over it.
  // Either first lets the saves already queued settle (the conflict refuses each), so none
  // lands on the file once it is read.
  const recoverConflict = useCallback(async (choice: "reload" | "overwrite") => {
    const module = conflictModuleRef.current
    if (module === null || recoveringRef.current) return
    recoveringRef.current = true
    setRecovering(true)
    try {
      await inflightSaveRef.current
      let read: { content: string; revision: string }
      try {
        read = await readUtilityFile(module)
      } catch (err) {
        const detail = err instanceof Error ? err.message : "unknown error"
        addToast("error", `Failed to load utility file "${module}": ${detail}`)
        return
      }
      // A read the panel has moved on from (closed, or another file) is dropped.
      if (
        !mountedRef.current
        || conflictModuleRef.current !== module
        || activeModuleRef.current !== module
      ) return
      revisionsRef.current.set(module, read.revision)
      conflictModuleRef.current = null
      setConflict(null)
      if (choice === "reload") {
        draftRef.current = null
        setUtilityUnsaved(false)
        setContent(read.content)
        setReadGeneration((generation) => generation + 1)
        setErrorLine(null)
        setErrorMsg(null)
      } else {
        const draft = draftRef.current?.module === module
          ? draftRef.current
          : { module, value: contentRef.current }
        if (draftRef.current === null) setUtilityUnsaved(true)
        draftRef.current = draft
        await sendDraft(draft)
      }
    } finally {
      recoveringRef.current = false
      if (mountedRef.current) setRecovering(false)
    }
  }, [addToast, sendDraft, setUtilityUnsaved])

  const selectImports = useCallback(async () => {
    if (!await saveDraft(true)) return
    setImportsSelected(true)
    setErrorLine(null)
    setErrorMsg(null)
  }, [saveDraft])

  // Auto-select first file
  useEffect(() => {
    if (!importsSelected && files.length > 0 && activeModule === null) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- auto-select on initial load
      loadFile(files[0].module)
    }
  }, [files, activeModule, importsSelected, loadFile])

  const handleCreate = useCallback(async () => {
    const name = newName.trim().replace(/\.py$/, "")
    if (!name) return
    setCreateError(null)
    try {
      const res = await createUtilityFile({ name })
      revisionsRef.current.set(res.module, res.revision)
      reportSaveCapture(res)
      setCreating(false)
      setNewName("")
      // Auto-add import to preamble
      if (res.import_line) {
        onImportAdded(res.import_line)
      }
      await loadFiles()
      loadFile(res.module)
    } catch (err) {
      const syntaxErr = parseSyntaxError(err)
      setCreateError(syntaxErr ? syntaxErr.error : err instanceof Error ? err.message : "Failed to create")
    }
  }, [newName, loadFiles, loadFile, onImportAdded])

  const handleDelete = useCallback(async () => {
    if (!activeModule) return
    if (!(await confirmed({ title: `Delete ${activeModule}?`, confirm: "Delete", danger: true }))) return
    try {
      const revision = revisionsRef.current.get(activeModule)
      if (revision === undefined) throw new Error(`no revision of ${activeModule} was read`)
      reportSaveCapture(await deleteUtilityFile(activeModule, revision))
      // The file is gone, its draft with it; a refused delete keeps both.
      draftRef.current = null
      setUtilityUnsaved(false)
      revisionsRef.current.delete(activeModule)
      setActiveModule(null)
      setContent("")
      await loadFiles()
    } catch (err) {
      const detail = err instanceof Error ? err.message : "unknown error"
      addToast("error", `Failed to delete utility file "${activeModule}": ${detail}`)
      setErrorMsg("Failed to delete")
    }
  }, [activeModule, setUtilityUnsaved, loadFiles, addToast])

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
              aria-label="New utility module name"
              aria-invalid={createError !== null}
              onChange={(e) => { setNewName(e.target.value); setCreateError(null) }}
              onBlur={() => { if (createError === null) { setCreating(false); setNewName("") } }}
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
                data-testid="utility-file-selector"
                onClick={() => setDropdownOpen((v) => !v)}
                className="w-full flex items-center gap-1.5 px-2 py-1 text-[12px] font-mono rounded-md transition-colors"
                style={{
                  background: dropdownOpen ? 'var(--accent-soft)' : 'var(--bg-input)',
                  border: `1px solid ${dropdownOpen ? 'var(--accent)' : 'var(--border)'}`,
                  color: 'var(--text-primary)',
                }}
              >
                <span className="flex-1 text-left truncate">
                  {importsSelected ? IMPORTS_LABEL : activeModule ?? "No files"}
                </span>
                <ChevronDown size={11} style={{ color: 'var(--text-muted)', transition: 'transform 150ms', transform: dropdownOpen ? 'rotate(180deg)' : undefined }} />
              </button>
              {dropdownOpen && (
                <div className="absolute top-full left-0 right-0 mt-1 rounded-lg shadow-2xl z-50 overflow-hidden" style={{ background: 'var(--bg-panel)', border: '1px solid var(--border)' }}>
                  <div className="py-1">
                    <button
                      data-testid="utility-imports-entry"
                      onClick={() => { setDropdownOpen(false); if (!importsSelected) void selectImports() }}
                      className={`w-full flex items-center px-3 py-1.5 text-[12px] font-mono text-left transition-colors ${importsSelected ? "" : "hover:bg-[var(--bg-hover)]"}`}
                      style={{
                        color: importsSelected ? 'var(--accent)' : 'var(--text-secondary)',
                        background: importsSelected ? 'var(--accent-soft)' : 'transparent',
                      }}
                    >
                      {IMPORTS_LABEL}
                    </button>
                    {files.map((f) => {
                      const isActive = !importsSelected && f.module === activeModule
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
            {activeModule && !importsSelected && (
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

      {createError && (
        <div
          role="alert"
          data-testid="utility-create-error"
          className="px-3 py-1.5 text-[11px] shrink-0"
          style={{ color: 'var(--danger)', borderBottom: '1px solid var(--border)' }}
        >
          {createError}
        </div>
      )}

      {/* Editor */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        {importsSelected ? (
          <div className="h-full flex flex-col" data-testid="utility-imports">
            <div className="px-3 py-2 shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
              <p className="text-[11px] font-mono" style={{ color: 'var(--text-muted)' }}>
                <span style={{ color: "var(--text-accent-muted)" }}>import polars as pl</span> and <span style={{ color: "var(--text-accent-muted)" }}>import haute</span> are always included
              </p>
            </div>
            <div className="flex-1 min-h-0">
              <CodeEditor
                key="imports"
                defaultValue={preamble}
                onChange={onPreambleChange}
                placeholder={"from utility.features import *\nimport numpy as np\nfrom catboost import CatBoostRegressor"}
              />
            </div>
          </div>
        ) : activeModule ? (
          <div
            className="h-full flex flex-col"
            onKeyDown={(event) => {
              // The file's own save; the pipeline's Ctrl+S skips a key handled here.
              if (!hasModifier(event.nativeEvent) || event.key !== "s") return
              event.preventDefault()
              void saveDraft(false)
            }}
          >
            <div className="flex-1 min-h-0">
              <CodeEditor
                key={`${activeModule}:${readGeneration}`}
                defaultValue={content}
                onEditorView={(view) => { editorViewRef.current = view }}
                // The draft is the text as it is at every keystroke, so a close, a switch or
                // Ctrl+S right after typing saves all of it.
                reportEachChange
                onChange={(val) => {
                  setContent(val)
                  recordEdit(activeModule, val)
                  // A file that changed outside the panel keeps saying so until Reload or Overwrite.
                  if (conflictModuleRef.current === activeModule) return
                  setErrorLine(null)
                  setErrorMsg(null)
                }}
                errorLine={errorLine}
                placeholder="# Write reusable helper functions here\n\nimport polars as pl\n\ndef my_helper(df):\n    return df"
              />
            </div>
            {conflict?.module === activeModule ? (
              <div
                data-testid="utility-conflict"
                className="px-3 py-2 text-[11px] shrink-0 flex items-center gap-2"
                style={{ color: 'var(--danger)', borderTop: '1px solid var(--border)' }}
              >
                <span className="flex-1 min-w-0">{conflict.message}</span>
                <button
                  type="button"
                  disabled={recovering}
                  onClick={() => void recoverConflict("reload")}
                  className="px-2 py-0.5 text-[11px] font-medium rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--accent-soft-hover)]"
                  style={{ color: 'var(--accent)', background: 'var(--accent-soft)' }}
                >
                  Reload
                </button>
                <button
                  type="button"
                  disabled={recovering}
                  onClick={() => void recoverConflict("overwrite")}
                  className="px-2 py-0.5 text-[11px] font-medium rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--danger-soft)]"
                  style={{ color: 'var(--danger)' }}
                >
                  Overwrite
                </button>
                {unsaved && <DiscardButton disabled={recovering} onClick={discardDraft} />}
              </div>
            ) : errorMsg && (
              <div className="px-3 py-2 text-[11px] shrink-0 flex items-center gap-2" style={{ color: 'var(--danger)', borderTop: '1px solid var(--border)' }}>
                <span className="flex-1 min-w-0">{errorMsg}</span>
                {unsaved && <DiscardButton onClick={discardDraft} />}
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

/** Close the panel without saving, after a refused save. */
function DiscardButton({ onClick, disabled = false }: { onClick: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      data-testid="utility-discard"
      disabled={disabled}
      onClick={onClick}
      className="px-2 py-0.5 text-[11px] font-medium rounded-md transition-colors disabled:opacity-50 hover:bg-[var(--bg-hover)]"
      style={{ color: 'var(--text-secondary)' }}
    >
      Discard
    </button>
  )
}
