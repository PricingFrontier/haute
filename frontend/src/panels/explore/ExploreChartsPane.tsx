import { AlertTriangle, BarChart3, Loader2 } from "lucide-react"
import { useMemo } from "react"

import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import type { SimpleEdge, SimpleNode } from "../editors"
import ComboChart from "./ComboChart"
import {
  ExploreResultEmptyState,
  PivotRunStatusActions,
} from "./ExploreResultCardChrome"
import {
  parseExploreCharts,
  resolveExploreChartSource,
  type ExploreChartConfig,
} from "./chartConfig"
import { adaptPivotChartData, ChartDataError } from "./chartData"
import {
  isPivotResultFresh,
  parseExplorePivots,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"
import useAutoUpdateExplorePivots from "./useAutoUpdateExplorePivots"
import useExplorePivotActions from "./useExplorePivotActions"

type ExploreChartsPaneProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  report: ExploreCacheReport | null
}

function EmptyCharts({ children }: { children: string }) {
  return <ExploreResultEmptyState icon={BarChart3}>{children}</ExploreResultEmptyState>
}

function CardMessage({
  children,
  danger = false,
}: {
  children: React.ReactNode
  danger?: boolean
}) {
  return (
    <div
      role={danger ? "alert" : undefined}
      className="m-3 rounded-md px-3 py-4 text-center text-[11px] leading-relaxed"
      style={{
        color: danger ? "var(--danger)" : "var(--text-muted)",
        background: danger ? "var(--danger-soft)" : "var(--bg-panel)",
        border: "1px solid var(--border)",
      }}
    >
      {children}
    </div>
  )
}

function adapterFailureMessage(error: unknown): string {
  if (error instanceof ChartDataError) {
    return `${error.message} ${error.remediation}`
  }
  return error instanceof Error ? error.message : String(error)
}

type ChartCardProps = {
  chart: ExploreChartConfig
  pivot: ExplorePivotConfig | null
  missingPivotId: string | null
  nodeId: string
  report: ExploreCacheReport | null
  submitting: boolean
  notice?: { message: string; failure?: { remediation: string } | null }
  onRetry: (
    pivot: ExplorePivotConfig,
    requestedDataframeCacheKey?: string | null,
  ) => void
  onCancel: (pivot: ExplorePivotConfig, jobId: string) => void
}

function ChartCard({
  chart,
  pivot,
  missingPivotId,
  nodeId,
  report,
  submitting,
  notice,
  onRetry,
  onCancel,
}: ChartCardProps) {
  const key = pivot ? explorePivotResultKey(nodeId, pivot.id) : null
  const cached = useNodeResultsStore((state) =>
    key === null ? undefined : state.pivotResults[key],
  )
  const job = useNodeResultsStore((state) =>
    key === null ? undefined : state.pivotJobs[key],
  )
  const pivotResult = cached?.result ?? null
  const currentIdentity = pivot ? pivotCalculationIdentity(pivot) : null
  const fresh =
    currentIdentity !== null &&
    isPivotResultFresh(cached, report?.dataframe_cache_key, currentIdentity)
  const status = job?.progress
  const failure =
    status?.failure ?? (!job && !submitting ? cached?.terminalStatus?.failure : null)
  const storedError = !job && !submitting ? cached?.error : undefined
  const alertMessage = notice?.message ?? failure?.message ?? storedError
  const remediation = notice?.failure?.remediation ?? failure?.remediation

  const adaptation = useMemo(() => {
    if (!fresh || !pivot || !pivotResult) {
      return { data: null, error: null }
    }
    try {
      return {
        data: adaptPivotChartData(chart, pivot, pivotResult),
        error: null,
      }
    } catch (error) {
      return { data: null, error: adapterFailureMessage(error) }
    }
  }, [chart, fresh, pivot, pivotResult])

  return (
    <section
      role="region"
      aria-label={chart.name}
      data-testid="explore-chart-visualisation"
      className="flex min-h-52 flex-col overflow-hidden rounded-lg"
      style={{
        background: "var(--bg-input)",
        border: "1px solid var(--border)",
      }}
    >
      <div
        className="flex items-center gap-2 px-3 py-2"
        style={{ borderBottom: "1px solid var(--border)" }}
      >
        <BarChart3
          size={14}
          aria-hidden="true"
          style={{ color: NODE_GROUP_COLORS.explore }}
        />
        <h3
          className="mr-auto truncate text-xs font-semibold"
          style={{ color: "var(--text-primary)" }}
        >
          {chart.name}
        </h3>
        {pivot && pivot.values.length > 0 && (
          <PivotRunStatusActions
            activeJobId={job?.jobId ?? null}
            submitting={submitting}
            canRetry={Boolean(alertMessage)}
            onCancel={(jobId) => void onCancel(pivot, jobId)}
            onRetry={() =>
              void onRetry(pivot, report?.dataframe_cache_key ?? null)
            }
          />
        )}
      </div>

      {pivot === null && missingPivotId === null && (
        <CardMessage>Select a source Pivot in Configure.</CardMessage>
      )}
      {pivot === null && missingPivotId !== null && (
        <CardMessage danger>
          Source Pivot &quot;{missingPivotId}&quot; no longer exists. Reassign it in
          Configure.
        </CardMessage>
      )}
      {pivot && pivot.values.length === 0 && (
        <CardMessage>
          Add at least one Value in {pivot.name}&apos;s configuration.
        </CardMessage>
      )}
      {pivot && job && (
        <div
          role="status"
          className="flex items-center gap-2 px-3 py-3 text-[11px]"
          style={{ color: "var(--text-muted)" }}
        >
          <Loader2 size={13} className="animate-spin" aria-hidden="true" />
          {status?.message || "Calculating Pivot"}
        </div>
      )}
      {pivot && cached?.result && !fresh && (
        <CardMessage>
          {!report
            ? "Waiting for current cached Explore data."
            : alertMessage
              ? "The current source result was retained after refresh failed."
              : "Updating source Pivot automatically…"}
        </CardMessage>
      )}
      {pivot && !cached?.result && !job && !alertMessage && (
        <CardMessage>
          {report
            ? "Calculating source Pivot automatically…"
            : "Cache the full Explore data above to calculate this chart automatically."}
        </CardMessage>
      )}
      {alertMessage && (
        <CardMessage danger>
          {alertMessage}
          {remediation ? ` ${remediation}` : ""}
        </CardMessage>
      )}
      {adaptation.error && (
        <CardMessage danger>
          <span className="inline-flex items-start gap-1">
            <AlertTriangle size={13} aria-hidden="true" />
            {adaptation.error}
          </span>
        </CardMessage>
      )}
      {adaptation.data && <ComboChart chart={chart} data={adaptation.data} />}
    </section>
  )
}

