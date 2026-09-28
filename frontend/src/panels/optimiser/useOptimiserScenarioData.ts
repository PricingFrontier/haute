/**
 * The optimiser node's pre-solve scenario data: its series, its quotes grouped
 * from the node's preview rows, quote navigation, and per-scenario statistics.
 * Shared by the data preview and the result workspace's Curves and Statistics
 * panes (`OptimiserScenarioPanes`).
 */

import { useCallback, useMemo, useState } from "react"
import type { PreviewData } from "../DataPreview"
import { computeScenarioStatsBySeries, type ScenarioStats } from "../optimiserScenarioStats"

export const OPTIMISER_DATA_PREVIEW_ROW_LIMIT = 5_000

export type QuoteRow = {
  scenarioIndex: number
  scenarioValue: number
  values: Record<string, number> // objective + constraint column values
}

type PreviewRow = PreviewData["preview"][number]

function toQuoteRow(
  row: PreviewRow,
  scenarioIndexCol: string,
  scenarioValueCol: string,
  allSeries: string[],
): QuoteRow {
  const vals: Record<string, number> = {}
  for (const col of allSeries) {
    vals[col] = Number(row[col] ?? 0)
  }
  return {
    scenarioIndex: Number(row[scenarioIndexCol] ?? 0),
    scenarioValue: Number(row[scenarioValueCol] ?? 0),
    values: vals,
  }
}

export interface OptimiserScenarioData {
  /** The objective column; "" when none is configured. */
  objectiveCol: string
  quoteIdCol: string
  scenarioIndexCol: string
  /** Objective first, then each constraint. */
  allSeries: string[]
  checkedSeries: Set<string>
  toggleSeries: (col: string) => void
  visibleSeries: string[]
  quoteIds: string[]
  clampedIndex: number
  currentQuoteId: string
  currentRows: QuoteRow[]
  goPrev: () => void
  goNext: () => void
  searchValue: string
  setSearchValue: (value: string) => void
  handleSearchSubmit: () => void
  scenarioStatsBySeries: Map<string, ScenarioStats[]>
  /** "N quotes | M scenarios", with the preview budget when rows were capped. */
  metadata: string
}

/**
 * The scenario data of *data* under the optimiser *config*. The statistics are
 * computed only while *statisticsActive*: sorting every series is costly.
 */
