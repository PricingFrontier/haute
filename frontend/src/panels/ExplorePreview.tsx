import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react"
import { Loader2, Play, XCircle } from "lucide-react"

import { cancelExplore, getExploreCacheSnapshot, runExplore } from "../api/client"
import type {
  ExploreCacheReport,
  ExploreCacheSnapshotResponse,
  ExploreStatusResponse,
} from "../api/types"
import useGraphStore from "../stores/useGraphStore"
import useNodeResultsStore, { hashConfig } from "../stores/useNodeResultsStore"
import useSettingsStore from "../stores/useSettingsStore"
import useToastStore from "../stores/useToastStore"
import useUIStore, { type ExplorePreviewPane } from "../stores/useUIStore"
import { NODE_GROUP_COLORS } from "../theme/colors"
import { buildGraph } from "../utils/buildGraph"
import ExecutionDiagnosticsSummary from "../components/ExecutionDiagnosticsSummary"
import DataPreview, { type PreviewData } from "./DataPreview"
import type { SimpleEdge, SimpleNode } from "./editors"
import { buildExploreCacheIdentity } from "./explore/cacheIdentity"
import PreviewPanelFrame from "./PreviewPanelFrame"
import PreviewPanelTabs from "./PreviewPanelTabs"
import { PREVIEW_PANEL_ACTION_BUTTON_CLASS } from "./previewPanelLayout"

const ExploreOverviewPane = lazy(() => import("./explore/ExploreOverviewPane"))
const ExplorePivotsPane = lazy(() => import("./explore/ExplorePivotsPane"))
const ExploreChartsPane = lazy(() => import("./explore/ExploreChartsPane"))

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
  { key: "pivots", label: "Pivots" },
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

type IdleCacheState = "current" | "stale" | "missing"

/**
 * The cache state shown while no job is running: an authoritative snapshot
 * answer wins; while an inspection is still in flight the client falls back
 * to what it retains locally rather than flashing "missing".
 */
