/**
 * The Segments view: where the optimiser adjusted, per level of one key (an
 * analysis column, or a ratebook result's rating factor), for the solved result
 * or the selected frontier point. Each level's mean chosen scenario value is
 * drawn against 1.0, the base price, with an aligned quote strip, a detail line
 * and a values table; everything comes from the backend's breakdown.
 *
 * The keys are the result's `segment_keys`, listed unranked until the target's
 * index ranks them by adjustment spread. The selected key and the search are the
 * Rates tab's, lifted into the preview's review. Loaded breakdowns and indexes
 * belong to the review too (`results`), keyed by job, generation, target, key
 * and weighting, so returning to one makes no request. Each request has its own
 * AbortController: switching key, weighting or point aborts the one in flight
 * and drops its reply. A 409 replaced by another point's apply is reissued; a
 * 410 says the point is gone, with no Retry; anything else offers Retry.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react"
import { AlertCircle, Loader2 } from "lucide-react"
import { getOptimiserSegmentIndex, getOptimiserSegments } from "../../api/client"
import { apiErrorCode, apiErrorMessage, apiErrorStatus } from "../../api/errors"
import type {
  OptimiserSegmentIndexResponse,
  OptimiserSegmentKey,
  OptimiserSegmentRow,
  OptimiserSegmentsResponse,
  OptimiserSolveResult,
} from "../../api/types"
import ChartFocusDetail from "../ChartFocusDetail"
import { ChartEmptyState, ChartLegend, ChartValuesTable } from "../modelling/ChartScaffold"
import type { FeatureItem, FeatureRanking } from "../modelling/FeatureBrowser"
import { FeatureDiagnosticLayout } from "../modelling/FeatureDiagnosticTab"
import { useDiagnosticFeature, type SharedFeatureBrowser } from "../modelling/useDiagnosticFeature"
import {
  RELATIVITY_ABOVE_COLOR,
  RELATIVITY_BELOW_COLOR,
  RelativityBars,
  type RelativityBar,
} from "../RelativityBars"
import { DEPLOYED_FACTOR_DIFFERS_LABEL, formatScenarioValue, formatShare } from "./adjustments"
import { formatVsNeutral } from "./ratebookFactorTables"

/** The key choice and search, shared with the Rates tab through the preview's review. */
export type SegmentsKeySelection = Omit<SharedFeatureBrowser, "features">

/** Breakdowns and indexes loaded in this review, by `breakdownKey` and `indexKey`. */
export type SegmentResults = {
  breakdowns: Readonly<Record<string, OptimiserSegmentsResponse>>
  indexes: Readonly<Record<string, OptimiserSegmentIndexResponse>>
}

export const NO_SEGMENT_KEYS = "Add analysis columns in the optimiser config"

const QUOTES_WEIGHT = "quotes"
const APPLY_REPLACED = "frontier_point_apply_replaced"
const QUOTE_STRIP_WIDTH = 96
const UNAVAILABLE = "—"

const RANKING_PENDING: FeatureRanking = {
  label: "Ranking…",
  description: "Ranking the keys by adjustment spread; listed in configured order until then.",
}

type LoadFailure = { key: string; gone: boolean; message: string }

/** A target's identity: point indices mean something only within one job and generation. */
function indexKey(jobId: string, frontierGeneration: number, pointIndex: number | null): string {
  return `${jobId}:${frontierGeneration}:${pointIndex ?? "solved"}`
}

function breakdownKey(target: string, key: string, weight: string): string {
  return JSON.stringify([target, key, weight])
}

/** One request's lifecycle: aborted when its identity changes, a replaced 409 reissued. */
function useLatestRequest<T>(
  identity: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  onLoaded: (identity: string, value: T) => void,
): { failure: LoadFailure | null; retry: () => void } {
  const [failure, setFailure] = useState<LoadFailure | null>(null)
  const [attempt, setAttempt] = useState(0)
  const loadRef = useRef(load)
  const onLoadedRef = useRef(onLoaded)
  useEffect(() => {
    loadRef.current = load
    onLoadedRef.current = onLoaded
  })
  const failed = failure !== null && failure.key === identity
  useEffect(() => {
    if (identity === null || failed) return
    const controller = new AbortController()
    loadRef.current(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) onLoadedRef.current(identity, value)
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return
        if (apiErrorStatus(error) === 409 && apiErrorCode(error) === APPLY_REPLACED) {
          setAttempt((current) => current + 1)
          return
        }
        setFailure({
          key: identity,
          gone: apiErrorStatus(error) === 410,
          message: apiErrorMessage(error, "The request failed."),
        })
      })
    return () => controller.abort()
  }, [identity, failed, attempt])
  return { failure: failed ? failure : null, retry: () => setFailure(null) }
}

