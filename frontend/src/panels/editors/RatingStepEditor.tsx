import { useEffect, useState, type ReactNode } from "react"
import { Code2, GitMerge, Plus, Search, Table2, X } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { CodeEditor } from "./CodeEditor"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { classifyBandingLevels } from "../../utils/banding"
import type { RatingFactorColumn, RatingTable } from "./rating/ratingTableUtils"
import {
  normaliseRatingTables,
  buildCartesianEntries,
  ratingTableStatus,
  tableStats,
  extractPreviewCategoricalLevels,
  extractTableEntryFactorLevels,
  mergeFactorLevels,
} from "./rating/ratingTableUtils"
import { OneWayEditor } from "./rating/OneWayEditor"
import { TwoWayGrid } from "./rating/TwoWayGrid"
import { useGraph } from "../useGraph"
import useUIStore, { type RatingStepEditorSection } from "../../stores/useUIStore"

type RatingSection = RatingStepEditorSection
type CombinedOperation = "multiply" | "add" | "min" | "max"

type CombinedOutput = {
  outputColumn: string
  operation: string
  baseValue: string
  isLegacy?: boolean
}

const OPERATION_OPTIONS: { value: CombinedOperation; label: string }[] = [
  { value: "multiply", label: "× Multiply" },
  { value: "add", label: "+ Add" },
  { value: "min", label: "Min" },
  { value: "max", label: "Max" },
]

function isCombinedOperation(value: string): value is CombinedOperation {
  return value === "multiply" || value === "add" || value === "min" || value === "max"
}

function asOperation(value: unknown): CombinedOperation {
  return typeof value === "string" && isCombinedOperation(value) ? value : "multiply"
}

function defaultBaseValue(operation: string): string {
  if (operation === "add") return "0.0"
  return "1.0"
}

function resolveInitialSection(config: Record<string, unknown>): RatingSection {
  if (typeof config.code === "string" && config.code.trim()) return "code"
  if (Array.isArray(config.combinedOutputs) && config.combinedOutputs.length > 0) return "combined"
  if (typeof config.combinedColumn === "string" && config.combinedColumn.trim()) return "combined"
  return "tables"
}

function normaliseCombinedOutputs(config: Record<string, unknown>): CombinedOutput[] {
  const legacyOperation = asOperation(config.operation)
  const legacyOutputColumn = typeof config.combinedColumn === "string" ? config.combinedColumn.trim() : ""
  const legacyOutput = legacyOutputColumn
    ? [{
        outputColumn: legacyOutputColumn,
        operation: legacyOperation,
        baseValue: "",
        isLegacy: true,
      }]
    : []

  if (Array.isArray(config.combinedOutputs) && config.combinedOutputs.length > 0) {
    const configured = config.combinedOutputs.map((raw) => {
      const item = raw && typeof raw === "object" ? raw as Record<string, unknown> : {}
      return {
        outputColumn: typeof item.outputColumn === "string" ? item.outputColumn : "",
        operation: typeof item.operation === "string" && item.operation.trim()
          ? item.operation
          : "multiply",
        baseValue: typeof item.baseValue === "string" || typeof item.baseValue === "number"
          ? String(item.baseValue)
          : "",
      }
    })
    const hasMirroredLegacy = configured.some(output => output.outputColumn.trim() === legacyOutputColumn)
    return legacyOutputColumn && !hasMirroredLegacy ? [...legacyOutput, ...configured] : configured
  }

  return legacyOutput
}

function formulaFor(operation: string, columns: string[], baseValue: string): string {
  const values = [baseValue, ...columns].filter(Boolean)
  if (operation === "add") return values.join(" + ")
  if (operation === "min") return `min(${values.join(", ")})`
  if (operation === "max") return `max(${values.join(", ")})`
  return values.join(" × ")
}

function nextCombinedOutputName(outputs: CombinedOutput[], tableOutputColumns: string[]): string {
  const used = new Set([
    ...tableOutputColumns,
    ...outputs.map(output => output.outputColumn.trim()).filter(Boolean),
  ])
  let idx = outputs.filter(output => !output.isLegacy).length + 1
  while (used.has(`combined_${idx}`)) idx += 1
  return `combined_${idx}`
}

function serialiseCombinedOutputs(outputs: CombinedOutput[]): CombinedOutput[] {
  return outputs
    .filter(output => !output.isLegacy)
    .map(output => ({
      outputColumn: output.outputColumn,
      operation: output.operation,
      baseValue: output.baseValue,
    }))
}

