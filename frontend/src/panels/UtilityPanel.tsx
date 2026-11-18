import { useState, useEffect, useCallback, useRef } from "react"
import type { EditorView } from "@codemirror/view"
import { Plus, Trash2, FileCode2, ChevronDown } from "lucide-react"
import { CodeEditor } from "./editors/CodeEditor"
import PanelShell from "./PanelShell"
import useClickOutside from "../hooks/useClickOutside"
import { hasModifier } from "../hooks/keyboardTargets"
import { noteProjectInputsChanged } from "../stores/projectInputs"
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
import { apiErrorMessage, apiErrorRawDetail, apiErrorStatus } from "../api/errors"
import type { UtilityDeleteResponse, UtilityFile, UtilityWriteResult } from "../api/types"
import { useRequestKind } from "../hooks/useRequestKind"
import { answer, newest, under } from "../utils/answers"
import { serial } from "../utils/serial"

/**
 * Extract syntax error info from an ApiError's flat string detail.
 *
 * Server responses are ``{"detail": "Syntax error on line N: <msg>"}``.
 * The line number is extracted via a ``/line (\d+)/`` regex for the editor's
 * gutter; the raw message is shown to the user.
 */
function parseSyntaxError(err: unknown): { error: string; error_line: number | null } | null {
  if (!(err instanceof ApiError) || err.status !== 400) return null
  // A refused save names its line and message (`{line, message}`); other refusals are text.
  const record = apiErrorRawDetail(err)
  if (record && typeof record.message === "string" && record.message.trim()) {
    return {
      error: record.message,
      error_line: typeof record.line === "number" ? record.line : null,
    }
  }
  if (typeof err.detail === "string" && err.detail.trim()) {
    return { error: err.detail, error_line: null }
  }
  return null
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

  // The edit not saved yet: its file and the editor's text. It is saved when the panel
  // closes, when another file opens, or on Ctrl+S, never while typing, so a project hook
  // the save runs never rewrites a file under the cursor. A refused save leaves it here.
  const draftRef = useRef<{ module: string; value: string } | null>(null)
  // The draft a save is sending, so a second trigger waits for that save, not another.
  const sendingRef = useRef<{ draft: { module: string; value: string }; result: Promise<boolean> } | null>(null)
  // Saves take turns, each sending the revision the one before returned; a Reload or an
  // Overwrite reads the file in the turn after the saves already queued.
  const [saves] = useState(serial)
  // What the panel's answers belong to (specs/frontend-shared, "Late answers"): its life; the
  // newest selection of a file or the Imports entry, so one overtaken while it waited (on a
  // save or a read) is dropped; and the file open, which a save and a delete of it belong to.
  const life = useRequestKind()
  const selections = useRequestKind()
  const opened = useRequestKind()
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

  /** Open *module*, or the Imports entry: what was open before stops being so. */
  const show = useCallback((module: string | null, imports: boolean) => {
    opened.bump()
    setActiveModule(module)
    setImportsSelected(imports)
  }, [opened])

  /** A request whose module has no revision read: refused before anything is sent. */
  const unread = (module: string) => Promise.reject(new Error(`no revision of ${module} was read`))

  // Persist one edit and reconcile the error banner. The save belongs to the file open: once
  // another is open, or the panel closed, its answer touches the editor no more, while what
  // it wrote (its capture, the revision the next save sends) is still kept.
  const persistSave = useCallback(async (module: string, value: string): Promise<boolean> => {
    if (conflictModuleRef.current === module) return false
    const revision = revisionsRef.current.get(module)
    let landed = false
    const wrote = (saved: UtilityWriteResult) => {
      reportSaveCapture(saved)
      // Helper code a node runs changed: every preview and estimate made before is old.
      noteProjectInputsChanged()
    }
    await answer([under(opened), under(life)], revision === undefined ? unread(module) : updateUtilityFile(module, value, revision), {
      ok: (saved) => {
        wrote(saved)
        if (contentRef.current !== value && saved.content !== value) {
          // The file is not what was sent (a project hook rewrote it, or another editor
          // changed it meanwhile) and text was typed since: neither replaces the other unasked.
          // The old revision stays, so nothing more saves until Reload or Overwrite.
          conflictModuleRef.current = module
          setConflict({
            module,
            message: `utility/${module}.py changed while it was saved. Reload it, or overwrite it with your edit.`,
          })
          return
        }
        revisionsRef.current.set(module, saved.revision)
        landed = true
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
      },
      failed: (err) => {
        const syntaxErr = parseSyntaxError(err)
        if (syntaxErr) {
          setErrorLine(syntaxErr.error_line)
          setErrorMsg(syntaxErr.error)
        } else if (apiErrorStatus(err) === 412) {
          conflictModuleRef.current = module
          setConflict({ module, message: apiErrorMessage(err) })
        } else {
          const detail = apiErrorMessage(err, "unknown error")
          addToast("error", `Failed to save utility file "${module}": ${detail}`)
          setErrorMsg("Failed to save")
        }
      },
      stale: (settled) => {
        if (!("value" in settled)) return
        wrote(settled.value)
        revisionsRef.current.set(module, settled.value.revision)
        landed = true
      },
    })
    return landed
  }, [addToast, life, opened])

  const queueSave = useCallback(
    (module: string, value: string): Promise<boolean> => saves(() => persistSave(module, value)),
    [persistSave, saves],
  )

  const recordEdit = useCallback((module: string, value: string) => {
    if (value === contentRef.current) return
    if (draftRef.current === null) setUtilityUnsaved(true)
    draftRef.current = { module, value }
    contentRef.current = value
  }, [setUtilityUnsaved])

  // Send *draft*, which stops being the draft once saved unless text was typed since.
  const sendDraft = useCallback((draft: { module: string; value: string }): Promise<boolean> => {
    const sent = queueSave(draft.module, draft.value)
    const result = (async () => {
      let saved = false
      await answer([under(life)], sent, {
        ok: (done) => {
          saved = done
          // Settled: a later save of the same draft sends it again.
          if (sendingRef.current?.draft === draft) sendingRef.current = null
          if (done && draftRef.current === draft) {
            draftRef.current = null
            useUIStore.getState().setUtilityUnsaved(false)
          }
        },
        stale: (settled) => {
          saved = "value" in settled && settled.value
        },
      })
      return saved
    })()
    sendingRef.current = { draft, result }
    return result
  }, [life, queueSave])

  // Save the draft: the text as it is now, or with *all* until no draft is left (text typed
  // during a save is saved next), as leaving the file needs. False when a save is refused,
  // the draft then kept.
  const saveDraft = useCallback((all: boolean): Promise<boolean> => {
    const next = async (): Promise<boolean> => {
      const draft = draftRef.current
      if (draft === null) return true
      const sending = sendingRef.current
      const sent = sending?.draft === draft ? sending.result : sendDraft(draft)
      if (!all) return sent
      let saved = false
      await answer([under(life)], sent, {
        ok: async (done) => {
          saved = done && (await next())
        },
        stale: (settled) => {
          saved = "value" in settled && settled.value
        },
      })
      return saved
    }
    return next()
  }, [life, sendDraft])

  // Closing, or another panel opening in this one's place, waits (the store holds it) for
  // the draft to save; a refused save keeps the panel open showing why.
  const leavingRef = useRef(false)
  useEffect(() => {
    if (leave === null || leavingRef.current) return
    leavingRef.current = true
    void answer([under(life)], saveDraft(true), {
      ok: (saved) => {
        leavingRef.current = false
        const ui = useUIStore.getState()
        if (saved) ui.completeUtilityLeave()
        else ui.cancelUtilityLeave()
      },
    })
  }, [leave, life, saveDraft])

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
  const listings = useRequestKind()
  const loadFiles = useCallback(() => answer([newest(listings), under(life)], listUtilityFiles(), {
    ok: (res) => setFiles(res.files),
    failed: (err) => {
      const detail = apiErrorMessage(err, "unknown error")
      addToast("error", `Failed to list utility files: ${detail}`)
      setFiles([])
    },
  }), [addToast, life, listings])

  useEffect(() => { void loadFiles() }, [loadFiles])

  // Load file content. The previous file's draft saves first, a refused save keeping it
  // open; text typed in it while the read waited saves too before it goes. A selection a
  // newer one overtook does nothing more: that file's draft is its own.
  const loadFile = useCallback(async (module: string) => {
    await answer([newest(selections), under(life)], saveDraft(true), {
      ok: async (saved, asked) => {
        if (!saved) return
        await asked.answer(readUtilityFile(module), {
          ok: async (res) => {
            await asked.answer(saveDraft(true), {
              ok: (savedAgain) => {
                if (!savedAgain) return
                revisionsRef.current.set(module, res.revision)
                setContent(res.content)
                show(module, false)
                setErrorLine(null)
                setErrorMsg(null)
              },
            })
          },
          failed: (err) => {
            const detail = apiErrorMessage(err, "unknown error")
            addToast("error", `Failed to load utility file "${module}": ${detail}`)
            setErrorMsg(`Failed to load ${module}`)
          },
        })
      },
    })
  }, [addToast, life, saveDraft, selections, show])

  // Reload drops the draft for the file as it is now; Overwrite saves the draft over it.
  // Either reads in the save queue's next turn, so the saves already queued settle first (the
  // conflict refuses each) and none lands on the file once it is read. A read the panel has
  // moved on from (closed, or another file) is dropped.
  const recoverConflict = useCallback(async (choice: "reload" | "overwrite") => {
    const module = conflictModuleRef.current
    if (module === null || recoveringRef.current) return
    await answer([under(opened), under(life)], saves(() => readUtilityFile(module)), {
      ok: async (read) => {
        if (conflictModuleRef.current !== module) return
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
      },
      failed: (err) => {
        const detail = apiErrorMessage(err, "unknown error")
        addToast("error", `Failed to load utility file "${module}": ${detail}`)
      },
    }, {
      inFlight: (flying) => {
        recoveringRef.current = flying
        setRecovering(flying)
      },
    })
  }, [addToast, life, opened, saves, sendDraft, setUtilityUnsaved])

  const selectImports = useCallback(async () => {
    await answer([newest(selections), under(life)], saveDraft(true), {
      ok: (saved) => {
        if (!saved) return
        show(activeModule, true)
        setErrorLine(null)
        setErrorMsg(null)
      },
    })
  }, [activeModule, life, saveDraft, selections, show])

  // Auto-select first file
  useEffect(() => {
    if (!importsSelected && files.length > 0 && activeModule === null) {
      loadFile(files[0].module)
    }
  }, [files, activeModule, importsSelected, loadFile])

  const handleCreate = useCallback(async () => {
    const name = newName.trim().replace(/\.py$/, "")
    if (!name) return
    setCreateError(null)
    // A created file is the project's whatever the panel shows by its answer: its revision,
    // its capture and its import line are kept.
    const created = (res: UtilityWriteResult) => {
      revisionsRef.current.set(res.module, res.revision)
      reportSaveCapture(res)
      noteProjectInputsChanged()
      // Auto-add import to preamble
      if (res.import_line) onImportAdded(res.import_line)
    }
    await answer([under(life)], createUtilityFile({ name }), {
      ok: async (res, asked) => {
        created(res)
        setCreating(false)
        setNewName("")
        await asked.answer(loadFiles(), { ok: () => loadFile(res.module) })
      },
      failed: (err) => {
        const syntaxErr = parseSyntaxError(err)
        setCreateError(syntaxErr ? syntaxErr.error : apiErrorMessage(err, "Failed to create"))
      },
      stale: (settled) => {
        if ("value" in settled) created(settled.value)
      },
    })
  }, [newName, life, loadFiles, loadFile, onImportAdded])

  // A delete belongs to the file open when it was pressed (FEP-04): its answer clears the
  // draft and the file only while that file is still open. A file chosen meanwhile keeps its
  // draft; the list is read again either way, since the deleted file has gone.
  const handleDelete = useCallback(async () => {
    const module = activeModule
    if (!module) return
    if (!(await confirmed({ title: `Delete ${module}?`, confirm: "Delete", danger: true }))) return
    const revision = revisionsRef.current.get(module)
    const deleted = (capture: UtilityDeleteResponse) => {
      reportSaveCapture(capture)
      noteProjectInputsChanged()
      revisionsRef.current.delete(module)
    }
    await answer([under(opened), under(life)], revision === undefined ? unread(module) : deleteUtilityFile(module, revision), {
      ok: async (capture) => {
        deleted(capture)
        // The file is gone, its draft with it; a refused delete keeps both.
        draftRef.current = null
        setUtilityUnsaved(false)
        show(null, false)
        setContent("")
        await loadFiles()
      },
      failed: (err) => {
        const detail = apiErrorMessage(err, "unknown error")
        addToast("error", `Failed to delete utility file "${module}": ${detail}`)
        setErrorMsg("Failed to delete")
      },
      stale: (settled) => {
        if (!("value" in settled)) return
        deleted(settled.value)
        void loadFiles()
      },
    })
  }, [activeModule, life, opened, setUtilityUnsaved, show, loadFiles, addToast])

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
