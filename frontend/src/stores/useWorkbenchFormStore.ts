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
import { apiErrorMessage, isStaleRevision } from "../api/errors"
import type { FormSpec, WorkbenchFormSaveResponse } from "../api/types"
import { fetchWorkbenchForm, saveWorkbenchForm } from "../api/workbench"
import { canonicalJson } from "../utils/canonicalJson"
import { reportSaveCapture } from "./saveCapture"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useQuestionStore from "./useQuestionStore"
import { appendHistoryEntry } from "./useGraphStore"
import useToastStore from "./useToastStore"
import useWorkbenchStore from "./useWorkbenchStore"
import { serial } from "../utils/serial"

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
   * How many times the form was read anew: by `load`, `reload`, or a sync adopting a
   * changed file. The view fits the sheets after a read, never after a save or an undo.
   */
  reads: number
  /** Read the form, unless it has been. */
  load: () => Promise<void>
  /** Read the form again, dropping edits and history. */
  reload: () => Promise<void>
  /**
   * Put in place a form read elsewhere, at `revision`, as the underwriter app puts the
   * release's sheets or a quote's older ones: as saved, without history, a gesture or a
   * save's lost answer from the form before. It is no read of the file (`reads`), so the
   * app's view keeps its zoom as a quote on other sheets opens.
   */
  install: (form: FormSpec, revision: string | null) => void
  /**
   * Read the file again after the pipeline's document was adopted anew. A file at the same
   * revision changes nothing; a changed one is adopted, history dropped, while the form has
   * no unsaved edits, and otherwise marks the form stale until a reload.
   */
  sync: () => Promise<void>
  /** An edit that undo reverses: `update` returns the next form, or the same one for no edit. */
  change: (update: (form: FormSpec) => FormSpec) => void
  /**
   * Record the form for undo before a run of `setFormRaw` updates, a burst of nudges' first:
   * the whole burst is then one undo step.
   */
  pushSnapshot: () => void
  /**
   * Replace the form without recording history, for each step of a gesture or a burst;
   * while a gesture is under way, whether the form is unsaved is worked out as it ends.
   */
  setFormRaw: (form: FormSpec) => void
  /**
   * Start a gesture on `before`, the form as pressed, its steps through `setFormRaw`;
   * `stop` stops it following the pointer. Any edit or history command made while it is
   * under way (`change`, `pushSnapshot`, `undo`, `redo`) finishes it first, so its later
   * steps never write over that edit and its undo step comes before it; a form read anew
   * drops it.
   */
  beginGesture: (before: FormSpec, stop: () => void) => void
  /**
   * Finish the gesture under way: the form as it found it recorded for undo, so the whole
   * gesture is one undo step, and nothing, redo kept, while the form is as it found it
   * (held at a limit, or back where it began); and whether the form is unsaved worked out.
   */
  endGesture: () => void
  undo: () => void
  redo: () => void
  /**
   * Write the form as it stands when the save's turn comes (saves run one after another).
   * Resolves whether it saved; a failure says why in one error toast, and a form that
   * changed on disk since it was read is never overwritten.
   */
  save: () => Promise<boolean>
  /** The project save's form step: `save` when the form holds unsaved edits, else true
   *  without a request. */
  flush: () => Promise<boolean>
}

/** The form as compared for unsaved edits: the server writes its keys in its own order. */
const serialise = (form: FormSpec): string => canonicalJson(form)

/** The gesture under way: the form as it found it, and what stops it following the pointer. */
let gesture: { before: FormSpec; stop: () => void } | null = null

/** Saves and syncs take turns, each acting on the form as it is when its turn comes. */
const takeTurn = serial()

