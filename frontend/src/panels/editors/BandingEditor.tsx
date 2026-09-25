import { useState } from "react"
import { Plus, AlertTriangle } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import { CommittedTextField } from "../../components/form"
import type { InputSource, OnUpdateConfig, SimpleNode } from "./_shared"
import type { CategoricalRule, BandingFactor, BandingMode, BreakpointRule } from "../../types/banding"
import {
  normaliseBandingFactors,
  inferBandingType,
  isNumericDtype,
  suggestOutputColumn,
  detectDuplicateCategorical,
  categoricalRuleCounts,
  generateSettingsFromBreakpoints,
  breakpointKinds,
  boundarySplitDayNumber,
  previewValueDayNumber,
  dayNumberToDate,
  calendarSettingsFromBreakpoints,
} from "./banding/bandingUtils"
import { isTemporalDtype } from "../../utils/polarsDtypes"
import { BandingRulesGrid } from "./banding/BandingRulesGrid"
import { BreakpointGrid } from "./banding/BreakpointGrid"
import { BandingHistogram } from "./banding/BandingHistogram"
import useBandingStats from "./banding/useBandingStats"
import { useGraph } from "../useGraph"
import { GenerateBandsDialog } from "./banding/GenerateBandsDialog"
import { CategoricalValuePicker } from "./banding/CategoricalValuePicker"
import { withAlpha } from "../../utils/color"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import SearchableItemList from "./shared/SearchableItemList"
import { useSearchableList, type SearchableListItem } from "./shared/useSearchableList"

const EMPTY_CATEGORICAL: CategoricalRule = { value: "", assignment: "" }