export default function ExploreChartsPane({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  report,
}: ExploreChartsPaneProps) {
  const parsedCharts = useMemo(
    () => parseExploreCharts(node.data.config ?? {}),
    [node.data.config],
  )
  const parsedPivots = useMemo(
    () => parseExplorePivots(node.data.config ?? {}),
    [node.data.config],
  )
  const {
    cancelPivot,
    notices,
    submitting,
    updatePivot,
  } = useExplorePivotActions({ node, allNodes, edges, submodels, preamble })
  const visibleCharts = useMemo(
    () =>
      parsedCharts.ok
        ? parsedCharts.charts.filter((chart) => chart.enabled)
        : [],
    [parsedCharts],
  )
  const sourcePivots = useMemo(() => {
    if (!parsedPivots.ok) return []
    const distinct = new Map<string, ExplorePivotConfig>()
    for (const chart of visibleCharts) {
      const source = resolveExploreChartSource(chart, parsedPivots.pivots)
      if (source.status === "resolved") {
        distinct.set(source.pivot.id, source.pivot)
      }
    }
    return [...distinct.values()]
  }, [parsedPivots, visibleCharts])
  useAutoUpdateExplorePivots({
    nodeId: node.id,
    pivots: sourcePivots,
    report,
    submitting,
    updatePivot,
  })

  if (!parsedCharts.ok) {
    return (
      <div data-testid="explore-charts-pane" className="flex-1 p-4">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsedCharts.error}
        </div>
      </div>
    )
  }
  if (!parsedPivots.ok) {
    return (
      <div data-testid="explore-charts-pane" className="flex-1 p-4">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsedPivots.error}
        </div>
      </div>
    )
  }

  if (parsedCharts.charts.length === 0) {
    return (
      <div data-testid="explore-charts-pane" className="flex flex-1">
        <EmptyCharts>Add a chart from the Charts settings pane.</EmptyCharts>
      </div>
    )
  }

  if (visibleCharts.length === 0) {
    return (
      <div data-testid="explore-charts-pane" className="flex flex-1">
        <EmptyCharts>No charts are currently shown.</EmptyCharts>
      </div>
    )
  }

  return (
    <div data-testid="explore-charts-pane" className="flex-1 overflow-auto p-3">
      <div
        data-testid="explore-chart-grid"
        className="grid items-start gap-3"
        style={{
          gridTemplateColumns:
            "repeat(auto-fit, minmax(min(100%, 28rem), 1fr))",
        }}
      >
        {visibleCharts.map((chart) => {
          const source = resolveExploreChartSource(chart, parsedPivots.pivots)
          const pivot = source.status === "resolved" ? source.pivot : null
          return (
            <ChartCard
              key={chart.id}
              chart={chart}
              pivot={pivot}
              missingPivotId={
                source.status === "missing" ? source.pivotId : null
              }
              nodeId={node.id}
              report={report}
              submitting={pivot ? submitting[pivot.id] === true : false}
              notice={pivot ? notices[pivot.id] : undefined}
              onRetry={updatePivot}
              onCancel={cancelPivot}
            />
          )
        })}
      </div>
    </div>
  )
}
