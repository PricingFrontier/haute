/** Durable stages outlive transient jobs; view choices never change an applied recipe. */
import { useCallback, useEffect, useState, useRef } from "react"
import { create } from "zustand"
import { persist } from "zustand/middleware"
import { readStages, inspectStagePath, applyStagePath, updateStagePreview, updateStageCheckpoint, restoreFinalStage, type TrainModelArgs } from "../api/client"
import { waitForJob } from "../hooks/jobPollingController"
import { apiErrorMessage } from "../api/errors"
import type { StageState } from "../types/trainGuards"
import useDocumentStatusStore, { captureDocumentExecutionFence, documentExecutionKey, isDocumentExecutionFenceCurrent } from "../stores/useDocumentStatusStore"
import { hashConfig } from "../utils/configHash"
import { INTERACTIVE_MODEL_KEYS, applyModelUpdate, interactiveModels, modelConfig } from "./interactiveModels"
import type { TrainResponse } from "../api/types"
import { selectedPathPoint, type AvailablePath } from "./pathSelection"
import { EMPTY_RECIPE, stageConfiguration, type PostFitStage } from "./stageSettings"
import type { ModelStage } from "./stageSettings"

/** Match the stage-training draft without committing edits when a saved view opens. */
export function stagedRequest(args: TrainModelArgs): TrainModelArgs {
  if (!args.model) throw new Error("Staged training requires a selected model")
  const modelId = args.model
  return { ...args, graph: { ...args.graph, nodes: args.graph.nodes.map((node) => {
    if (node.id !== args.node_id) return node
    const config = node.data.config as Record<string, unknown>
    const model = interactiveModels(config).find((entry) => entry.id === modelId)
    if (!model) throw new Error("The selected model is no longer available")
    const draft = stageConfiguration(modelConfig(config, model))
    return { ...node, data: { ...node.data, config: applyModelUpdate(config, modelId, draft) } }
  }) } }
}

let inspectionSequence = 0
/**
 * Path points already inspected, by model, applied revision, artifact, path and point: a
 * point's result never changes, so going back to a stage's path shows it at once.
 */
const inspected = new Map<string, TrainResponse>()
const INSPECTED_LIMIT = 16
function rememberInspection(identity: string, response: TrainResponse) {
  inspected.delete(identity)
  inspected.set(identity, response)
  if (inspected.size > INSPECTED_LIMIT) inspected.delete(inspected.keys().next().value!)
}
export interface StagedView {
  actionBusy?: boolean
  pathStage?: PostFitStage
  adoption?: { revision: string; stage: PostFitStage }
  inspection?: { requestId: number; currentRevision: string; artifactRevision: string; pathId: string;
    pointId: string; loading: boolean; response?: TrainResponse; error?: string }

  state?: StageState
  error?: string
  visible?: ModelStage[]
  tableStage?: ModelStage | "candidate"
  /** The stage Explain and Fit show; the applied one when unset. */
  diagnosticStage?: ModelStage
  comparison?: { stage: ModelStage; parent: ModelStage }
  finalExport?: { revision: string; jobId: string }
}
interface StagedViews {
  models: Record<string, StagedView>
  preferences: Record<string, Pick<StagedView, "visible" | "tableStage">>
  update: (key: string, update: Partial<StagedView>) => void
}
function preferenceKey(key: string): string {
  const [document, resultKey] = JSON.parse(key) as [string, string]
  const [sourceFile] = JSON.parse(document) as [string]
  return JSON.stringify([sourceFile, resultKey])
}

export const useStagedViews = create<StagedViews>()(persist((set) => ({
  models: {},
  preferences: {},
  update: (key, update) => set((value) => {
    const stableKey = preferenceKey(key)
    const previous = value.models[key]?.state
    const incoming = update.state
    const state = incoming === undefined ? previous : {
      ...incoming,
      current: incoming.current?.revision === previous?.current?.revision ? previous?.current ?? null : incoming.current,
      preview: incoming.preview?.revision === previous?.preview?.revision ? previous?.preview ?? null : incoming.preview,
    }
    const changedRevision = incoming !== undefined && (incoming.current?.revision !== previous?.current?.revision || incoming.preview?.revision !== previous?.preview?.revision)
    const model = { ...value.preferences[stableKey], ...value.models[key], ...update, state,
      ...(changedRevision ? { comparison: undefined, inspection: undefined } : {}),
      ...(incoming !== undefined && incoming.job?.id !== previous?.job?.id ? { finalExport: undefined } : {}),
    }
    return {
      models: { ...value.models, [key]: model },
      preferences: "visible" in update || "tableStage" in update ? {
        ...value.preferences, [stableKey]: { visible: model.visible, tableStage: model.tableStage },
      } : value.preferences,
    }
  }),
}), { name: "haute-staged-model-views", partialize: (state) => ({ preferences: state.preferences }) }))


export function useStagedViewKey(resultKey: string): string {
  const document = useDocumentStatusStore(documentExecutionKey)
  return JSON.stringify([document, resultKey])
}

