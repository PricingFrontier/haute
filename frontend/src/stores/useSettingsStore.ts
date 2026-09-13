/**
 * Zustand store for application-level settings and caches:
 *   - Row limit (preview configuration)
 *   - Streaming chunk size (rows per streaming chunk for pipeline execution)
 *   - MLflow destinations inventory (fetched once, shared by all panels)
 *   - Source system (data source routing)
 *   - Collapsible section states (persisted across panel mounts)
 *   - File listing cache (short-lived FS cache for file browsers)
 *
 * These are "global settings" that panels and hooks read but that don't
 * directly control layout or chrome visibility.
 */
import { create } from "zustand"
import { getMlflowDestinations } from "../api/client"
import type { FileListItem, MlflowDestinationEntry } from "../api/types"
import type { MlflowInventoryState } from "../utils/mlflowDestinations"
import { portableKey } from "../utils/portableKey"

export const MIN_STREAMING_CHUNK_SIZE = 1000
export const MAX_STREAMING_CHUNK_SIZE = 10_000_000

/**
 * Outcome of an `addSource` attempt. On success `key` is the minted (and now
 * persisted) source key. On rejection `reason` names WHY, so the caller can
 * word the right feedback rather than treating a silent failure as success:
 *   - `empty`     — the label was blank/whitespace-only; nothing to mint.
 *   - `duplicate` — the label sanitises to `key`, which already exists (a
 *                   distinct label can collide here because `portableKey`
 *                   maps e.g. "My Src" and "My-Src" onto the same key).
 * `addSource` used to return a bare `string | null`; the `null` hid these two
 * cases from the caller, so the toolbar form closed with no explanation.
 */
export type AddSourceResult =
  | { ok: true; key: string }
  | { ok: false; reason: "empty" }
  | { ok: false; reason: "duplicate"; key: string }

let _mlflowFetchingGuard = false
let _mlflowRefetchQueued = false

/**
 * Deadline for the whole inventory request. The backend probes each remote
 * concurrently under its own 5-second budget, so a probe that exhausts its
 * budget must still arrive (as an amber entry) rather than trip this deadline.
 */
export const MLFLOW_INVENTORY_TIMEOUT_MS = 15_000

function mlflowPending(): SettingsState["mlflow"] {
  return {
    status: "pending",
    installed: null,
    importable: null,
    destinations: [],
    detail: "",
  }
}

interface SettingsState {
  // Row limit
  rowLimit: number
  setRowLimit: (limit: number) => void

  streamingChunkSize: number
  setStreamingChunkSize: (size: number) => void

  // Open/closed section states (keyed by section ID, e.g. "optimiser.advanced")
  openSections: Record<string, boolean>
  toggleSection: (key: string) => void
  isSectionOpen: (key: string, defaultOpen?: boolean) => boolean

  // MLflow destinations inventory (fetched once, shared by all panels)
  mlflow: {
    /**
     * `"ready"` once the inventory arrived, whatever the probes said;
     * `"error"` when the package is missing or unimportable or the request
     * failed, with the reason in `detail`.
     */
    status: "pending" | "ready" | "error"
    installed: boolean | null
    importable: boolean | null
    /** The three wire entries, in backend order. */
    destinations: MlflowDestinationEntry[]
    detail: string
  }
  _mlflowFetching: boolean
  _mlflowLastAttempt: number
  fetchMlflow: () => void
  /** Reset to pending and refetch — call after PUT /api/mlflow/settings. */
  invalidateMlflow: () => void

  // Source system
  sources: string[]
  activeSource: string
  setSources: (sources: string[]) => void
  setActiveSource: (source: string) => void
  addSource: (name: string) => AddSourceResult
  removeSource: (name: string) => void

  // File listing cache (keyed by "dir|extensions")
  fileListCache: Record<string, { items: FileListItem[]; fetchedAt: number }>
  setFileListCache: (key: string, items: FileListItem[]) => void
  getFileListCache: (key: string) => FileListItem[] | null
}

