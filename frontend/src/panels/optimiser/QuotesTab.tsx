/**
 * The Quotes explorer (OPT-V12): the per-quote chosen scenarios of the current
 * publish target (the solved result or one frontier point), in both modes, one
 * server page at a time.
 *
 * Sorting, the quote-id search and the filters run on the server over every
 * quote; the tab only holds the query. Each page is read through the result
 * store's `/apply` cache under its full request identity (job, frontier
 * generation, target and the canonical query), so reopening the tab or
 * returning to a page already seen makes no request, while a new job or a
 * recomputed frontier loads afresh. A response that arrives after the query or
 * target moved on is dropped. Viewing detail never consumes the result: saving
 * and logging still work.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react"
import { AlertCircle, Loader2, X } from "lucide-react"
import { applyOptimiser } from "../../api/client"
import { apiErrorMessage, apiErrorStatus } from "../../api/errors"
import type {
  ApplyOptimiserResponse,
  OptimiserQuoteColumn,
  OptimiserQuoteFilters,
} from "../../api/types"
import useNodeResultsStore, {
  optimiserApplyKey,
  type OptimiserApplyIdentity,
  type OptimiserApplyQuery,
} from "../../stores/useNodeResultsStore"
import { formatValue } from "../../utils/formatValue"
import { frontierGenerationMismatch } from "./optimiserHelpers"
import {
  SortableValuesTable,
  ValuesTableSearch,
  type SortableValuesColumn,
} from "../SortableValuesTable"
import { nextSort, type SortState } from "../valuesSort"

/** The most rows one page holds, and the deepest row any page reaches (the server's limits). */
const PAGE_LIMIT = 100
const PAGE_DEPTH_LIMIT = 10_000
const SEARCH_DELAY_MS = 300
const SCENARIO = "optimal_scenario_value"
const DASH = "—"

const NO_FILTERS: OptimiserQuoteFilters = {
  scenario_value_min: null,
  scenario_value_max: null,
  at_range_edge: false,
  analysis_equals: {},
  deployed_factor_differs: false,
}

const DEFAULT_QUERY: OptimiserApplyQuery = {
  sort_by: null,
  descending: false,
  quote_id_prefix: null,
  filters: NO_FILTERS,
  offset: 0,
  limit: PAGE_LIMIT,
}

type AnalysisValue = OptimiserQuoteFilters["analysis_equals"][string]
type QuoteRow = Record<string, unknown>

interface Preset {
  label: string
  sortBy: string | null
  descending: boolean
  filters: OptimiserQuoteFilters
  ratebookOnly: boolean
}

const PRESETS: readonly Preset[] = [
  { label: "Highest adjustment", sortBy: SCENARIO, descending: true, filters: NO_FILTERS, ratebookOnly: false },
  { label: "Lowest adjustment", sortBy: SCENARIO, descending: false, filters: NO_FILTERS, ratebookOnly: false },
  {
    label: "At range edge",
    sortBy: null,
    descending: false,
    filters: { ...NO_FILTERS, at_range_edge: true },
    ratebookOnly: false,
  },
  {
    label: "Deployed ≠ evaluated",
    sortBy: null,
    descending: false,
    filters: { ...NO_FILTERS, deployed_factor_differs: true },
    ratebookOnly: true,
  },
]

function sameFilters(a: OptimiserQuoteFilters, b: OptimiserQuoteFilters): boolean {
  const aAnalysis = Object.entries(a.analysis_equals)
  return a.scenario_value_min === b.scenario_value_min
    && a.scenario_value_max === b.scenario_value_max
    && a.at_range_edge === b.at_range_edge
    && a.deployed_factor_differs === b.deployed_factor_differs
    && aAnalysis.length === Object.keys(b.analysis_equals).length
    && aAnalysis.every(([column, value]) => (
      column in b.analysis_equals && b.analysis_equals[column] === value
    ))
}

function presetPressed(preset: Preset, query: OptimiserApplyQuery): boolean {
  return query.sort_by === preset.sortBy
    && (preset.sortBy === null || query.descending === preset.descending)
    && sameFilters(query.filters, preset.filters)
}

