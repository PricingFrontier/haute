import { useCallback, useEffect, useRef } from "react"
import { useGraph, useRequestScope } from "../panels/useGraph"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import { hashConfig } from "../utils/configHash"
import { trainingIdentityConfig } from "../utils/modellingExportConfig"
import { prepareCanvasRequest } from "../utils/requestGateway"
import { useModelResultKey } from "./modelRuns"
import { useStagedTraining, useAppliedStageRecipe } from "./useStagedTraining"

/** Non-selected models restore their overlays too, including a job running at reload. */
export function StageRestorer({ nodeId, modelId, config, onUpdate }: {
  nodeId: string; modelId: string; config: Record<string, unknown>; onUpdate: (updates: Record<string, unknown>) => unknown
}) {
  const scope = useRequestScope()
  const graph = useGraph()
  const source = useSettingsStore((state) => state.activeSource)
  const version = useGraphStore((state) => state.structuralVersion)
  const key = useModelResultKey(nodeId, modelId)
  const job = useNodeResultsStore((state) => state.trainJobs[key])
  const cached = useNodeResultsStore((state) => state.trainResults[key])
  const request = useCallback(() => {
    void graph.allNodes
    void graph.edges
    void version
    const target = prepareCanvasRequest(scope, nodeId)
    return { graph: target.graph, node_id: target.nodeId, model: modelId, source }
  // Graph edits refresh staleness; prepareCanvasRequest reads the canonical document.
  }, [scope, nodeId, modelId, source, graph.allNodes, graph.edges, version])
  const saved = useStagedTraining({ enabled: scope !== null, resultKey: key, request, jobId: job?.jobId })
  useAppliedStageRecipe(saved.state, config, onUpdate, saved.key)
  const resumed = useRef<string | null>(null)
  useEffect(() => {
    const pending = saved.state?.job
    if (job || pending?.status !== "running" || pending.id === resumed.current || pending.id === cached?.jobId) return
    resumed.current = pending.id
    useNodeResultsStore.getState().startTrainJob(key, pending.id, "Model training",
      hashConfig(trainingIdentityConfig(config)), source, version)
  }, [saved.state?.job, job, cached?.jobId, key, config, source, version])
  return null
}
