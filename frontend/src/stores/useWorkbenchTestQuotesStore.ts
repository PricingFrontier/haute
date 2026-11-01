/**
 * Zustand store for the test quotes an analyst keeps in Preview (specs/workbench, Test
 * quotes; Control flow 37): the test-quote folder's files, the panel that lists them, and the
 * last run's results.
 *
 * Test quotes are priced on the project as saved, as `haute app build` prices them in CI, so
 * each action that prices saves the project first, through the host's save, while the
 * pipeline or the form has unsaved edits. A save keeps the sample as saved under a name; an
 * open puts a test quote's values in the sample's place, one undo step, onto the form as the
 * view holds it; a run prices each test quote from its stored request, one at a time, and
 * keeps its result with the file's revision and the basis it priced on, which an accept
 * names so the server accepts nothing decided on what has changed since. Results, and any
 * answer still on its way, are forgotten when the branch changes or the pipeline's document
 * is adopted anew. Each write's capture on the save ledger is reported as a save's is.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { WorkbenchTestQuoteRunResponse, WorkbenchTestQuoteSummary } from "../api/types"
import {
  acceptTestQuotes,
  deleteTestQuote,
  fetchTestQuotes,
  openTestQuote,
  runTestQuote,
  saveTestQuote,
} from "../api/workbench"
import { canonicalJson } from "../utils/canonicalJson"
import { latest } from "../utils/latest"
import { reportSaveCapture } from "./saveCapture"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useGraphStore from "./useGraphStore"
import useWorkbenchFormStore from "./useWorkbenchFormStore"

/** A test quote as the last run left it: under way, or its result. */
export type QuoteRun = { status: "running" } | ({ status: "done" } & WorkbenchTestQuoteRunResponse)

interface WorkbenchTestQuotesState {
  open: boolean
  quotes: WorkbenchTestQuoteSummary[]
  /** The branch the list was read on, which every write names. */
  branch: string | null
  loading: boolean
  /** Why the last list, save, open, accept or delete failed, until the next succeeds. */
  error: string | null
  /** What the last open could not place, or what the last accept did not accept. */
  notice: string | null
  /** A save, an accept or a delete under way: no other write starts meanwhile. */
  busy: boolean
  /** The file opened last, which Replace saves over. */
  opened: string | null
  /** The files ticked for a run. */
  ticked: string[]
  runs: Record<string, QuoteRun>
  /** How far a run has come, and whether a stop was asked for; null while none runs. */
  running: { done: number; of: number; stopping: boolean } | null
  /** The host's save of the project; whether it saved. Set while the view shows. */
  saveProject: (() => Promise<boolean>) | null
  setOpen: (open: boolean) => void
  setSaveProject: (save: (() => Promise<boolean>) | null) => void
  load: () => Promise<void>
  /** Save the sample as a new test quote named `name`, or over the one `replace` names. */
  save: (args: { name?: string; replace?: string }) => Promise<boolean>
  openQuote: (file: string) => Promise<void>
  toggle: (file: string) => void
  /** Run the ticked test quotes, or every one when none is ticked. */
  run: () => Promise<void>
  stop: () => void
  accept: (file: string) => Promise<void>
  acceptAll: () => Promise<void>
  remove: (file: string) => Promise<void>
  /** Forget the results and drop any answer on its way. */
  forget: () => void
}

/** The run asked for last, the list read last and the quote opened last: a late answer to an
 * earlier one, or to one asked before `forget`, is nobody's. */
const runAsked = latest()
const listAsked = latest()
const openAsked = latest()

/** Whether the project has edits a pricing on the saved project would not see. */
function projectDirty(): boolean {
  return useGraphStore.getState().dirty || useWorkbenchFormStore.getState().dirty
}

