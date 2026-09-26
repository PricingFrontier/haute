/**
 * The Adjustments view: how the optimiser adjusted the book relative to the
 * base price (scenario value 1.0), for the solved result or the selected
 * frontier point. One bar per value of the solve's scenario grid, from the
 * backend's adjustment report; nothing here is inferred from the chosen rows.
 * A ratebook report describes the grid step price-contour evaluated for each
 * quote and counts the quotes whose deployed factor differs from it.
 *
 * The solved result carries its report. A selected point's report is loaded
 * only while this view is open, through frontier select with
 * `include_adjustments`, in the Rates flow's pattern: one AbortController per
 * request and a request sequence, so a reply for a point, job or generation the
 * view has moved past is dropped. A browser abort only discards the reply; a
 * 409 replaced by another point's apply is reissued, not shown; a 410 says the
 * point is gone, with no Retry; anything else offers Retry. Loaded reports
 * belong to the preview's review (`pointReports`), so stepping back to a point
 * already loaded makes no request.
 */

import { useEffect, useId, useRef, useState } from "react"
import { AlertCircle, Loader2 } from "lucide-react"
import { selectFrontierPoint } from "../../api/client"
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from "../../api/errors"
import type {
  OptimiserAdjustmentBar,
  OptimiserAdjustmentReport,
  OptimiserAdjustmentWeighting,
  OptimiserSolveResult,
} from "../../api/types"
import { CHART_COLORS } from "../../theme/colors"
import ChartFocusDetail from "../ChartFocusDetail"
import HistogramChart, { type HistogramBar } from "../HistogramChart"
import { ChartValuesTable, ResponsiveChart } from "../modelling/ChartScaffold"
import {
  DEPLOYED_FACTOR_DIFFERS_LABEL,
  DEPLOYED_FACTOR_DIFFERS_NOTE,
  NO_UNADJUSTED_NOTE,
  formatScenarioValue,
  formatShare,
  pointAdjustmentKey,
} from "./adjustments"

/** Loaded frontier point reports, keyed by `pointAdjustmentKey`. */
export type PointAdjustmentReports = Readonly<Record<string, OptimiserAdjustmentReport>>

const QUOTES_WEIGHT = "quotes"
const BASE_PRICE = 1
const APPLY_REPLACED = "frontier_point_apply_replaced"
const BAR_COLOR = CHART_COLORS.predicted
const BASE_PRICE_COLOR = CHART_COLORS.residualZero

type PointLoadFailure = { key: string; gone: boolean; message: string }

interface AdjustmentsTabProps {
  jobId: string
  /** The solve's frontier generation: point indices are only meaningful within one. */
  frontierGeneration: number
  /** The selected frontier point, or null for the solved result. */
  pointIndex: number | null
  /** The as-solved result: its report, or the diagnostic saying why it has none. */
  solvedResult: OptimiserSolveResult
  pointReports: PointAdjustmentReports
  onPointReport: (key: string, report: OptimiserAdjustmentReport) => void
  width?: number
}

export default function AdjustmentsTab({
  jobId,
  frontierGeneration,
  pointIndex,
  solvedResult,
  pointReports,
  onPointReport,
  width,
}: AdjustmentsTabProps) {
  const pointKey = pointIndex === null ? null : pointAdjustmentKey(jobId, frontierGeneration, pointIndex)
  const pointReport = pointKey === null ? undefined : pointReports[pointKey]
  const [failure, setFailure] = useState<PointLoadFailure | null>(null)
  // A replaced request is reissued by bumping the attempt.
  const [attempt, setAttempt] = useState(0)
  const requestSeqRef = useRef(0)
  const onPointReportRef = useRef(onPointReport)
  useEffect(() => {
    onPointReportRef.current = onPointReport
  })
  const failed = failure !== null && failure.key === pointKey
  const needsLoad = pointKey !== null && pointReport === undefined && !failed

  useEffect(() => {
    if (!needsLoad || pointIndex === null || pointKey === null) return
    const requestId = requestSeqRef.current + 1
    requestSeqRef.current = requestId
    const controller = new AbortController()
    const isCurrent = () => !controller.signal.aborted && requestSeqRef.current === requestId
    selectFrontierPoint(
      { job_id: jobId, point_index: pointIndex, include_adjustments: true },
      { signal: controller.signal },
    )
      .then((response) => {
        if (!isCurrent()) return
        if (response.adjustments === null) {
          setFailure({
            key: pointKey,
            gone: false,
            message: `The server sent no adjustment report for frontier point ${pointIndex + 1}.`,
          })
          return
        }
        onPointReportRef.current(pointKey, response.adjustments)
      })
      .catch((error: unknown) => {
        if (!isCurrent()) return
        if (apiErrorStatus(error) === 409 && apiErrorCode(error) === APPLY_REPLACED) {
          setAttempt((current) => current + 1)
          return
        }
        setFailure({
          key: pointKey,
          gone: apiErrorStatus(error) === 410,
          message: apiErrorMessage(error, "The request failed."),
        })
      })
    return () => controller.abort()
  }, [needsLoad, pointIndex, pointKey, jobId, attempt])

  if (pointIndex !== null) {
    if (failed) {
      return <LoadFailure failure={failure} onRetry={() => setFailure(null)} />
    }
    if (pointReport === undefined) {
      return (
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
          <Loader2 size={14} className="animate-spin" />
          {`Loading frontier point ${pointIndex + 1}'s adjustments...`}
        </div>
      )
    }
    return <AdjustmentsReport report={pointReport} caption={`Frontier point ${pointIndex + 1}`} width={width} />
  }

  const report = solvedResult.adjustments
  if (report === null) {
    const reason = solvedResult.diagnostics_errors.find((error) => error.diagnostic === "adjustments")
    if (!reason) {
      throw new Error("The result has no adjustment report and no diagnostic saying why.")
    }
    return (
      <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span>The adjustment report could not be built: {reason.message}</span>
      </div>
    )
  }
  return <AdjustmentsReport report={report} caption="As solved" width={width} />
}

