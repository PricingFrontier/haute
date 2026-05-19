import { useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, Play, XCircle } from "lucide-react"

import { cancelExplore, runExplore } from "../api/client"
import type { ExploreStatusResponse } from "../api/types"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ExplorePreviewPane } from "../stores/useUIStore"
import { NODE_GROUP_COLORS } from "../theme/colors"
import { buildGraph } from "../utils/buildGraph"
import DataPreview, { type PreviewData } from "./DataPreview"
import type { SimpleEdge, SimpleNode } from "./editors"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs from "./PreviewPanelTabs"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "./previewPanelLayout"

type ExplorePreviewProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  previewData?: PreviewData | null
  onCellClick?: (rowIndex: number, column: string, rowValues?: Record<string, unknown>) => void
  tracedCell?: { rowIndex: number; column: string } | null
}

const EXPLORE_PREVIEW_PANES = [
  { key: "preview", label: "Preview" },
  { key: "overview", label: "Overview" },
  { key: "relationships", label: "Relationships" },
  { key: "charts", label: "Charts" },
] as const satisfies readonly { key: ExplorePreviewPane; label: string }[]

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
  previewData,
  onCellClick,
  tracedCell,
}: ExplorePreviewProps) {
  const nodeId = node.id
  const nodeLabel = String(node.data.label || node.id)
  const nodeType = node.data.nodeType
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
  const rememberedPane = useUIStore((s) => s.explorePreviewPanes[nodeId])
  const setExplorePreviewPane = useUIStore((s) => s.setExplorePreviewPane)

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
  const progress = status?.status === "completed" ? 1 : (status?.progress ?? (submitting ? 0.03 : 0))
  const activePane = rememberedPane ?? "preview"
  const activePaneMeta = EXPLORE_PREVIEW_PANES.find((pane) => pane.key === activePane) ?? EXPLORE_PREVIEW_PANES[0]

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

  const actions = exploreJob ? (
    <button
      type="button"
      onClick={handleCancel}
      className={PREVIEW_PANEL_ACTION_BUTTON_CLASS}
      style={{ color: "var(--danger)", background: "var(--danger-soft)", border: "1px solid var(--danger-border)" }}
    >
      <XCircle size={12} className="shrink-0" />
      <span className="truncate">Cancel</span>
    </button>
  ) : (
    <button
      type="button"
      onClick={handleRun}
      disabled={!hasInput || isBusy}
      className={`${PREVIEW_PANEL_ACTION_BUTTON_CLASS} disabled:opacity-45 disabled:cursor-not-allowed`}
      style={{ color: "var(--text-on-accent)", background: NODE_GROUP_COLORS.explore }}
    >
      {submitting ? <Loader2 size={12} className="shrink-0 animate-spin" /> : <Play size={12} className="shrink-0" />}
      <span className="truncate">{report ? "Re-cache full data" : "Process & cache full data"}</span>
    </button>
  )

  return (
    <PreviewPanelFrame
      nodeLabel={nodeLabel}
      nodeType={nodeType}
      subtitle={`${activeSource} | ${statusMessage(status, submitting)}`}
      actions={actions}
      data-testid="explore-preview-frame"
    >
      {isBusy && (
        <div className="h-1 w-full shrink-0" style={{ background: "var(--accent-soft)" }}>
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${Math.max(progress * 100, 2)}%`, background: NODE_GROUP_COLORS.explore }}
          />
        </div>
      )}

      <PreviewPanelTabs
        tabs={EXPLORE_PREVIEW_PANES}
        activeTab={activePane}
        onChange={(pane) => setExplorePreviewPane(nodeId, pane)}
        ariaLabel="Explore result panes"
        accentColor={NODE_GROUP_COLORS.explore}
        idPrefix="explore-preview"
        equalWidth
      />

      <div className="flex-1 min-h-0 flex flex-col" data-testid="explore-preview-body">
        <div
          id={`explore-preview-${activePaneMeta.key}-pane`}
          role="tabpanel"
          aria-labelledby={`explore-preview-${activePaneMeta.key}-tab`}
          className="flex-1 min-h-0 flex flex-col"
          data-testid={`explore-preview-${activePaneMeta.key}-pane`}
        >
          {activePane === "preview" ? (
            <DataPreview
              data={previewData ?? null}
              onCellClick={onCellClick}
              tracedCell={tracedCell}
              embedded
            />
          ) : null}
        </div>
      </div>
    </PreviewPanelFrame>
  )
}
