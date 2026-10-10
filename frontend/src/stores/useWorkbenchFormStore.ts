/**
 * Zustand store for the workbench's form while its view edits it (specs/workbench): the
 * form as read from GET /api/workbench/form with the file's revision, its undo and redo
 * history on the graph store's history rule, whether it has unsaved edits, and its saving
 * through PUT /api/workbench/form against that revision, each save reported as the
 * pipeline's saves are (the ledger commit, the warnings, the identity prompt). The form is
 * read once and kept across view switches, so unsaved edits and history survive a trip to
 * the pipeline editor, and read again whenever the pipeline's document is adopted anew (a
 * branch switched by hand, a change on disk), so the view never shows another branch's
 * form.
 */
import { create } from "zustand"
import { ApiError } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec } from "../api/types"
import { fetchWorkbenchForm, saveWorkbenchForm } from "../api/workbench"
import { canonicalJson } from "../utils/canonicalJson"
import { reportSaveCapture } from "./saveCapture"
import useDocumentStatusStore from "./useDocumentStatusStore"
import { appendHistoryEntry } from "./useGraphStore"
import useToastStore from "./useToastStore"
import useWorkbenchStore from "./useWorkbenchStore"

export type FormLoadStatus = "idle" | "loading" | "ready" | "failed"

interface WorkbenchFormState {
  /** The form as the view edits it; null until read. */
  form: FormSpec | null
  /**
   * The revision of the file the form was read from or last saved to, which the next save
   * quotes; null while the form has never been saved.
   */
  revision: string | null
  status: FormLoadStatus
  /** Why the form could not be read, while `status` is "failed". */
  loadError: string | null
  /** The form as read or last saved, serialised: an edit since is unsaved. */
  savedForm: string
  /** The form differs from `savedForm`. */
  dirty: boolean
  undoStack: FormSpec[]
  redoStack: FormSpec[]
  /**
   * The file changed on disk since the form was read: a save was refused for it, or a sync
   * found it while the form had unsaved edits. Cleared by a reload.
   */
  stale: boolean
  saving: boolean
  /**
   * The last save was written but not captured on the ledger, for want of a git identity
   * or because the capture failed: the next flush saves the form again, unchanged, so a
   * capture can be made (a new file is otherwise never tracked, and a milestone's sweep
   * takes tracked files alone).
   */
  uncaptured: boolean
  /** Read the form, unless it has been. */
  load: () => Promise<void>
  /** Read the form again, dropping edits and history. */
  reload: () => Promise<void>
  /**
   * Read the file again after the pipeline's document was adopted anew. A file at the same
   * revision changes nothing; a changed one is adopted, history dropped, while the form has
   * no unsaved edits, and otherwise marks the form stale until a reload.
   */
  sync: () => Promise<void>
  /** An edit that undo reverses: `update` returns the next form, or the same one for no edit. */
  change: (update: (form: FormSpec) => FormSpec) => void
  /**
   * Record the form for undo before a run of `setFormRaw` updates, a drag's first move:
   * the whole gesture is then one undo step.
   */
  pushSnapshot: () => void
  /** Replace the form without recording history, for each step of a gesture. */
  setFormRaw: (form: FormSpec) => void
  undo: () => void
  redo: () => void
  /**
   * Write the form as it stands when the save's turn comes (saves run one after another).
   * Resolves whether it saved; a failure says why in one error toast, and a form that
   * changed on disk since it was read is never overwritten.
   */
  save: () => Promise<boolean>
  /**
   * The git flows' save, before a milestone, a move or an identity retry: `save` when the
   * form holds unsaved edits or a save the ledger did not capture, else true without a
   * request.
   */
  flush: () => Promise<boolean>
}

/** The form as compared for unsaved edits: the server writes its keys in its own order. */
const serialise = (form: FormSpec): string => canonicalJson(form)

const isStaleRevision = (error: unknown): boolean =>
  error instanceof ApiError
  && error.status === 409
  && typeof error.detail === "string"
  && error.detail.startsWith("stale_document_revision")

/** Saves and syncs take turns, each acting on the form as it is when its turn comes. */
let turns: Promise<unknown> = Promise.resolve()

const takeTurn = <T>(act: () => Promise<T>): Promise<T> => {
  const turn = turns.then(act)
  turns = turn.catch(() => undefined)
  return turn
}

