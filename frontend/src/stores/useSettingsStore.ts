/**
 * Zustand store for application-level settings and caches:
 *   - Row limit (preview configuration)
 *   - MLflow connection status (fetched once, shared by all panels)
 *   - Source system (data source routing)
 *   - Collapsible section states (persisted across panel mounts)
 *   - File listing cache (short-lived FS cache for file browsers)
 *
 * These are "global settings" that panels and hooks read but that don't
 * directly control layout or chrome visibility.
 */
import { create } from "zustand"
import { checkMlflow } from "../api/client"

let _mlflowFetchingGuard = false

interface SettingsState {
  // Row limit
  rowLimit: number
  setRowLimit: (limit: number) => void

  // Open/closed section states (keyed by section ID, e.g. "optimiser.advanced")
  openSections: Record<string, boolean>
  toggleSection: (key: string) => void
  isSectionOpen: (key: string, defaultOpen?: boolean) => boolean

  // MLflow status cache (fetched once, shared by all panels)
  mlflow: { status: "pending" | "connected" | "error"; backend: string; host: string }
  _mlflowFetching: boolean
  _mlflowLastAttempt: number
  fetchMlflow: () => void

  // Source system
  sources: string[]
  activeSource: string
  setSources: (sources: string[]) => void
  setActiveSource: (source: string) => void
  addSource: (name: string) => string | null
  removeSource: (name: string) => void

  // File listing cache (keyed by "dir|extensions")
  fileListCache: Record<string, { items: { name: string; path: string; type: "file" | "directory"; size?: number }[]; fetchedAt: number }>
  setFileListCache: (key: string, items: { name: string; path: string; type: "file" | "directory"; size?: number }[]) => void
  getFileListCache: (key: string) => { name: string; path: string; type: "file" | "directory"; size?: number }[] | null
}

const useSettingsStore = create<SettingsState>()((set, get) => ({
  // Row limit
  rowLimit: 100,
  setRowLimit: (limit) => set({ rowLimit: limit }),

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
  mlflow: { status: "pending", backend: "", host: "" },
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
      timeoutId = setTimeout(() => reject(new Error("MLflow check timed out after 5s")), 5_000)
    })
    Promise.race([checkMlflow(), timeout])
      .then((data) => {
        if (data.mlflow_installed) {
          set({ mlflow: { status: "connected", backend: data.backend || "local", host: data.databricks_host || "" } })
        } else {
          set({ mlflow: { status: "error", backend: "", host: "" } })
        }
      })
      .catch((e) => {
        console.warn("MLflow check failed:", e)
        set({ mlflow: { status: "error", backend: "", host: "" } })
      })
      .finally(() => {
        clearTimeout(timeoutId)
        _mlflowFetchingGuard = false
        set({ _mlflowFetching: false })
      })
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
    const trimmed = name.trim().toLowerCase().replace(/\s+/g, "_")
    const current = get().sources
    if (!trimmed || current.includes(trimmed)) return null
    set({ sources: [...current, trimmed] })
    return trimmed
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
    mlflowBackend: mlflow.backend,
  }
}