export function useStagedTraining({ enabled, resultKey, request, jobId }: {
  enabled: boolean
  resultKey: string
  request: () => TrainModelArgs
  jobId?: string
}) {
  const key = useStagedViewKey(resultKey)
  const view = useStagedViews((state) => state.models[key])
  const busy = view?.actionBusy ?? false
  const setBusy = useCallback((actionBusy: boolean) => useStagedViews.getState().update(key, { actionBusy }), [key])
  const [refreshKey, setRefreshKey] = useState(0)
  const refresh = useCallback(() => setRefreshKey((value) => value + 1), [])
  useEffect(() => {
    if (!enabled) return
    const controller = new AbortController()
    const fence = captureDocumentExecutionFence()
    const timer = window.setTimeout(() => {
      void Promise.resolve().then(() => readStages({ ...stagedRequest(request()), signal: controller.signal })).then((state) => {
        if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
        useStagedViews.getState().update(key, { state, error: undefined })
      }).catch((error: unknown) => {
        if (controller.signal.aborted || !isDocumentExecutionFenceCurrent(fence)) return
        useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
      })
    }, 100)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [enabled, key, request, jobId, refreshKey])

  const running = view?.state?.job?.status === "running" || view?.state?.next_preview?.status === "queued"
  useEffect(() => {
    if (!enabled || !running || jobId !== undefined) return
    const controller = new AbortController()
    const fence = captureDocumentExecutionFence()
    void waitForJob({
      poll: async (signal) => {
        const state = await readStages({ ...stagedRequest(request()), signal })
        if (!signal.aborted && isDocumentExecutionFenceCurrent(fence)) {
          useStagedViews.getState().update(key, { state, error: undefined })
        }
        return state
      },
      isTerminal: (state) => state.job?.status !== "running" && state.next_preview?.status !== "queued",
      intervalMs: 750,
      signal: controller.signal,
    }).then((state) => {
      if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) {
        useStagedViews.getState().update(key, { state, error: undefined })
      }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted && isDocumentExecutionFenceCurrent(fence)) {
        useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
      }
    })
    return () => controller.abort()
  }, [enabled, running, jobId, key, request])

  const inspect = useCallback(async (available: AvailablePath, pointId: string) => {
    const current = useStagedViews.getState().models[key]?.state?.current
    if (!current) return
    const fence = captureDocumentExecutionFence()
    const selection = { requestId: ++inspectionSequence, currentRevision: current.revision,
      artifactRevision: available.artifactRevision, pathId: available.path.id, pointId, loading: true }
    const identity = JSON.stringify([key, current.revision, available.artifactRevision, available.path.id, pointId])
    const known = inspected.get(identity)
    if (known) {
      useStagedViews.getState().update(key, { inspection: { ...selection, loading: false, response: known }, pathStage: available.path.stage })
      return
    }
    useStagedViews.getState().update(key, { inspection: selection, pathStage: available.path.stage })
    const isCurrent = () => isDocumentExecutionFenceCurrent(fence)
      && useStagedViews.getState().models[key]?.inspection?.requestId === selection.requestId
      && useStagedViews.getState().models[key]?.state?.current?.revision === current.revision
    try {
      const result = await inspectStagePath({ ...stagedRequest(request()), current_revision: current.revision,
        artifact_revision: available.artifactRevision, path_id: available.path.id, point_id: pointId })
      if (!isCurrent()) return
      if (result.current_revision !== current.revision || result.artifact_revision !== available.artifactRevision
        || result.path_id !== available.path.id || result.point_id !== pointId) throw new Error("Path inspection identity changed")
      rememberInspection(identity, result.response)
      useStagedViews.getState().update(key, { inspection: { ...selection, loading: false, response: result.response } })
    } catch (error) {
      if (isCurrent()) useStagedViews.getState().update(key, { inspection: { ...selection, loading: false, error: apiErrorMessage(error) } })
    }
  }, [key, request])
  const applyPoint = useCallback(async () => {
    const view = useStagedViews.getState().models[key]
    const selected = selectedPathPoint(view)
    const current = view?.state?.current
    if (!selected || !current || view.inspection?.loading || view.inspection?.error) return null
    const fence = captureDocumentExecutionFence()
    setBusy(true)
    try {
      const next = await applyStagePath({ ...stagedRequest(request()), current_revision: current.revision,
        artifact_revision: selected.available.artifactRevision, path_id: selected.available.path.id,
        point_id: selected.pointId, prepare_next: true })
      if (!isDocumentExecutionFenceCurrent(fence)) return null
      useStagedViews.getState().update(key, { state: next, error: undefined, pathStage: undefined,
        adoption: next.current ? { revision: next.current.revision, stage: selected.available.path.stage } : undefined })
      return next
    } catch (error) {
      if (isDocumentExecutionFenceCurrent(fence)) useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
      return null
    } finally { setBusy(false) }
  }, [key, request, setBusy])
  const previewAction = useCallback(async (action: "apply" | "discard") => {
    if (action === "apply" && selectedPathPoint(useStagedViews.getState().models[key])) return applyPoint()
    const state = useStagedViews.getState().models[key]?.state
    const fence = captureDocumentExecutionFence()
    setBusy(true)
    try {
      if (!state?.current || !state.preview) throw new Error("The stage preview is no longer available. Refresh the model.")
      const next = await updateStagePreview(action, {
        ...stagedRequest(request()), current_revision: state.current.revision, preview_revision: state.preview.revision,
        ...(action === "apply" ? { prepare_next: true } : {}),
      })
      if (!isDocumentExecutionFenceCurrent(fence)) return null
      useStagedViews.getState().update(key, { state: next, error: undefined })
      return next
    } catch (error) {
      if (isDocumentExecutionFenceCurrent(fence)) useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
      return null
    } finally { setBusy(false) }
  }, [key, request, applyPoint, setBusy])
  const checkpointAction = useCallback(async (action: "save" | "restore" | "delete", value: string, onRestore: (updates: Record<string, unknown>) => unknown) => {
    const current = useStagedViews.getState().models[key]?.state?.current
    if (!current) return false
    const fence = captureDocumentExecutionFence()
    setBusy(true)
    try {
      const next = await updateStageCheckpoint({ ...stagedRequest(request()), action, current_revision: current.revision,
        ...(action === "save" ? { name: value } : { checkpoint_id: value }) })
      if (!isDocumentExecutionFenceCurrent(fence)) return false
      if (action === "restore") {
        const saved = next.current?.result.config
        if (!saved) throw new Error("The restored checkpoint has no model settings")
        const actual = next.current?.result.response.staged?.configuration
        const restored = Object.fromEntries(INTERACTIVE_MODEL_KEYS.map((field) => [field, saved[field]]))
        if (actual) restored.stages = { ...(saved.stages as Record<string, unknown> | undefined),
          applied_recipe: actual.applied_recipe }
        const result = onRestore(restored)
        if (result && typeof result === "object" && "ok" in result && result.ok === false) {
          throw new Error("The checkpoint was restored, but its settings could not be adopted. Reopen the model and restore again.")
        }
      }
      useStagedViews.getState().update(key, { state: next, error: undefined, ...(action === "restore" ? { finalExport: undefined } : {}) })
      refresh()
      return true
    } catch (error) {
      if (isDocumentExecutionFenceCurrent(fence)) useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
      return false
    } finally { setBusy(false) }
  }, [key, request, refresh, setBusy])
  const prepareExport = useCallback(async () => {
    const final = useStagedViews.getState().models[key]?.state?.final
    if (!final) return
    const fence = captureDocumentExecutionFence()
    setBusy(true)
    try {
      const response = await restoreFinalStage(stagedRequest(request()))
      if (!isDocumentExecutionFenceCurrent(fence)) return
      if (!response.job_id) throw new Error("The restored model has no export handle")
      useStagedViews.getState().update(key, { finalExport: { revision: final.revision, jobId: response.job_id }, error: undefined })
    } catch (error) {
      if (isDocumentExecutionFenceCurrent(fence)) useStagedViews.getState().update(key, { error: apiErrorMessage(error) })
    } finally { setBusy(false) }
  }, [key, request, setBusy])
  const finalJobId = view?.finalExport?.revision === view?.state?.final?.revision ? view?.finalExport?.jobId : undefined
  return { ...view, key, busy, refresh, inspect, applyPoint, previewAction, checkpointAction, prepareExport, finalJobId }
}


