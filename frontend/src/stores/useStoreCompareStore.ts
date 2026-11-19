/** Per-environment catalogue snapshots and shared, explicit rename choices (specs/workbench).
 * Snapshots survive pane closure and are persisted by the server. A project/form adoption
 * invalidates session state and all replies in flight; editing the form only recomputes
 * comparisons, without reading a database.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchCatalogueTable, WorkbenchStoreCompareResponse, WorkbenchStoreRename } from "../api/types"
import { fetchStoreCatalogue, fetchStoreCompare } from "../api/workbench"
import { answer, watch } from "../utils/answers"
import useDocumentStatusStore from "./useDocumentStatusStore"
import useWorkbenchFormStore from "./useWorkbenchFormStore"
import useWorkbenchStore from "./useWorkbenchStore"

export type CatalogueRead =
  | { status: "idle" }
  | { status: "reading" }
  | { status: "failed"; message: string }
  | { status: "read"; tables: WorkbenchCatalogueTable[]; checkedAt: number; target: string }

interface Basis {
  form: FormSpec
  renames: WorkbenchStoreRename[]
  read: CatalogueRead
}

export type Compared =
  | { status: "none" }
  | (Basis & { status: "comparing" })
  | (Basis & { status: "failed"; message: string })
  | (Basis & { status: "compared"; answer: WorkbenchStoreCompareResponse })

export interface EnvironmentComparison {
  read: CatalogueRead
  compared: Compared
}

interface StoreCompareState {
  environment: string | null
  environments: Record<string, EnvironmentComparison>
  renames: WorkbenchStoreRename[]
  choose: (environment: string | null, read?: boolean) => void
  readAgain: () => void
  pair: (rename: WorkbenchStoreRename) => void
  unpair: (table: string, column: string) => void
  compare: (form: FormSpec) => void
  verify: () => Promise<WorkbenchStoreCompareResponse | null>
  reset: () => void
}

const UNCHECKED: EnvironmentComparison = { read: { status: "idle" }, compared: { status: "none" } }

export function environmentState(state: StoreCompareState, name = state.environment): EnvironmentComparison {
  return name === null ? UNCHECKED : state.environments[name] ?? UNCHECKED
}

export function currentComparison(state: StoreCompareState, form: FormSpec | null, name = state.environment): WorkbenchStoreCompareResponse | null {
  const { read, compared } = environmentState(state, name)
  return compared.status === "compared" && compared.form === form && compared.renames === state.renames && compared.read === read
    ? compared.answer : null
}

export function comparisonFailure(state: StoreCompareState, form: FormSpec | null, name = state.environment): string | null {
  const { read, compared } = environmentState(state, name)
  return read.status === "failed" ? read.message
    : compared.status === "failed" && compared.form === form && compared.renames === state.renames && compared.read === read ? compared.message : null
}

export function comparisonStatus(state: StoreCompareState, form: FormSpec | null, name = state.environment): string {
  const { read } = environmentState(state, name)
  if (read.status === "idle") return "Not checked"
  if (read.status === "reading") return "Checking"
  if (comparisonFailure(state, form, name) !== null) return "Check failed"
  const answer = currentComparison(state, form, name)
  if (answer === null) return "Comparing"
  if (answer.blocked.length > 0 || answer.manual.length > 0 || answer.changes.some((change) => change.disposition === "manual")) return "Needs attention"
  return answer.matches ? "Compatible" : "Changes needed"
}

const useStoreCompareStore = create<StoreCompareState>()((set, get, api) => {
  const update = (name: string, value: EnvironmentComparison) =>
    set((state) => ({ environments: { ...state.environments, [name]: value } }))

  const readDatabase = (name: string, refresh: boolean) => {
    const pending: CatalogueRead = { status: "reading" }
    update(name, { read: pending, compared: { status: "none" } })
    const failed = (error: unknown) =>
      update(name, { read: { status: "failed", message: apiErrorMessage(error) }, compared: { status: "none" } })
    // A read answers while it is the environment's: a newer read, or a reset, replaces it.
    void answer([watch(api, (state) => state.environments[name]?.read)], fetchStoreCatalogue(name, refresh), {
      ok: (catalogue) => {
        if (catalogue.environment !== name) {
          failed(new Error("The database check answered for a different environment. Check again."))
          return
        }
        update(name, { read: { status: "read", tables: catalogue.tables, checkedAt: catalogue.checked_at, target: catalogue.target }, compared: { status: "none" } })
      },
      failed,
    })
  }

  const compareEnvironment = async (form: FormSpec, name: string, read: Extract<CatalogueRead, { status: "read" }>) => {
    const { renames } = get()
    const pending: Compared = { status: "comparing", form, renames, read }
    update(name, { read, compared: pending })
    // A comparison answers while it is the environment's, made of the renames and the form as they are.
    const belongsTo = [
      watch(api, (state) => state.environments[name]?.compared),
      watch(api, (state) => state.renames),
      watch(useWorkbenchFormStore, (state) => state.form, (_, now) => now === form),
    ] as const
    let compared: WorkbenchStoreCompareResponse | null = null
    await answer(belongsTo, fetchStoreCompare(form, name, read.tables, renames, read.target), {
      ok: (comparison) => {
        update(name, { read, compared: { ...pending, status: "compared", answer: comparison } })
        compared = comparison
      },
      failed: (error) => update(name, { read, compared: { ...pending, status: "failed", message: apiErrorMessage(error) } }),
    })
    return compared
  }

  return {
    environment: null,
    environments: {},
    renames: [],
    choose: (environment, read = true) => {
      set({ environment })
      if (read && environment !== null) readDatabase(environment, false)
    },
    readAgain: () => {
      const name = get().environment
      if (name !== null) readDatabase(name, true)
    },
    pair: (rename) => set((state) => ({
      renames: [...state.renames.filter((each) => !(each.table === rename.table && (each.column === rename.column || each.was === rename.was))), rename],
    })),
    unpair: (table, column) => set((state) => ({ renames: state.renames.filter((each) => !(each.table === table && each.column === column)) })),
    compare: (form) => {
      const { environments, renames } = get()
      for (const [name, { read, compared }] of Object.entries(environments)) {
        if (read.status !== "read") continue
        if (compared.status !== "none" && compared.form === form && compared.renames === renames && compared.read === read) continue
        void compareEnvironment(form, name, read)
      }
    },
    verify: async () => {
      const name = get().environment
      const form = useWorkbenchFormStore.getState().form
      const { read } = environmentState(get())
      if (name === null || form === null || read.status !== "read" || currentComparison(get(), form) === null) return null
      let verified: WorkbenchStoreCompareResponse | null = null
      // The comparison of the environment chosen, as it stands: another chosen meanwhile verifies nothing.
      await answer([watch(api, (state) => state.environment)], compareEnvironment(form, name, read), {
        ok: (compared) => {
          if (currentComparison(get(), form) === compared) verified = compared
        },
      })
      return verified
    },
    reset: () => set({ environment: null, environments: {}, renames: [] }),
  }
})

useDocumentStatusStore.subscribe((state, previous) => {
  if (state.executionGeneration !== previous.executionGeneration) useStoreCompareStore.getState().reset()
})
useWorkbenchFormStore.subscribe((state, previous) => {
  if (state.reads !== previous.reads || (state.status === "loading" && previous.status !== "loading")) useStoreCompareStore.getState().reset()
})
useWorkbenchStore.subscribe((state, previous) => {
  if (state.formPath !== previous.formPath || state.environments.join("\n") !== previous.environments.join("\n")) useStoreCompareStore.getState().reset()
})

export function useDatabaseComparison() {
  const state = useStoreCompareStore()
  const form = useWorkbenchFormStore((s) => s.form)
  return { state, form, answer: currentComparison(state, form) }
}

export default useStoreCompareStore