const useWorkbenchTestQuotesStore = create<WorkbenchTestQuotesState>()((set, get) => {
  /** Save the project first while it has unsaved edits; whether it is saved now. */
  const saved = async (): Promise<boolean> => {
    if (!projectDirty()) return true
    const save = get().saveProject
    if (save === null || !(await save()) || projectDirty()) {
      set({ error: "Save the project first: test quotes are priced on the project as saved." })
      return false
    }
    return true
  }
  /** The branch, read now: the list's when it is fresh. */
  const branchNow = async (): Promise<string> => (await fetchTestQuotes()).branch

  return {
    open: false,
    quotes: [],
    branch: null,
    loading: false,
    error: null,
    notice: null,
    busy: false,
    opened: null,
    ticked: [],
    runs: {},
    running: null,
    saveProject: null,
    setOpen: (open) => {
      set({ open })
      if (open) void get().load()
    },
    setSaveProject: (saveProject) => set({ saveProject }),
    load: async () => {
      const mine = listAsked.take()
      set({ loading: true })
      try {
        const { quotes, branch } = await fetchTestQuotes()
        if (!listAsked.is(mine)) return
        // Another branch's results are nobody's here.
        if (get().branch !== null && get().branch !== branch) {
          runAsked.bump()
          openAsked.bump()
          set({ runs: {}, running: null, opened: null })
        }
        const files = new Set(quotes.map((quote) => quote.file))
        set((s) => ({ quotes, branch, error: null, ticked: s.ticked.filter((file) => files.has(file)) }))
      } catch (error: unknown) {
        if (listAsked.is(mine)) set({ error: apiErrorMessage(error) })
      } finally {
        if (listAsked.is(mine)) set({ loading: false })
      }
    },
    save: async ({ name, replace }) => {
      if (get().busy || get().running !== null) return false
      set({ busy: true, error: null, notice: null })
      try {
        if (!(await saved())) return false
        const formRevision = useWorkbenchFormStore.getState().revision
        if (formRevision === null) {
          set({ error: "Save the project first: test quotes are priced on the project as saved." })
          return false
        }
        const quote = replace === undefined ? undefined : get().quotes.find((each) => each.file === replace)
        if (replace !== undefined && quote === undefined) {
          set({ error: `${replace} is no longer listed: list the test quotes again.` })
          return false
        }
        const branch = await branchNow()
        reportSaveCapture(
          await saveTestQuote({
            name,
            replace: quote === undefined ? undefined : { file: quote.file, revision: quote.revision },
            branch,
            formRevision,
          }),
        )
        await get().load()
        return true
      } catch (error: unknown) {
        set({ error: `Not saved: ${apiErrorMessage(error)}` })
        return false
      } finally {
        set({ busy: false })
      }
    },
    openQuote: async (file) => {
      const { form } = useWorkbenchFormStore.getState()
      if (form === null) return
      const mine = openAsked.take()
      const sent = canonicalJson(form)
      try {
        const opened = await openTestQuote(file, form)
        // Typing since, a later open, or another branch's sheets: the values are not wanted.
        const now = useWorkbenchFormStore.getState().form
        if (!openAsked.is(mine) || now === null || canonicalJson(now) !== sent) return
        useWorkbenchFormStore.getState().change((spec) => ({ ...spec, sample: opened.values }))
        set({
          opened: file,
          error: null,
          notice:
            opened.not_opened.length === 0 ? null : `Opened ${file}. Not opened: ${opened.not_opened.join(", ")}.`,
        })
      } catch (error: unknown) {
        if (openAsked.is(mine)) set({ error: apiErrorMessage(error) })
      }
    },
    toggle: (file) =>
      set((s) => ({ ticked: s.ticked.includes(file) ? s.ticked.filter((x) => x !== file) : [...s.ticked, file] })),
    run: async () => {
      if (get().running !== null || get().busy) return
      set({ error: null, notice: null })
      if (!(await saved())) return
      const { quotes, ticked } = get()
      const files = (ticked.length > 0 ? ticked : quotes.map((quote) => quote.file)).filter(
        (file) => quotes.find((quote) => quote.file === file)?.problem === null,
      )
      const mine = runAsked.take()
      set({
        running: { done: 0, of: files.length, stopping: false },
        runs: Object.fromEntries(files.map((file) => [file, { status: "running" } as QuoteRun])),
      })
      // One quote at a time, each its own request, as the build prices them.
      for (const [index, file] of files.entries()) {
        if (!runAsked.is(mine) || get().running?.stopping) break
        try {
          const result = await runTestQuote(file)
          if (!runAsked.is(mine)) return
          // A result read on another branch than the list's is nobody's here.
          if (result.branch !== get().branch) {
            runAsked.bump()
            set({ runs: {}, running: null, error: "The branch changed during the run: run again." })
            void get().load()
            return
          }
          set((s) => ({ runs: { ...s.runs, [file]: { status: "done", ...result } } }))
        } catch (error: unknown) {
          if (!runAsked.is(mine)) return
          set((s) => {
            const runs = { ...s.runs }
            delete runs[file]
            return { runs, error: apiErrorMessage(error) }
          })
        }
        if (runAsked.is(mine)) set({ running: { done: index + 1, of: files.length, stopping: get().running?.stopping ?? false } })
      }
      if (runAsked.is(mine)) {
        // Quotes a stop left unrun show nothing.
        set((s) => ({
          running: null,
          runs: Object.fromEntries(Object.entries(s.runs).filter(([, run]) => run.status === "done")),
        }))
      }
    },
    stop: () => set((s) => (s.running === null ? {} : { running: { ...s.running, stopping: true } })),
    accept: async (file) => {
      await acceptFiles([file])
    },
    acceptAll: async () => {
      const files = Object.entries(get().runs)
        .filter(([, run]) => run.status === "done" && (run.outcome === "moved" || run.outcome === "no_longer_compares"))
        .map(([file]) => file)
      if (files.length > 0) await acceptFiles(files)
    },
    remove: async (file) => {
      const quote = get().quotes.find((each) => each.file === file)
      const branch = get().branch
      if (quote === undefined || branch === null || get().busy) return
      set({ busy: true })
      try {
        reportSaveCapture(await deleteTestQuote(file, quote.revision, branch))
        set((s) => {
          const runs = { ...s.runs }
          delete runs[file]
          return { error: null, runs, ticked: s.ticked.filter((x) => x !== file), opened: s.opened === file ? null : s.opened }
        })
        await get().load()
      } catch (error: unknown) {
        set({ error: `Not deleted: ${apiErrorMessage(error)}` })
      } finally {
        set({ busy: false })
      }
    },
    forget: () => {
      runAsked.bump()
      listAsked.bump()
      openAsked.bump()
      set({ runs: {}, running: null, opened: null })
      if (get().open) void get().load()
    },
  }

  /** Accept *files*' new answers, each at the revision its result was made on, on the
   * basis the run priced on. */
  async function acceptFiles(files: string[]): Promise<void> {
    const { runs, busy, running } = get()
    if (busy || running !== null) return
    const results = files.map((file) => runs[file]).filter((run): run is Extract<QuoteRun, { status: "done" }> => run?.status === "done")
    if (results.length === 0) return
    const [first] = results
    if (first.basis === null) return
    set({ busy: true, error: null, notice: null })
    const mine = runAsked.current()
    try {
      const answered = await acceptTestQuotes({
        branch: first.branch,
        basis: first.basis,
        quotes: results.map((result) => ({ file: result.file, revision: result.revision })),
      })
      reportSaveCapture(answered)
      const accepted = new Set(answered.accepted.map((quote) => quote.file))
      if (runAsked.is(mine)) {
        set((s) => {
          const kept = { ...s.runs }
          for (const file of accepted) {
            const run = kept[file]
            if (run?.status === "done") kept[file] = { ...run, outcome: "unchanged", moves: [], removed: [], why: null }
          }
          return { runs: kept }
        })
      }
      if (answered.refused.length > 0) {
        set({ notice: `Not accepted: ${answered.refused.map((each) => `${each.file} (${each.why})`).join(", ")}.` })
      }
      await get().load()
    } catch (error: unknown) {
      set({ error: `Not accepted: ${apiErrorMessage(error)}` })
    } finally {
      set({ busy: false })
    }
  }
})

// The test quotes follow the pipeline's document: results read before an adoption (a branch
// switched by hand, a change on disk) are nobody's now.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useWorkbenchTestQuotesStore.getState().forget()
})

export default useWorkbenchTestQuotesStore
