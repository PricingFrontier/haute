import { useState } from "react"
import { expectRows, followRows, rowIdentity, type RowChange, type RowIdentity } from "../../utils/rowKeys"
import { ArrowRight, Plus } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import { useRowKeys } from "./shared/useRowKeys"
import { CommittedTextField } from "../../components/form"
import { ColumnSelector } from "../modelling/ColumnSelector"
import type { InputSource, OnUpdateConfig, SimpleNode } from "./_shared"
import type { CategoricalRule, BandingFactor, BandingMode, BreakpointRule } from "../../types/banding"
import {
  normaliseBandingFactors,
  inferBandingType,
  isNumericDtype,
  suggestOutputColumn,
  bandingOutputCollisions,
  bandingOutputCollisionMessage,
  categoricalRuleCounts,
  generateSettingsFromBreakpoints,
  breakpointKinds,
  boundarySplitDayNumber,
  previewValueDayNumber,
  dayNumberToDate,
  calendarSettingsFromBreakpoints,
  bandName,
  ruleForValue,
} from "./banding/bandingUtils"
import { castFamily, isStringOrCategoricalDtype, isTemporalDtype, type CastFamily } from "../../utils/polarsDtypes"
import { BandingRulesGrid } from "./banding/BandingRulesGrid"
import { BreakpointGrid } from "./banding/BreakpointGrid"
import { BandingHistogram } from "./banding/BandingHistogram"
import useBandingStats from "./banding/useBandingStats"
import { wholeDataStatusText } from "./shared/useWholeDataAnswer"
import { useGraph } from "../useGraph"
import { useDataInputColumns } from "../../hooks/useDataInputColumns"
import useUIStore, { type BandingRuleStash } from "../../stores/useUIStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import { GenerateBandsDialog } from "./banding/GenerateBandsDialog"
import { CategoricalValuePicker } from "./banding/CategoricalValuePicker"
import { ImportParamValues } from "./banding/ImportParamValues"
import { withAlpha } from "../../utils/color"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import SearchableItemList from "./shared/SearchableItemList"
import { indexAfterMove, isListMove, moveListItem } from "./shared/moveListItem"
import { useSearchableList, type SearchableListItem } from "./shared/useSearchableList"
import { readTypedNumber } from "../../utils/typedNumbers"

const EMPTY_CATEGORICAL: CategoricalRule = { value: "", assignment: "" }

/** The column types whose preview values read as the text a categorical rule
 * matches (`cast(pl.Utf8)`): a float's `1.0` arrives in the preview as `1`, and a
 * date or decimal in another form, so those wait for the whole dataset's values. */
