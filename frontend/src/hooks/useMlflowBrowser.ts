import { useState, useRef, useCallback, useEffect } from "react"
import {
  getExperiments,
  getRuns,
  getModels,
  getModelVersions,
  ApiError,
} from "../api/client"

/**
 * Shared hook for lazy-loading MLflow dropdown data (experiments, runs,
 * registered models, model versions).
 *
 * Used by ModelScoreEditor and OptimiserApplyEditor to avoid duplicating
 * ~90 lines of identical state management and fetch logic.
 *
 * @param opts.runTag    - Optional artifact filter passed to `getRuns` (e.g. "optimiser")
 * @param opts.initialExpId - Pre-selected experiment id to initialize browseExpId
 */

export type Experiment = { experiment_id: string; name: string }
export type Run = { run_id: string; run_name: string; metrics: Record<string, number>; artifacts: string[] }
export type RegisteredModel = { name: string; latest_versions: { version: string; status: string; run_id: string }[] }
export type ModelVersion = { version: string; run_id: string; status: string; description: string }

export interface MlflowBrowserState {
  experiments: Experiment[]
  runs: Run[]
  models: RegisteredModel[]
  modelVersions: ModelVersion[]
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

export function useMlflowBrowser(opts?: { runTag?: string; initialExpId?: string }): MlflowBrowserState {
  const runTag = opts?.runTag
  const initialExpId = opts?.initialExpId ?? ""

  // Lazy-loaded dropdown data -- fetched on focus only
  const [experiments, setExperiments] = useState<Experiment[]>([])
  const [runs, setRuns] = useState<Run[]>([])
  const [models, setModels] = useState<RegisteredModel[]>([])
  const [modelVersions, setModelVersions] = useState<ModelVersion[]>([])

  const [loadingExperiments, setLoadingExperiments] = useState(false)
  const [loadingRuns, setLoadingRuns] = useState(false)
  const [loadingModels, setLoadingModels] = useState(false)
  const [loadingVersions, setLoadingVersions] = useState(false)

  const [errorExperiments, setErrorExperiments] = useState("")
  const [errorRuns, setErrorRuns] = useState("")
  const [errorModels, setErrorModels] = useState("")
  const [errorVersions, setErrorVersions] = useState("")

  const [browseExpId, setBrowseExpId] = useState(initialExpId)

  // Fetch guards -- only fetch once per mount, not on every focus
  const fetchedExperiments = useRef(false)
  const fetchedModels = useRef(false)
  const fetchedRunsFor = useRef("")
  const fetchedVersionsFor = useRef("")

  // Reset all fetch guards on mount so data is re-fetched after remount
  useEffect(() => {
    fetchedExperiments.current = false
    fetchedModels.current = false
    fetchedRunsFor.current = ""
    fetchedVersionsFor.current = ""
  }, [])

  const errorMsg = (e: Error) => e instanceof ApiError ? e.detail || e.message : e.message

  const refreshExperiments = useCallback(() => {
    if (fetchedExperiments.current) return
    fetchedExperiments.current = true
    setLoadingExperiments(true)
    setErrorExperiments("")
    getExperiments()
      .then((data) => { setExperiments(Array.isArray(data) ? data : []); setLoadingExperiments(false) })
      .catch((e: Error) => { setExperiments([]); setLoadingExperiments(false); setErrorExperiments(errorMsg(e) || "Failed to load experiments"); fetchedExperiments.current = false })
  }, [])

  const refreshRuns = useCallback((expId: string) => {
    if (!expId) return
    if (fetchedRunsFor.current === expId) return
    fetchedRunsFor.current = expId
    setLoadingRuns(true)
    setErrorRuns("")
    getRuns(expId, runTag)
      .then((data) => { setRuns(Array.isArray(data) ? data : []); setLoadingRuns(false) })
      .catch((e: Error) => { setRuns([]); setLoadingRuns(false); setErrorRuns(errorMsg(e) || "Failed to load runs"); fetchedRunsFor.current = "" })
  }, [runTag])

  const refreshModels = useCallback(() => {
    if (fetchedModels.current) return
    fetchedModels.current = true
    setLoadingModels(true)
    setErrorModels("")
    getModels()
      .then((data) => { setModels(Array.isArray(data) ? data : []); setLoadingModels(false) })
      .catch((e: Error) => { setModels([]); setLoadingModels(false); setErrorModels(errorMsg(e) || "Failed to load models"); fetchedModels.current = false })
  }, [])

  const refreshVersions = useCallback((modelName: string) => {
    if (!modelName) return
    if (fetchedVersionsFor.current === modelName) return
    fetchedVersionsFor.current = modelName
    setLoadingVersions(true)
    setErrorVersions("")
    getModelVersions(modelName)
      .then((data) => { setModelVersions(Array.isArray(data) ? data : []); setLoadingVersions(false) })
      .catch((e: Error) => { setModelVersions([]); setLoadingVersions(false); setErrorVersions(errorMsg(e) || "Failed to load versions"); fetchedVersionsFor.current = "" })
  }, [])

  const resetRunsGuard = useCallback(() => {
    fetchedRunsFor.current = ""
  }, [])

  return {
    experiments,
    runs,
    models,
    modelVersions,
    loadingExperiments,
    loadingRuns,
    loadingModels,
    loadingVersions,
    errorExperiments,
    errorRuns,
    errorModels,
    errorVersions,
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