function LoadFailure({ failure, onRetry }: { failure: PointLoadFailure; onRetry: () => void }) {
  return (
    <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="flex-1">Adjustments could not be loaded: {failure.message}</span>
      {!failure.gone && (
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium"
          style={{ border: "1px solid var(--danger-border-strong)", color: "var(--danger)" }}
        >
          Retry
        </button>
      )}
    </div>
  )
}

/** Where 1.0 sits among the grid's bars: its own bar, between two, or off the grid. */
function basePricePosition(bars: readonly OptimiserAdjustmentBar[]): number | null {
  const values = bars.map((bar) => bar.scenario_value)
  const exact = values.indexOf(BASE_PRICE)
  if (exact >= 0) return exact
  for (let index = 0; index < values.length - 1; index += 1) {
    const [low, high] = [values[index], values[index + 1]]
    if (low < BASE_PRICE && BASE_PRICE < high) return index + (BASE_PRICE - low) / (high - low)
  }
  return null
}

function barWeight(bar: OptimiserAdjustmentBar, weighting: OptimiserAdjustmentWeighting): number {
  if (weighting.key === QUOTES_WEIGHT) return bar.quotes
  const weight = bar.weights[weighting.key]
  if (weight === undefined) {
    throw new Error(`Adjustment bar ${bar.optimal_step} has no ${weighting.key} weight`)
  }
  return weight
}

function formatWeight(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 })
}

