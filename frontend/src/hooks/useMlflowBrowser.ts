import { useState, useRef, useCallback } from "react"
import {
  getExperiments,
  getRuns,
  getModels,
  getModelVersions,
} from "../api/client"
import { apiErrorMessage } from "../api/errors"
import { useMlflowDestinations } from "../stores/useSettingsStore"
import {
  effectiveMlflowDestination,
  mlflowDestinationEntry,
} from "../utils/mlflowDestinations"

/**
 * Shared hook for lazy-loading MLflow dropdown data (experiments, runs,
 * registered models, model versions) from ONE destination.
 *
 * Used by ModelScoreEditor, OptimiserApplyEditor and the modelling Train
 * pane to avoid duplicating identical state management and fetch logic.
 *
 * Every request carries the caller's `destination` (`""` = local folder), so the
 * pickers list only that backend's content. Everything the hook holds is
 * stamped with the scope it was fetched under — the effective destination key
 * plus the destination string the inventory resolves it to, and a generation
 * that makes an A→B→A switch a new scope rather than the old one. A scope
 * change therefore empties the arrays, guards, errors and loading flags and
 * clears `browseExpId` by derivation, with no effect to run and no window in
 * which the previous backend's content is still on screen. A response that
 * lands after a scope change (or after a newer request for the same picker)
 * carries an older request number and is dropped.
 *
 * @param opts.runTag      - Optional artifact filter passed to `getRuns` (e.g. "optimiser")
 * @param opts.initialExpId - Pre-selected experiment id to initialize browseExpId
 * @param opts.destination - The node's stored `mlflow_destination` (`""` = local folder)
 */

export type Experiment = { experiment_id: string; name: string }
export type Run = {
  run_id: string
  run_name: string
  metrics: Record<string, number>
  params?: Record<string, string>
  artifacts: string[]
}
export type RegisteredModel = { name: string; latest_versions: { version: string; status: string; run_id: string }[] }
export type ModelVersion = {
  version: string
  run_id: string
  status: string
  description: string
  params?: Record<string, string>
  aliases?: string[]
}

export interface MlflowBrowserOptions {
  runTag?: string
  initialExpId?: string
  /** The node's stored `mlflow_destination`: `""` (local folder) or a destination key. */
  destination: string
}

export interface MlflowBrowserState {
  experiments: Experiment[]
  runs: Run[]
  models: RegisteredModel[]
  modelVersions: ModelVersion[]
  modelVersionsFor: string
  loadingExperiments: boolean
  loadingRuns: boolean
  loadingModels: boolean
  loadingVersions: boolean
  errorExperiments: string
  errorRuns: string
  errorModels: string
  errorVersions: string
  browseExpId: string
  setBrowseExpId: React.Dispatch<React.SetStateAction<string>>
  setRuns: React.Dispatch<React.SetStateAction<Run[]>>
  refreshExperiments: () => void
  refreshRuns: (expId: string) => void
  refreshModels: () => void
  refreshVersions: (modelName: string) => void
  /** Reset the fetch guard for runs so the next refreshRuns call re-fetches. */
  resetRunsGuard: () => void
}

/**
 * One picker's data plus the scope it belongs to and the request that filled
 * it. `scope: null` means "never fetched": no real scope compares equal to it.
 */
interface ScopedFetch<T> {
  scope: string | null
  request: number
  items: T[]
  loading: boolean
  error: string
}

function emptyFetch<T>(scope: string | null = null): ScopedFetch<T> {
  return { scope, request: 0, items: [], loading: false, error: "" }
}

// Stable empties, so a consumer memoising on `runs` or `models` does not see a
// new array identity on every render while the picker is out of scope.
const NO_EXPERIMENTS: Experiment[] = []
const NO_RUNS: Run[] = []
const NO_MODELS: RegisteredModel[] = []
const NO_VERSIONS: ModelVersion[] = []

