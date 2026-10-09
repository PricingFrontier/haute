/**
 * Zustand store for the workbench's form while its view edits it (specs/workbench): the
 * form as read from GET /api/workbench/form with the file's revision, its undo and redo
 * history on the graph store's history rule, whether it has unsaved edits, and its saving
 * through PUT /api/workbench/form against that revision. The form is read once and kept
 * across view switches, so unsaved edits and history survive a trip to the pipeline editor.
 */
import { create } from "zustand"
import { ApiError } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec } from "../api/types"
import { fetchWorkbenchForm, saveWorkbenchForm } from "../api/workbench"
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
  /** The last save was refused because the file changed on disk; cleared by a reload. */
  stale: boolean
  saving: boolean
  /** Read the form, unless it has been. */
  load: () => Promise<void>
  /** Read the form again, dropping edits and history. */
  reload: () => Promise<void>
  /** An edit that undo reverses: `update` returns the next form, or the same one for no edit. */
  change: (update: (form: FormSpec) => FormSpec) => void
  undo: () => void
  redo: () => void
  /**
   * Write the form as it stands when the save's turn comes (saves run one after another).
   * Resolves whether it saved; a failure says why in one error toast, and a form that
   * changed on disk since it was read is never overwritten.
   */
  save: () => Promise<boolean>
}

const serialise = (form: FormSpec): string => JSON.stringify(form)

const isStaleRevision = (error: unknown): boolean =>
  error instanceof ApiError
  && error.status === 409
  && typeof error.detail === "string"
  && error.detail.startsWith("stale_document_revision")

/** Saves run one after another, each writing the form as it is when its turn comes. */
let saves: Promise<boolean> = Promise.resolve(true)

const useWorkbenchFormStore = create<WorkbenchFormState>()((set, get) => {
  const read = async (): Promise<void> => {
    set({ status: "loading", loadError: null })
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
    load: async () => {
      if (get().status === "idle") await read()
    },
    reload: read,
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
    save: () => {
      saves = saves.then(async () => {
        const { form, revision, status } = get()
        if (form === null || status !== "ready") return false
        const { addToast } = useToastStore.getState()
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
          })
          addToast("success", `Saved → ${useWorkbenchStore.getState().formPath ?? "the form"}`)
          // The schema may have changed: the Workbench Input's and Workbench Output's
          // copies follow through the same fetch that keeps them current.
          useWorkbenchStore.getState().refreshTables()
          return true
        } catch (error: unknown) {
          if (isStaleRevision(error)) {
            set({ stale: true })
            addToast("error", "Save rejected: the form changed on disk. Reload the workbench first.")
          } else {
            addToast("error", `Could not save the workbench's form: ${apiErrorMessage(error)}`)
          }
          return false
        } finally {
          set({ saving: false })
        }
      })
      return saves
    },
  }
})

export default useWorkbenchFormStore