const PREVIEW_TEXT_FAMILIES: ReadonlySet<CastFamily | null> = new Set<CastFamily>([
  "text",
  "category",
  "integer",
  "boolean",
])

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
  // The rules a factor had under each banding type, by the factor's row identity, so
  // switching the type back restores them until the pipeline reloads. Editor state,
  // never config: it lives in the UI store under the node's id. The identities follow
  // the editor's own writes of the factors; factors replaced from outside (an Undo, a
  // Redo, a sync frame) take new ones, so no stash passes to another factor. An editor
  // bound to no node keeps none.
  const ruleStash = useUIStore((state) => (nodeId ? state.bandingRuleStash[nodeId] : undefined))
  const setBandingRuleStash = useUIStore((state) => state.setBandingRuleStash)
  // The factors' identities as they stand: the stash's, followed to the factors on screen.
  const factorIdentity = (): RowIdentity => followRows(ruleStash?.identity ?? rowIdentity(factors), factors)
  const safeIdx = Math.max(0, Math.min(activeIdx, factors.length - 1))
  const factor = factors[safeIdx]
  const [showGenerateDialog, setShowGenerateDialog] = useState(false)
  const [showImport, setShowImport] = useState(false)

  // The whole dataset this node reads, when it is cached: the counts, values
  // and distribution below are execution's rather than a sample's. A workbench
  // project bands the quote being priced, so there is no dataset to count: the
  // editor asks nothing and shows no counts.
  const graph = useGraph()
  const workbench = useWorkbenchStore((state) => state.enabled)
  const node = nodeId && !workbench ? graph.allNodes.find((candidate: SimpleNode) => candidate.id === nodeId) ?? null : null

  // The input's columns: those its last preview recorded, else asked for as the editor
  // opens, so a factor whose column the input no longer has is marked at once.
  const inputNode = inputSources.length === 1 && !workbench ? inputSources[0].sourceNodeId : ""
  const fetchedColumns = useDataInputColumns(inputNode, graph.allNodes, graph.edges, graph.submodels, graph.preamble, {
    enabled: upstreamColumns.length === 0 && inputNode !== "",
    fallbackColumns: upstreamColumns,
  })
  const inputColumns = upstreamColumns.length > 0 ? upstreamColumns : fetchedColumns
  const colMap = Object.fromEntries(inputColumns.map(c => [c.name, c.dtype]))

  const { stats, current: statsCurrent, status } = useBandingStats({
    node,
    allNodes: graph.allNodes,
    edges: graph.edges,
    submodels: graph.submodels,
    preamble: graph.preamble,
    factor: factors[safeIdx] ?? null,
  })

  // The factors' identities while the editor shows: what a factor's import is kept by.
  const factorKeys = useRowKeys(factors)

  // Every write of the factors says what it does, so a stash's identities, and the editor's,
  // follow it.
  const commitFactors = (next: BandingFactor[], change: RowChange, rules = ruleStash?.rules) => {
    if (nodeId && rules) setBandingRuleStash(nodeId, { identity: expectRows(factorIdentity(), next, change), rules })
    factorKeys.expect(next, change)
    onUpdate("factors", next)
  }

  const updateFactor = (idx: number, patch: Partial<BandingFactor>, rules = ruleStash?.rules) => {
    const next = factors.map((f, i) => i === idx ? { ...f, ...patch } : f)
    commitFactors(next, { kind: "edit" }, rules)
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
    if (!currentFactor.output_column || currentFactor.output_column === prevSuggestion) {
      patch.output_column = colName ? suggestOutputColumn(colName) : ""
    }
    updateFactor(idx, patch)
  }

  const switchBandingType = (newType: BandingMode) => {
    const current = factors[safeIdx]
    const key = factorIdentity().keys[safeIdx]
    const stash: BandingRuleStash = { ...ruleStash?.rules[key], [current.banding]: current.rules }
    updateFactor(safeIdx, { banding: newType, rules: stash[newType] ?? [] }, { ...ruleStash?.rules, [key]: stash })
  }

  // A new factor is Numeric; choosing a column that is not numeric makes it
  // categorical.
  const addFactor = () => {
    const added: BandingFactor = { banding: "breakpoints", column: "", output_column: "", rules: [], default: null }
    const next = [...factors, added]
    commitFactors(next, { kind: "add" })
    setActiveIdx(next.length - 1)
    factorList.reset()
  }

  const removeFactor = (idx: number) => {
    if (factors.length <= 1) return
    const next = factors.filter((_, i) => i !== idx)
    commitFactors(next, { kind: "remove", index: idx })
    if (safeIdx >= next.length) setActiveIdx(next.length - 1)
  }

  // List order is the order execution applies the factors in: a row dragged
  // onto another, or moved with Alt+Up/Down, takes its place.
  const moveFactor = (from: number, to: number) => {
    if (!isListMove(factors.length, from, to)) return
    commitFactors(moveListItem(factors, from, to), { kind: "move", from, to })
    setActiveIdx(indexAfterMove(safeIdx, from, to))
  }

  // Execution refuses two active factors writing one column, so both are issues.
  const outputCollisions = bandingOutputCollisions(factors)
  // Why a factor cannot take an output column: renaming it in the list
  // refuses such a name, saying why under the box.
  const outputColumnError = (idx: number, candidate: string): string | null => {
    const next = factors.map((f, i) => (i === idx ? { ...f, output_column: candidate } : f))
    const others = bandingOutputCollisions(next).get(idx)
    return others ? bandingOutputCollisionMessage(others, candidate) : null
  }
  // What is wrong with a factor's output column: none, or a name another
  // active factor also writes.
  const nameIssues = (f: BandingFactor, i: number): { missing: string[]; collision: string[] } => {
    const collidesWith = outputCollisions.get(i)
    return {
      missing: f.output_column ? [] : ["No output column"],
      collision: collidesWith ? [bandingOutputCollisionMessage(collidesWith, f.output_column)] : [],
    }
  }
  // A factor's input column, in its row of the list before the arrow to its
  // name: a dropdown with a search box over the input's columns, never typed
  // free (until the input has been previewed, it says no columns are known).
  // Using it selects the row first: choosing a column of another type clears
  // the factor's rules, which must be the rules on screen. Only the selected
  // row's dropdown is in the tab order, so Tab reaches the list once.
  const renderInputColumn = (idx: number, selected: boolean) => {
    const f = factors[idx]
    if (!f) return null
    return (
      <ColumnSelector
        compact
        label={`${factorItems[idx]?.name ?? `Column ${idx + 1}`} input column`}
        value={f.column || ""}
        columns={inputColumns}
        placeholder="Select column..."
        tabIndex={selected ? 0 : -1}
        onFocus={() => { if (!selected) setActiveIdx(idx) }}
        onChange={(column) => {
          setActiveIdx(idx)
          setColumnWithAutoDetect(idx, column)
        }}
      />
    )
  }
  const activeNames = factors[safeIdx] ? nameIssues(factors[safeIdx], safeIdx) : null
  const activeNameIssues = activeNames ? [...activeNames.missing, ...activeNames.collision] : []
  const factorItems: SearchableListItem[] = factors.map((f, i) => {
    const ruleCount = (f.rules || []).length
    const names = nameIssues(f, i)
    const issues = [
      ...(!f.column ? ["No input column"] : inputColumns.length > 0 && !Object.hasOwn(colMap, f.column) ? [`${f.column} is not in the input`] : []),
      ...names.missing,
      ...(ruleCount > 0 ? [] : ["No rules yet"]),
      ...names.collision,
    ]
    return {
      index: i,
      name: f.output_column || f.column || `Column ${i + 1}`,
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
  const rightClosed = factor.right_closed ?? true
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
  // The whole dataset's answer is on its way (rather than needing a Refresh,
  // which the server can say even of a point that looked current, or never
  // coming, when the data cannot be read).
  const counting =
    node !== null && !stats && (status.kind === "counting" || status.kind === "building")
  const wholeDataCounts = (() => {
    const rules = factor.rules || []
    if (!stats || !rules.length) return undefined
    if (statsCurrent) return stats.rule_counts
    if (factor.banding === "categorical") {
      const complete = stats.distinct_count !== null && stats.values.length >= stats.distinct_count
      const valueCounts = new Map(stats.values.map(({ value, count }) => [value, count]))
      const exactKeys = columnDtype !== undefined && isStringOrCategoricalDtype(columnDtype)
      return categoricalRuleCounts(rules as CategoricalRule[], valueCounts, complete, exactKeys)
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

  // ─── Histogram data ───────────────────────────────────────────
  const histogramData = (() => {
    if (factor.banding !== "breakpoints") return null
    if (!factor.column) return null

    const boundaries: number[] = []
    for (const bp of breakpointRules) {
      // The open-ended band's boundary is blank, and Number("") is 0.
      if ((bp.boundary ?? "").trim() === "") continue
      // A date's band holds the whole of its day, so it is drawn ending there.
      const position = temporal ? boundarySplitDayNumber(bp.boundary, rightClosed) : readTypedNumber(bp.boundary)
      if (position !== null) boundaries.push(position)
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
    // Until then the preview's values help write rules, but without counts, and
    // only where the preview gives the text execution matches on.
    if (!previewRows?.length) return null
    const family = columnDtype === undefined ? undefined : castFamily(columnDtype)
    if (family !== undefined && !PREVIEW_TEXT_FAMILIES.has(family)) return null
    const values = new Set<string>()
    for (const row of previewRows) {
      const raw = row[factor.column]
      // A column of unknown type may be a float's 1 that execution reads as "1.0".
      if (family === undefined && typeof raw !== "string") continue
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
    updateFactor(safeIdx, { rules: [...(factor.rules || []), ruleForValue(value)] })
  }

  // A rule mapping each imported value to itself, as a value chip adds one.
  const handleImportValues = (values: string[]) => {
    const imported: CategoricalRule[] = values.map(ruleForValue)
    updateFactor(safeIdx, { rules: [...(factor.rules || []), ...imported] })
    setShowImport(false)
  }

  const handleGenerateBands = (breakpoints: { boundary: string; label: string }[]) => {
    updateFactor(safeIdx, { rules: breakpoints as BreakpointRule[] })
    setShowGenerateDialog(false)
  }

  // Check if breakpoints are empty (for showing prominent Generate action)
  const breakpointsEmpty = factor.banding === "breakpoints" && (factor.rules || []).length === 0
  const categoricalEmpty = factor.banding === "categorical" && (factor.rules || []).length === 0

  // Import opens where it was pressed, as Generate does; another factor's starts afresh. It
  // belongs to its factor by identity, never position: a removed factor's import goes with
  // it, and its read answers nothing into the factor that takes its place.
  const importOptions = (
    <ImportParamValues
      key={`import-${factorKeys.keys[safeIdx]}`}
      existingValues={(factor.rules || []).map(r => (r as CategoricalRule).value).filter(Boolean)}
      onImport={handleImportValues}
      onClose={() => setShowImport(false)}
    />
  )

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


  // Whose rows the numbers describe, or why there are none yet. A failure is
  // said even while the last answer stays on screen.
  const statusText = wholeDataStatusText(status)
  const failed = status.kind === "failed" || (status.kind === "stale" && status.failure !== null)
  const statusLabel =
    status.kind === "failed" || status.kind === "refused" || !stats
      ? statusText ?? "Counting…"
      : `All rows · ${totalRows.toLocaleString()}`

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {node && (
        <div className="flex items-center justify-between gap-2" data-testid="banding-data-basis">
          <span
            className="text-[11px]"
            style={{ color: failed ? "var(--danger)" : "var(--text-muted)" }}
            title={failed ? statusLabel : undefined}
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
        rename={{
          value: (idx) => factors[idx]?.output_column ?? "",
          validate: outputColumnError,
          commit: (idx, name) => updateFactor(idx, { output_column: name }),
        }}
        rowLead={(idx, selected) => (
          <>
            {renderInputColumn(idx, selected)}
            <ArrowRight size={11} aria-hidden="true" className="shrink-0" style={{ color: 'var(--text-muted)' }} />
          </>
        )}
        labels={{
          list: "Banding columns",
          search: "Search banding columns",
          add: "Add banding",
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
      {/* What is wrong with the selected factor's output column: its name is
          set in the list, which has no room to say. The rest of a factor's
          issues show where they are fixed (its input column, its rules). */}
      {activeNameIssues.length > 0 && (
        <div
          data-testid="banding-factor-name-issues"
          className="space-y-1 rounded-lg px-2.5 py-2 text-[10px] font-medium"
          style={{
            background: 'var(--warning-soft-strong)',
            border: '1px solid var(--warning-border-emphasis)',
            color: 'var(--warning-strong)',
          }}
        >
          {activeNameIssues.map(issue => (
            <div key={issue}>{issue}</div>
          ))}
        </div>
      )}

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
      ) : categoricalEmpty ? (
        showImport ? importOptions : (
        <div
          className="rounded-lg px-4 py-5 text-center space-y-3"
          style={{ background: "var(--bg-elevated)", border: "1px solid var(--border)" }}
        >
          <div className="text-[11px] font-medium" style={{ color: "var(--text-muted)" }}>
            No rules yet.
          </div>
          <div className="flex items-center justify-center gap-3">
            <button
              onClick={() => setShowImport(true)}
              className="px-3 py-1.5 rounded-md text-[11px] font-medium transition-colors"
              style={{
                background: withAlpha(accentColor, 0.15),
                border: `1px solid ${withAlpha(accentColor, 0.4)}`,
                color: accentColor,
              }}
            >
              Import from params
            </button>
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>or</span>
            <button
              onClick={handleAddRule}
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
        <div className="space-y-2">
          <div className="flex items-center justify-between mb-1.5">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              Rules ({(factor.rules || []).length})
            </label>
            <div className="flex items-center gap-1.5">
              <button
                onClick={() => setShowImport(true)}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
                style={{ background: withAlpha(accentColor, 0.1), color: accentColor, border: `1px solid ${withAlpha(accentColor, 0.3)}` }}
              >
                Import
              </button>
              <button
                onClick={handleAddRule}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
                style={{ background: withAlpha(accentColor, 0.1), color: accentColor, border: `1px solid ${withAlpha(accentColor, 0.3)}` }}
              >
                <Plus size={11} /> Add
              </button>
            </div>
          </div>
          {showImport && importOptions}
          <BandingRulesGrid
            key={safeIdx}
            factor={factor}
            onUpdateFactor={(patch) => updateFactor(safeIdx, patch)}
            accentColor={accentColor}
            matchCounts={matchCounts}
            addsRuleOnEnter
          />
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
          onCommit={(v) => {
            const name = bandName(v)
            updateFactor(safeIdx, { default: name !== "" ? name : null })
          }}
          className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
          style={INPUT_STYLE} />
      </div>
        </>
      )}
    </div>
  )
}