const useWorkbenchFormStore = create<WorkbenchFormState>()((set, get) => {
  /**
   * Whether the form holds unsaved edits now: worked out afresh while a gesture is under
   * way, whose steps leave `dirty` as it was until it ends.
   */
  const unsaved = (): boolean => {
    const { form, savedForm, dirty } = get()
    return gesture === null || form === null ? dirty : serialise(form) !== savedForm
  }

  const finishGesture = (): void => {
    const active = gesture
    if (active === null) return
    gesture = null
    active.stop()
    const { form, undoStack, savedForm } = get()
    if (form === null) return
    // Worked out once here, not at each of the gesture's steps.
    const now = serialise(form)
    const dirty = now !== savedForm
    if (now === serialise(active.before)) set({ dirty })
    else set({ undoStack: appendHistoryEntry(undoStack, active.before), redoStack: [], dirty })
  }
  // A form read anew drops the history a gesture under way would join, and the gesture.
  const dropGesture = (): void => {
    gesture?.stop()
    gesture = null
  }

  /**
   * The form a save sent whose answer never came: it may have landed. A later save refused
   * as stale reads the file, and when the file holds exactly this, takes its revision as
   * the base and saves again, so the typing since is not lost to a reload.
   */
  let unanswered: string | null = null

  // A read takes a turn like a save or a sync: a sync queued behind it, as a document
  // adopted while the file is being read queues one, reads the file again after it, so
  // the form never ends on the branch the read began on.
  const read = (): Promise<void> => {
    set({ status: "loading", loadError: null })
    return takeTurn(async () => {
      try {
        const { form, revision } = await fetchWorkbenchForm()
        dropGesture()
        unanswered = null
        set((state) => ({
          form,
          revision,
          status: "ready",
          savedForm: serialise(form),
          dirty: false,
          undoStack: [],
          redoStack: [],
          stale: false,
          reads: state.reads + 1,
        }))
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
    reads: 0,
    load: async () => {
      if (get().status === "idle") await read()
    },
    reload: read,
    install: (form, revision) => {
      dropGesture()
      unanswered = null
      set({ form, revision, status: "ready", loadError: null, savedForm: serialise(form), dirty: false, undoStack: [], redoStack: [], stale: false })
    },
    sync: () =>
      takeTurn(async () => {
        if (get().status !== "ready") return
        const path = useWorkbenchStore.getState().formPath ?? "forms/form.json"
        try {
          const { form, revision } = await fetchWorkbenchForm()
          if (revision === get().revision) return
          if (unsaved()) {
            // The banner says the file changed on disk, and its Reload reads it.
            set({ stale: true })
            return
          }
          dropGesture()
          set((state) => ({
            form,
            revision,
            savedForm: serialise(form),
            dirty: false,
            undoStack: [],
            redoStack: [],
            stale: false,
            reads: state.reads + 1,
          }))
        } catch (error: unknown) {
          useToastStore.getState().addToast("error", `Could not read ${path} again: ${apiErrorMessage(error)}`)
        }
      }),
    change: (update) => {
      finishGesture()
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
      finishGesture()
      const { form, undoStack } = get()
      if (form === null) throw new Error("The workbench's form is not loaded")
      set({ undoStack: appendHistoryEntry(undoStack, form), redoStack: [] })
    },
    setFormRaw: (form) => {
      set(gesture === null ? { form, dirty: serialise(form) !== get().savedForm } : { form })
    },
    beginGesture: (before, stop) => {
      finishGesture()
      gesture = { before, stop }
    },
    endGesture: finishGesture,
    undo: () => {
      finishGesture()
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
      finishGesture()
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
        const { addToast } = useToastStore.getState()
        const path = useWorkbenchStore.getState().formPath ?? "forms/form.json"
        if (form === null) return false
        // A reload asked for after this save drops the edits it would have saved.
        if (status !== "ready") {
          addToast("error", `Not saved: ${path} is being read again.`)
          return false
        }
        set({ saving: true })
        try {
          let saved: WorkbenchFormSaveResponse
          try {
            saved = await saveWorkbenchForm(form, revision)
          } catch (error: unknown) {
            if (!isStaleRevision(error) || unanswered === null) throw error
            // The save whose answer was lost may be what changed the file: when the file
            // holds exactly what it sent, its revision is this save's base.
            const file = await fetchWorkbenchForm()
            if (serialise(file.form) !== unanswered) throw error
            set({ revision: file.revision, savedForm: unanswered })
            saved = await saveWorkbenchForm(form, file.revision)
          }
          unanswered = null
          const savedForm = serialise(saved.form)
          // The form may have been edited while the save ran: those edits stay unsaved.
          set({
            revision: saved.revision,
            savedForm,
            dirty: serialise(get().form ?? saved.form) !== savedForm,
            stale: false,
          })
          addToast("success", `Saved → ${path}`)
          reportSaveCapture(saved)
          // The schema may have changed: the project save that this is the form step of
          // fetches the tables afresh next, the one fetch that brings the Workbench Input's
          // and Workbench Output's copies up to date before the pipeline is saved. A fetch
          // of this save's own would publish a second update of each node, and a save
          // waits for every update, the superseded one included.
          return true
        } catch (error: unknown) {
          if (isStaleRevision(error)) {
            set({ stale: true })
            addToast("error", `Save rejected: ${path} changed on disk. Reload the workbench first.`)
          } else {
            // Its answer, not necessarily the save, was lost.
            unanswered = serialise(form)
            addToast("error", `Could not save ${path}: ${apiErrorMessage(error)}`)
          }
          return false
        } finally {
          set({ saving: false })
        }
      }),
    flush: () => (get().status === "ready" && unsaved() ? get().save() : Promise.resolve(true)),
  }
})

// The editor's guards against losing edits (a branch switch, a move) ask about the form's
// too: they read the mirror on the eager workbench store, since this store is a lazy chunk.
useWorkbenchFormStore.subscribe((state, previous) => {
  if (state.dirty !== previous.dirty) useWorkbenchStore.setState({ formDirty: state.dirty })
  // A question asked of the form before it was read anew is nobody's: what it would delete
  // may be gone, or another thing under the same id.
  if (state.reads !== previous.reads) useQuestionStore.getState().answer(null)
})

// The form follows the pipeline's document: each adoption of one (`executionGeneration`),
// as a branch switched by hand or a change on disk brings, reads the file again.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) void useWorkbenchFormStore.getState().sync()
})

export default useWorkbenchFormStore