interface SegmentsTabProps {
  jobId: string
  /** The solve's frontier generation: point indices are only meaningful within one. */
  frontierGeneration: number
  /** The selected frontier point, or null for the solved result. */
  pointIndex: number | null
  /** The as-solved result: its segment keys, constraints and mode are every target's. */
  solvedResult: OptimiserSolveResult
  selection: SegmentsKeySelection
  results: SegmentResults
  onBreakdown: (key: string, response: OptimiserSegmentsResponse) => void
  onIndex: (key: string, response: OptimiserSegmentIndexResponse) => void
}

type SegmentsKeyRow = { feature: string; key: OptimiserSegmentKey }

export default function SegmentsTab({
  jobId,
  frontierGeneration,
  pointIndex,
  solvedResult,
  selection,
  results,
  onBreakdown,
  onIndex,
}: SegmentsTabProps) {
  const catalogue = solvedResult.segment_keys
  const target = indexKey(jobId, frontierGeneration, pointIndex)
  const index = results.indexes[target]
  const indexRequest = useLatestRequest(
    catalogue.length > 0 && index === undefined ? target : null,
    (signal) => getOptimiserSegmentIndex({ job_id: jobId, point_index: pointIndex }, { signal }),
    onIndex,
  )
  // The index may find a key the gate admitted unavailable; its verdict wins once loaded.
  const keys: OptimiserSegmentKey[] = index?.keys ?? catalogue
  const features = useMemo<FeatureItem[]>(
    () => (index
      ? index.keys.map((entry) => ({ feature: entry.key, importance: entry.spread }))
      : catalogue.map((entry) => ({ feature: entry.key, importance: null }))),
    [index, catalogue],
  )
  const rows = useMemo<SegmentsKeyRow[]>(() => keys.map((entry) => ({ feature: entry.key, key: entry })), [keys])
  const browser = useDiagnosticFeature(features, {
    ...selection,
    features,
    selected: features.some((item) => item.feature === selection.selected)
      ? selection.selected
      : (features[0]?.feature ?? null),
  })

  if (catalogue.length === 0) return <ChartEmptyState>{NO_SEGMENT_KEYS}</ChartEmptyState>

  const rankedBy: FeatureRanking = index ? index.statistic : RANKING_PENDING
  return (
    <div className="space-y-2">
      {indexRequest.failure && (
        <LoadFailureNotice
          what="The keys could not be ranked"
          failure={indexRequest.failure}
          retryLabel="Retry ranking"
          onRetry={indexRequest.retry}
        />
      )}
      <FeatureDiagnosticLayout
        rows={rows}
        browser={browser}
        rankedBy={rankedBy}
        itemNoun="key"
        noun="segment"
        renderChart={(row) => (
          <SegmentKeyPane
            key={row.feature}
            segmentKey={row.key}
            jobId={jobId}
            pointIndex={pointIndex}
            target={target}
            solvedResult={solvedResult}
            results={results}
            onBreakdown={onBreakdown}
          />
        )}
      />
    </div>
  )
}

function weightOptions(result: OptimiserSolveResult): { key: string; label: string }[] {
  return [
    { key: QUOTES_WEIGHT, label: "Quotes" },
    { key: "optimal_objective", label: "Objective at the chosen scenario" },
    ...Object.keys(result.constraints).map((name) => ({
      key: `optimal_${name}`,
      label: `${name} at the chosen scenario`,
    })),
  ]
}