function columnLabel(column: OptimiserQuoteColumn): string {
  switch (column.role) {
    case "id": return "Quote ID"
    case "scenario": return "Scenario value"
    case "objective": return "Objective"
    case "constraint": return column.name.replace(/^optimal_/, "")
    case "factor": return "Factor product"
    case "flag": return "Deployed ≠ evaluated"
    case "analysis": return column.name
  }
}

/** A cell's text: numbers grouped with at most four decimals, a missing value as a dash. */
function formatCell(value: unknown): string {
  if (value === null || value === undefined) return DASH
  if (typeof value === "boolean") return value ? "Yes" : "No"
  return formatValue(value)
}

function formatFilterValue(value: AnalysisValue): string {
  return value === null ? "missing" : formatCell(value)
}

/** The scenario value with its direction against 1.0 as a glyph and in words, never colour alone. */
function ScenarioValue({ value }: { value: unknown }) {
  if (typeof value !== "number") throw new Error(`A quote's scenario value must be a number, got ${JSON.stringify(value)}`)
  const [glyph, words] = value > 1 ? ["▲", "Adjusted up"] : value < 1 ? ["▼", "Adjusted down"] : ["=", "Unadjusted"]
  return (
    <span className="inline-flex items-center gap-1">
      <span role="img" aria-label={words} title={words}>{glyph}</span>
      {formatValue(value)}
    </span>
  )
}