export function useOptimiserScenarioData(
  data: PreviewData,
  config: Record<string, unknown>,
  statisticsActive: boolean,
): OptimiserScenarioData {
  const objectiveCol = (config.objective as string) || ""
  const constraintsMap = useMemo(
    () => (config.constraints ?? {}) as Record<string, Record<string, number>>,
    [config.constraints],
  )
  const constraintCols = useMemo(() => Object.keys(constraintsMap), [constraintsMap])
  const quoteIdCol = (config.quote_id as string) || "quote_id"
  const scenarioIndexCol = (config.scenario_index as string) || "scenario_index"
  const scenarioValueCol = (config.scenario_value as string) || "scenario_value"

  // All plottable series: objective first, then constraints
  const allSeries = useMemo(() => {
    const s: string[] = []
    if (objectiveCol) s.push(objectiveCol)
    for (const c of constraintCols) {
      if (c !== objectiveCol) s.push(c)
    }
    return s
  }, [objectiveCol, constraintCols])

  const previewRows = useMemo(
    () =>
      data.preview.length > OPTIMISER_DATA_PREVIEW_ROW_LIMIT
        ? data.preview.slice(0, OPTIMISER_DATA_PREVIEW_ROW_LIMIT)
        : data.preview,
    [data.preview],
  )
  const previewTotalRows = Math.max(data.row_count ?? 0, data.preview.length)
  const previewBudgetLabel =
    previewRows.length < previewTotalRows
      ? `${previewRows.length.toLocaleString()} of ${previewTotalRows.toLocaleString()} rows`
      : null

  // ── Which series are visible: every series again whenever the list changes ──
  // Reset during render (keyed by the series list) so a stale set never paints.
  const allSeriesKey = JSON.stringify(allSeries)
  const [seriesState, setSeriesState] = useState(() => ({
    key: allSeriesKey,
    checked: new Set(allSeries),
  }))
  if (seriesState.key !== allSeriesKey) {
    setSeriesState({ key: allSeriesKey, checked: new Set(allSeries) })
  }
  const checkedSeries = useMemo(
    () => (seriesState.key === allSeriesKey ? seriesState.checked : new Set(allSeries)),
    [allSeries, allSeriesKey, seriesState],
  )

  const toggleSeries = useCallback((col: string) => {
    setSeriesState((prev) => {
      const next = new Set(prev.checked)
      if (next.has(col)) next.delete(col)
      else next.add(col)
      return { ...prev, checked: next }
    })
  }, [])

  // ── Group preview rows by quote_id ──
  const { quoteIds, quoteRowsByQuote } = useMemo(() => {
    const map = new Map<string, PreviewRow[]>()
    const ids: string[] = []
    for (const row of previewRows) {
      const qid = String(row[quoteIdCol] ?? "")
      const rows = map.get(qid)
      if (rows) {
        rows.push(row)
      } else {
        map.set(qid, [row])
        ids.push(qid)
      }
    }
    return { quoteIds: ids, quoteRowsByQuote: map }
  }, [previewRows, quoteIdCol])

  // ── Quote navigation: back to the first quote whenever the quote count changes ──
  const [navigation, setNavigation] = useState({ quoteCount: quoteIds.length, index: 0, search: "" })
  if (navigation.quoteCount !== quoteIds.length) {
    setNavigation({ quoteCount: quoteIds.length, index: 0, search: "" })
  }
  const current = navigation.quoteCount === quoteIds.length
    ? navigation
    : { quoteCount: quoteIds.length, index: 0, search: "" }
  const searchValue = current.search

  const clampedIndex = Math.min(current.index, Math.max(0, quoteIds.length - 1))
  const currentQuoteId = quoteIds[clampedIndex] ?? ""
  const currentRows = useMemo(() => {
    const rows = quoteRowsByQuote.get(currentQuoteId) ?? []
    return rows
      .map((row) => toQuoteRow(row, scenarioIndexCol, scenarioValueCol, allSeries))
      .sort((a, b) => a.scenarioIndex - b.scenarioIndex)
  }, [quoteRowsByQuote, currentQuoteId, scenarioIndexCol, scenarioValueCol, allSeries])

  const goPrev = useCallback(
    () => setNavigation((prev) => ({ ...prev, index: Math.max(0, prev.index - 1) })),
    [],
  )
  const goNext = useCallback(
    () => setNavigation((prev) => ({
      ...prev,
      index: Math.min(quoteIds.length - 1, prev.index + 1),
    })),
    [quoteIds.length],
  )
  const setSearchValue = useCallback(
    (search: string) => setNavigation((prev) => ({ ...prev, search })),
    [],
  )
  const handleSearchSubmit = useCallback(() => {
    const idx = quoteIds.indexOf(searchValue.trim())
    if (idx >= 0) {
      setNavigation((prev) => ({ ...prev, index: idx, search: "" }))
    }
  }, [quoteIds, searchValue])

  const visibleSeries = useMemo(
    () => allSeries.filter((s) => checkedSeries.has(s)),
    [allSeries, checkedSeries],
  )

  // ── Scenario-level summary statistics (aggregated across all quotes) ──
  // Deferred: only computed while the statistics pane is showing to avoid
  // expensive O(n·log·n × #series) sorting on every render.
  const scenarioStatsBySeries = useMemo(() => {
    if (!statisticsActive) return new Map<string, ScenarioStats[]>()
    return computeScenarioStatsBySeries({
      rows: previewRows,
      series: allSeries,
      scenarioIndexCol,
      scenarioValueCol,
    })
  }, [allSeries, previewRows, scenarioIndexCol, scenarioValueCol, statisticsActive])

  const metadata = `${quoteIds.length.toLocaleString()} quotes | ${currentRows.length.toLocaleString()} scenarios${previewBudgetLabel ? ` | ${previewBudgetLabel}` : ""}`

  return {
    objectiveCol,
    quoteIdCol,
    scenarioIndexCol,
    allSeries,
    checkedSeries,
    toggleSeries,
    visibleSeries,
    quoteIds,
    clampedIndex,
    currentQuoteId,
    currentRows,
    goPrev,
    goNext,
    searchValue,
    setSearchValue,
    handleSearchSubmit,
    scenarioStatsBySeries,
    metadata,
  }
}

/** Why there is nothing to chart, or null when the scenario data can be shown. */
export function scenarioDataUnavailable(
  scenario: OptimiserScenarioData,
): "no_objective" | "no_data" | null {
  if (!scenario.objectiveCol) return "no_objective"
  if (scenario.quoteIds.length === 0 || scenario.currentRows.length === 0) return "no_data"
  return null
}