function SegmentKeyPane({
  segmentKey,
  jobId,
  pointIndex,
  target,
  solvedResult,
  results,
  onBreakdown,
}: {
  segmentKey: OptimiserSegmentKey
  jobId: string
  pointIndex: number | null
  target: string
  solvedResult: OptimiserSolveResult
  results: SegmentResults
  onBreakdown: (key: string, response: OptimiserSegmentsResponse) => void
}) {
  const weightById = useId()
  const [weight, setWeight] = useState(QUOTES_WEIGHT)
  const identity = breakdownKey(target, segmentKey.key, weight)
  const response = results.breakdowns[identity]
  const request = useLatestRequest(
    segmentKey.available && response === undefined ? identity : null,
    (signal) => getOptimiserSegments(
      { job_id: jobId, point_index: pointIndex, key: segmentKey.key, weight },
      { signal },
    ),
    onBreakdown,
  )

  if (!segmentKey.available) {
    return (
      <ChartEmptyState>
        {`${segmentKey.key} cannot be broken down: ${segmentKey.unavailable_reason}`}
      </ChartEmptyState>
    )
  }
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span id={weightById} style={{ color: "var(--text-secondary)" }}>Weight by</span>
        <div role="group" aria-labelledby={weightById} className="flex flex-wrap gap-1">
          {weightOptions(solvedResult).map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={option.key === weight}
              onClick={() => setWeight(option.key)}
              className="validation-control"
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>
      {request.failure ? (
        <LoadFailureNotice
          what="The segments could not be loaded"
          failure={request.failure}
          retryLabel="Retry"
          onRetry={request.retry}
        />
      ) : response === undefined ? (
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
          <Loader2 size={14} className="animate-spin" />
          {`Loading ${segmentKey.key} segments...`}
        </div>
      ) : (
        <SegmentsChart response={response} />
      )}
    </div>
  )
}

function LoadFailureNotice({
  what,
  failure,
  retryLabel,
  onRetry,
}: {
  what: string
  failure: LoadFailure
  retryLabel: string
  onRetry: () => void
}) {
  return (
    <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
      <AlertCircle size={14} className="mt-0.5 shrink-0" />
      <span className="flex-1">{`${what}: ${failure.message}`}</span>
      {!failure.gone && (
        <button
          type="button"
          onClick={onRetry}
          aria-label={retryLabel}
          className="shrink-0 rounded px-2 py-0.5 text-[11px] font-medium"
          style={{ border: "1px solid var(--danger-border-strong)", color: "var(--danger)" }}
        >
          Retry
        </button>
      )}
    </div>
  )
}

function levelKey(row: OptimiserSegmentRow): string {
  return `${row.kind}:${row.label}`
}

function levelLabel(row: OptimiserSegmentRow): string {
  return row.kind === "other" ? `Other (${row.merged_levels!.toLocaleString()} levels)` : row.label
}

