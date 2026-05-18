import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, Play, Search, XCircle } from "lucide-react"

import { cancelExplore, runExplore } from "../api/client"
import type { ExploreStatusResponse } from "../api/types"
import { useDragResize } from "../hooks/useDragResize"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import { NODE_GROUP_COLORS } from "../theme/colors"
import { buildGraph } from "../utils/buildGraph"
import type { SimpleEdge, SimpleNode } from "./editors"

type ExplorePreviewProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
}

function statusMessage(status: ExploreStatusResponse | null, submitting: boolean): string {
  if (submitting) return "Starting"
  if (!status) return "Ready"
  if (status.status === "completed") return "Cached"
  if (status.status === "running") return "Caching"
  if (status.status === "cancelled") return "Cancelled"
  if (status.status === "error") return "Error"
  return status.status
}

export default function ExplorePreview({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
}: ExplorePreviewProps) {
  const nodeId = node.id
  const nodeLabel = String(node.data.label || node.id)
  const activeSource = useSettingsStore((s) => s.activeSource)
  const streamingChunkSize = useSettingsStore((s) => s.streamingChunkSize)
  const structuralVersion = useGraphStore((s) => s.structuralVersion)
  const addToast = useToastStore((s) => s.addToast)
  const exploreJob = useNodeResultsStore((s) => s.exploreJobs[nodeId])
  const cachedResult = useNodeResultsStore((s) => s.exploreResults[nodeId])
  const startExploreJob = useNodeResultsStore((s) => s.startExploreJob)
  const updateExploreProgress = useNodeResultsStore((s) => s.updateExploreProgress)
  const completeExploreJob = useNodeResultsStore((s) => s.completeExploreJob)
  const failExploreJob = useNodeResultsStore((s) => s.failExploreJob)
  const touchExplorePreview = useNodeResultsStore((s) => s.touchExplorePreview)
  const { height, containerRef, onDragStart } = useDragResize({ initialHeight: 200, minHeight: 140, maxHeight: 480 })

  const [submitting, setSubmitting] = useState(false)

  const hasInput = useMemo(() => edges.some((edge) => edge.target === nodeId), [edges, nodeId])
  const configHash = useMemo(
    () => hashConfig({
      config: node.data.config ?? {},
      source: activeSource,
      structuralVersion,
    }),
    [activeSource, node.data.config, structuralVersion],
  )
  const report = cachedResult?.result ?? null
  const status = exploreJob?.progress ?? cachedResult?.terminalStatus ?? null
  const isBusy = submitting || !!exploreJob
  const progress = status?.status === "completed" ? 1 : status?.progress ?? (submitting ? 0.03 : 0)

  useEffect(() => {
    touchExplorePreview(nodeId)
  }, [nodeId, report, touchExplorePreview])

  const handleRun = useCallback(async () => {
    if (!hasInput || isBusy) return
    setSubmitting(true)
    try {
      const response = await runExplore({
        graph: buildGraph(allNodes, edges, submodels, preamble),
        node_id: nodeId,
        source: activeSource,
        streamingChunkSize,
      })
      if (response.status === "completed") {
        if (!response.result) throw new Error("Explore completed without a cache report")
        const jobId = response.job_id ?? `cached:${nodeId}`
        startExploreJob(nodeId, jobId, nodeLabel, configHash, activeSource, structuralVersion)
        completeExploreJob(nodeId, response.result, {
          status: "completed",
          progress: 1,
          message: response.cached ? "Cached" : response.message,
          result: response.result,
          terminal_reason: "completed",
        })
        addToast("success", `${response.cached ? "Explore cache hit" : "Explore cached"}: ${nodeLabel}`)
        return
      }
      if (!response.job_id) throw new Error("Explore job did not return a job id")
      startExploreJob(nodeId, response.job_id, nodeLabel, configHash, activeSource, structuralVersion)
      updateExploreProgress(nodeId, {
        status: "running",
        progress: 0.05,
        message: response.message || "Caching",
        result: null,
      })
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      startExploreJob(nodeId, `startup-failure:${nodeId}`, nodeLabel, configHash, activeSource, structuralVersion)
      failExploreJob(nodeId, message, {
        status: "error",
        progress: 1,
        message,
        result: null,
        terminal_reason: "startup_failure",
      })
      addToast("error", `Explore failed: ${message}`)
    } finally {
      setSubmitting(false)
    }
  }, [
    activeSource,
    addToast,
    allNodes,
    completeExploreJob,
    configHash,
    edges,
    failExploreJob,
    hasInput,
    isBusy,
    nodeId,
    nodeLabel,
    preamble,
    startExploreJob,
    streamingChunkSize,
    structuralVersion,
    submodels,
    updateExploreProgress,
  ])

  const handleCancel = useCallback(async () => {
    if (!exploreJob) return
    try {
      const cancelled = await cancelExplore(exploreJob.jobId)
      if (cancelled.status === "completed" && cancelled.result) {
        completeExploreJob(nodeId, cancelled.result, cancelled)
      } else {
        failExploreJob(nodeId, cancelled.message || "Cancelled", cancelled)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      failExploreJob(nodeId, message)
      addToast("error", `Explore cancel failed: ${message}`)
    }
  }, [addToast, completeExploreJob, exploreJob, failExploreJob, nodeId])

  return (
    <div
      ref={containerRef}
      style={{ height, borderTop: "1px solid var(--border)", background: "var(--bg-panel)" }}
      className="flex flex-col shrink-0 relative"
    >
      <div
        onMouseDown={onDragStart}
        className="drag-handle-hover absolute top-0 left-0 right-0 h-1 cursor-ns-resize z-10"
      />

      {isBusy && (
        <div className="h-1 w-full shrink-0" style={{ background: "var(--accent-soft)" }}>
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${Math.max(progress * 100, 2)}%`, background: NODE_GROUP_COLORS.explore }}
          />
        </div>
      )}

      <div
        className="min-h-10 flex items-center gap-2 px-4 py-2 shrink-0"
        style={{ borderBottom: "1px solid var(--border)", background: "var(--bg-elevated)" }}
      >
        <Search size={15} style={{ color: NODE_GROUP_COLORS.explore }} />
        <div className="min-w-0">
          <div className="text-xs font-bold truncate" style={{ color: "var(--text-primary)" }}>{nodeLabel}</div>
          <div className="text-[10px] tabular-nums" style={{ color: "var(--text-muted)" }}>
            {activeSource} | {statusMessage(status, submitting)}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {exploreJob ? (
            <button
              type="button"
              onClick={handleCancel}
              className="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-semibold"
              style={{ color: "var(--danger)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
            >
              <XCircle size={12} />
              Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={handleRun}
              disabled={!hasInput || isBusy}
              className="inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-semibold disabled:opacity-45 disabled:cursor-not-allowed"
              style={{ color: "var(--text-on-accent)", background: NODE_GROUP_COLORS.explore }}
            >
              {submitting ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
              {report ? "Re-cache full data" : "Process & cache full data"}
            </button>
          )}
        </div>
      </div>

      {/* Content area is intentionally empty in v1 — future EDA work
          (overview, charts, relationships) renders here. */}
      <div className="flex-1 min-h-0" data-testid="explore-preview-body" />
    </div>
  )
}
