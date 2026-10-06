/**
 * Zustand store for application-level settings and caches:
 *   - Row limit (preview configuration)
 *   - Pipeline settings (the project's .haute/pipeline-settings.json: chunk
 *     rows, caching, cache size, preview memory, kept-free memory, time limits)
 *   - MLflow destinations inventory (fetched once, shared by all panels)
 *   - Source system (data source routing)
 *   - Collapsible section states (persisted across panel mounts)
 *   - File listing cache (short-lived FS cache for file browsers)
 *
 * These are "global settings" that panels and hooks read but that don't
 * directly control layout or chrome visibility.
 */
import { create } from "zustand"
import { getMlflowDestinations, getPipelineSettings, patchPipelineSettings } from "../api/client"
import { apiErrorMessage } from "../api/errors"
import type {
  FileListItem,
  MlflowDestinationEntry,
  PipelineSettingsResponse,
  PipelineSettingsValues,
} from "../api/types"
import type { MlflowInventoryState } from "../utils/mlflowDestinations"
import { portableKey } from "../utils/portableKey"
import useToastStore from "./useToastStore"

export const MIN_CHUNK_ROWS = 1000
export const MAX_CHUNK_ROWS = 10_000_000

export type PipelineSettingKey = keyof PipelineSettingsValues

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

// Settings requests can overlap (a load still in flight when the user commits,
// the modal reopened during a save, or Enter followed by blur). A load never
// overrides a pending save or settings a save confirmed after the load began,
// only the latest save's response decides what is displayed, and saves reach
// the server in order.
let _settingsLoadSeq = 0
let _settingsSaveSeq = 0
let _settingsConfirmations = 0
let _settingsSaves: Promise<void> = Promise.resolve()

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

  /** The project's pipeline settings and each key's automatic figure; null until loaded. */
  pipelineSettings: PipelineSettingsResponse | null
  /** Why the last load failed, until a load succeeds. */
  pipelineSettingsError: string | null
  /** Loads the settings — call when the settings pane opens. Reopened while a
   *  save is in flight, it waits for that save's outcome instead. */
  loadPipelineSettings: () => Promise<void>
  /** Shows the new value at once and saves that one key (`null` restores
   *  automatic). On failure it restores the last confirmed settings and toasts. */
  savePipelineSetting: <K extends PipelineSettingKey>(
    key: K,
    value: PipelineSettingsValues[K],
  ) => Promise<void>
  /** The last settings the server confirmed; a failed save restores them. */
  _confirmedPipelineSettings: PipelineSettingsResponse | null
  /** The value each queued save will send, so a repeat is not sent twice. */
  _pendingPipelineSettings: Partial<PipelineSettingsValues>

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

  pipelineSettings: null,
  pipelineSettingsError: null,
  _confirmedPipelineSettings: null,
  _pendingPipelineSettings: {},
  loadPipelineSettings: async () => {
    if (Object.keys(get()._pendingPipelineSettings).length > 0) {
      // The pending saves' outcome is what to show; loading would race it.
      await _settingsSaves
      return
    }
    const request = ++_settingsLoadSeq
    const confirmations = _settingsConfirmations
    try {
      const settings = await getPipelineSettings()
      // A save confirmed since this load began holds newer settings.
      if (confirmations !== _settingsConfirmations) return
      set({ _confirmedPipelineSettings: settings })
      // A pending save shows its own value; its failure restores these.
      if (request !== _settingsLoadSeq || Object.keys(get()._pendingPipelineSettings).length > 0) {
        return
      }
      set({ pipelineSettings: settings, pipelineSettingsError: null })
    } catch (e) {
      if (request !== _settingsLoadSeq || Object.keys(get()._pendingPipelineSettings).length > 0) {
        return
      }
      set({
        pipelineSettings: null,
        pipelineSettingsError: apiErrorMessage(e, "Could not load the pipeline settings."),
      })
    }
  },
  savePipelineSetting: async (key, value) => {
    const state = get()
    const shown = state.pipelineSettings
    // Nothing is saved before the settings have loaded: the pane keeps every
    // field disabled until then.
    if (shown === null) return
    const pending = state._pendingPipelineSettings
    const alreadySaving = key in pending && pending[key] === value
    const alreadySaved = !(key in pending)
      && state._confirmedPipelineSettings?.settings[key] === value
    if (alreadySaving || alreadySaved) {
      await _settingsSaves
      return
    }
    const request = ++_settingsSaveSeq
    set({
      pipelineSettings: { ...shown, settings: { ...shown.settings, [key]: value } },
      _pendingPipelineSettings: { ...pending, [key]: value },
    })
    const settle = () => {
      // This save no longer waits; a later save of the same key still does.
      const current = get()._pendingPipelineSettings
      if (key in current && current[key] === value) {
        const rest = { ...current }
        delete rest[key]
        set({ _pendingPipelineSettings: rest })
      }
    }
    const save = async () => {
      try {
        const settings = await patchPipelineSettings({ [key]: value })
        _settingsConfirmations += 1
        set({ _confirmedPipelineSettings: settings })
        settle()
        // Only the latest save's response is displayed: it holds every
        // earlier save's key too, because saves reach the server in order.
        if (request === _settingsSaveSeq) {
          set({ pipelineSettings: settings, _pendingPipelineSettings: {} })
        }
      } catch (e) {
        settle()
        // Every failed save is reported: another key's later save does not
        // retry this one.
        useToastStore.getState().addToast(
          "error",
          apiErrorMessage(e, "Could not save the pipeline settings."),
        )
        if (request === _settingsSaveSeq) {
          set({
            pipelineSettings: get()._confirmedPipelineSettings,
            _pendingPipelineSettings: {},
          })
        }
      }
    }
    _settingsSaves = _settingsSaves.then(save)
    await _settingsSaves
  },

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