function SegmentsChart({ response }: { response: OptimiserSegmentsResponse }) {
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const weighted = response.weight !== QUOTES_WEIGHT
  // A refused weighting has no weighted figures at all: the chart draws the unweighted means.
  const drawsWeighted = weighted && response.weighted_mean_scenario_value !== null
  const drawn = (row: OptimiserSegmentRow): number | null => (
    drawsWeighted ? (row.weighted?.mean_scenario_value ?? null) : row.unweighted.mean_scenario_value
  )
  const byKey = new Map(response.rows.map((row) => [levelKey(row), row]))
  const maxQuotes = Math.max(...response.rows.map((row) => row.quotes))
  const quoteShare = (row: OptimiserSegmentRow) => formatShare(row.quotes / response.n_quotes)
  const meanLabel = drawsWeighted ? `Mean (${response.weight_label})` : "Mean"
  const bars: RelativityBar[] = response.rows.map((row) => ({
    key: levelKey(row),
    label: levelLabel(row),
    value: drawn(row),
  }))
  const active = activeKey === null ? undefined : byKey.get(activeKey)
  const formatMean = (value: number | null) => (
    value === null ? UNAVAILABLE : `${formatScenarioValue(value)} (${formatVsNeutral(value)} vs 1.0)`
  )
  const shares = (row: OptimiserSegmentRow) => (drawsWeighted ? row.weighted : row.unweighted)
  const levelsNoun = response.binning === "numeric" ? "bins" : "levels"
  const deployed = response.rows[0].deployed_factor_differs !== null

  return (
    <>
      {response.diagnostics_errors.length > 0 && (
        <ul className="m-0 list-none space-y-0.5 p-0 text-[12px]" style={{ color: "var(--text-muted)" }}>
          {response.diagnostics_errors.map((error) => (
            <li key={error.message}>{error.message}</li>
          ))}
        </ul>
      )}
      <div className="validation-chart-title">
        <div>
          <h4 className="validation-feature-heading">{response.key}</h4>
          <p className="validation-chart-description">
            {`${response.n_levels.toLocaleString()} ${levelsNoun} · ${response.n_quotes.toLocaleString()} quotes · `
              + `${drawsWeighted ? `mean weighted by ${response.weight_label}` : "mean chosen scenario value"} against 1.0 = base price`}
          </p>
        </div>
        <ChartLegend
          items={[
            { label: "Above 1.0 (adjusted up)", color: RELATIVITY_ABOVE_COLOR, swatch: "bar" },
            { label: "Below 1.0 (adjusted down)", color: RELATIVITY_BELOW_COLOR, swatch: "bar" },
            { label: "Quotes", color: "var(--text-muted)", swatch: "bar", opacity: 0.5 },
          ]}
        />
      </div>
      <RelativityBars
        ariaLabel={`Mean chosen scenario value for ${response.key}`}
        bars={bars}
        formatValue={formatScenarioValue}
        interaction={{
          activeKey,
          onActivate: setActiveKey,
          describe: (bar) => {
            const row = byKey.get(bar.key)!
            return (
              `${bar.label}. ${meanLabel} ${formatMean(bar.value)}. `
              + `${row.quotes.toLocaleString()} quotes, ${quoteShare(row)} of quotes.`
            )
          },
        }}
        aside={{
          header: `Quotes 0–${maxQuotes.toLocaleString()}`,
          width: QUOTE_STRIP_WIDTH,
          render: (bar, isActive) => {
            const row = byKey.get(bar.key)!
            return (
              <span className="block h-2 w-full">
                <span
                  data-testid="quote-strip-bar"
                  className="block h-full rounded-sm"
                  title={`${bar.label}: ${row.quotes.toLocaleString()} quotes`}
                  style={{
                    width: `${(row.quotes / maxQuotes) * 100}%`,
                    background: "var(--text-muted)",
                    opacity: isActive ? 0.65 : 0.3,
                  }}
                />
              </span>
            )
          },
        }}
      />
      <ChartFocusDetail placeholder="Hover or focus a level to inspect its adjustments.">
        {active ? (
          <>
            <strong>{levelLabel(active)}</strong>
            <span>{`Quotes: ${active.quotes.toLocaleString()} (${quoteShare(active)})`}</span>
            <span>{`${meanLabel}: ${formatMean(drawn(active))}`}</span>
            {drawsWeighted && (
              <span>{`Unweighted mean: ${formatMean(active.unweighted.mean_scenario_value)}`}</span>
            )}
            {(() => {
              const figures = shares(active)
              return figures === null ? (
                <span>{`${response.weight_label} is zero for every quote in this level.`}</span>
              ) : (
                <>
                  <span>{`Adjusted up: ${formatShare(figures.share_up)}`}</span>
                  <span>{`Adjusted down: ${formatShare(figures.share_down)}`}</span>
                  <span>{`At range edge: ${formatShare(figures.share_at_edge)}`}</span>
                </>
              )
            })()}
            {active.deployed_factor_differs !== null && (
              <span>{`${DEPLOYED_FACTOR_DIFFERS_LABEL}: ${active.deployed_factor_differs.toLocaleString()}`}</span>
            )}
          </>
        ) : null}
      </ChartFocusDetail>
      <ChartValuesTable
        summary="View segment values"
        ariaLabel={`${response.key} segment values`}
        headers={[
          "Level",
          "Quotes",
          "Mean (unweighted)",
          ...(weighted ? [`Mean (${response.weight_label})`] : []),
          "Adjusted up",
          "Adjusted down",
          "At range edge",
          ...(deployed ? ["Deployed ≠ evaluated step"] : []),
        ]}
        rows={response.rows.map((row) => {
          const figures = shares(row)
          return [
            levelLabel(row),
            row.quotes.toLocaleString(),
            formatScenarioValue(row.unweighted.mean_scenario_value),
            ...(weighted
              ? [row.weighted === null ? UNAVAILABLE : formatScenarioValue(row.weighted.mean_scenario_value)]
              : []),
            figures === null ? UNAVAILABLE : formatShare(figures.share_up),
            figures === null ? UNAVAILABLE : formatShare(figures.share_down),
            figures === null ? UNAVAILABLE : formatShare(figures.share_at_edge),
            ...(deployed ? [row.deployed_factor_differs!.toLocaleString()] : []),
          ]
        })}
      />
    </>
  )
}
