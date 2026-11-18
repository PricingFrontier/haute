import { useState, useRef, useCallback, useEffect } from "react"
import {
  getExperiments,
  getRuns,
  getModels,
  getModelVersions,
} from "../api/client"
import { apiErrorMessage } from "../api/errors"
import { useMlflowDestinations } from "../stores/useSettingsStore"
import { answer, newest, under } from "../utils/answers"
import {
  effectiveMlflowDestination,
  mlflowDestinationEntry,
} from "../utils/mlflowDestinations"
import { useRequestKind } from "./useRequestKind"

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
 * lands after a scope change, or after a newer request for the same picker, is
 * dropped, and the picker stops loading the moment it is.
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
 * One picker's data plus the scope it belongs to. `scope: null` means "never
 * fetched": no real scope compares equal to it.
 */
interface ScopedFetch<T> {
  scope: string | null
  items: T[]
  loading: boolean
  error: string
}

function emptyFetch<T>(scope: string | null = null): ScopedFetch<T> {
  return { scope, items: [], loading: false, error: "" }
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
  // The experiment browsed belongs to the stored experiment it was chosen over: an Undo,
  // a Redo or any other change of the node's experiment lets it go, so the runs listed
  // are the stored experiment's.
  const [browse, setBrowse] = useState(() => ({ scope, id: initialExpId, over: initialExpId }))

  // Fetch guards -- only fetch once per scope, not on every focus
  const fetchedExperiments = useRef<string | null>(null)
  const fetchedModels = useRef<string | null>(null)
  const fetchedRunsFor = useRef<{ scope: string | null; expId: string }>({ scope: null, expId: "" })
  const fetchedVersionsFor = useRef<{ scope: string | null; model: string }>({ scope: null, model: "" })
  // Per picker: a completion writes only while it is still the newest request
  // that picker started, in the scope it was started in.
  const experimentsAsked = useRequestKind()
  const runsAsked = useRequestKind()
  const modelsAsked = useRequestKind()
  const versionsAsked = useRequestKind()
  const inScopeAsked = useRequestKind()
  useEffect(() => () => inScopeAsked.bump(), [scope, inScopeAsked])

  const inScope = <T,>(state: ScopedFetch<T>): boolean => state.scope === scope

  const experiments = inScope(experimentsState) ? experimentsState.items : NO_EXPERIMENTS
  const runs = inScope(runsState) ? runsState.items : NO_RUNS
  const models = inScope(modelsState) ? modelsState.items : NO_MODELS
  const modelVersions = inScope(versionsState) ? versionsState.items : NO_VERSIONS
  const modelVersionsFor = inScope(versionsState) ? versionsState.for : ""
  const browseExpId = browse.scope !== scope ? "" : browse.over === initialExpId ? browse.id : initialExpId

  const setBrowseExpId = useCallback<React.Dispatch<React.SetStateAction<string>>>((value) => {
    setBrowse((prev) => {
      const current = prev.scope !== scope ? "" : prev.over === initialExpId ? prev.id : initialExpId
      return { scope, id: typeof value === "function" ? value(current) : value, over: initialExpId }
    })
  }, [scope, initialExpId])

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
    const belongsTo = [newest(experimentsAsked), under(inScopeAsked)] as const
    setExperimentsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<Experiment>(scope)),
      scope,
      loading: true,
      error: "",
    }))
    void answer(belongsTo, getExperiments(destination), {
      ok: (data) => setExperimentsState((prev) => ({ ...prev, items: Array.isArray(data) ? data : [] })),
      failed: (e) => {
        if (fetchedExperiments.current === scope) fetchedExperiments.current = null
        setExperimentsState((prev) => ({ ...prev, items: [], error: apiErrorMessage(e, "Failed to load experiments") }))
      },
    }, { pending: (loading) => setExperimentsState((prev) => ({ ...prev, loading })) })
  }, [destination, experimentsAsked, inScopeAsked, scope])

  const refreshRuns = useCallback((expId: string) => {
    if (!expId) return
    const guard = fetchedRunsFor.current
    if (guard.scope === scope && guard.expId === expId) return
    fetchedRunsFor.current = { scope, expId }
    const belongsTo = [newest(runsAsked), under(inScopeAsked)] as const
    setRunsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<Run>(scope)),
      scope,
      loading: true,
      error: "",
    }))
    void answer(belongsTo, getRuns(expId, runTag, destination), {
      ok: (data) => setRunsState((prev) => ({ ...prev, items: Array.isArray(data) ? data : [] })),
      failed: (e) => {
        const current = fetchedRunsFor.current
        if (current.scope === scope && current.expId === expId) {
          fetchedRunsFor.current = { scope: null, expId: "" }
        }
        setRunsState((prev) => ({ ...prev, items: [], error: apiErrorMessage(e, "Failed to load runs") }))
      },
    }, { pending: (loading) => setRunsState((prev) => ({ ...prev, loading })) })
  }, [destination, inScopeAsked, runTag, runsAsked, scope])

  const refreshModels = useCallback(() => {
    if (fetchedModels.current === scope) return
    fetchedModels.current = scope
    const belongsTo = [newest(modelsAsked), under(inScopeAsked)] as const
    setModelsState((prev) => ({
      ...(prev.scope === scope ? prev : emptyFetch<RegisteredModel>(scope)),
      scope,
      loading: true,
      error: "",
    }))
    void answer(belongsTo, getModels(destination), {
      ok: (data) => setModelsState((prev) => ({ ...prev, items: Array.isArray(data) ? data : [] })),
      failed: (e) => {
        if (fetchedModels.current === scope) fetchedModels.current = null
        setModelsState((prev) => ({ ...prev, items: [], error: apiErrorMessage(e, "Failed to load models") }))
      },
    }, { pending: (loading) => setModelsState((prev) => ({ ...prev, loading })) })
  }, [destination, inScopeAsked, modelsAsked, scope])

  const refreshVersions = useCallback((modelName: string) => {
    if (!modelName) return
    const guard = fetchedVersionsFor.current
    if (guard.scope === scope && guard.model === modelName) return
    fetchedVersionsFor.current = { scope, model: modelName }
    const belongsTo = [newest(versionsAsked), under(inScopeAsked)] as const
    setVersionsState({
      scope,
      for: modelName,
      items: [],
      loading: true,
      error: "",
    })
    void answer(belongsTo, getModelVersions(modelName, destination), {
      ok: (data) => setVersionsState((prev) => ({ ...prev, items: Array.isArray(data) ? data : [] })),
      failed: (e) => {
        const current = fetchedVersionsFor.current
        if (current.scope === scope && current.model === modelName) {
          fetchedVersionsFor.current = { scope: null, model: "" }
        }
        setVersionsState((prev) => ({ ...prev, items: [], error: apiErrorMessage(e, "Failed to load versions") }))
      },
    }, { pending: (loading) => setVersionsState((prev) => ({ ...prev, loading })) })
  }, [destination, inScopeAsked, scope, versionsAsked])

  const resetRunsGuard = useCallback(() => {
    fetchedRunsFor.current = { scope: null, expId: "" }
    // Retire any runs request still in flight, so its response cannot land on
    // the experiment the caller just moved away from.
    runsAsked.bump()
  }, [runsAsked])

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