/** Every model publishes its applied recipe, even while another model's editor is open. */
export function useAppliedStageRecipe(state: StageState | undefined, config: Record<string, unknown>, onUpdate: (updates: Record<string, unknown>) => unknown, viewKey?: string) {
  const adoption = useStagedViews((views) => viewKey ? views.models[viewKey]?.adoption : undefined)
  const appliedRevision = useRef<string | null>(null)
  useEffect(() => {
    const applied = state?.current
    if (!applied || (appliedRevision.current === applied.revision && adoption?.revision !== applied.revision) || state?.stale_reason) return
    const actual = applied.result.response.staged?.configuration
    if (!actual) return
    const drafts = config.stages as Record<string, unknown> | undefined
    const selected: Record<string, unknown> = { applied_recipe: actual.applied_recipe }
    if (adoption?.revision === applied.revision) {
      const section = adoption.stage === "pruned" ? "pruning" : adoption.stage === "banded" ? "banding" : "smoothing"
      selected[section] = actual[section]
      const draftSettings = stageConfiguration(config).stages
      const actualSettings = stageConfiguration({ stages: actual }).stages
      selected.enabled = { ...draftSettings.enabled, [adoption.stage]: actualSettings.enabled[adoption.stage] }
      if (adoption.stage === "pruned") selected.path = actual.path
    }
    if (adoption?.revision === applied.revision || hashConfig(selected) !== hashConfig({ applied_recipe: drafts?.applied_recipe ?? EMPTY_RECIPE })) {
      const result = onUpdate({ stages: { ...drafts, ...selected } })
      if (result && typeof result === "object" && "ok" in result && result.ok === false) return
    }
    appliedRevision.current = applied.revision
    if (viewKey && adoption?.revision === applied.revision) useStagedViews.getState().update(viewKey, { adoption: undefined })
  }, [state, config, onUpdate, adoption, viewKey])
}
