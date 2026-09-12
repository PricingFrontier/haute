/**
 * Zustand store for application-level settings and caches:
 *   - Row limit (preview configuration)
 *   - Streaming chunk size (rows per streaming chunk for pipeline execution)
 *   - MLflow connection status (fetched once, shared by all panels)
 *   - Source system (data source routing)
 *   - Collapsible section states (persisted across panel mounts)
 *   - File listing cache (short-lived FS cache for file browsers)
 *
 * These are "global settings" that panels and hooks read but that don't
 * directly control layout or chrome visibility.
 */
import { create } from "zustand"
import { getMlflowStatus } from "../api/client"
import type { FileListItem } from "../api/types"
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

const MLFLOW_PENDING = {
  status: "pending" as const,
  mode: "",
  destination: "",
  configSource: "",
  installed: null,
  importable: null,
  configured: null,
  detail: "",
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

  // MLflow status cache (fetched once, shared by all panels)
  mlflow: {
    status: "pending" | "connected" | "error"
    /** Resolved backend mode: "" | "databricks" | "server" | "local". */
    mode: string
    /** Human-readable destination: workspace host, server URI, or runs folder. */
    destination: string
    /** Which precedence tier decided: "" | "toml" | "env" | "default". */
    configSource: string
    installed: boolean | null
    importable: boolean | null
    configured: boolean | null
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

  // MLflow status cache — fetched once on first call, shared by all panels
  mlflow: { ...MLFLOW_PENDING },
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
      timeoutId = setTimeout(() => reject(new Error("MLflow status check timed out after 5s")), 5_000)
    })
    Promise.race([getMlflowStatus(), timeout])
      .then((data) => {
        const connected = data.mlflow_installed && data.mlflow_importable && data.configured
        set({
          mlflow: {
            status: connected ? "connected" : "error",
            mode: data.mode || "",
            destination: data.destination || "",
            configSource: data.config_source || "",
            installed: data.mlflow_installed,
            importable: data.mlflow_importable,
            configured: data.configured,
            detail: data.detail || "",
          },
        })
      })
      .catch((e) => {
        console.warn("MLflow status check failed:", e)
        set({
          mlflow: {
            ...MLFLOW_PENDING,
            status: "error",
            detail: e instanceof Error ? e.message : "MLflow status check failed",
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
          set({ mlflow: { ...MLFLOW_PENDING } })
          get().fetchMlflow()
        }
      })
  },
  invalidateMlflow: () => {
    if (_mlflowFetchingGuard) {
      _mlflowRefetchQueued = true
      return
    }
    set({ mlflow: { ...MLFLOW_PENDING } })
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

/** Derive MLflow connection status for panel display (maps "pending" -> "loading"). */
export function useMlflowStatus() {
  const mlflow = useSettingsStore((s) => s.mlflow)
  return {
    mlflowStatus: mlflow.status === "pending" ? "loading" as const : mlflow.status,
    mlflowMode: mlflow.mode,
    mlflowDestination: mlflow.destination,
    mlflowConfigSource: mlflow.configSource,
    mlflowInstalled: mlflow.installed,
    mlflowImportable: mlflow.importable,
    mlflowConfigured: mlflow.configured,
    mlflowDetail: mlflow.detail,
  }
}
