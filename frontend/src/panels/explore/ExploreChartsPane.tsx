import { AlertTriangle, BarChart3, Loader2, Play, XCircle } from "lucide-react"
import { useMemo } from "react"

import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import type { SimpleEdge, SimpleNode } from "../editors"
import ComboChart from "./ComboChart"
import {
  parseExploreCharts,
  resolveExploreChartSource,
  type ExploreChartConfig,
} from "./chartConfig"
import { adaptPivotChartData, ChartDataError } from "./chartData"
import {
  parseExplorePivots,
  pivotCalculationIdentity,
  type ExplorePivotConfig,
} from "./pivotConfig"
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
  return (
    <div className="flex flex-1 items-center justify-center p-4">
      <div className="max-w-md text-center">
        <BarChart3
          size={24}
          className="mx-auto mb-2"
          aria-hidden="true"
          style={{ color: NODE_GROUP_COLORS.explore }}
        />
        <div
          className="text-xs font-semibold"
          style={{ color: "var(--text-secondary)" }}
        >
          {children}
        </div>
      </div>
    </div>
  )
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
  onUpdate: (pivot: ExplorePivotConfig) => void
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
  onUpdate,
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
  const fresh = Boolean(
    pivot &&
      pivotResult &&
      report &&
      pivotResult.dataframe_cache_key === report.dataframe_cache_key &&
      cached?.calculationIdentity === currentIdentity,
  )
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
        {pivot && pivot.values.length > 0 &&
          (job ? (
            <button
              type="button"
              onClick={() => void onCancel(pivot, job.jobId)}
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold"
              style={{
                color: "var(--danger)",
                border: "1px solid var(--danger-border)",
              }}
            >
              <XCircle size={12} aria-hidden="true" />
              Cancel
            </button>
          ) : (
            <button
              type="button"
              onClick={() => void onUpdate(pivot)}
              disabled={submitting}
              className="inline-flex items-center gap-1 rounded px-2 py-1 text-[11px] font-semibold disabled:opacity-45"
              style={{
                color: "var(--text-on-accent)",
                background: NODE_GROUP_COLORS.explore,
              }}
            >
              {submitting ? (
                <Loader2 size={12} className="animate-spin" aria-hidden="true" />
              ) : (
                <Play size={12} aria-hidden="true" />
              )}
              {submitting ? "Starting" : "Update"}
            </button>
          ))}
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
          Source Pivot result is out of date. Update to recalculate it.
        </CardMessage>
      )}
      {pivot && !cached?.result && !job && !submitting && !alertMessage && (
        <CardMessage>Update this Pivot to calculate chart data.</CardMessage>
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

  const visibleCharts = parsedCharts.charts.filter((chart) => chart.enabled)
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
              onUpdate={updatePivot}
              onCancel={cancelPivot}
            />
          )
        })}
      </div>
    </div>
  )
}