function asAnalysisValue(value: unknown, column: string): AnalysisValue {
  if (value === null || typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value
  throw new Error(`Analysis column ${column} holds a value that cannot be filtered: ${JSON.stringify(value)}`)
}

interface QuotesTabProps {
  nodeId: string
  jobId: string
  mode: "online" | "ratebook"
  /** The solve's frontier generation: point indices are only meaningful within one. */
  frontierGeneration: number
  /** The publish target: a frontier point, or null for the solved result. */
  pointIndex: number | null
}

export default function QuotesTab({ nodeId, jobId, mode, frontierGeneration, pointIndex }: QuotesTabProps) {
  const [query, setQuery] = useState<OptimiserApplyQuery>(DEFAULT_QUERY)
  const [searchText, setSearchText] = useState("")
  const target = pointIndex ?? "solved"
  // A new target starts at its first page (adjusted during render, so no stale page is requested).
  const targetKey = `${jobId}:${frontierGeneration}:${target}`
  const [shownTarget, setShownTarget] = useState(targetKey)
  if (shownTarget !== targetKey) {
    setShownTarget(targetKey)
    if (query.offset !== 0) setQuery({ ...query, offset: 0 })
  }

  // The search is sent once typing pauses; an empty box sends none.
  useEffect(() => {
    const prefix = searchText === "" ? null : searchText
    if (prefix === query.quote_id_prefix) return
    const timer = setTimeout(() => {
      setQuery((current) => ({ ...current, quote_id_prefix: prefix, offset: 0 }))
    }, SEARCH_DELAY_MS)
    return () => clearTimeout(timer)
  }, [searchText, query.quote_id_prefix])

  const identity = useMemo<OptimiserApplyIdentity>(
    () => ({ jobId, frontierGeneration, target, query }),
    [jobId, frontierGeneration, target, query],
  )
  const key = optimiserApplyKey(identity)
  const cached = useNodeResultsStore((s) => s.optimiserApplyCache.find((entry) => entry.key === key))
  const recordApply = useNodeResultsStore((s) => s.recordOptimiserApply)
  const touchApply = useNodeResultsStore((s) => s.touchOptimiserApply)
  const [failure, setFailure] = useState<{ key: string; error: string; gone: boolean } | null>(null)
  const hasCached = cached !== undefined
  const failed = failure !== null && failure.key === key

  useEffect(() => {
    if (hasCached) {
      touchApply(key)
      return
    }
    // A failed request is reissued only by Retry, which clears the failure.
    if (failed) return
    const controller = new AbortController()
    applyOptimiser(
      {
        job_id: identity.jobId,
        ...(identity.target !== "solved" ? { point_index: identity.target } : {}),
        ...identity.query,
      },
      { signal: controller.signal },
    )
      .then((response) => {
        // Aborted means this tab's identity (its query included) moved on; the
        // store also refuses an identity whose job, generation or target did.
        if (controller.signal.aborted) return
        // The server may have recomputed the frontier after this request.
        const mismatch = frontierGenerationMismatch(
          response.frontier_generation,
          identity.frontierGeneration,
        )
        if (mismatch) {
          setFailure({ key, error: mismatch, gone: false })
          return
        }
        recordApply(nodeId, identity, response)
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setFailure({
          key,
          error: apiErrorMessage(error, "The request failed."),
          gone: apiErrorStatus(error) === 410,
        })
      })
    return () => controller.abort()
  }, [failed, hasCached, identity, key, nodeId, recordApply, touchApply])

  const applyPreset = (preset: Preset) => setQuery((current) => ({
    ...current,
    sort_by: preset.sortBy,
    descending: preset.descending,
    filters: preset.filters,
    offset: 0,
  }))
  const sort: SortState<string> | null = query.sort_by === null
    ? null
    : { key: query.sort_by, dir: query.descending ? "desc" : "asc" }
  const onSort = (column: string) => {
    const next = nextSort(sort, column)
    setQuery((current) => ({ ...current, sort_by: next.key, descending: next.dir === "desc", offset: 0 }))
  }
  const setAnalysisFilter = (column: string, value: AnalysisValue | undefined) => setQuery((current) => {
    const analysis = { ...current.filters.analysis_equals }
    if (value === undefined) delete analysis[column]
    else analysis[column] = value
    return { ...current, filters: { ...current.filters, analysis_equals: analysis }, offset: 0 }
  })

  const targetName = pointIndex === null ? "the solved result" : `frontier point ${pointIndex + 1}`
  const analysisFilters = Object.entries(query.filters.analysis_equals)

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-end gap-2">
        <ValuesTableSearch label="Search quote IDs" value={searchText} onChange={setSearchText} />
        <div role="group" aria-label="Presets" className="flex flex-wrap gap-1">
          {PRESETS.filter((preset) => !preset.ratebookOnly || mode === "ratebook").map((preset) => {
            const pressed = presetPressed(preset, query)
            return (
              <button
                key={preset.label}
                type="button"
                aria-pressed={pressed}
                onClick={() => applyPreset(preset)}
                className="focus-ring rounded px-2 py-1 text-[12px]"
                style={{
                  border: "1px solid var(--border)",
                  color: pressed ? "var(--accent)" : "var(--text-secondary)",
                  fontWeight: pressed ? 600 : 400,
                }}
              >
                {preset.label}
              </button>
            )
          })}
        </div>
      </div>
      {analysisFilters.length > 0 && (
        <div role="group" aria-label="Filters" className="flex flex-wrap gap-1">
          {analysisFilters.map(([column, value]) => (
            <button
              key={column}
              type="button"
              aria-label={`Remove filter ${column} = ${formatFilterValue(value)}`}
              onClick={() => setAnalysisFilter(column, undefined)}
              className="focus-ring inline-flex items-center gap-1 rounded px-2 py-0.5 text-[12px]"
              style={{ border: "1px solid var(--border)", color: "var(--text-primary)" }}
            >
              {`${column} = ${formatFilterValue(value)}`}
              <X size={12} aria-hidden />
            </button>
          ))}
        </div>
      )}
      <QuotesBody
        failed={failed ? failure : null}
        onRetry={() => setFailure(null)}
        response={cached?.response ?? null}
        targetName={targetName}
        sort={sort}
        onSort={onSort}
        onFilter={(column, value) => setAnalysisFilter(column, value)}
        onPage={(offset) => setQuery((current) => ({ ...current, offset }))}
      />
    </div>
  )
}