function deriveIdleCacheState({
  cacheState,
  completedByThisMount,
  report,
  hasRetainedResult,
}: {
  cacheState: ExploreCacheSnapshotResponse["state"] | "checking" | "error"
  completedByThisMount: boolean
  report: ExploreCacheReport | null
  hasRetainedResult: boolean
}): IdleCacheState {
  if (completedByThisMount || cacheState === "current") return "current"
  if (cacheState === "stale") return "stale"
  if (cacheState === "missing" || cacheState === "error") return "missing"
  // The snapshot request is still checking this identity.
  if (report) return "current"
  return hasRetainedResult ? "stale" : "missing"
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
  const cacheIdentity = useMemo(
    () => buildExploreCacheIdentity({ node, allNodes, edges, submodels, preamble }),
    [allNodes, edges, node, preamble, submodels],
  )
  const configHash = useMemo(
    () => hashConfig({ graph: cacheIdentity, source: activeSource }),
    [activeSource, cacheIdentity],
  )
  const [cacheSnapshot, setCacheSnapshot] = useState<{
    configHash: string
    state: ExploreCacheSnapshotResponse["state"] | "checking" | "error"
  }>({ configHash, state: "checking" })
  const [runJobId, setRunJobId] = useState<string | null>(null)
  const cacheState = !hasInput
    ? "missing"
    : cacheSnapshot.configHash === configHash
      ? cacheSnapshot.state
      : "checking"
  const currentExploreJob = exploreJob ?? null
  const hasActiveExploreJob = currentExploreJob !== null
  const currentCachedResult =
    cachedResult && cachedResult.configHash === configHash
      ? cachedResult
      : null
  const completedByThisMount = !!(
    currentCachedResult?.result
    && currentCachedResult.terminalStatus?.status === "completed"
    && runJobId === currentCachedResult.jobId
  )
  const report = completedByThisMount || cacheState === "current" || cacheState === "checking"
    ? (currentCachedResult?.result ?? null)
    : null
  const idleCacheState = deriveIdleCacheState({
    cacheState,
    completedByThisMount,
    report,
    hasRetainedResult: Boolean(cachedResult?.result),
  })
  const status = currentExploreJob?.progress ?? (report ? currentCachedResult?.terminalStatus : null) ?? null
  const isBusy = submitting || !!currentExploreJob
  const progress = status?.status === "completed" ? 1 : (status?.progress ?? (submitting ? 0.03 : 0))
  const progressPercent = Math.min(Math.max(progress * 100, 0), 100)
  const activePane =
    rememberedPane === "overview" || rememberedPane === "pivots" || rememberedPane === "charts"
      ? rememberedPane
      : "preview"
  const activePaneMeta = EXPLORE_PREVIEW_PANES.find((pane) => pane.key === activePane) ?? EXPLORE_PREVIEW_PANES[0]
  const statusSource = currentExploreJob?.source ?? activeSource

  useEffect(() => {
    if (!hasInput || hasActiveExploreJob) return
    const controller = new AbortController()
    void getExploreCacheSnapshot({
      graph: buildGraph(allNodes, edges, submodels, preamble),
      node_id: nodeId,
      source: activeSource,
      streamingChunkSize,
      signal: controller.signal,
    }).then((snapshot) => {
      if (controller.signal.aborted) return
      if (snapshot.state === "current") {
        if (!snapshot.result) throw new Error("Current Explore cache status omitted its report")
        startExploreJob(nodeId, `cache-status:${nodeId}`, nodeLabel, configHash, activeSource, structuralVersion)
        completeExploreJob(nodeId, snapshot.result, {
          status: "completed",
          progress: 1,
          message: "Cached",
          result: snapshot.result,
          terminal_reason: "completed",
        })
      }
      setCacheSnapshot({ configHash, state: snapshot.state })
    }).catch((err: unknown) => {
      if (controller.signal.aborted || (err instanceof Error && err.name === "AbortError")) return
      const message = err instanceof Error ? err.message : String(err)
      setCacheSnapshot({ configHash, state: "error" })
      addToast("error", `Explore cache inspection failed: ${message}`)
    })
    return () => controller.abort()
    // Gated on the Explore cache identity hash rather than the graph objects:
    // any render that keeps the same configHash captures a graph snapshot and
    // active source whose data-affecting parts are identical (the hash covers
    // the source and node labels), so unrelated edits such as node drags or
    // downstream structural changes do not re-fire the inspection request,
    // while an identity change re-runs it with the fresh capture in the same
    // render. The structuralVersion recorded by a hydration is therefore the
    // one captured at the last identity change; Explore results are staleness-
    // gated by configHash, not structuralVersion, so that capture is safe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [addToast, completeExploreJob, configHash, hasActiveExploreJob, hasInput, nodeId, nodeLabel, startExploreJob, streamingChunkSize])

  useEffect(() => {
    if (report) touchExplorePreview(nodeId)
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
        refresh: cacheState === "error" || idleCacheState !== "missing",
      })
      if (response.status === "completed") {
        if (!response.result) throw new Error("Explore completed without a cache report")
        const jobId = response.job_id ?? `cached:${nodeId}`
        setRunJobId(jobId)
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
      setRunJobId(response.job_id)
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
    idleCacheState,
    cacheState,
    startExploreJob,
    streamingChunkSize,
    structuralVersion,
    submodels,
    updateExploreProgress,
  ])

  const handleCancel = useCallback(async () => {
    if (!currentExploreJob) return
    try {
      const cancelled = await cancelExplore(currentExploreJob.jobId)
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
  }, [addToast, completeExploreJob, currentExploreJob, failExploreJob, nodeId])

  const cacheButtonStyle = idleCacheState === "current"
    ? { color: "var(--text-on-accent)", background: "var(--success-fill)" }
    : idleCacheState === "stale"
      ? { color: "var(--text-on-light-accent)", background: "var(--warning-strong)" }
      : { color: "var(--text-on-accent)", background: "var(--danger-solid)" }
  const actions = currentExploreJob ? (
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
      style={cacheButtonStyle}
    >
      {submitting ? <Loader2 size={12} className="shrink-0 animate-spin" /> : <Play size={12} className="shrink-0" />}
      <span className="truncate">{idleCacheState === "missing" ? "Needs caching" : "Re-cache"}</span>
    </button>
  )

  return (
    <PreviewPanelFrame
      nodeLabel={nodeLabel}
      nodeType={nodeType}
      subtitle={`${statusSource} | ${isBusy ? statusMessage(status, submitting) : idleCacheState === "current" ? "Cached" : idleCacheState === "stale" ? "Cache stale" : "Needs caching"}`}
      actions={actions}
      data-testid="explore-preview-frame"
    >
      {isBusy && (
        <div
          role="progressbar"
          aria-label="Explore run progress"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={progressPercent}
          className="h-1 w-full shrink-0"
          style={{ background: "var(--accent-soft)" }}
        >
          <div
            className="h-full transition-all duration-300"
            style={{ width: `${Math.max(progressPercent, 2)}%`, background: NODE_GROUP_COLORS.explore }}
          />
        </div>
      )}

      <ExecutionDiagnosticsSummary
        metrics={status?.execution_metrics ?? report?.execution_metrics}
        status={status?.status}
        terminalReason={status?.terminal_reason}
      />

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
          ) : (
            <Suspense
              fallback={
                <div
                  role="status"
                  className="flex flex-1 items-center justify-center text-xs"
                  style={{ color: "var(--text-muted)" }}
                >
                  Loading {activePane}…
                </div>
              }
            >
              {activePane === "overview" ? (
                <ExploreOverviewPane node={node} report={report} />
              ) : activePane === "pivots" ? (
                <ExplorePivotsPane
                  node={node}
                  allNodes={allNodes}
                  edges={edges}
                  submodels={submodels}
                  preamble={preamble}
                  report={report}
                />
              ) : (
                <ExploreChartsPane
                  node={node}
                  allNodes={allNodes}
                  edges={edges}
                  submodels={submodels}
                  preamble={preamble}
                  report={report}
                />
              )}
            </Suspense>
          )}
        </div>
      </div>
    </PreviewPanelFrame>
  )
}