function combinedOutputHasIssue(
  output: CombinedOutput,
  idx: number,
  outputs: CombinedOutput[],
  tableOutputColumns: string[],
): boolean {
  const outputName = output.outputColumn.trim()
  const outputNameIssue = !outputName ||
    tableOutputColumns.includes(outputName) ||
    outputs.some((other, otherIdx) => otherIdx !== idx && other.outputColumn.trim() === outputName)
  const operationIssue = !isCombinedOperation(output.operation)
  const baseValueIssue = !output.isLegacy && (
    output.baseValue.trim() === "" || !Number.isFinite(Number(output.baseValue))
  )
  return outputNameIssue || operationIssue || baseValueIssue
}

function onlyNonBandedLevels(
  levels: Record<string, string[]>,
  configuredBandingOutputs: string[],
): Record<string, string[]> {
  const result: Record<string, string[]> = {}
  for (const [name, values] of Object.entries(levels)) {
    if (configuredBandingOutputs.includes(name)) continue
    result[name] = values
  }
  return result
}

function tableDisplayName(table: RatingTable, idx: number): string {
  return table.outputColumn.trim() || `Table ${idx + 1}`
}

function tableNameFromOutputColumn(outputColumn: string, idx: number): string {
  return outputColumn.trim() || `Table ${idx + 1}`
}

function tableStatusLabel(state: "healthy" | "problem"): string {
  return state === "healthy" ? "healthy" : "problem"
}

// ─── Main Editor ──────────────────────────────────────────────────