const useWorkbenchFormStore = create<WorkbenchFormState>()((set, get) => {
  // A read takes a turn like a save or a sync: a sync queued behind it, as a document
  // adopted while the file is being read queues one, reads the file again after it, so
  // the form never ends on the branch the read began on.
  const read = (): Promise<void> => {
    set({ status: "loading", loadError: null })
    return takeTurn(async () => {
      try {
        const { form, revision } = await fetchWorkbenchForm()
        set({
          form,
          revision,
          status: "ready",
          savedForm: serialise(form),
          dirty: false,
          undoStack: [],
          redoStack: [],
          stale: false,
        })
      } catch (error: unknown) {
        set({ status: "failed", loadError: apiErrorMessage(error) })
      }
    })
  }

  return {
    form: null,
    revision: null,
    status: "idle",
    loadError: null,
    savedForm: "",
    dirty: false,
    undoStack: [],
    redoStack: [],
    stale: false,
    saving: false,
    uncaptured: false,
    load: async () => {
      if (get().status === "idle") await read()
    },
    reload: read,
    sync: () =>
      takeTurn(async () => {
        if (get().status !== "ready") return
        const path = useWorkbenchStore.getState().formPath ?? "forms/form.json"
        try {
          const { form, revision } = await fetchWorkbenchForm()
          if (revision === get().revision) return
          if (get().dirty) {
            // The banner says the file changed on disk, and its Reload reads it.
            set({ stale: true })
            return
          }
          set({
            form,
            revision,
            savedForm: serialise(form),
            dirty: false,
            undoStack: [],
            redoStack: [],
            stale: false,
          })
        } catch (error: unknown) {
          useToastStore.getState().addToast("error", `Could not read ${path} again: ${apiErrorMessage(error)}`)
        }
      }),
    change: (update) => {
      const { form, undoStack, savedForm } = get()
      if (form === null) throw new Error("The workbench's form is not loaded")
      const next = update(form)
      if (next === form) return
      set({
        form: next,
        undoStack: appendHistoryEntry(undoStack, form),
        redoStack: [],
        dirty: serialise(next) !== savedForm,
      })
    },
    pushSnapshot: () => {
      const { form, undoStack } = get()
      if (form === null) throw new Error("The workbench's form is not loaded")
      set({ undoStack: appendHistoryEntry(undoStack, form), redoStack: [] })
    },
    setFormRaw: (form) => {
      set({ form, dirty: serialise(form) !== get().savedForm })
    },
    undo: () => {
      const { form, undoStack, redoStack, savedForm } = get()
      const previous = undoStack.at(-1)
      if (form === null || previous === undefined) return
      set({
        form: previous,
        undoStack: undoStack.slice(0, -1),
        redoStack: [...redoStack, form],
        dirty: serialise(previous) !== savedForm,
      })
    },
    redo: () => {
      const { form, undoStack, redoStack, savedForm } = get()
      const next = redoStack.at(-1)
      if (form === null || next === undefined) return
      set({
        form: next,
        undoStack: appendHistoryEntry(undoStack, form),
        redoStack: redoStack.slice(0, -1),
        dirty: serialise(next) !== savedForm,
      })
    },
    save: () =>
      takeTurn(async () => {
        const { form, revision, status } = get()
        if (form === null || status !== "ready") return false
        const { addToast } = useToastStore.getState()
        const path = useWorkbenchStore.getState().formPath ?? "forms/form.json"
        set({ saving: true })
        try {
          const saved = await saveWorkbenchForm(form, revision)
          const savedForm = serialise(saved.form)
          // The form may have been edited while the save ran: those edits stay unsaved.
          set({
            revision: saved.revision,
            savedForm,
            dirty: serialise(get().form ?? saved.form) !== savedForm,
            stale: false,
            // Every warning on a form save is the capture's: there is nothing else to warn of.
            uncaptured: saved.identity_required || (saved.git_sha === null && saved.warnings.length > 0),
          })
          addToast("success", `Saved → ${path}`)
          reportSaveCapture(saved)
          // The schema may have changed: the Workbench Input's and Workbench Output's
          // copies follow through the same fetch that keeps them current.
          useWorkbenchStore.getState().refreshTables()
          return true
        } catch (error: unknown) {
          if (isStaleRevision(error)) {
            set({ stale: true })
            addToast("error", `Save rejected: ${path} changed on disk. Reload the workbench first.`)
          } else {
            addToast("error", `Could not save ${path}: ${apiErrorMessage(error)}`)
          }
          return false
        } finally {
          set({ saving: false })
        }
      }),
    flush: () => {
      const { status, dirty, uncaptured } = get()
      return status === "ready" && (dirty || uncaptured) ? get().save() : Promise.resolve(true)
    },
  }
})

// The editor's guards against losing edits (a branch switch, a move) ask about the form's
// too: they read the mirror on the eager workbench store, since this store is a lazy chunk.
useWorkbenchFormStore.subscribe((state, previous) => {
  if (state.dirty !== previous.dirty) useWorkbenchStore.setState({ formDirty: state.dirty })
})

// The form follows the pipeline's document: each adoption of one (`executionGeneration`),
// as a branch switched by hand or a change on disk brings, reads the file again.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) void useWorkbenchFormStore.getState().sync()
})

export default useWorkbenchFormStore