export function useMlflowBrowser(opts: MlflowBrowserOptions): MlflowBrowserState {
  const runTag = opts.runTag
  const initialExpId = opts.initialExpId ?? ""
  const destination = opts.destination

  // The backend this node browses: the remote it names, else the local folder,
  // and the destination string that key currently points at (so a repointed
  // server counts as a different backend).
  const inventory = useMlflowDestinations()
  const effectiveKey = effectiveMlflowDestination(destination)
  const entry = mlflowDestinationEntry(inventory.destinations, effectiveKey)
  const scopeKey = `${effectiveKey}|${entry?.destination ?? ""}`

  // A generation makes every *transition* a new scope, so switching A→B→A
  // re-fetches instead of re-showing what A held before the detour.
  const [tracked, setTracked] = useState(() => ({ key: scopeKey, generation: 0 }))
  const generation = tracked.key === scopeKey ? tracked.generation : tracked.generation + 1
  if (tracked.key !== scopeKey) setTracked({ key: scopeKey, generation })
  const scope = `${generation}:${scopeKey}`

  // Lazy-loaded dropdown data -- fetched on focus only
  const [experimentsState, setExperimentsState] = useState<ScopedFetch<Experiment>>(emptyFetch)
  const [runsState, setRunsState] = useState<ScopedFetch<Run>>(emptyFetch)
  const [modelsState, setModelsState] = useState<ScopedFetch<RegisteredModel>>(emptyFetch)
  const [versionsState, setVersionsState] = useState<ScopedFetch<ModelVersion> & { for: string }>(
    () => ({ ...emptyFetch<ModelVersion>(), for: "" }),
  )
  const [browse, setBrowse] = useState(() => ({ scope, id: initialExpId }))

  // Fetch guards -- only fetch once per scope, not on every focus
  const fetchedExperiments = useRef<string | null>(null)
  const fetchedModels = useRef<string | null>(null)
  const fetchedRunsFor = useRef<{ scope: string | null; expId: string }>({ scope: null, expId: "" })
  const fetchedVersionsFor = useRef<{ scope: string | null; model: string }>({ scope: null, model: "" })
  // Monotonic per picker: a completion writes only while it is still the
  // newest request that picker started.
  const experimentsRequest = useRef(0)
  const runsRequest = useRef(0)
  const modelsRequest = useRef(0)
  const versionsRequest = useRef(0)

  const inScope = <T,>(state: ScopedFetch<T>): boolean => state.scope === scope

  const experiments = inScope(experimentsState) ? experimentsState.items : NO_EXPERIMENTS
  const runs = inScope(runsState) ? runsState.items : NO_RUNS
  const models = inScope(modelsState) ? modelsState.items : NO_MODELS
  const modelVersions = inScope(versionsState) ? versionsState.items : NO_VERSIONS
  const modelVersionsFor = inScope(versionsState) ? versionsState.for : ""
  const browseExpId = browse.scope === scope ? browse.id : ""

  const setBrowseExpId = useCallback<React.Dispatch<React.SetStateAction<string>>>((value) => {
    setBrowse((prev) => {
      const current = prev.scope === scope ? prev.id : ""
      return { scope, id: typeof value === "function" ? value(current) : value }
    })
  }, [scope])

  const setRuns = useCallback<React.Dispatch<React.SetStateAction<Run[]>>>((value) => {
    setRunsState((prev) => {
      const current = prev.scope === scope ? prev : emptyFetch<Run>(scope)
      return {
        ...current,
        items: typeof value === "function" ? value(current.items) : value,
      }
    })
  }, [scope])

  const refreshExperiments = useCallback(() => {
    if (fetchedExperiments.current === scope) return
    fetchedExperiments.current = scope
    const request = ++experimentsRequest.current
    setExperimentsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<Experiment>(scope)),
      scope,
      request,
      loading: true,
      error: "",
    }))
    getExperiments(destination)
      .then((data) => {
        setExperimentsState((prev) => prev.request === request
          ? { ...prev, items: Array.isArray(data) ? data : [], loading: false }
          : prev)
      })
      .catch((e: Error) => {
        if (fetchedExperiments.current === scope) fetchedExperiments.current = null
        setExperimentsState((prev) => prev.request === request
          ? { ...prev, items: [], loading: false, error: apiErrorMessage(e, "Failed to load experiments") }
          : prev)
      })
  }, [destination, scope])

  const refreshRuns = useCallback((expId: string) => {
    if (!expId) return
    const guard = fetchedRunsFor.current
    if (guard.scope === scope && guard.expId === expId) return
    fetchedRunsFor.current = { scope, expId }
    const request = ++runsRequest.current
    setRunsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<Run>(scope)),
      scope,
      request,
      loading: true,
      error: "",
    }))
    getRuns(expId, runTag, destination)
      .then((data) => {
        setRunsState((prev) => prev.request === request
          ? { ...prev, items: Array.isArray(data) ? data : [], loading: false }
          : prev)
      })
      .catch((e: Error) => {
        const current = fetchedRunsFor.current
        if (current.scope === scope && current.expId === expId) {
          fetchedRunsFor.current = { scope: null, expId: "" }
        }
        setRunsState((prev) => prev.request === request
          ? { ...prev, items: [], loading: false, error: apiErrorMessage(e, "Failed to load runs") }
          : prev)
      })
  }, [destination, runTag, scope])

  const refreshModels = useCallback(() => {
    if (fetchedModels.current === scope) return
    fetchedModels.current = scope
    const request = ++modelsRequest.current
    setModelsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<RegisteredModel>(scope)),
      scope,
      request,
      loading: true,
      error: "",
    }))
    getModels(destination)
      .then((data) => {
        setModelsState((prev) => prev.request === request
          ? { ...prev, items: Array.isArray(data) ? data : [], loading: false }
          : prev)
      })
      .catch((e: Error) => {
        if (fetchedModels.current === scope) fetchedModels.current = null
        setModelsState((prev) => prev.request === request
          ? { ...prev, items: [], loading: false, error: apiErrorMessage(e, "Failed to load models") }
          : prev)
      })
  }, [destination, scope])

  const refreshVersions = useCallback((modelName: string) => {
    if (!modelName) return
    const guard = fetchedVersionsFor.current
    if (guard.scope === scope && guard.model === modelName) return
    fetchedVersionsFor.current = { scope, model: modelName }
    const request = ++versionsRequest.current
    setVersionsState({
      scope,
      request,
      for: modelName,
      items: [],
      loading: true,
      error: "",
    })
    getModelVersions(modelName, destination)
      .then((data) => {
        setVersionsState((prev) => prev.request === request
          ? { ...prev, items: Array.isArray(data) ? data : [], loading: false }
          : prev)
      })
      .catch((e: Error) => {
        const current = fetchedVersionsFor.current
        if (current.scope === scope && current.model === modelName) {
          fetchedVersionsFor.current = { scope: null, model: "" }
        }
        setVersionsState((prev) => prev.request === request
          ? { ...prev, items: [], loading: false, error: apiErrorMessage(e, "Failed to load versions") }
          : prev)
      })
  }, [destination, scope])

  const resetRunsGuard = useCallback(() => {
    fetchedRunsFor.current = { scope: null, expId: "" }
    // Retire any runs request still in flight, so its response cannot land on
    // the experiment the caller just moved away from.
    setRunsState((prev) => ({ ...prev, request: ++runsRequest.current }))
  }, [])

  return {
    experiments,
    runs,
    models,
    modelVersions,
    modelVersionsFor,
    loadingExperiments: inScope(experimentsState) && experimentsState.loading,
    loadingRuns: inScope(runsState) && runsState.loading,
    loadingModels: inScope(modelsState) && modelsState.loading,
    loadingVersions: inScope(versionsState) && versionsState.loading,
    errorExperiments: inScope(experimentsState) ? experimentsState.error : "",
    errorRuns: inScope(runsState) ? runsState.error : "",
    errorModels: inScope(modelsState) ? modelsState.error : "",
    errorVersions: inScope(versionsState) ? versionsState.error : "",
    browseExpId,
    setBrowseExpId,
    setRuns,
    refreshExperiments,
    refreshRuns,
    refreshModels,
    refreshVersions,
    resetRunsGuard,
  }
}
