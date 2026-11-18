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
import { draftPart } from "../components/form/useCommittedDraft"
import { answer, ask, newest, requestKind, under, watch, type Settled } from "../utils/answers"
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

/** What the panel's answers are asked under: `forget` (another branch, a document adopted
 * anew) ends it, so a late answer to one asked before is nobody's. The run, the list and the
 * quote opened are each the newest of their kind besides. */
const panel = requestKind()
const runAsked = requestKind()
const listAsked = requestKind()
const openAsked = requestKind()

const SAVE_FIRST = "Save the project first: test quotes are priced on the project as saved."

/** Whether the project has edits a pricing on the saved project would not see. */
function projectDirty(): boolean {
  return useGraphStore.getState().dirty || useWorkbenchFormStore.getState().dirty
}

/** A write that landed reports its capture on the save ledger, whatever came of the panel. */
function settleWrite(settled: Settled<Parameters<typeof reportSaveCapture>[0]>): void {
  if ("value" in settled) reportSaveCapture(settled.value)
}

const useWorkbenchTestQuotesStore = create<WorkbenchTestQuotesState>()((set, get) => {
  /** Save the project first while it has unsaved edits; whether it is saved now. */
  const saved = async (): Promise<boolean> => {
    if (!projectDirty()) return true
    const save = get().saveProject
    if (save === null) {
      set({ error: SAVE_FIRST })
      return false
    }
    let done = false
    await answer([under(panel)], save(), {
      ok: (landed) => {
        done = landed && !projectDirty()
        if (!done) set({ error: SAVE_FIRST })
      },
      failed: () => set({ error: SAVE_FIRST }),
    })
    return done
  }
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
      await answer([newest(listAsked), under(panel)], fetchTestQuotes(), {
        ok: ({ quotes, branch }) => {
          // Another branch's results are nobody's here.
          if (get().branch !== null && get().branch !== branch) {
            runAsked.bump()
            openAsked.bump()
            set({ runs: {}, running: null, opened: null })
          }
          const files = new Set(quotes.map((quote) => quote.file))
          set((s) => ({ quotes, branch, error: null, ticked: s.ticked.filter((file) => files.has(file)) }))
        },
        failed: (error) => set({ error: apiErrorMessage(error) }),
      }, { pending: (loading) => set({ loading }) })
    },
    save: async ({ name, replace }) => {
      if (get().busy || get().running !== null) return false
      set({ error: null, notice: null })
      let landed = false
      await answer([under(panel)], saved(), {
        ok: async (isSaved, asked) => {
          if (!isSaved) return
          const formRevision = useWorkbenchFormStore.getState().revision
          if (formRevision === null) {
            set({ error: SAVE_FIRST })
            return
          }
          const quote = replace === undefined ? undefined : get().quotes.find((each) => each.file === replace)
          if (replace !== undefined && quote === undefined) {
            set({ error: `${replace} is no longer listed: list the test quotes again.` })
            return
          }
          // The branch the panel read on: a save decided there is refused on any other.
          const branch = get().branch
          if (branch === null) {
            set({ error: "List the test quotes again before saving one." })
            return
          }
          await asked.answer(
            saveTestQuote({
              name,
              replace: quote === undefined ? undefined : { file: quote.file, revision: quote.revision },
              branch,
              formRevision,
            }),
            {
              ok: async (capture) => {
                reportSaveCapture(capture)
                landed = true
                await get().load()
              },
              failed: (error) => set({ error: `Not saved: ${apiErrorMessage(error)}` }),
              stale: settleWrite,
            },
          )
        },
      }, { inFlight: (busy) => set({ busy }) })
      return landed
    },
    openQuote: async (file) => {
      const { form } = useWorkbenchFormStore.getState()
      if (form === null) return
      // Typing since, even text not yet committed, a later open, or another branch's sheets:
      // the values are not wanted.
      await answer(
        [newest(openAsked), under(panel), watch(useWorkbenchFormStore, (state) => state.form), draftPart()],
        openTestQuote(file, form),
        {
          ok: (opened) => {
            useWorkbenchFormStore.getState().change((spec) => ({ ...spec, sample: opened.values }))
            set({
              opened: file,
              error: null,
              notice:
                opened.not_opened.length === 0 ? null : `Opened ${file}. Not opened: ${opened.not_opened.join(", ")}.`,
            })
          },
          failed: (error) => set({ error: apiErrorMessage(error) }),
        },
      )
    },
    toggle: (file) =>
      set((s) => ({ ticked: s.ticked.includes(file) ? s.ticked.filter((x) => x !== file) : [...s.ticked, file] })),
    run: async () => {
      if (get().running !== null || get().busy) return
      set({ error: null, notice: null })
      await answer([under(panel)], saved(), {
        ok: async (isSaved) => {
          if (!isSaved) return
          const { quotes, ticked } = get()
          const files = (ticked.length > 0 ? ticked : quotes.map((quote) => quote.file)).filter(
            (file) => quotes.find((quote) => quote.file === file)?.problem === null,
          )
          const asked = ask([newest(runAsked), under(panel)])
          try {
            set({
              running: { done: 0, of: files.length, stopping: false },
              runs: Object.fromEntries(files.map((file) => [file, { status: "running" } as QuoteRun])),
            })
            // One quote at a time, each its own request, as the build prices them.
            for (const [index, file] of files.entries()) {
              if (!asked.current() || get().running?.stopping) break
              await asked.answer(runTestQuote(file), {
                ok: async (result) => {
                  // A result read on another branch than the list's is nobody's here.
                  if (result.branch !== get().branch) {
                    runAsked.bump()
                    set({ runs: {}, running: null })
                    // Listed again first, so the reason stays once the list is read.
                    await answer([under(panel)], get().load(), {
                      ok: () => set({ error: "The branch changed during the run: run again." }),
                    })
                    return
                  }
                  set((s) => ({
                    runs: { ...s.runs, [file]: { status: "done", ...result } },
                    running: { done: index + 1, of: files.length, stopping: s.running?.stopping ?? false },
                  }))
                },
                failed: (error) => {
                  set((s) => {
                    const runs = { ...s.runs }
                    delete runs[file]
                    return {
                      runs,
                      error: apiErrorMessage(error),
                      running: { done: index + 1, of: files.length, stopping: s.running?.stopping ?? false },
                    }
                  })
                },
              })
            }
            // Quotes a stop left unrun show nothing.
            asked.apply(() =>
              set((s) => ({
                running: null,
                runs: Object.fromEntries(Object.entries(s.runs).filter(([, run]) => run.status === "done")),
              })),
            )
          } finally {
            asked.release()
          }
        },
      })
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
      await answer([under(panel)], deleteTestQuote(file, quote.revision, branch), {
        ok: async (capture) => {
          reportSaveCapture(capture)
          set((s) => {
            const runs = { ...s.runs }
            delete runs[file]
            return { error: null, runs, ticked: s.ticked.filter((x) => x !== file), opened: s.opened === file ? null : s.opened }
          })
          await get().load()
        },
        failed: (error) => set({ error: `Not deleted: ${apiErrorMessage(error)}` }),
        stale: settleWrite,
      }, { inFlight: (busy) => set({ busy }) })
    },
    forget: () => {
      panel.bump()
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
    set({ error: null, notice: null })
    // Accepting prices again: on the project as saved, which a save of edits made since the
    // run changes, and the server then refuses as priced on something else.
    await answer([under(panel)], saved(), {
      ok: async (isSaved) => {
        if (!isSaved) return
        // The run the results are from: a newer one keeps its own results.
        await answer([under(runAsked), under(panel)], acceptTestQuotes({
          branch: first.branch,
          basis: first.basis as string,
          quotes: results.map((result) => ({ file: result.file, revision: result.revision })),
        }), {
          ok: async (answered) => {
            reportSaveCapture(answered)
            const accepted = new Set(answered.accepted.map((quote) => quote.file))
            set((s) => {
              const kept = { ...s.runs }
              for (const file of accepted) {
                const run = kept[file]
                if (run?.status === "done") kept[file] = { ...run, outcome: "unchanged", moves: [], removed: [], why: null }
              }
              return { runs: kept }
            })
            if (answered.refused.length > 0) {
              set({ notice: `Not accepted: ${answered.refused.map((each) => `${each.file} (${each.why})`).join(", ")}.` })
            }
            await get().load()
          },
          failed: (error) => set({ error: `Not accepted: ${apiErrorMessage(error)}` }),
          stale: (settled) => {
            if (!("value" in settled)) return
            reportSaveCapture(settled.value)
            if (settled.value.refused.length > 0) {
              set({ notice: `Not accepted: ${settled.value.refused.map((each) => `${each.file} (${each.why})`).join(", ")}.` })
            }
            void get().load()
          },
        })
      },
    }, { inFlight: (busy) => set({ busy }) })
  }
})

// The test quotes follow the pipeline's document: results read before an adoption (a branch
// switched by hand, a change on disk) are nobody's now.
useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useWorkbenchTestQuotesStore.getState().forget()
})

export default useWorkbenchTestQuotesStore