const useSettingsStore = create<SettingsState>()((set, get) => ({
  // Row limit
  rowLimit: 100,
  setRowLimit: (limit) => set({ rowLimit: limit }),

  streamingChunkSize: 500_000,
  setStreamingChunkSize: (size) => set({
    streamingChunkSize: Math.min(MAX_STREAMING_CHUNK_SIZE, Math.max(MIN_STREAMING_CHUNK_SIZE, Math.round(size))),
  }),

  // Open/closed sections
  openSections: {},
  toggleSection: (key) => set((s) => ({
    openSections: { ...s.openSections, [key]: !s.openSections[key] },
  })),
  isSectionOpen: (key, defaultOpen = false) => {
    const val = get().openSections[key]
    // undefined means use default; stored value is "isOpen"
    return val === undefined ? defaultOpen : val
  },

  // MLflow inventory — fetched once on first call, shared by all panels
  mlflow: mlflowPending(),
  _mlflowFetching: false,
  _mlflowLastAttempt: 0,
  fetchMlflow: () => {
    const state = get()
    // Allow fetch if pending, or if errored and cooldown (10s) has elapsed
    const canRetry =
      state.mlflow.status === "error" &&
      Date.now() - state._mlflowLastAttempt >= 10_000
    if (_mlflowFetchingGuard) return
    if (state.mlflow.status !== "pending" && !canRetry) return
    _mlflowFetchingGuard = true
    set({ _mlflowFetching: true, _mlflowLastAttempt: Date.now() })
    let timeoutId: ReturnType<typeof setTimeout> | undefined
    const timeout = new Promise<never>((_, reject) => {
      timeoutId = setTimeout(
        () => reject(new Error(
          `MLflow inventory check timed out after ${MLFLOW_INVENTORY_TIMEOUT_MS / 1000}s`,
        )),
        MLFLOW_INVENTORY_TIMEOUT_MS,
      )
    })
    Promise.race([getMlflowDestinations(true), timeout])
      .then((data) => {
        // The probes' verdicts live in the entries; only the package facts
        // decide whether the inventory itself is usable.
        const usable = data.mlflow_installed && data.mlflow_importable
        set({
          mlflow: {
            status: usable ? "ready" : "error",
            installed: data.mlflow_installed,
            importable: data.mlflow_importable,
            destinations: data.destinations,
            detail: data.detail,
          },
        })
      })
      .catch((e) => {
        console.warn("MLflow inventory check failed:", e)
        set({
          mlflow: {
            ...mlflowPending(),
            status: "error",
            detail: e instanceof Error ? e.message : "MLflow inventory check failed",
          },
        })
      })
      .finally(() => {
        clearTimeout(timeoutId)
        _mlflowFetchingGuard = false
        set({ _mlflowFetching: false })
        if (_mlflowRefetchQueued) {
          // An invalidation arrived while this fetch was in flight: the
          // response just stored is potentially stale, so reset and fetch
          // exactly once more.
          _mlflowRefetchQueued = false
          set({ mlflow: mlflowPending() })
          get().fetchMlflow()
        }
      })
  },
  invalidateMlflow: () => {
    if (_mlflowFetchingGuard) {
      _mlflowRefetchQueued = true
      return
    }
    set({ mlflow: mlflowPending() })
    get().fetchMlflow()
  },

  // Source system
  sources: ["live"],
  activeSource: "live",
  setSources: (sources) => set((s) => ({
    sources,
    // Reset activeSource to "live" if it no longer exists in the new sources list (Issue #9)
    activeSource: sources.includes(s.activeSource) ? s.activeSource : "live",
  })),
  setActiveSource: (source) => set({ activeSource: source }),
  addSource: (name) => {
    // Mint the persisted source key through portableKey, not an ad-hoc fold.
    // Case is preserved; punctuation/word separators may still converge, so
    // the occupied-key check below is part of this operation's correctness.
    // Keys already persisted in sidecars are read back as opaque strings, so
    // previously-saved sources are unaffected.
    //
    // Rejections return a discriminated reason (not a bare null) so the caller
    // can surface WHY the add failed instead of closing the form silently.
    if (!name.trim()) return { ok: false, reason: "empty" }
    const key = portableKey(name)
    const current = get().sources
    if (current.includes(key)) return { ok: false, reason: "duplicate", key }
    set({ sources: [...current, key] })
    return { ok: true, key }
  },
  removeSource: (name) => set((s) => {
    if (name === "live") return s
    const next = s.sources.filter((sc) => sc !== name)
    return {
      sources: next,
      activeSource: s.activeSource === name ? "live" : s.activeSource,
    }
  }),

  // File listing cache
  fileListCache: {},
  setFileListCache: (key, items) => set((s) => ({
    fileListCache: { ...s.fileListCache, [key]: { items, fetchedAt: Date.now() } },
  })),
  getFileListCache: (key) => {
    const entry = get().fileListCache[key]
    if (!entry) return null
    // Expire after 30s — file system can change
    if (Date.now() - entry.fetchedAt > 30_000) return null
    return entry.items
  },
}))

export default useSettingsStore

/**
 * The MLflow inventory as display code wants it: the store's `"pending"`
 * becomes `"loading"` here and nowhere else — the store itself never uses
 * that word.
 */
export function useMlflowDestinations(): MlflowInventoryState {
  const mlflow = useSettingsStore((s) => s.mlflow)
  return {
    status: mlflow.status === "pending" ? "loading" as const : mlflow.status,
    installed: mlflow.installed,
    importable: mlflow.importable,
    destinations: mlflow.destinations,
    detail: mlflow.detail,
  }
}
