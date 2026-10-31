/**
 * Zustand store for the schema pane's Compare (specs/workbench): the environment chosen, the
 * tables its database holds as they were last read, the columns the analyst paired as
 * renames, and the form compared with those tables. The database is read as an environment
 * is chosen and as `readAgain` is asked, never as the form changes; the form is compared
 * with what was read as it changes (`compare`). The view's own, neither saved nor undone:
 * `reset` as the pane closes. Only the newest read and the newest comparison land.
 */
import { create } from "zustand"
import { apiErrorMessage } from "../api/errors"
import type { FormSpec, WorkbenchCatalogueTable, WorkbenchStoreCompareResponse, WorkbenchStoreRename } from "../api/types"
import { fetchStoreCatalogue, fetchStoreCompare } from "../api/workbench"
import { latest } from "../utils/latest"

/** The chosen environment's database, as last read. */
export type CatalogueRead =
  | { status: "idle" }
  | { status: "reading" }
  | { status: "failed"; message: string }
  | { status: "read"; tables: WorkbenchCatalogueTable[] }

/** The form compared with what was read: the form it was asked of, and the answer. */
export type Compared =
  | { status: "none" }
  | { status: "failed"; message: string }
  | { status: "compared"; form: FormSpec; answer: WorkbenchStoreCompareResponse }

interface StoreCompareState {
  /** The environment Compare chose; null for none. */
  environment: string | null
  read: CatalogueRead
  /** The columns the analyst paired as renames, as the last comparison took them. */
  renames: WorkbenchStoreRename[]
  compared: Compared
  /** Choose an environment, or none, and read its database. */
  choose: (environment: string | null) => void
  /** Read the chosen environment's database again, keeping the pairings. */
  readAgain: () => void
  /** Pair a column of the database with one of the form's, replacing any pairing of either. */
  pair: (rename: WorkbenchStoreRename) => void
  /** Take the pairing of the form's column `column` in table `table` out. */
  unpair: (table: string, column: string) => void
  /** Compare `form` with what was read; nothing while nothing is. */
  compare: (form: FormSpec) => void
  reset: () => void
}

const reads = latest()
const compares = latest()

const sameRenames = (one: readonly WorkbenchStoreRename[], other: readonly WorkbenchStoreRename[]) =>
  one.length === other.length &&
  one.every((each, at) => each.table === other[at].table && each.column === other[at].column && each.was === other[at].was)

const INITIAL = {
  environment: null,
  read: { status: "idle" },
  renames: [],
  compared: { status: "none" },
} as const satisfies Pick<StoreCompareState, "environment" | "read" | "renames" | "compared">

const useStoreCompareStore = create<StoreCompareState>()((set, get) => {
  const readDatabase = (environment: string) => {
    const ticket = reads.take()
    compares.bump()
    set({ read: { status: "reading" }, compared: { status: "none" } })
    fetchStoreCatalogue(environment).then(
      (answer) => {
        if (reads.is(ticket)) set({ read: { status: "read", tables: answer.tables } })
      },
      (error: unknown) => {
        if (reads.is(ticket)) set({ read: { status: "failed", message: apiErrorMessage(error) } })
      },
    )
  }
  return {
    ...INITIAL,
    choose: (environment) => {
      set({ environment, renames: [] })
      if (environment === null) {
        reads.bump()
        compares.bump()
        set({ read: { status: "idle" }, compared: { status: "none" } })
        return
      }
      readDatabase(environment)
    },
    readAgain: () => {
      const { environment } = get()
      if (environment !== null) readDatabase(environment)
    },
    pair: (rename) => {
      const kept = get().renames.filter(
        (each) => !(each.table === rename.table && (each.column === rename.column || each.was === rename.was)),
      )
      set({ renames: [...kept, rename] })
    },
    unpair: (table, column) => {
      set({ renames: get().renames.filter((each) => !(each.table === table && each.column === column)) })
    },
    compare: (form) => {
      const { environment, read, renames } = get()
      if (environment === null || read.status !== "read") return
      const ticket = compares.take()
      fetchStoreCompare(form, environment, read.tables, renames).then(
        (answer) => {
          if (!compares.is(ticket)) return
          set({ compared: { status: "compared", form, answer } })
          // A pairing the form or the database no longer allows is dropped as the comparison
          // drops it; an unchanged list is left as it is, so nothing compares again.
          if (!sameRenames(answer.renames, get().renames)) set({ renames: answer.renames })
        },
        (error: unknown) => {
          if (compares.is(ticket)) set({ compared: { status: "failed", message: apiErrorMessage(error) } })
        },
      )
    },
    reset: () => {
      reads.bump()
      compares.bump()
      set(INITIAL)
    },
  }
})

export default useStoreCompareStore