interface QuotesBodyProps {
  failed: { error: string; gone: boolean } | null
  onRetry: () => void
  response: ApplyOptimiserResponse | null
  targetName: string
  sort: SortState<string> | null
  onSort: (column: string) => void
  onFilter: (column: string, value: AnalysisValue) => void
  onPage: (offset: number) => void
}

function QuotesBody({ failed, onRetry, response, targetName, sort, onSort, onFilter, onPage }: QuotesBodyProps) {
  if (failed) {
    return (
      <div role="alert" className="flex items-start gap-2 text-xs px-3 py-2 rounded" style={{ background: "var(--danger-soft)", color: "var(--danger)" }}>
        <AlertCircle size={14} className="mt-0.5 shrink-0" />
        <span className="flex-1">Per-quote detail could not be loaded: {failed.error}</span>
        {!failed.gone && (
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
  if (!response) {
    return (
      <div className="flex items-center gap-2 text-xs" style={{ color: "var(--text-muted)" }}>
        <Loader2 size={14} className="animate-spin" />
        Loading per-quote detail...
      </div>
    )
  }

  const columns: SortableValuesColumn<QuoteRow, string>[] = response.columns.map((column) => ({
    key: column.name,
    label: columnLabel(column),
    align: column.role === "id" || column.role === "analysis" ? "left" : "right",
    sort: column.sortable ? "sortable" : "none",
    cell: (row: QuoteRow): ReactNode => {
      const value = row[column.name]
      if (column.role === "scenario") return <ScenarioValue value={value} />
      if (column.filterable) {
        const filterValue = asAnalysisValue(value, column.name)
        return (
          <button
            type="button"
            aria-label={`Show only quotes with ${column.name} = ${formatFilterValue(filterValue)}`}
            onClick={() => onFilter(column.name, filterValue)}
            className="focus-ring underline decoration-dotted"
          >
            {formatCell(value)}
          </button>
        )
      }
      return formatCell(value)
    },
  }))
  const { offset, matched_row_count: matched, row_count: total, preview_row_count: shown, preview_row_limit: limit } = response
  const pageEnd = offset + shown
  const summary = shown > 0
    ? `Showing ${(offset + 1).toLocaleString()}–${pageEnd.toLocaleString()} of ${matched.toLocaleString()} matching (of ${total.toLocaleString()})`
    : matched === 0
      ? `No quotes match (of ${total.toLocaleString()})`
      : `No quotes on this page: ${matched.toLocaleString()} matching (of ${total.toLocaleString()})`
  const moreMatch = offset + limit < matched
  const nextWithinDepth = offset + 2 * limit <= PAGE_DEPTH_LIMIT
  return (
    <div className="space-y-2">
      <SortableValuesTable
        label="Per-quote detail"
        columns={columns}
        rows={response.preview}
        rowKey={(row) => String(row.quote_id)}
        sort={sort}
        onSort={onSort}
        emptyMessage={`No quotes of ${targetName} match.`}
        className="w-full text-[12px] font-mono [&_td]:tabular-nums [&_td]:whitespace-nowrap"
        maxHeight={360}
      />
      <div className="flex flex-wrap items-center gap-2 text-[12px]" style={{ color: "var(--text-secondary)" }}>
        <span role="status">{summary}</span>
        <button
          type="button"
          disabled={offset === 0}
          onClick={() => onPage(Math.max(0, offset - limit))}
          className="focus-ring rounded px-2 py-0.5 disabled:opacity-50"
          style={{ border: "1px solid var(--border)" }}
        >
          Previous
        </button>
        <button
          type="button"
          disabled={!moreMatch || !nextWithinDepth}
          onClick={() => onPage(offset + limit)}
          className="focus-ring rounded px-2 py-0.5 disabled:opacity-50"
          style={{ border: "1px solid var(--border)" }}
        >
          Next
        </button>
      </div>
      {moreMatch && !nextWithinDepth && (
        <p role="note" className="text-[12px]" style={{ color: "var(--text-muted)" }}>
          {`Pages reach the first ${PAGE_DEPTH_LIMIT.toLocaleString()} rows; narrow the filter or search the quote ID to see the rest.`}
        </p>
      )}
    </div>
  )
}
