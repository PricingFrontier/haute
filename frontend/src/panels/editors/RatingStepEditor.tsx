import { useState } from "react"
import { X, Plus, Table2 } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { extractBandingLevels } from "../../utils/banding"
import type { RatingTable } from "./rating/ratingTableUtils"
import { normaliseRatingTables, buildCartesianEntries, tableStats } from "./rating/ratingTableUtils"
import { OneWayEditor } from "./rating/OneWayEditor"
import { TwoWayGrid } from "./rating/TwoWayGrid"
import { useGraph } from "../useGraph"

// ─── Main Editor ──────────────────────────────────────────────────

export default function RatingStepEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  accentColor,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  accentColor: string
}) {
  const { allNodes } = useGraph()
  const [activeTab, setActiveTab] = useState(0)
  const [sliceIdx, setSliceIdx] = useState(0)
  const tables = normaliseRatingTables(config)
  const bandingLevels = extractBandingLevels(allNodes)
  const operation = configField(config, "operation", "multiply")
  const combinedColumn = configField(config, "combinedColumn", "")

  const availableColumns = Object.keys(bandingLevels)
  const safeIdx = Math.min(activeTab, tables.length - 1)
  const table = tables[safeIdx] || { name: "Table 1", factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }

  const commitTables = (next: RatingTable[]) => onUpdate("tables", next)

  const updateTable = (idx: number, patch: Partial<RatingTable>) => {
    const next = tables.map((t, i) => i === idx ? { ...t, ...patch } : t)
    commitTables(next)
  }

  const setFactors = (idx: number, newFactors: string[]) => {
    const t = tables[idx]
    const rebuilt = buildCartesianEntries(newFactors, bandingLevels, t.entries, t.defaultValue)
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

  const addTable = () => {
    commitTables([...tables, { name: `Table ${tables.length + 1}`, factors: [], outputColumn: "", defaultValue: "1.0", entries: [] }])
    setActiveTab(tables.length)
  }

  const removeTable = (idx: number) => {
    if (tables.length <= 1) return
    const next = tables.filter((_, i) => i !== idx)
    commitTables(next)
    setActiveTab(Math.min(activeTab, next.length - 1))
  }

  const rebuildCurrentEntries = () => {
    const t = tables[safeIdx]
    const rebuilt = buildCartesianEntries(t.factors, bandingLevels, t.entries, t.defaultValue)
    updateTable(safeIdx, { entries: rebuilt })
  }

  const factorCount = table.factors.length

  // For 3-way: factor[2] is the slice dimension
  const sliceFactor = factorCount === 3 ? table.factors[2] : null
  const sliceLevels = sliceFactor ? (bandingLevels[sliceFactor] || []) : []
  const safeSliceIdx = Math.min(sliceIdx, Math.max(0, sliceLevels.length - 1))

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
        style={{ background: withAlpha(accentColor, 0.1), border: `1px solid ${withAlpha(accentColor, 0.3)}`, color: accentColor }}>
        <Table2 size={13} />
        <span>Rating Tables · {tables.length} table{tables.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Combination controls -- shown when 2+ tables exist */}
      {tables.length >= 2 && (
        <div className="space-y-2 p-2.5 rounded-lg" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em] shrink-0" style={{ color: 'var(--text-muted)' }}>Combine</label>
            <select value={operation}
              onChange={(e) => onUpdate("operation", e.target.value)}
              className="flex-1 px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
              style={INPUT_STYLE}>
              <option value="multiply">× Multiply (relativities)</option>
              <option value="add">+ Add (loadings)</option>
              <option value="min">↓ Min</option>
              <option value="max">↑ Max</option>
            </select>
          </div>
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Combined Output Column</label>
            <input type="text" defaultValue={combinedColumn}
              onBlur={(e) => onUpdate("combinedColumn", e.target.value)}
              className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
              style={INPUT_STYLE} placeholder="combined_factor" />
          </div>
          {/* Formula summary */}
          {tables.some(t => t.outputColumn) && (() => {
            const cols = tables.filter(t => t.outputColumn).map(t => t.outputColumn)
            const lhs = combinedColumn || '?'
            let formula = ''
            if (operation === 'multiply') formula = cols.join(' × ')
            else if (operation === 'add') formula = cols.join(' + ')
            else if (operation === 'min') formula = `min(${cols.join(', ')})`
            else if (operation === 'max') formula = `max(${cols.join(', ')})`
            return (
              <div className="text-[10px] font-mono px-2 py-1.5 rounded flex items-center gap-1.5"
                style={{ background: 'var(--bg-elevated)', color: 'var(--text-secondary)', border: '1px solid var(--border)' }}>
                <span style={{ color: accentColor, fontWeight: 600 }}>{lhs}</span>
                <span style={{ color: 'var(--text-muted)' }}>=</span>
                <span>{formula}</span>
                {!combinedColumn && <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}> — name the output above</span>}
              </div>
            )
          })()}
        </div>
      )}

      {/* Tab bar */}
      <div className="flex items-center gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Rating tables">
        {tables.map((t, i) => {
          const tStats = tableStats(t.entries || [])
          return (
            <div key={i} role="tab" aria-selected={i === safeIdx} tabIndex={i === safeIdx ? 0 : -1}
              onClick={() => { setActiveTab(i); setSliceIdx(0) }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setActiveTab(i); setSliceIdx(0) }
                if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
                  e.preventDefault()
                  const next = e.key === 'ArrowRight' ? (i + 1) % tables.length : (i - 1 + tables.length) % tables.length
                  setActiveTab(next); setSliceIdx(0)
                  const tablist = e.currentTarget.parentElement
                  if (tablist) { const tabs = tablist.querySelectorAll<HTMLElement>('[role="tab"]'); tabs[next]?.focus() }
                }
              }}
              className="group flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium whitespace-nowrap transition-colors cursor-pointer"
              style={{
                background: i === safeIdx ? withAlpha(accentColor, 0.12) : 'var(--bg-surface)',
                border: i === safeIdx ? `1px solid ${withAlpha(accentColor, 0.4)}` : '1px solid var(--border)',
                color: i === safeIdx ? accentColor : 'var(--text-secondary)',
              }}>
              {t.name || `Table ${i + 1}`}
              {tStats && (
                <span className="text-[9px] font-mono px-1 py-0.5 rounded"
                  style={{ background: i === safeIdx ? withAlpha(accentColor, 0.15) : 'var(--bg-elevated)', color: 'var(--text-muted)' }}>
                  {tStats.count}
                </span>
              )}
              {tables.length > 1 && (
                <button type="button" aria-label="Remove table" onClick={(e) => { e.stopPropagation(); removeTable(i) }}
                  className="ml-0.5 opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer"
                  style={{ color: 'var(--text-muted)' }}><X size={10} /></button>
              )}
            </div>
          )
        })}
        <button onClick={addTable}
          className="accent-hover-btn p-1.5 rounded-lg"
          style={{ color: 'var(--text-muted)', border: '1px dashed var(--border)', ['--node-accent' as string]: accentColor }}>
          <Plus size={12} />
        </button>
      </div>

      {/* Table name */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Table Name</label>
        <input key={`name-${safeIdx}`} type="text" defaultValue={table.name}
          onBlur={(e) => updateTable(safeIdx, { name: e.target.value })}
          className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
          style={INPUT_STYLE} placeholder="Age Factor" />
      </div>

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
                onChange={(e) => {
                  const next = [...table.factors]
                  next[fi] = e.target.value
                  setFactors(safeIdx, next)
                }}
                className="flex-1 px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
                style={INPUT_STYLE}>
                <option value="">Select column...</option>
                {availableColumns.map(c => (
                  <option key={c} value={c}>{c} ({(bandingLevels[c] || []).length} levels)</option>
                ))}
              </select>
              <button onClick={() => removeFactor(safeIdx, fi)}
                className="icon-danger-btn p-1 rounded">
                <X size={11} />
              </button>
            </div>
          ))}
          {factorCount < 3 && (
            <select value="" onChange={(e) => { if (e.target.value) addFactor(safeIdx, e.target.value) }}
              className="w-full px-2 py-1.5 text-xs rounded-lg focus:outline-none"
              style={{ ...INPUT_STYLE, color: 'var(--text-muted)' }}>
              <option value="">+ Add factor...</option>
              {availableColumns.filter(c => !table.factors.includes(c)).map(c => (
                <option key={c} value={c}>{c} ({(bandingLevels[c] || []).length} levels)</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* Output column + default */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Output Column</label>
          <input key={`out-${safeIdx}`} type="text" defaultValue={table.outputColumn}
            onBlur={(e) => updateTable(safeIdx, { outputColumn: e.target.value })}
            className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
            style={INPUT_STYLE} placeholder="age_factor" />
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
          ↻ Rebuild from banding levels
        </button>
      )}

      {/* Table editor */}
      {factorCount === 0 && (
        <div className="px-2 py-4 text-center text-[11px]" style={{ color: 'var(--text-muted)' }}>
          Select at least one factor to populate the rating table
        </div>
      )}
      {factorCount === 1 && (
        <OneWayEditor table={table} bandingLevels={bandingLevels}
          onUpdateEntries={(e) => onUpdateEntries(safeIdx, e)} />
      )}
      {factorCount === 2 && (
        <TwoWayGrid table={table} bandingLevels={bandingLevels}
          onUpdateEntries={(e) => onUpdateEntries(safeIdx, e)} />
      )}
      {factorCount === 3 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
              {sliceFactor}
            </label>
            <select value={safeSliceIdx} onChange={(e) => setSliceIdx(Number(e.target.value))}
              className="flex-1 px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none"
              style={INPUT_STYLE}>
              {sliceLevels.map((level, i) => (
                <option key={level} value={i}>{level}</option>
              ))}
            </select>
          </div>
          {sliceLevels.length > 0 && (
            <TwoWayGrid table={table} bandingLevels={bandingLevels}
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
    </div>
  )
}
