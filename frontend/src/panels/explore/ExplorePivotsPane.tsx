import { Loader2, Table2 } from "lucide-react"
import { useMemo } from "react"

import type { ExploreCacheReport } from "../../api/types"
import useNodeResultsStore, {
  explorePivotResultKey,
} from "../../stores/useNodeResultsStore"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import type { SimpleEdge, SimpleNode } from "../editors"
import {
  ExploreResultEmptyState,
  PivotRunStatusActions,
} from "./ExploreResultCardChrome"
import PivotTableGrid from "./PivotTableGrid"
import {
  isPivotResultFresh,
  parseExplorePivots,
  pivotCalculationIdentity,
} from "./pivotConfig"
import useAutoUpdateExplorePivots from "./useAutoUpdateExplorePivots"
import useExplorePivotActions from "./useExplorePivotActions"

type ExplorePivotsPaneProps = {
  node: SimpleNode
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  report: ExploreCacheReport | null
}

function EmptyPivots({ children }: { children: string }) {
  return <ExploreResultEmptyState icon={Table2}>{children}</ExploreResultEmptyState>
}

export default function ExplorePivotsPane({
  node,
  allNodes,
  edges,
  submodels,
  preamble,
  report,
}: ExplorePivotsPaneProps) {
  const pivotResults = useNodeResultsStore((state) => state.pivotResults)
  const pivotJobs = useNodeResultsStore((state) => state.pivotJobs)
  const {
    cancelPivot: cancel,
    notices,
    submitting,
    updatePivot: update,
  } = useExplorePivotActions({ node, allNodes, edges, submodels, preamble })
  const parsed = useMemo(
    () => parseExplorePivots(node.data.config ?? {}),
    [node.data.config],
  )
  const enabledPivots = useMemo(
    () => (parsed.ok ? parsed.pivots.filter((pivot) => pivot.enabled) : []),
    [parsed],
  )
  useAutoUpdateExplorePivots({
    nodeId: node.id,
    pivots: enabledPivots,
    report,
    submitting,
    updatePivot: update,
  })

  if (!parsed.ok) {
    return (
      <div data-testid="explore-pivots-pane" className="flex-1 p-4">
        <div
          role="alert"
          className="rounded-lg px-3 py-2 text-xs leading-relaxed"
          style={{
            color: "var(--danger)",
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border)",
          }}
        >
          {parsed.error}
        </div>
      </div>
    )
  }

  if (parsed.pivots.length === 0) {
    return (
      <div data-testid="explore-pivots-pane" className="flex flex-1">
        <EmptyPivots>Add a pivot from the Pivots settings pane.</EmptyPivots>
      </div>
    )
  }

  if (enabledPivots.length === 0) {
    return (
      <div data-testid="explore-pivots-pane" className="flex flex-1">
        <EmptyPivots>No pivots are currently shown.</EmptyPivots>
      </div>
    )
  }

  return (
    <div data-testid="explore-pivots-pane" className="flex-1 overflow-auto p-3">
      <div className="flex flex-col gap-3">
        {enabledPivots.map((pivot) => {
          const key = explorePivotResultKey(node.id, pivot.id)
          const cached = pivotResults[key]
          const job = pivotJobs[key]
          const isSubmitting = submitting[pivot.id] === true
          const notice = notices[pivot.id]
          const currentIdentity = pivotCalculationIdentity(pivot)
          const fresh = isPivotResultFresh(
            cached,
            report?.dataframe_cache_key,
            currentIdentity,
          )
          const status = job?.progress
          const failure =
            status?.failure
            ?? (!job && !isSubmitting ? cached?.terminalStatus?.failure : null)
          const storedError = !job && !isSubmitting ? cached?.error : undefined
          const alertMessage = notice?.message ?? failure?.message ?? storedError
          const alertFailure = notice?.failure ?? failure

          return (
            <section
              key={pivot.id}
              role="region"
              aria-label={pivot.name}
              className="min-h-32 overflow-hidden rounded-lg"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
              }}
            >
              <div
                className="flex items-center gap-2 px-3 py-2"
                style={{ borderBottom: "1px solid var(--border)" }}
              >
                <Table2
                  size={14}
                  aria-hidden="true"
                  style={{ color: NODE_GROUP_COLORS.explore }}
                />
                <h3
                  className="mr-auto text-xs font-semibold"
                  style={{ color: "var(--text-primary)" }}
                >
                  {pivot.name}
                </h3>
                <PivotRunStatusActions
                  activeJobId={job?.jobId ?? null}
                  submitting={isSubmitting}
                  canRetry={Boolean(alertMessage)}
                  onCancel={(jobId) => void cancel(pivot, jobId)}
                  onRetry={() =>
                    void update(pivot, report?.dataframe_cache_key ?? null)
                  }
                />
              </div>

              {pivot.values.length === 0 ? (
                <div
                  className="px-3 py-5 text-center text-[11px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  Add at least one Value in this pivot&apos;s configuration.
                </div>
              ) : (
                <>
                  {job && (
                    <div
                      role="status"
                      className="flex items-center gap-2 px-3 py-2 text-[11px]"
                      style={{ color: "var(--text-muted)" }}
                    >
                      <Loader2
                        size={13}
                        className="animate-spin"
                        aria-hidden="true"
                      />
                      {status?.message || "Calculating pivot"}
                    </div>
                  )}

                  {cached?.result && (
                    <>
                      <div
                        className="px-3 py-2 text-[11px]"
                        style={{
                          color: fresh ? "var(--text-muted)" : "var(--warning)",
                        }}
                      >
                        {fresh
                          ? "Current result"
                          : !report
                            ? "Waiting for current cached Explore data."
                            : alertMessage
                              ? "Current result retained after the refresh failed."
                              : "Updating automatically…"}
                      </div>
                      <PivotTableGrid result={cached.result} pivot={pivot} />
                    </>
                  )}

                  {alertMessage && (
                    <div
                      role="alert"
                      className="m-3 rounded px-3 py-2 text-[11px]"
                      style={{
                        color: "var(--danger)",
                        background: "var(--danger-soft)",
                      }}
                    >
                      {alertMessage}
                      {alertFailure?.remediation
                        ? ` ${alertFailure.remediation}`
                        : ""}
                    </div>
                  )}

                  {!cached?.result && !job && !alertMessage && (
                    <div
                      className="px-3 py-5 text-center text-[11px]"
                      style={{ color: "var(--text-muted)" }}
                    >
                      {report
                        ? "Calculating automatically…"
                        : "Cache the full Explore data above to calculate this pivot automatically."}
                    </div>
                  )}
                </>
              )}
            </section>
          )
        })}
      </div>
    </div>
  )
}
