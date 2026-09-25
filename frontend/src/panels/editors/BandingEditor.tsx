import { useState } from "react"
import { X, Plus, Copy, AlertTriangle } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import { CommittedTextField } from "../../components/form"
import type { InputSource, OnUpdateConfig, SimpleNode } from "./_shared"
import type { ContinuousRule, CategoricalRule, BandingFactor, BandingMode, BreakpointRule } from "../../types/banding"
import {
  normaliseBandingFactors,
  inferBandingType,
  suggestOutputColumn,
  detectOverlaps,
  detectGaps,
  validateRule,
  detectDuplicateCategorical,
  matchesContinuousRule,
  breakpointsToRules,
} from "./banding/bandingUtils"
import { BandingRulesGrid } from "./banding/BandingRulesGrid"
import { BreakpointGrid } from "./banding/BreakpointGrid"
import { BandingHistogram } from "./banding/BandingHistogram"
import { equalWidthBins } from "./banding/bandingBins"
import useBandingStats from "./banding/useBandingStats"
import { useGraph } from "../useGraph"
import { GenerateBandsDialog } from "./banding/GenerateBandsDialog"
import { CategoricalValuePicker } from "./banding/CategoricalValuePicker"
import { withAlpha } from "../../utils/color"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"

const EMPTY_CONTINUOUS: ContinuousRule = { op1: ">", val1: "", op2: "", val2: "", assignment: "" }
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
  const { stats, loading: statsLoading, basis, error: statsError } = useBandingStats({
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
      patch.banding = detected as BandingMode
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

  const addFactor = () => {
    const next = [...factors, { banding: "continuous" as const, column: "", outputColumn: "", rules: [] as (ContinuousRule | CategoricalRule | BreakpointRule)[], default: null }]
    commitFactors(next)
    setActiveIdx(next.length - 1)
  }

  const duplicateFactor = (idx: number) => {
    const src = factors[idx]
    const dup: BandingFactor = {
      banding: src.banding,
      column: "",
      outputColumn: "",
      rules: src.rules.map(r => ({ ...r })),
      default: src.default,
    }
    const next = [...factors, dup]
    commitFactors(next)
    setActiveIdx(next.length - 1)
  }

  const removeFactor = (idx: number) => {
    if (factors.length <= 1) return
    const next = factors.filter((_, i) => i !== idx)
    commitFactors(next)
    if (safeIdx >= next.length) setActiveIdx(next.length - 1)
  }

  const tabLabel = (f: BandingFactor, i: number) => {
    if (f.outputColumn) return f.outputColumn
    if (f.column) return f.column
    return `Column ${i + 1}`
  }

  const isFactorComplete = (f: BandingFactor) =>
    !!(f.column && f.outputColumn && (f.rules || []).length > 0)

  // ─── Determine if type toggle should be shown ─────────────────
  // Hide when: single unconfigured factor (no column selected and no rules)
  const shouldShowTypeToggle = factors.length > 1 || factor.column !== "" || (factor.rules || []).length > 0

  // ─── Determine if tabs should be shown ────────────────────────
  // Hide tabs when there's only 1 factor and it's unconfigured
  const singleUnconfigured = factors.length === 1 && !factors[0].column && !factors[0].outputColumn
  const shouldShowTabs = factors.length > 1 || !singleUnconfigured

  // ─── Match counts ─────────────────────────────────────────────
  const previewMatchCounts = (() => {
    if (!previewRows?.length || !factor.column) return undefined
    const column = factor.column
    const rules = factor.rules || []
    if (!rules.length) return undefined

    if (factor.banding === "categorical") {
      // Execution builds one remap in rule order, so a value named by several
      // rules is claimed by the *last* of them, and a rule missing either its
      // value or its assignment claims nothing at all.
      const counts = new Array<number>(rules.length).fill(0)
      const claimant = new Map<string, number>()
      rules.forEach((rule, index) => {
        const { value, assignment } = rule as CategoricalRule
        if (value && assignment) claimant.set(value, index)
      })
      for (const row of previewRows) {
        const raw = row[column]
        if (raw === null || raw === undefined) continue
        const index = claimant.get(String(raw))
        if (index !== undefined) counts[index] += 1
      }
      return counts
    }
    // Breakpoints are evaluated as the intervals they become, but counted
    // against the breakpoint the user wrote.
    const sources =
      factor.banding === "breakpoints"
        ? breakpointsToRules(rules as BreakpointRule[], factor.rightClosed ?? true).map(
            (rule, index) => ({ rule, index }),
          )
        : (rules as ContinuousRule[]).map((rule, index) => ({ rule, index }))
    const counts = new Array<number>(rules.length).fill(0)
    for (const row of previewRows) {
      const raw = row[column]
      if (raw === null || raw === undefined || raw === "") continue
      const value = Number(raw)
      if (isNaN(value)) continue
      const claimed = sources.find(({ rule }) => matchesContinuousRule(value, rule))
      // A converted interval keeps its position only when the conversion did
      // not reorder; where it did, the count belongs to the interval shown.
      if (claimed && claimed.index < counts.length) counts[claimed.index] += 1
    }
    return counts
  })()

  // Whole-dataset counts when the point is cached; the sample's otherwise.
  const matchCounts = stats?.rule_counts.length ? stats.rule_counts : previewMatchCounts
  const totalRows = stats ? stats.total_rows : previewRows?.length ?? 0
  const matchedRows = matchCounts ? matchCounts.reduce((a, b) => a + b, 0) : 0
  const unmatchedCount =
    stats?.unmatched_count ?? Math.max(totalRows - matchedRows, 0)

  // ─── Validation warnings ──────────────────────────────────────
  const warnings = (() => {
    const rules = factor.rules || []
    if (!rules.length) return []
    const w: string[] = []

    if (factor.banding === "categorical") {
      const dupes = detectDuplicateCategorical(rules as CategoricalRule[])
      for (const d of dupes) {
        w.push(`Duplicate value "${d.value}" in rules ${d.indices.map(i => i + 1).join(", ")}`)
      }
    } else if (factor.banding === "continuous") {
      const contRules = rules as ContinuousRule[]
      // Individual rule validation
      for (let i = 0; i < contRules.length; i++) {
        const err = validateRule(contRules[i])
        if (err) w.push(`Rule ${i + 1}: ${err}`)
      }
      // Overlaps
      const overlaps = detectOverlaps(contRules)
      for (const o of overlaps) {
        w.push(o.desc)
      }
      // Gaps
      const gaps = detectGaps(contRules)
      for (const g of gaps) {
        w.push(g)
      }
    }
    return w
  })()

  // ─── Histogram data ───────────────────────────────────────────
  const histogramData = (() => {
    if (factor.banding !== "continuous" && factor.banding !== "breakpoints") return null
    if (!factor.column) return null

    const boundaries: number[] = []
    for (const r of (factor.rules || [])) {
      if (factor.banding === "breakpoints") {
        const bp = r as BreakpointRule
        const n = Number(bp.boundary)
        if (!isNaN(n)) boundaries.push(n)
      }
    }
    // The whole dataset's distribution when it is cached; the same shape built
    // from preview rows otherwise, so the picture never mixes the two.
    if (stats) {
      return stats.bins.length ? { bins: stats.bins, boundaries } : null
    }
    if (!previewRows?.length) return null
    const values: number[] = []
    for (const row of previewRows) {
      // A missing value is missing, not zero: `Number(null)` is 0, which would
      // put an observation at the origin and stretch the extent to reach it.
      const raw = row[factor.column]
      if (raw === null || raw === undefined || raw === "") continue
      const v = Number(raw)
      if (!isNaN(v)) values.push(v)
    }
    const bins = equalWidthBins(values, 40)
    return bins.length ? { bins, boundaries } : null
  })()

  // ─── Categorical available values ─────────────────────────────
  const categoricalValues = (() => {
    if (factor.banding !== "categorical" || !factor.column) return null
    // Every value in the data, as the text execution matches on, when the point
    // is cached: a rare category is missing from a sample by definition.
    if (stats) return stats.values.map(({ value, count }) => ({ value, count }))
    if (!previewRows?.length) return null
    const counts = new Map<string, number>()
    for (const row of previewRows) {
      const raw = row[factor.column]
      if (raw === null || raw === undefined) continue
      const v = String(raw)
      if (v) counts.set(v, (counts.get(v) || 0) + 1)
    }
    return Array.from(counts.entries())
      .map(([value, count]) => ({ value, count }))
      .sort((a, b) => b.count - a.count)
  })()

  // ─── Data min/max for generate dialog ─────────────────────────
  const dataMinMax = (() => {
    if (stats && stats.minimum !== null && stats.minimum !== undefined) {
      return { dataMin: stats.minimum, dataMax: stats.maximum ?? undefined }
    }
    if (!factor.column || !previewRows?.length) return { dataMin: undefined, dataMax: undefined }
    let min = Infinity, max = -Infinity
    for (const row of previewRows) {
      const raw = row[factor.column]
      if (raw === null || raw === undefined || raw === "") continue
      const v = Number(raw)
      if (!isNaN(v)) { if (v < min) min = v; if (v > max) max = v }
    }
    return min <= max ? { dataMin: min, dataMax: max } : { dataMin: undefined, dataMax: undefined }
  })()

  const handleAddRule = () => {
    if (factor.banding === "breakpoints") return // breakpoints have their own add
    const empty = factor.banding === "continuous" ? { ...EMPTY_CONTINUOUS } : { ...EMPTY_CATEGORICAL }
    updateFactor(safeIdx, { rules: [...(factor.rules || []), empty] })
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


  const basisLabel =
    basis === "all"
      ? `All rows · ${totalRows.toLocaleString()}`
      : basis === "stale"
        ? "Cached data is out of date"
        : `Sample · ${(previewRows?.length ?? 0).toLocaleString()} rows`

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
            {statsLoading
              ? "Counting…"
              : statsError
                ? `Counting the whole dataset failed: ${statsError}`
                : basisLabel}
          </span>
        </div>
      )}

      {/* Factor tabs — hidden when single unconfigured factor */}
      {shouldShowTabs && (
        <div>
          <div className="flex items-center gap-1 overflow-x-auto flex-nowrap whitespace-nowrap"
            role="tablist" aria-label="Banding columns">
            {factors.map((f, i) => (
              <div
                key={i}
                role="tab"
                id={`banding-tab-${i}`}
                aria-selected={i === safeIdx}
                tabIndex={i === safeIdx ? 0 : -1}
                onClick={() => setActiveIdx(i)}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setActiveIdx(i) } }}
                className="relative flex items-center gap-1 px-2.5 py-1.5 rounded-t-lg text-[11px] font-medium transition-colors cursor-pointer shrink-0"
                style={{
                  background: i === safeIdx ? 'var(--bg-input)' : 'transparent',
                  border: i === safeIdx ? '1px solid var(--border)' : '1px solid transparent',
                  borderBottom: i === safeIdx ? '1px solid var(--bg-input)' : '1px solid var(--border)',
                  color: i === safeIdx ? accentColor : 'var(--text-muted)',
                }}
              >
                {/* Completeness dot */}
                <span
                  className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: isFactorComplete(f) ? 'var(--success)' : 'var(--warning-strong)' }}
                />
                <span className="font-mono truncate max-w-[100px]">{tabLabel(f, i)}</span>
                {/* Duplicate button (only if factor has rules) */}
                {(f.rules || []).length > 0 && (
                  <button
                    type="button"
                    aria-label="Duplicate column"
                    onClick={(e) => { e.stopPropagation(); duplicateFactor(i) }}
                    className="ml-0.5 p-0.5 rounded transition-colors cursor-pointer hover:bg-[rgba(0,0,0,0.1)] focus-visible:bg-[rgba(0,0,0,0.1)]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <Copy size={9} />
                  </button>
                )}
                {factors.length > 1 && (
                  <button
                    type="button"
                    aria-label="Remove column"
                    onClick={(e) => { e.stopPropagation(); removeFactor(i) }}
                    className="ml-0.5 p-0.5 rounded transition-colors cursor-pointer hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <X size={9} />
                  </button>
                )}
              </div>
            ))}
            <button
              onClick={addFactor}
              aria-label="Add column"
              className="flex items-center gap-0.5 px-2 py-1.5 rounded-lg text-[11px] font-medium transition-colors shrink-0 hover:bg-[rgba(0,0,0,0.05)]"
              style={{ color: accentColor }}
            >
              <Plus size={11} />
            </button>
          </div>
          <div style={{ borderTop: '1px solid var(--border)', marginTop: -1 }} />
        </div>
      )}

      {/* Active factor config */}
      {shouldShowTypeToggle && (
        <div role="tabpanel" id="banding-tabpanel" aria-labelledby={`banding-tab-${safeIdx}`}>
          <div className="flex items-center gap-1.5">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Type</label>
          </div>
          <div className="mt-1">
            <ToggleButtonGroup
              value={factor.banding}
              onChange={switchBandingType}
              options={[
                { key: "breakpoints" as BandingMode, label: "Numeric" },
                { key: "categorical" as BandingMode, label: "Categorical" },
              ]}
              accentColor={accentColor}
            />
          </div>
        </div>
      )}

      {/* Empty tabpanel for accessibility when type toggle is hidden but tabs exist */}
      {!shouldShowTypeToggle && shouldShowTabs && (
        <div role="tabpanel" id="banding-tabpanel" aria-labelledby={`banding-tab-${safeIdx}`} />
      )}

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
              <BreakpointGrid
                breakpoints={(factor.rules || []) as BreakpointRule[]}
                onUpdate={(bps) => updateFactor(safeIdx, { rules: bps })}
                rightClosed={factor.rightClosed ?? true}
                accentColor={accentColor}
                matchCounts={matchCounts}
              />
            </>
          )}
          {/* Generate dialog overlay */}
          {showGenerateDialog && (
            <div className="relative">
              <GenerateBandsDialog
                onGenerate={handleGenerateBands}
                onClose={() => setShowGenerateDialog(false)}
                accentColor={accentColor}
                dataMin={dataMinMax.dataMin}
                dataMax={dataMinMax.dataMax}
              />
            </div>
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
              style={{ color: unmatchedCount === 0 ? 'var(--success)' : 'var(--warning-strong)' }}
            >
              {unmatchedCount} of {totalRows} rows
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
    </div>
  )
}