export default function RatingStepEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  upstreamColumns = [],
  previewRows = [],
  accentColor,
  errorLine,
  nodeId,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns?: RatingFactorColumn[]
  previewRows?: Record<string, unknown>[]
  accentColor: string
  errorLine?: number | null
  nodeId?: string
}) {
  const { allNodes } = useGraph()
  const rememberedSection = useUIStore((s) => nodeId ? s.ratingStepEditorSections[nodeId] : undefined)
  const setRememberedSection = useUIStore((s) => s.setRatingStepEditorSection)
  const [activeTab, setActiveTab] = useState(0)
  const [sliceIdx, setSliceIdx] = useState(0)
  const [activeSection, setActiveSectionState] = useState<RatingSection>(() => (
    rememberedSection ?? resolveInitialSection(config)
  ))
  const [tableSearch, setTableSearch] = useState("")
  const [tableFilter, setTableFilter] = useState<"all" | "problems">("all")
  const tables = normaliseRatingTables(config)
  const bandingClassification = classifyBandingLevels(allNodes)
  const bandingLevels = bandingClassification.levels
  const rawStringLevels = extractPreviewCategoricalLevels(previewRows, upstreamColumns)
  const savedEntryLevels = extractTableEntryFactorLevels(tables)
  const rawFactorLevels = mergeFactorLevels(
    onlyNonBandedLevels(rawStringLevels, bandingClassification.configuredOutputs),
    onlyNonBandedLevels(savedEntryLevels, bandingClassification.configuredOutputs),
  )
  const factorLevels = mergeFactorLevels(bandingLevels, rawFactorLevels)
  const combinedOutputs = normaliseCombinedOutputs(config)
  const [activeCombinedIdx, setActiveCombinedIdx] = useState(0)

  const setActiveSection = (section: RatingSection) => {
    setActiveSectionState(section)
    if (nodeId) {
      setRememberedSection(nodeId, section)
    }
  }

  const availableColumns = Object.keys(factorLevels)
  const safeIdx = Math.min(activeTab, tables.length - 1)
  const table = tables[safeIdx] || { name: "Table 1", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }
  const tableStatuses = tables.map((candidate, idx) => ratingTableStatus(candidate, idx, tables))
  const activeTableStatus = tableStatuses[safeIdx] || { state: "problem" as const, issues: [] }
  const activeTableSummaryIssues = activeTableStatus.issues.filter(issue => !issue.startsWith("Output column"))
  const problemTableCount = tableStatuses.filter(status => status.state === "problem").length
  const visibleTableItems = tables
    .map((candidate, idx) => ({
      table: candidate,
      idx,
      status: tableStatuses[idx] || ratingTableStatus(candidate, idx, tables),
      displayName: tableDisplayName(candidate, idx),
      stats: tableStats(candidate.entries || []),
    }))
    .filter(item => {
      if (tableFilter === "problems" && item.status.state !== "problem") return false
      const query = tableSearch.trim().toLowerCase()
      if (!query) return true
      return item.displayName.toLowerCase().includes(query) ||
        item.table.factors.some(factor => factor.toLowerCase().includes(query))
    })
  const activeTableVisible = visibleTableItems.some(item => item.idx === safeIdx)
  const firstVisibleTableIdx = visibleTableItems[0]?.idx ?? null
  const tableEditorUnavailable = firstVisibleTableIdx === null && (
    tableSearch.trim().length > 0 || tableFilter === "problems"
  )
  const outputColumnBlank = table.outputColumn.trim() === ""
  const outputColumnDuplicate = !outputColumnBlank && tables.some((candidate, idx) => (
    idx !== safeIdx && candidate.outputColumn.trim() === table.outputColumn.trim()
  ))
  const outputColumnInvalid = outputColumnBlank || outputColumnDuplicate
  const outputColumnInputId = `rating-output-column-${safeIdx}`
  const outputColumnErrorId = `${outputColumnInputId}-error`
  const hasCombinedOutput = combinedOutputs.length > 0
  const safeCombinedIdx = hasCombinedOutput ? Math.min(activeCombinedIdx, combinedOutputs.length - 1) : 0
  const combinedOutput = combinedOutputs[safeCombinedIdx] || { outputColumn: "", operation: "multiply" as CombinedOperation, baseValue: "1.0" }
  const combinedColumnBlank = combinedOutput.outputColumn.trim() === ""
  const combinedColumnInputId = `rating-combined-output-column-${safeCombinedIdx}`
  const combinedColumnErrorId = `${combinedColumnInputId}-error`
  const combinedBaseValueInputId = `rating-combined-base-value-${safeCombinedIdx}`
  const combinedBaseValueErrorId = `${combinedBaseValueInputId}-error`
  const combinedOperationErrorId = `rating-combined-operation-${safeCombinedIdx}-error`
  const combinedBaseValueBlank = combinedOutput.baseValue.trim() === ""
  const combinedBaseValueInvalid = hasCombinedOutput && !combinedOutput.isLegacy && (
    combinedBaseValueBlank || !Number.isFinite(Number(combinedOutput.baseValue))
  )
  const combinedOperationInvalid = hasCombinedOutput && !isCombinedOperation(combinedOutput.operation)
  const tableOutputColumns = tables.map(t => t.outputColumn.trim()).filter(Boolean)
  const combinedOutputColumns = combinedOutputs.map(t => t.outputColumn.trim()).filter(Boolean)
  const currentCombinedOutputName = combinedOutput.outputColumn.trim()
  const combinedColumnDuplicate = currentCombinedOutputName !== "" && (
    tableOutputColumns.includes(currentCombinedOutputName) ||
    combinedOutputs.some((item, idx) => (
      idx !== safeCombinedIdx && item.outputColumn.trim() === currentCombinedOutputName
    ))
  )
  const code = configField(config, "code", "")
  const hasConfiguredCombined = combinedOutputColumns.length > 0
  const hasConfiguredCode = code.trim().length > 0
  const combinedColumnInvalid = hasCombinedOutput && (combinedColumnBlank || combinedColumnDuplicate)
  const sectionOptions: { key: RatingSection; label: string; icon: ReactNode }[] = [
    { key: "tables", label: "Tables", icon: <Table2 size={13} /> },
    { key: "combined", label: hasConfiguredCombined ? "Combined set" : "Combined", icon: <GitMerge size={13} /> },
    { key: "code", label: hasConfiguredCode ? "Code set" : "Code", icon: <Code2 size={13} /> },
  ]
  const codeAvailableColumns = Array.from(new Set([
    ...upstreamColumns.map(col => col.name),
    ...tableOutputColumns,
    ...combinedOutputColumns,
  ]))

  useEffect(() => {
    setActiveSectionState(rememberedSection ?? resolveInitialSection(config))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset the visible editor section only when switching rating nodes
  }, [nodeId])

  useEffect(() => {
    if (activeSection !== "tables" || activeTableVisible || firstVisibleTableIdx === null) return
    setActiveTab(firstVisibleTableIdx)
    setSliceIdx(0)
  }, [activeSection, activeTableVisible, firstVisibleTableIdx])

  const commitTables = (next: RatingTable[]) => onUpdate("tables", next)

  const updateTable = (idx: number, patch: Partial<RatingTable>) => {
    const next = tables.map((t, i) => i === idx ? { ...t, ...patch } : t)
    commitTables(next)
  }

  const setFactors = (idx: number, newFactors: string[]) => {
    const t = tables[idx]
    const rebuilt = buildCartesianEntries(newFactors, factorLevels, t.entries, t.defaultValue)
    updateTable(idx, { factors: newFactors, entries: rebuilt })
  }

  const addFactor = (idx: number, col: string) => {
    const t = tables[idx]
    if (t.factors.length >= 3 || t.factors.includes(col)) return
    setFactors(idx, [...t.factors, col])
  }

  const removeFactor = (idx: number, factorIdx: number) => {
    const t = tables[idx]
    const next = t.factors.filter((_, i) => i !== factorIdx)
    setFactors(idx, next)
  }

  const onUpdateEntries = (idx: number, entries: Record<string, string | number>[]) => {
    updateTable(idx, { entries })
  }

  const selectTable = (idx: number) => {
    setActiveTab(idx)
    setSliceIdx(0)
  }

  const addTable = () => {
    commitTables([...tables, { name: `Table ${tables.length + 1}`, factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }])
    setTableSearch("")
    setTableFilter("all")
    selectTable(tables.length)
  }

  const removeTable = (idx: number) => {
    if (tables.length <= 1) return
    const next = tables.filter((_, i) => i !== idx)
    commitTables(next)
    const nextActiveTab = idx === activeTab
      ? Math.min(idx, next.length - 1)
      : idx < activeTab
        ? activeTab - 1
        : activeTab
    selectTable(Math.max(0, nextActiveTab))
  }

  const commitCombinedOutputs = (next: CombinedOutput[]) => {
    const legacyOutput = next.find(output => output.isLegacy)
    const configuredOutputs = serialiseCombinedOutputs(next)
    const compatibilityOutput = legacyOutput ?? configuredOutputs[0]
    onUpdate({
      combinedOutputs: configuredOutputs,
      combinedColumn: compatibilityOutput?.outputColumn ?? "",
      operation: compatibilityOutput?.operation ?? "multiply",
    })
  }

  const updateCombinedOutput = (idx: number, patch: Partial<CombinedOutput>) => {
    const target = combinedOutputs[idx]
    if (target?.isLegacy) {
      const operation = patch.operation ?? target.operation
      const outputColumn = patch.outputColumn ?? target.outputColumn
      onUpdate({
        combinedColumn: outputColumn,
        operation,
      })
      return
    }

    const next = combinedOutputs.map((item, i) => {
      if (i !== idx) return item
      const operation = patch.operation ?? item.operation
      const baseValue = patch.operation && !patch.baseValue
        ? defaultBaseValue(operation)
        : patch.baseValue ?? item.baseValue
      return { ...item, ...patch, operation, baseValue }
    })
    commitCombinedOutputs(next)
  }

  const addCombinedOutput = () => {
    const next = [
      ...combinedOutputs,
      { outputColumn: nextCombinedOutputName(combinedOutputs, tableOutputColumns), operation: "multiply" as CombinedOperation, baseValue: "1.0" },
    ]
    commitCombinedOutputs(next)
    setActiveCombinedIdx(combinedOutputs.length)
  }

  const removeCombinedOutput = (idx: number) => {
    if (combinedOutputs.length === 0) return
    const next = combinedOutputs.filter((_, i) => i !== idx)
    commitCombinedOutputs(next)
    setActiveCombinedIdx(next.length === 0 ? 0 : Math.min(activeCombinedIdx, next.length - 1))
  }

  const rebuildCurrentEntries = () => {
    const t = tables[safeIdx]
    const rebuilt = buildCartesianEntries(t.factors, factorLevels, t.entries, t.defaultValue)
    updateTable(safeIdx, { entries: rebuilt })
  }

  const factorCount = table.factors.length

  // For 3-way: factor[2] is the slice dimension
  const sliceFactor = factorCount === 3 ? table.factors[2] : null
  const sliceLevels = sliceFactor ? (factorLevels[sliceFactor] || []) : []
  const safeSliceIdx = Math.min(sliceIdx, Math.max(0, sliceLevels.length - 1))

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {bandingClassification.zeroLevelOutputs.length > 0 && (
        <div
          role="alert"
          className="px-3 py-2 rounded-lg text-xs"
          style={{
            background: "var(--warning-soft)",
            border: "1px solid var(--warning-border)",
          }}
        >
          Banding outputs {bandingClassification.zeroLevelOutputs.join(", ")} have no valid levels. Add a labelled Banding rule before using them as rating factors.
        </div>
      )}

      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Rating Section</label>
        <div className="mt-1">
          <ToggleButtonGroup
            value={activeSection}
            onChange={setActiveSection}
            options={sectionOptions}
            accentColor={accentColor}
            ariaLabel="Rating section"
          />
        </div>
      </div>

      {activeSection === "tables" && (
        <>
      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
        style={{ background: withAlpha(accentColor, 0.1), border: `1px solid ${withAlpha(accentColor, 0.3)}`, color: accentColor }}>
        <Table2 size={13} />
        <span>Rating Tables · {tables.length} table{tables.length !== 1 ? 's' : ''}</span>
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center gap-1.5">
          <div className="relative flex-1 min-w-0">
            <Search
              size={12}
              className="absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none"
              style={{ color: 'var(--text-muted)' }}
            />
            <input
              type="search"
              aria-label="Search rating tables"
              value={tableSearch}
              onChange={(e) => setTableSearch(e.target.value)}
              className="w-full pl-7 pr-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
              style={INPUT_STYLE}
            />
          </div>
          <div
            className="flex items-center rounded-lg p-0.5 shrink-0"
            style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}
          >
            {(["all", "problems"] as const).map(filter => (
              <button
                key={filter}
                type="button"
                onClick={() => setTableFilter(filter)}
                aria-pressed={tableFilter === filter}
                className="px-2 py-1 rounded-md text-[10px] font-medium transition-colors"
                style={{
                  background: tableFilter === filter ? withAlpha(accentColor, 0.14) : 'transparent',
                  color: tableFilter === filter ? accentColor : 'var(--text-muted)',
                }}
              >
                {filter === "all" ? "All" : `Issues${problemTableCount > 0 ? ` ${problemTableCount}` : ""}`}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={addTable}
            aria-label="Add table"
            className="accent-hover-btn p-1.5 rounded-lg shrink-0"
            style={{ color: 'var(--text-muted)', border: '1px dashed var(--border)', ['--node-accent' as string]: accentColor }}
          >
            <Plus size={12} />
          </button>
        </div>

        <div
          role="group"
          aria-label="Rating tables"
          className="max-h-44 overflow-y-auto rounded-lg"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}
        >
          {visibleTableItems.map((item, position) => {
            const selected = item.idx === safeIdx
            const focusable = selected || (!activeTableVisible && position === 0)
            const statusLabel = tableStatusLabel(item.status.state)
            return (
              <div
                key={item.idx}
                className="group flex items-center gap-1.5 px-1.5 py-1 text-[11px] transition-colors border-b last:border-b-0"
                style={{
                  background: selected ? withAlpha(accentColor, 0.1) : 'transparent',
                  borderColor: 'var(--border-subtle)',
                  color: selected ? 'var(--text-primary)' : 'var(--text-secondary)',
                }}
              >
                <button
                  type="button"
                  aria-label={`${item.displayName} ${statusLabel}`}
                  aria-pressed={selected}
                  title={item.status.issues.length > 0 ? item.status.issues.join("; ") : "Healthy"}
                  tabIndex={focusable ? 0 : -1}
                  data-rating-table-option="true"
                  onClick={() => selectTable(item.idx)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                      e.preventDefault()
                      const nextPosition = e.key === "ArrowDown"
                        ? (position + 1) % visibleTableItems.length
                        : (position - 1 + visibleTableItems.length) % visibleTableItems.length
                      const next = visibleTableItems[nextPosition]
                      if (!next) return
                      selectTable(next.idx)
                      const selector = e.currentTarget.closest('[aria-label="Rating tables"]')
                      const options = selector?.querySelectorAll<HTMLElement>('[data-rating-table-option="true"]')
                      window.requestAnimationFrame(() => options?.[nextPosition]?.focus())
                    }
                  }}
                  className="min-w-0 flex flex-1 items-center gap-2 rounded-md px-1 py-0.5 text-left focus:outline-none focus:ring-1"
                  style={{ color: 'inherit', background: 'transparent' }}
                >
                  <span
                    aria-hidden="true"
                    className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                    style={{ background: item.status.state === "healthy" ? 'var(--success)' : 'var(--warning-strong)' }}
                  />
                  <span className="min-w-0 flex-1 truncate font-mono">{item.displayName}</span>
                  <span
                    className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-mono"
                    style={{ background: selected ? withAlpha(accentColor, 0.14) : 'var(--bg-elevated)', color: 'var(--text-muted)' }}
                  >
                    {item.table.factors.length}f
                  </span>
                  {item.stats && (
                    <span
                      className="shrink-0 rounded px-1.5 py-0.5 text-[9px] font-mono"
                      style={{ background: selected ? withAlpha(accentColor, 0.14) : 'var(--bg-elevated)', color: 'var(--text-muted)' }}
                    >
                      {item.stats.count}
                    </span>
                  )}
                </button>
                {tables.length > 1 && (
                  <button
                    type="button"
                    aria-label={`Remove ${item.displayName} table`}
                    onClick={(e) => { e.stopPropagation(); removeTable(item.idx) }}
                    className="p-0.5 rounded opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity cursor-pointer hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
                    style={{ color: 'var(--text-muted)' }}
                  >
                    <X size={10} />
                  </button>
                )}
              </div>
            )
          })}
          {visibleTableItems.length === 0 && (
            <div className="px-2.5 py-3 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>
              No matching tables
            </div>
          )}
        </div>
      </div>

      {tableEditorUnavailable ? (
        <div
          className="px-2 py-4 text-center text-[11px]"
          style={{ color: 'var(--text-muted)' }}
        >
          Select a matching table to edit its setup
        </div>
      ) : (
        <>
      {activeTableSummaryIssues.length > 0 && (
        <div
          className="space-y-1 rounded-lg px-2.5 py-2 text-[10px] font-medium"
          style={{
            background: 'var(--warning-soft-strong)',
            border: '1px solid var(--warning-border-emphasis)',
            color: 'var(--warning-strong)',
          }}
        >
          {activeTableSummaryIssues.map(issue => (
            <div key={issue}>{issue}</div>
          ))}
        </div>
      )}

      {/* Factor selection */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>
          Factors ({factorCount}/3)
        </label>
        <div className="space-y-1.5">
          {table.factors.map((f, fi) => (
            <div key={fi} className="flex items-center gap-1.5">
              <span className="text-[10px] font-bold w-4 text-center" style={{ color: 'var(--text-muted)' }}>{fi + 1}</span>
              <select key={`fsel-${safeIdx}-${fi}`} value={f}
                aria-label={`Factor ${fi + 1}`}
                onChange={(e) => {
                  const next = [...table.factors]
                  next[fi] = e.target.value
                  setFactors(safeIdx, next)
                }}
                className="flex-1 px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
                style={INPUT_STYLE}>
                <option value="">Select column...</option>
                {availableColumns.map(c => (
                  <option key={c} value={c}>{c} ({(factorLevels[c] || []).length} levels)</option>
                ))}
              </select>
              <button type="button" aria-label={`Remove factor ${fi + 1}: ${f}`}
                onClick={() => removeFactor(safeIdx, fi)}
                className="icon-danger-btn p-1 rounded">
                <X size={11} />
              </button>
            </div>
          ))}
          {factorCount < 3 && (
            <select aria-label="Add factor" value=""
              onChange={(e) => { if (e.target.value) addFactor(safeIdx, e.target.value) }}
              className="w-full px-2 py-1.5 text-xs rounded-lg focus:outline-none"
              style={{ ...INPUT_STYLE, color: 'var(--text-muted)' }}>
              <option value="">+ Add factor...</option>
              {availableColumns.filter(c => !table.factors.includes(c)).map(c => (
                <option key={c} value={c}>{c} ({(factorLevels[c] || []).length} levels)</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Output column + default */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor={outputColumnInputId} className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: outputColumnInvalid ? 'var(--danger)' : 'var(--text-muted)' }}>Output Column</label>
          <input key={`out-${safeIdx}`} type="text" defaultValue={table.outputColumn}
            id={outputColumnInputId}
            onBlur={(e) => {
              const outputColumn = e.target.value
              updateTable(safeIdx, {
                outputColumn,
                name: tableNameFromOutputColumn(outputColumn, safeIdx),
              })
            }}
            aria-invalid={outputColumnInvalid}
            aria-describedby={outputColumnInvalid ? outputColumnErrorId : undefined}
            className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
            style={{ ...INPUT_STYLE, border: outputColumnInvalid ? '1px solid var(--danger-border-strong)' : INPUT_STYLE.border }} />
          {outputColumnInvalid && (
            <div id={outputColumnErrorId} className="mt-1 text-[10px] font-medium" style={{ color: 'var(--danger)' }}>
              {outputColumnBlank ? "Output column is required" : "Output column name must be unique"}
            </div>
          )}
        </div>
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Default</label>
          <input key={`def-${safeIdx}`} type="number" step="0.01" defaultValue={table.defaultValue ?? "1.0"}
            onBlur={(e) => updateTable(safeIdx, { defaultValue: e.target.value })}
            className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
            style={INPUT_STYLE} placeholder="1.0" />
        </div>
      </div>

      {/* Rebuild button */}
      {factorCount > 0 && (
        <button onClick={rebuildCurrentEntries}
          className="accent-hover-btn w-full px-2 py-1.5 text-[11px] font-medium rounded-lg"
          style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', color: 'var(--text-secondary)', ['--node-accent' as string]: accentColor }}>
          ↻ Rebuild from factor levels
        </button>
      )}

      {/* Table editor */}
      {factorCount === 0 && (
        <div className="px-2 py-4 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Select at least one factor to populate the rating table
        </div>
      )}
      {factorCount === 1 && (
        <OneWayEditor table={table} bandingLevels={factorLevels}
          onUpdateEntries={(e) => onUpdateEntries(safeIdx, e)} />
      )}
      {factorCount === 2 && (
        <TwoWayGrid table={table} bandingLevels={factorLevels}
          onUpdateEntries={(e) => onUpdateEntries(safeIdx, e)} />
      )}
      {factorCount === 3 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              {sliceFactor}
            </label>
            <select aria-label={`${sliceFactor} slice`} value={safeSliceIdx}
              onChange={(e) => setSliceIdx(Number(e.target.value))}
              className="flex-1 px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
              style={INPUT_STYLE}>
              {sliceLevels.map((level, i) => (
                <option key={level} value={i}>{level}</option>
              ))}
            </select>
          </div>
          {sliceLevels.length > 0 && (
            <TwoWayGrid table={table} bandingLevels={factorLevels}
              onUpdateEntries={(e) => onUpdateEntries(safeIdx, e)}
              factorOverrides={{
                factors: [table.factors[0], table.factors[1]],
                sliceKey: { [table.factors[2]]: sliceLevels[safeSliceIdx] },
              }} />
          )}
        </div>
      )}

      {/* Summary */}
      {table.entries.length > 0 && (() => {
        const s = tableStats(table.entries)
        return (
          <div className="flex items-center justify-between text-[10px] font-mono px-1"
            style={{ color: 'var(--text-muted)' }}>
            <span>{table.outputColumn ? <span style={{ color: 'var(--text-secondary)' }}>{table.outputColumn}</span> : 'untitled'}</span>
            <span>{table.entries.length} entries{s ? ` · range ${s.min.toFixed(2)}–${s.max.toFixed(2)}` : ''}</span>
          </div>
        )
      })()}
        </>
      )}

        </>
      )}

      {activeSection === "combined" && (
        <div className="space-y-3">
          <div>
            <div className="flex items-center gap-1 overflow-x-auto flex-nowrap whitespace-nowrap" role="tablist" aria-label="Combined outputs">
              {combinedOutputs.map((item, i) => {
                const itemHasIssue = combinedOutputHasIssue(item, i, combinedOutputs, tableOutputColumns)
                return (
                  <div
                    key={i}
                    role="tab"
                    id={`combined-output-tab-${i}`}
                    aria-selected={i === safeCombinedIdx}
                    tabIndex={i === safeCombinedIdx ? 0 : -1}
                    onClick={() => setActiveCombinedIdx(i)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault()
                        setActiveCombinedIdx(i)
                      }
                      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                        e.preventDefault()
                        const next = e.key === "ArrowRight"
                          ? (i + 1) % combinedOutputs.length
                          : (i - 1 + combinedOutputs.length) % combinedOutputs.length
                        setActiveCombinedIdx(next)
                        const tablist = e.currentTarget.parentElement
                        if (tablist) {
                          const tabs = tablist.querySelectorAll<HTMLElement>('[role="tab"]')
                          tabs[next]?.focus()
                        }
                      }
                    }}
                    className="group relative flex items-center gap-1.5 px-2.5 py-1.5 rounded-t-lg text-[11px] font-medium transition-colors cursor-pointer shrink-0"
                    style={{
                      background: i === safeCombinedIdx ? 'var(--bg-input)' : 'transparent',
                      border: i === safeCombinedIdx ? '1px solid var(--border)' : '1px solid transparent',
                      borderBottom: i === safeCombinedIdx ? '1px solid var(--bg-input)' : '1px solid var(--border)',
                      color: i === safeCombinedIdx ? accentColor : 'var(--text-muted)',
                    }}
                  >
                    <span
                      className="inline-block w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ background: itemHasIssue ? 'var(--warning-strong)' : 'var(--success)' }}
                    />
                    <span className="font-mono truncate max-w-[130px]">{item.outputColumn || `Combined ${i + 1}`}</span>
                    <button
                      type="button"
                      aria-label="Remove combined output"
                      onClick={(e) => { e.stopPropagation(); removeCombinedOutput(i) }}
                      className="ml-0.5 p-0.5 rounded transition-colors cursor-pointer hover:text-[var(--danger)] focus-visible:text-[var(--danger)]"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      <X size={9} />
                    </button>
                  </div>
                )
              })}
              <button
                type="button"
                onClick={addCombinedOutput}
                aria-label="Add combined output"
                className="accent-hover-btn p-1.5 rounded-lg shrink-0"
                style={{ color: 'var(--text-muted)', border: '1px dashed var(--border)', ['--node-accent' as string]: accentColor }}
              >
                <Plus size={12} />
              </button>
            </div>
            <div style={{ borderTop: '1px solid var(--border)', marginTop: -1 }} />
          </div>

          {!hasCombinedOutput && (
            <div className="px-2 py-4 text-center text-[11px] rounded-lg" style={{ background: 'var(--bg-surface)', border: '1px dashed var(--border)', color: 'var(--text-muted)' }}>
              No combined output
            </div>
          )}

          {hasCombinedOutput && (
          <div role="tabpanel" id="combined-output-tabpanel" aria-labelledby={`combined-output-tab-${safeCombinedIdx}`} className="space-y-3">
            <div>
              <label htmlFor={combinedColumnInputId} className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: combinedColumnInvalid ? 'var(--danger)' : 'var(--text-muted)' }}>Combined Output Column</label>
              <input
                key={`combined-out-${safeCombinedIdx}`}
                type="text"
                defaultValue={combinedOutput.outputColumn}
                id={combinedColumnInputId}
                onBlur={(e) => updateCombinedOutput(safeCombinedIdx, { outputColumn: e.target.value })}
                aria-invalid={combinedColumnInvalid}
                aria-describedby={combinedColumnInvalid ? combinedColumnErrorId : undefined}
                className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
                style={{ ...INPUT_STYLE, border: combinedColumnInvalid ? '1px solid var(--danger-border-strong)' : INPUT_STYLE.border }}
              />
              {combinedColumnInvalid && (
                <div id={combinedColumnErrorId} className="mt-1 text-[10px] font-medium" style={{ color: 'var(--danger)' }}>
                  {combinedColumnBlank ? "Combined output column is required" : "Output column name must be unique"}
                </div>
              )}
            </div>

            <div className="grid grid-cols-2 gap-2">
              <div>
                <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Operation</label>
                <select
                  value={combinedOutput.operation}
                  onChange={(e) => updateCombinedOutput(safeCombinedIdx, { operation: asOperation(e.target.value) })}
                  aria-invalid={combinedOperationInvalid}
                  aria-describedby={combinedOperationInvalid ? combinedOperationErrorId : undefined}
                  className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
                  style={{ ...INPUT_STYLE, border: combinedOperationInvalid ? '1px solid var(--danger-border-strong)' : INPUT_STYLE.border }}
                >
                  {combinedOperationInvalid && (
                    <option value={combinedOutput.operation}>{combinedOutput.operation}</option>
                  )}
                  {OPERATION_OPTIONS.map(option => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
                {combinedOperationInvalid && (
                  <div id={combinedOperationErrorId} className="mt-1 text-[10px] font-medium" style={{ color: 'var(--danger)' }}>
                    Operation is not supported
                  </div>
                )}
              </div>
              {!combinedOutput.isLegacy && (
                <div>
                  <label htmlFor={combinedBaseValueInputId} className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: combinedBaseValueInvalid ? 'var(--danger)' : 'var(--text-muted)' }}>Base Value</label>
                  <input
                    key={`combined-base-${safeCombinedIdx}-${combinedOutput.operation}`}
                    id={combinedBaseValueInputId}
                    type="number"
                    step="any"
                    defaultValue={combinedOutput.baseValue}
                    onBlur={(e) => updateCombinedOutput(safeCombinedIdx, { baseValue: e.target.value })}
                    aria-invalid={combinedBaseValueInvalid}
                    aria-describedby={combinedBaseValueInvalid ? combinedBaseValueErrorId : undefined}
                    className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
                    style={{ ...INPUT_STYLE, border: combinedBaseValueInvalid ? '1px solid var(--danger-border-strong)' : INPUT_STYLE.border }}
                  />
                  {combinedBaseValueInvalid && (
                    <div id={combinedBaseValueErrorId} className="mt-1 text-[10px] font-medium" style={{ color: 'var(--danger)' }}>
                      Base value is required
                    </div>
                  )}
                </div>
              )}
            </div>

            {tableOutputColumns.length > 0 && (
              <div className="text-[10px] font-mono px-2 py-1.5 rounded flex items-center gap-1.5 overflow-x-auto"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                <span style={{ color: accentColor, fontWeight: 600 }}>{combinedOutput.outputColumn || "?"}</span>
                <span style={{ color: 'var(--text-muted)' }}>=</span>
                <span>{formulaFor(combinedOutput.operation, tableOutputColumns, combinedOutput.baseValue)}</span>
              </div>
            )}
          </div>
          )}
        </div>
      )}

      {activeSection === "code" && (
        <div className="px-0 py-1 flex flex-col gap-2">
          <div className="flex items-center justify-between shrink-0">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              Polars Code
              <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
            </label>
            <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
              use <code className="px-0.5 rounded" style={{ background: 'var(--bg-hover)' }}>df</code> for rated data
            </span>
          </div>
          <CodeEditor
            defaultValue={code}
            onChange={(value) => onUpdate("code", value)}
            availableColumns={codeAvailableColumns}
            placeholder=""
            errorLine={errorLine}
          />
        </div>
      )}
    </div>
  )
}