export default function BandingEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  upstreamColumns = [],
  accentColor,
  previewRows,
  nodeId,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns?: { name: string; dtype: string }[]
  accentColor: string
  /** Preview rows from the banding node's output (includes input columns). */
  previewRows?: Record<string, unknown>[]
  /** The node being edited, so its shared data point can be read. */
  nodeId?: string
}) {
  const factors = normaliseBandingFactors(config)
  const [activeIdx, setActiveIdx] = useState(0)
  const safeIdx = Math.max(0, Math.min(activeIdx, factors.length - 1))
  const factor = factors[safeIdx]
  const [showGenerateDialog, setShowGenerateDialog] = useState(false)

  const colMap = Object.fromEntries(upstreamColumns.map(c => [c.name, c.dtype]))

  // The whole dataset this node reads, when it is cached: the counts, values
  // and distribution below are execution's rather than a sample's.
  const graph = useGraph()
  const node = nodeId ? graph.allNodes.find((candidate: SimpleNode) => candidate.id === nodeId) ?? null : null
  const { cache, stats, current: statsCurrent, cacheRequired, error: statsError } = useBandingStats({
    node,
    allNodes: graph.allNodes,
    edges: graph.edges,
    submodels: graph.submodels,
    preamble: graph.preamble,
    factor: factors[safeIdx] ?? null,
  })

  const commitFactors = (next: BandingFactor[]) => {
    onUpdate("factors", next)
  }

  const updateFactor = (idx: number, patch: Partial<BandingFactor>) => {
    const next = factors.map((f, i) => i === idx ? { ...f, ...patch } : f)
    commitFactors(next)
  }

  const setColumnWithAutoDetect = (idx: number, colName: string) => {
    const patch: Partial<BandingFactor> = { column: colName }
    const detected = inferBandingType(colName, colMap)
    if (detected && detected !== factors[idx].banding) {
      patch.banding = detected
      patch.rules = []
    }
    // Auto-suggest output column
    const currentFactor = factors[idx]
    const prevSuggestion = currentFactor.column ? suggestOutputColumn(currentFactor.column) : ""
    if (!currentFactor.outputColumn || currentFactor.outputColumn === prevSuggestion) {
      patch.outputColumn = colName ? suggestOutputColumn(colName) : ""
    }
    updateFactor(idx, patch)
  }

  const switchBandingType = (newType: BandingMode) => {
    const current = factors[safeIdx]
    const prevRules = { ...(current._prevRules || {}), [current.banding]: current.rules }
    const restoredRules = prevRules[newType] || []
    updateFactor(safeIdx, {
      banding: newType,
      rules: restoredRules,
      _prevRules: prevRules,
    })
  }

  // A new factor is Numeric; choosing a column that is not numeric makes it
  // categorical.
  const addFactor = () => {
    const added: BandingFactor = { banding: "breakpoints", column: "", outputColumn: "", rules: [], default: null }
    const next = [...factors, added]
    commitFactors(next)
    setActiveIdx(next.length - 1)
    factorList.reset()
  }

  const removeFactor = (idx: number) => {
    if (factors.length <= 1) return
    const next = factors.filter((_, i) => i !== idx)
    commitFactors(next)
    if (safeIdx >= next.length) setActiveIdx(next.length - 1)
  }

  // List order is the order execution applies the factors in: a row dragged
  // onto another, or moved with Alt+Up/Down, takes its place.
  const moveFactor = (from: number, to: number) => {
    if (from === to || to < 0 || to >= factors.length) return
    const next = [...factors]
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    commitFactors(next)
    const current = safeIdx
    setActiveIdx(
      current === from ? to
        : from < current && current <= to ? current - 1
          : to <= current && current < from ? current + 1
            : current,
    )
  }

  const factorItems: SearchableListItem[] = factors.map((f, i) => {
    const ruleCount = (f.rules || []).length
    const issues = [
      ...(f.column ? [] : ["No input column"]),
      ...(f.outputColumn ? [] : ["No output column"]),
      ...(ruleCount > 0 ? [] : ["No rules yet"]),
    ]
    return {
      index: i,
      name: f.outputColumn || f.column || `Column ${i + 1}`,
      searchTerms: f.column ? [f.column] : [],
      healthy: issues.length === 0,
      issues,
      badges: [`${ruleCount} rule${ruleCount === 1 ? "" : "s"}`],
    }
  })
  // A factor the search or filter hides is not the one being edited.
  const factorList = useSearchableList(factorItems, safeIdx, setActiveIdx)

  // Numeric bands compare the column against number or date boundaries, so a
  // column whose dtype is known and is neither cannot use them.
  const columnDtype = factor.column ? colMap[factor.column] : undefined
  const numericUnavailableReason =
    columnDtype && !isNumericDtype(columnDtype) && !isTemporalDtype(columnDtype)
      ? `${factor.column} is a ${columnDtype} column; numeric bands need a number or date column.`
      : undefined

  // A Numeric factor bands dates when its column is a Date or Datetime, or when
  // its breakpoints are dates (as on a column whose dtype is not known). Its
  // boundaries and values are then counted and drawn as wall-clock day numbers,
  // the unit of the whole-dataset statistics.
  const rightClosed = factor.rightClosed ?? true
  const breakpointRules = factor.banding === "breakpoints" ? (factor.rules || []) as BreakpointRule[] : []
  const boundaryKinds = breakpointKinds(breakpointRules)
  const temporal =
    factor.banding === "breakpoints" &&
    ((columnDtype !== undefined && isTemporalDtype(columnDtype)) ||
      boundaryKinds.has("date") ||
      boundaryKinds.has("datetime"))
  /** A preview value as a number (a day number for dates), for Generate's starting range. */
  const previewNumber = (raw: unknown): number | null => {
    if (temporal) return previewValueDayNumber(raw)
    // A missing value is missing, not zero: `Number(null)` is 0.
    if (raw === null || raw === undefined || raw === "") return null
    const value = Number(raw)
    return isNaN(value) ? null : value
  }

  // ─── Match counts ─────────────────────────────────────────────
  // Every number here describes the whole dataset; none comes from the preview.
  // While the answer to a rule edit is on its way, the last answer still
  // describes the column, so it stays: categorical counts follow the edit from
  // the data's value counts, and a count not yet known is pending (null).
  const availability = cache.availability
  // The whole dataset's answer is on its way (rather than needing a Refresh,
  // which the server can say even of a point that looked current).
  const counting =
    node !== null &&
    !stats &&
    !statsError &&
    !cacheRequired &&
    (availability === "checking" || availability === "current" || availability === "building")
  const wholeDataCounts = (() => {
    const rules = factor.rules || []
    if (!stats || !rules.length) return undefined
    if (statsCurrent) return stats.rule_counts
    if (factor.banding === "categorical") {
      const complete = stats.distinct_count !== null && stats.values.length >= stats.distinct_count
      const valueCounts = new Map(stats.values.map(({ value, count }) => [value, count]))
      return categoricalRuleCounts(rules as CategoricalRule[], valueCounts, complete)
    }
    return rules.map(() => null)
  })()
  const pendingCounts = counting && (factor.rules || []).length ? (factor.rules || []).map(() => null) : undefined
  const matchCounts = stats ? wholeDataCounts : pendingCounts
  const totalRows = stats ? stats.total_rows : 0
  const knownCounts = matchCounts?.every((count) => count !== null) ? (matchCounts as number[]) : null
  const unmatchedCount =
    (statsCurrent ? stats?.unmatched_count : null) ??
    (knownCounts ? Math.max(totalRows - knownCounts.reduce((a, b) => a + b, 0), 0) : null)

  // ─── Validation warnings ──────────────────────────────────────
  const warnings =
    factor.banding === "categorical"
      ? detectDuplicateCategorical((factor.rules || []) as CategoricalRule[]).map(
          (d) => `Duplicate value "${d.value}" in rules ${d.indices.map(i => i + 1).join(", ")}`,
        )
      : []

  // ─── Histogram data ───────────────────────────────────────────
  const histogramData = (() => {
    if (factor.banding !== "breakpoints") return null
    if (!factor.column) return null

    const boundaries: number[] = []
    for (const bp of breakpointRules) {
      // The open-ended band's boundary is blank, and Number("") is 0.
      if ((bp.boundary ?? "").trim() === "") continue
      // A date's band holds the whole of its day, so it is drawn ending there.
      const position = temporal ? boundarySplitDayNumber(bp.boundary, rightClosed) : Number(bp.boundary)
      if (position !== null && !isNaN(position)) boundaries.push(position)
    }
    // Only the whole dataset's distribution is drawn, never the preview's.
    return stats?.bins.length ? { bins: stats.bins, boundaries } : null
  })()

  // ─── Categorical available values ─────────────────────────────
  const categoricalValues = (() => {
    if (factor.banding !== "categorical" || !factor.column) return null
    // Every value in the data, as the text execution matches on, when the point
    // is cached: a rare category is missing from a sample by definition.
    if (stats) return stats.values.map(({ value, count }) => ({ value, count }))
    // Until then the preview's values help write rules, but without counts.
    if (!previewRows?.length) return null
    const values = new Set<string>()
    for (const row of previewRows) {
      const raw = row[factor.column]
      if (raw === null || raw === undefined) continue
      const value = String(raw)
      if (value) values.add(value)
    }
    return Array.from(values).sort().map((value) => ({ value }))
  })()

  // ─── Data min/max for generate dialog ─────────────────────────
  // Day numbers on a date factor, which Generate is given as dates.
  const dataMinMax = (() => {
    if (stats && stats.minimum !== null && stats.minimum !== undefined) {
      return { dataMin: stats.minimum, dataMax: stats.maximum ?? undefined }
    }
    if (!factor.column || !previewRows?.length) return { dataMin: undefined, dataMax: undefined }
    let min = Infinity, max = -Infinity
    for (const row of previewRows) {
      const v = previewNumber(row[factor.column])
      if (v !== null) { if (v < min) min = v; if (v > max) max = v }
    }
    return min <= max ? { dataMin: min, dataMax: max } : { dataMin: undefined, dataMax: undefined }
  })()
  const asDate = (dayNumber: number | undefined) => (dayNumber === undefined ? undefined : dayNumberToDate(dayNumber))

  // Breakpoints have their own add; this one is the categorical rules table's.
  const handleAddRule = () => {
    updateFactor(safeIdx, { rules: [...(factor.rules || []), { ...EMPTY_CATEGORICAL }] })
  }

  const handleAddCategoricalValue = (value: string) => {
    const newRule: CategoricalRule = { value, assignment: value }
    updateFactor(safeIdx, { rules: [...(factor.rules || []), newRule] })
  }

  const handleGenerateBands = (breakpoints: { boundary: string; label: string }[]) => {
    updateFactor(safeIdx, { rules: breakpoints as BreakpointRule[] })
    setShowGenerateDialog(false)
  }

  // Check if breakpoints are empty (for showing prominent Generate action)
  const breakpointsEmpty = factor.banding === "breakpoints" && (factor.rules || []).length === 0

  // Generate's options open where it was pressed: above the breakpoints, or in
  // place of the empty prompt. Regenerating starts from the settings the
  // breakpoints were made with.
  const generateOptions = temporal ? (
    <GenerateBandsDialog
      temporal
      onGenerate={handleGenerateBands}
      onClose={() => setShowGenerateDialog(false)}
      accentColor={accentColor}
      dataMin={asDate(dataMinMax.dataMin)}
      dataMax={asDate(dataMinMax.dataMax)}
      rightClosed={rightClosed}
      initial={calendarSettingsFromBreakpoints(breakpointRules, rightClosed)}
    />
  ) : (
    <GenerateBandsDialog
      onGenerate={handleGenerateBands}
      onClose={() => setShowGenerateDialog(false)}
      accentColor={accentColor}
      dataMin={dataMinMax.dataMin}
      dataMax={dataMinMax.dataMax}
      initial={
        factor.banding === "breakpoints"
          ? generateSettingsFromBreakpoints((factor.rules || []) as BreakpointRule[])
          : null
      }
    />
  )


  // Whose rows the numbers describe, or why there are none yet.
  const REFRESH = "Refresh this node to count all rows"
  const statusLabel = statsError
    ? `Counting the whole dataset failed: ${statsError}`
    : stats
      ? `All rows · ${totalRows.toLocaleString()}`
      : availability === "building"
        ? "Caching the data…"
        : availability === "stale"
          ? `Cached data is out of date · ${REFRESH}`
          : counting
            ? "Counting…"
            : `Not cached · ${REFRESH}`

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {node && (
        <div className="flex items-center justify-between gap-2" data-testid="banding-data-basis">
          <span
            className="text-[11px]"
            style={{ color: statsError ? "var(--danger)" : "var(--text-muted)" }}
            title={statsError ?? undefined}
          >
            {statusLabel}
          </span>
        </div>
      )}

      {/* Factors: always listed, so the first column does not move the layout */}
      <SearchableItemList
        list={factorList}
        selectedIndex={safeIdx}
        onSelect={setActiveIdx}
        onAdd={addFactor}
        onRemove={factors.length > 1 ? removeFactor : undefined}
        onMove={moveFactor}
        labels={{
          list: "Banding columns",
          search: "Search banding columns",
          add: "Add column",
          remove: (name) => `Remove ${name} column`,
          status: (healthy) => (healthy ? "complete" : "incomplete"),
          empty: "No matching columns",
        }}
        accentColor={accentColor}
      />

      {factorList.noneVisible ? (
        <div className="px-2 py-4 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Select a matching column to edit it
        </div>
      ) : (
        <>
      {/* Active factor config */}
      <div>
        <div className="flex items-center gap-1.5">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Type</label>
        </div>
        <div className="mt-1">
          <ToggleButtonGroup
            value={factor.banding}
            onChange={switchBandingType}
            options={[
              {
                key: "breakpoints" as BandingMode,
                label: "Numeric",
                disabled: numericUnavailableReason !== undefined,
                disabledReason: numericUnavailableReason,
              },
              { key: "categorical" as BandingMode, label: "Categorical" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={`banding-input-col-${safeIdx}`} className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Input Column</label>
          {upstreamColumns.length > 0 ? (
            <select
              id={`banding-input-col-${safeIdx}`}
              key={`col-${safeIdx}`}
              value={factor.column}
              onChange={(e) => setColumnWithAutoDetect(safeIdx, e.target.value)}
              className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
              style={INPUT_STYLE}
            >
              <option value="">Select column...</option>
              {upstreamColumns.map(c => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.dtype})
                </option>
              ))}
            </select>
          ) : (
            <CommittedTextField
              id={`banding-input-col-${safeIdx}`}
              key={`col-${safeIdx}`}
              type="text" value={factor.column || ""}
              onCommit={(v) => updateFactor(safeIdx, { column: v })}
              className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
              style={INPUT_STYLE} />
          )}
        </div>
        <div>
          <label htmlFor={`banding-output-col-${safeIdx}`} className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Output Column</label>
          <CommittedTextField
            id={`banding-output-col-${safeIdx}`}
            key={`out-${safeIdx}`}
            type="text"
            placeholder=""
            value={factor.outputColumn || ""}
            onCommit={(v) => updateFactor(safeIdx, { outputColumn: v })}
            className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
            style={INPUT_STYLE} />
        </div>
      </div>

      {/* Histogram */}
      {histogramData && (
        <BandingHistogram
          bins={histogramData.bins}
          boundaries={histogramData.boundaries}
          accentColor={accentColor}
          formatValue={temporal ? dayNumberToDate : undefined}
        />
      )}

      {/* Categorical value picker */}
      {categoricalValues && (
        <CategoricalValuePicker
          availableValues={categoricalValues}
          existingValues={(factor.rules || []).map(r => (r as CategoricalRule).value).filter(Boolean)}
          onAddValue={handleAddCategoricalValue}
          accentColor={accentColor}
        />
      )}

      {/* Rules grid + add button */}
      {factor.banding === "breakpoints" ? (
        <div className="space-y-2">
          {breakpointsEmpty ? (
            showGenerateDialog ? generateOptions : (
            /* Prominent empty state for breakpoints */
            <div
              className="rounded-lg px-4 py-5 text-center space-y-3"
              style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
            >
              <div className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
                No breakpoints yet.
              </div>
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={() => setShowGenerateDialog(true)}
                  className="px-3 py-1.5 rounded-md text-[11px] font-medium transition-colors"
                  style={{
                    background: withAlpha(accentColor, 0.15),
                    border: `1px solid ${withAlpha(accentColor, 0.4)}`,
                    color: accentColor,
                  }}
                >
                  Generate even bands
                </button>
                <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>or</span>
                <button
                  onClick={() => updateFactor(safeIdx, { rules: [{ boundary: "", label: "" } as BreakpointRule] })}
                  className="px-3 py-1.5 rounded-md text-[11px] font-medium transition-colors"
                  style={{
                    background: "var(--bg-panel)",
                    border: "1px solid var(--border)",
                    color: "var(--text-secondary)",
                  }}
                >
                  Add manually
                </button>
              </div>
            </div>
            )
          ) : (
            <>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
                  Breakpoints ({(factor.rules || []).filter(r => (r as BreakpointRule).boundary?.trim() !== "").length})
                </label>
                <button
                  onClick={() => setShowGenerateDialog(true)}
                  className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
                  style={{ background: withAlpha(accentColor, 0.1), color: accentColor, border: `1px solid ${withAlpha(accentColor, 0.3)}` }}
                >
                  Generate
                </button>
              </div>
              {showGenerateDialog && generateOptions}
              <BreakpointGrid
                breakpoints={(factor.rules || []) as BreakpointRule[]}
                onUpdate={(bps) => updateFactor(safeIdx, { rules: bps })}
                rightClosed={rightClosed}
                accentColor={accentColor}
                matchCounts={matchCounts}
                temporal={temporal}
              />
            </>
          )}
        </div>
      ) : (
        <div>
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              Rules ({(factor.rules || []).length})
            </label>
            <button
              onClick={handleAddRule}
              className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
              style={{ background: withAlpha(accentColor, 0.1), color: accentColor, border: `1px solid ${withAlpha(accentColor, 0.3)}` }}
            >
              <Plus size={11} /> Add
            </button>
          </div>
          <BandingRulesGrid
            key={safeIdx}
            factor={factor}
            onUpdateFactor={(patch) => updateFactor(safeIdx, patch)}
            accentColor={accentColor}
            matchCounts={matchCounts}
            onAddRule={handleAddRule}
          />
        </div>
      )}

      {/* Validation warnings */}
      {warnings.length > 0 && (
        <div className="space-y-1">
          {warnings.map((w, i) => (
            <div key={i} className="flex items-center gap-1.5 px-2.5 py-1.5 rounded text-[11px]"
              style={{ background: 'var(--warning-soft-strong)', border: '1px solid var(--warning-border-emphasis)', color: 'var(--warning-strong)' }}>
              <AlertTriangle size={12} />
              <span>{w}</span>
            </div>
          ))}
        </div>
      )}

      {/* Default value */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
            Default <span className="ml-1.5 normal-case tracking-normal font-normal">(unmatched rows)</span>
          </label>
          {matchCounts && totalRows > 0 && (
            <span
              className="text-[10px] font-medium"
              style={{
                color:
                  unmatchedCount === null
                    ? 'var(--text-muted)'
                    : unmatchedCount === 0
                      ? 'var(--success)'
                      : 'var(--warning-strong)',
              }}
            >
              {unmatchedCount ?? "…"} of {totalRows} rows
            </span>
          )}
        </div>
        <CommittedTextField
          key={`def-${safeIdx}`}
          type="text" value={factor.default ?? ""}
          onCommit={(v) => updateFactor(safeIdx, { default: v !== "" ? v : null })}
          className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
          style={INPUT_STYLE} />
      </div>
        </>
      )}
    </div>
  )
}