function AdjustmentsReport({
  report,
  caption,
  width,
}: {
  report: OptimiserAdjustmentReport
  caption: string
  width?: number
}) {
  const weightById = useId()
  const [weightKey, setWeightKey] = useState(QUOTES_WEIGHT)
  const [activeKey, setActiveKey] = useState<string | null>(null)
  // A weighting this report refused (another point's may not have) is not
  // offered, so the view shows quote count, which every report has, first.
  const weighting = report.weightings.find((candidate) => candidate.key === weightKey) ?? report.weightings[0]
  const weighted = weighting.key !== QUOTES_WEIGHT
  const byKey = new Map(report.bars.map((bar) => [String(bar.optimal_step), bar]))
  const bars: HistogramBar[] = report.bars.map((bar) => ({
    key: String(bar.optimal_step),
    value: barWeight(bar, weighting),
    label: formatScenarioValue(bar.scenario_value),
  }))
  const referenceAt = basePricePosition(report.bars)
  const first = report.bars[0]
  const last = report.bars[report.bars.length - 1]
  const active = activeKey === null ? undefined : byKey.get(activeKey)
  const quoteShare = (bar: OptimiserAdjustmentBar) => formatShare(bar.quotes / report.n_quotes)
  const weightShare = (bar: OptimiserAdjustmentBar) => formatShare(barWeight(bar, weighting) / weighting.total)
  const { quantiles } = weighting

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span id={weightById} style={{ color: "var(--text-secondary)" }}>Weight by</span>
        <div role="group" aria-labelledby={weightById} className="flex flex-wrap gap-1">
          {report.weightings.map((candidate) => (
            <button
              key={candidate.key}
              type="button"
              aria-pressed={candidate.key === weighting.key}
              onClick={() => setWeightKey(candidate.key)}
              className="validation-control"
            >
              {candidate.label}
            </button>
          ))}
        </div>
      </div>
      {report.diagnostics_errors.length > 0 && (
        <ul className="m-0 list-none space-y-0.5 p-0 text-[12px]" style={{ color: "var(--text-muted)" }}>
          {report.diagnostics_errors.map((error) => (
            <li key={error.message}>Not offered as a weighting: {error.message}</li>
          ))}
        </ul>
      )}
      <ResponsiveChart width={width}>
        {(chartWidth) => (
          <HistogramChart
            title="Chosen scenario values"
            description={`${caption}: ${report.n_quotes.toLocaleString()} quotes`}
            ariaLabel="Chosen scenario values histogram"
            bars={bars}
            axis={{ kind: "categorical" }}
            width={chartWidth}
            height={260}
            xLabel="Scenario value (1.0 = base price)"
            yLabel={weighting.label}
            color={BAR_COLOR}
            barTestId="adjustment-bar"
            reference={referenceAt === null ? null : {
              at: referenceAt,
              color: BASE_PRICE_COLOR,
              label: "1.0 = base price (no adjustment)",
              testId: "base-price-line",
            }}
            legend={[{ label: weighting.label, color: BAR_COLOR, swatch: "bar" }]}
            interaction={{
              activeKey,
              onActivate: setActiveKey,
              describe: (histogramBar) => {
                const bar = byKey.get(histogramBar.key)!
                return (
                  `Scenario value ${formatScenarioValue(bar.scenario_value)}: `
                  + `${bar.quotes.toLocaleString()} quotes, ${quoteShare(bar)} of quotes`
                  + (weighted ? `; ${weighting.label} ${formatWeight(barWeight(bar, weighting))}, ${weightShare(bar)}` : "")
                )
              },
            }}
          />
        )}
      </ResponsiveChart>
      {!report.has_unadjusted && (
        <p className="validation-chart-description m-0">{NO_UNADJUSTED_NOTE}</p>
      )}
      {referenceAt === null && (
        <p className="validation-chart-description m-0">
          The base price 1.0 lies outside the scenario range, so every quote is adjusted
          {first.scenario_value > BASE_PRICE ? " up." : " down."}
        </p>
      )}
      <ChartFocusDetail placeholder="Hover or focus a bar to inspect its values.">
        {active ? (
          <>
            <strong>{formatScenarioValue(active.scenario_value)}</strong>
            <span>{active.quotes.toLocaleString()} quotes</span>
            <span>{quoteShare(active)} of quotes</span>
            {weighted && (
              <span>{`${weighting.label}: ${formatWeight(barWeight(active, weighting))} (${weightShare(active)})`}</span>
            )}
          </>
        ) : null}
      </ChartFocusDetail>
      <dl role="group" aria-label="Quantiles" className="validation-stat-row m-0 flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono">
        {([
          ["P5", quantiles.p5],
          ["P25", quantiles.p25],
          ["Median", quantiles.p50],
          ["P75", quantiles.p75],
          ["P95", quantiles.p95],
          ["Mean", weighting.mean],
        ] as const).map(([label, value]) => (
          <div key={label} className="flex gap-1.5">
            <dt style={{ color: "var(--text-muted)" }}>{label}</dt>
            <dd className="m-0" style={{ color: "var(--text-primary)" }}>{formatScenarioValue(value)}</dd>
          </div>
        ))}
      </dl>
      <dl role="group" aria-label="Shares" className="m-0 flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono">
        {([
          ["Adjusted up", weighting.share_up],
          ["Adjusted down", weighting.share_down],
          ...(weighting.share_unadjusted === null ? [] : [["Unadjusted", weighting.share_unadjusted] as const]),
          [`At range minimum (${formatScenarioValue(first.scenario_value)})`, weighting.share_at_min],
          [`At range maximum (${formatScenarioValue(last.scenario_value)})`, weighting.share_at_max],
        ] as const).map(([label, share]) => (
          <div key={label} className="flex gap-1.5">
            <dt style={{ color: "var(--text-muted)" }}>{label}</dt>
            <dd className="m-0" style={{ color: "var(--text-primary)" }}>{formatShare(share)}</dd>
          </div>
        ))}
      </dl>
      {report.deployed_factor_differs !== null && (
        <DeployedFactorDiffers count={report.deployed_factor_differs} nQuotes={report.n_quotes} />
      )}
      <ChartValuesTable
        summary="View adjustment values"
        ariaLabel="Adjustment values"
        headers={[
          "Scenario value",
          "Step",
          "Quotes",
          "Share of quotes",
          ...(weighted ? [weighting.label, `Share of ${weighting.label}`] : []),
        ]}
        rows={report.bars.map((bar) => [
          formatScenarioValue(bar.scenario_value),
          String(bar.optimal_step),
          bar.quotes.toLocaleString(),
          quoteShare(bar),
          ...(weighted ? [formatWeight(barWeight(bar, weighting)), weightShare(bar)] : []),
        ])}
      />
    </div>
  )
}

/** A ratebook report's count of quotes whose deployed factor differs from the evaluated step. */
function DeployedFactorDiffers({ count, nQuotes }: { count: number; nQuotes: number }) {
  return (
    <section role="group" aria-label="Deployed factor" className="space-y-1 text-xs">
      <dl className="m-0 flex flex-wrap gap-x-4 gap-y-1 font-mono">
        <div className="flex gap-1.5">
          <dt style={{ color: "var(--text-muted)" }}>{DEPLOYED_FACTOR_DIFFERS_LABEL}</dt>
          <dd className="m-0" style={{ color: "var(--text-primary)" }}>
            {`${count.toLocaleString()} quotes (${formatShare(count / nQuotes)})`}
          </dd>
        </div>
      </dl>
      <p className="validation-chart-description m-0">{DEPLOYED_FACTOR_DIFFERS_NOTE}</p>
    </section>
  )
}
