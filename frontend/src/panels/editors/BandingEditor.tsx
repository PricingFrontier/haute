import { useState } from "react"
import { X, Plus, SlidersHorizontal } from "lucide-react"
import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import type { ContinuousRule, CategoricalRule, BandingFactor } from "../../types/banding"
import { normaliseBandingFactors, inferBandingType } from "./banding/bandingUtils"
import { BandingRulesGrid } from "./banding/BandingRulesGrid"
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
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns?: { name: string; dtype: string }[]
  accentColor: string
}) {
  const factors = normaliseBandingFactors(config)
  const [activeIdx, setActiveIdx] = useState(0)
  const safeIdx = Math.min(activeIdx, factors.length - 1)
  const factor = factors[safeIdx]

  const colMap = Object.fromEntries(upstreamColumns.map(c => [c.name, c.dtype]))

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
    updateFactor(idx, patch)
  }

  const addFactor = () => {
    const next = [...factors, { banding: "continuous" as const, column: "", outputColumn: "", rules: [] as (ContinuousRule | CategoricalRule)[], default: null }]
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
    return `Factor ${i + 1}`
  }

  return (
    <div className="px-4 py-3 space-y-3 overflow-y-auto">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      <div className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
        style={{ background: withAlpha(accentColor, 0.1), border: `1px solid ${withAlpha(accentColor, 0.3)}`, color: accentColor }}>
        <SlidersHorizontal size={14} />
        <span>Group values into bands — {factors.length} factor{factors.length !== 1 ? 's' : ''}</span>
      </div>

      {/* Factor tabs */}
      <div>
        <div className="flex items-center gap-1 flex-wrap">
          {factors.map((f, i) => (
            <button
              key={i}
              onClick={() => setActiveIdx(i)}
              className="relative flex items-center gap-1 px-2.5 py-1.5 rounded-t-lg text-[11px] font-medium transition-colors"
              style={{
                background: i === safeIdx ? 'var(--bg-input)' : 'transparent',
                border: i === safeIdx ? '1px solid var(--border)' : '1px solid transparent',
                borderBottom: i === safeIdx ? '1px solid var(--bg-input)' : '1px solid var(--border)',
                color: i === safeIdx ? accentColor : 'var(--text-muted)',
              }}
            >
              <span className="font-mono truncate max-w-[100px]">{tabLabel(f, i)}</span>
              {factors.length > 1 && (
                <button
                  type="button"
                  aria-label="Remove factor"
                  onClick={(e) => { e.stopPropagation(); removeFactor(i) }}
                  className="ml-0.5 p-0.5 rounded transition-colors cursor-pointer"
                  style={{ color: 'var(--text-muted)' }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = '#ef4444' }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-muted)' }}
                >
                  <X size={9} />
                </button>
              )}
            </button>
          ))}
          <button
            onClick={addFactor}
            className="flex items-center gap-0.5 px-2 py-1.5 rounded-lg text-[11px] font-medium transition-colors"
            style={{ color: accentColor }}
            onMouseEnter={(e) => { e.currentTarget.style.background = withAlpha(accentColor, 0.1) }}
            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          >
            <Plus size={11} />
          </button>
        </div>
        <div style={{ borderTop: '1px solid var(--border)', marginTop: -1 }} />
      </div>

      {/* Active factor config */}
      <div>
        <div className="flex items-center gap-1.5">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Type</label>
          {factor.column && colMap[factor.column] && (
            <span className="text-[10px] font-medium" style={{ color: 'var(--text-muted)', opacity: 0.7 }}>
              auto: {colMap[factor.column]}
            </span>
          )}
        </div>
        <div className="mt-1">
          <ToggleButtonGroup
            value={factor.banding}
            onChange={(bt) => updateFactor(safeIdx, { banding: bt, rules: [] })}
            options={[
              { key: "continuous", label: "Continuous" },
              { key: "categorical", label: "Categorical" },
            ]}
            accentColor={accentColor}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Input Column</label>
          {upstreamColumns.length > 0 ? (
            <select
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
            <input
              key={`col-${safeIdx}`}
              type="text" placeholder="driver_age" value={factor.column || ""}
              onChange={(e) => updateFactor(safeIdx, { column: e.target.value })}
              className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
              style={INPUT_STYLE} />
          )}
        </div>
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>Output Column</label>
          <input
            key={`out-${safeIdx}`}
            type="text" placeholder="age_band" value={factor.outputColumn || ""}
            onChange={(e) => updateFactor(safeIdx, { outputColumn: e.target.value })}
            className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
            style={INPUT_STYLE} />
        </div>
      </div>

      {/* Rules grid + add button */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
            Rules ({(factor.rules || []).length})
          </label>
          <button
            onClick={() => {
              const empty = factor.banding === "continuous" ? { ...EMPTY_CONTINUOUS } : { ...EMPTY_CATEGORICAL }
              updateFactor(safeIdx, { rules: [...(factor.rules || []), empty] })
            }}
            className="flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors"
            style={{ background: withAlpha(accentColor, 0.1), color: accentColor, border: `1px solid ${withAlpha(accentColor, 0.3)}` }}
            onMouseEnter={(e) => { e.currentTarget.style.background = withAlpha(accentColor, 0.2) }}
            onMouseLeave={(e) => { e.currentTarget.style.background = withAlpha(accentColor, 0.1) }}
          >
            <Plus size={11} /> Add
          </button>
        </div>
        <BandingRulesGrid
          key={safeIdx}
          factor={factor}
          onUpdateFactor={(patch) => updateFactor(safeIdx, patch)}
        />
      </div>

      {/* Default value */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1" style={{ color: 'var(--text-muted)' }}>
          Default <span className="ml-1.5 normal-case tracking-normal font-normal">(unmatched rows)</span>
        </label>
        <input
          key={`def-${safeIdx}`}
          type="text" placeholder="null" defaultValue={factor.default ?? ""}
          onChange={(e) => updateFactor(safeIdx, { default: e.target.value !== "" ? e.target.value : null })}
          className="w-full px-2 py-1.5 text-xs font-mono rounded-lg focus:outline-none focus:ring-2"
          style={INPUT_STYLE} />
      </div>

      {/* Summary across all factors */}
      {factors.some(f => f.column && f.outputColumn && (f.rules || []).length > 0) && (
        <div className="rounded-lg px-3 py-2 space-y-1" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
          {factors.map((f, i) => {
            if (!f.column || !f.outputColumn || !(f.rules || []).length) return null
            return (
              <div key={i} className="text-[10px] leading-relaxed" style={{ color: 'var(--text-muted)' }}>
                <span className="font-mono font-medium" style={{ color: 'var(--text-secondary)' }}>{f.column}</span>
                {' → '}
                <span className="font-mono font-medium" style={{ color: accentColor }}>{f.outputColumn}</span>
                {' · '}{f.rules.length} rule{f.rules.length !== 1 ? 's' : ''}
                {' · '}{f.banding}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
