/**
 * Summary tab for the optimiser preview.
 *
 * Renders the objective, the constraint-attainment table (with λ, for online
 * and ratebook results alike), the scenario-value histogram and the
 * scenario-value statistics, captioned "As solved" or "Frontier point N".
 */

import { Loader2 } from "lucide-react"
import { formatNumber } from "../../utils/formatValue"
import type {
  OptimiserScenarioValueHistogram,
  OptimiserScenarioValueStats,
  OptimiserSolveResult,
} from "../../api/types"
import { effectiveConstraintBounds } from "../../stores/useNodeResultsStore"
import RatebookImpactBeeswarm from "./RatebookImpactBeeswarm"
import { hasFactorTables } from "./ratebookFactorTables"
import ConstraintAttainmentTable from "./ConstraintAttainmentTable"

type RatebookRatesLoadState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: string }

interface SummaryTabProps {
  /** The displayed result: the selected frontier point's, else the solve's. */
  result: OptimiserSolveResult
  /** The selected frontier point, or null for the as-solved result. */
  selectedPointIndex: number | null
  canMaterialiseRatebookRates?: boolean
  ratebookRatesDetail?: RatebookRatesLoadState
}

export default function SummaryTab({
  result,
  selectedPointIndex,
  canMaterialiseRatebookRates = false,
  ratebookRatesDetail = { status: "idle" },
}: SummaryTabProps) {
  const bounds = effectiveConstraintBounds(result)
  const showRatebookImpactStatus = (
    result.mode === "ratebook"
    && canMaterialiseRatebookRates
    && !hasFactorTables(result.factor_tables)
  )

  return (
    <div className="flex gap-6 flex-wrap">
      {/* Left column: objective + constraints */}
      <div className="space-y-3 min-w-[200px]">
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Objective</label>
          <div className="mt-1 space-y-0.5">
            <div className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>Optimised</span>
              <span style={{ color: "var(--text-primary)" }}>{formatNumber(result.total_objective)}</span>
            </div>
          </div>
        </div>

        {Object.keys(bounds).length > 0 && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
            <div className="mt-1">
              <ConstraintAttainmentTable
                bounds={bounds}
                achieved={result.constraints}
                lambdas={result.lambdas}
              />
            </div>
          </div>
        )}

        {result.mode === "ratebook" && result.clamp_rate != null && (
          <div className="flex justify-between text-xs font-mono">
            <span style={{ color: "var(--text-muted)" }}>Clamp rate</span>
            <span style={{ color: "var(--warning-strong)" }}>{(result.clamp_rate * 100).toFixed(1)}%</span>
          </div>
        )}

      </div>

      {result.mode === "ratebook" && (
        <RatebookImpactBeeswarm factorTables={result.factor_tables} />
      )}
      {showRatebookImpactStatus && (
        <RatebookImpactStatus detail={ratebookRatesDetail} />
      )}

      {/* Middle column: histogram + stats. A frontier point reports statistics
          but no histogram, so the statistics never depend on one. */}
      {(result.scenario_value_histogram || result.scenario_value_stats) && (
        <div className="min-w-[200px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Scenario Value Distribution</label>
          {result.scenario_value_histogram && (
            <ScenarioValueHistogram histogram={result.scenario_value_histogram} />
          )}
          {result.scenario_value_stats && (
            <ScenarioValueStatsGrid
              stats={result.scenario_value_stats}
              label={selectedPointIndex == null ? "As solved" : `Frontier point ${selectedPointIndex + 1}`}
            />
          )}
        </div>
      )}

    </div>
  )
}

function ScenarioValueHistogram({ histogram }: { histogram: OptimiserScenarioValueHistogram }) {
  const { counts, edges } = histogram
  if (counts.length === 0) return null
  const maxCount = Math.max(...counts)
  const w = 320, h = 100, px = 2, py = 2
  const chartW = w - px * 2, chartH = h - py * 2
  const barW = chartW / counts.length
  const eMin = edges[0], eMax = edges[edges.length - 1]
  const oneX = eMax > eMin ? px + ((1.0 - eMin) / (eMax - eMin)) * chartW : null
  return (
    <>
      <svg width={w} height={h} className="mt-1" style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
        {counts.map((c, i) => {
          const barH = maxCount > 0 ? (c / maxCount) * chartH : 0
          return (
            <rect key={i} x={px + i * barW + 0.5} y={py + chartH - barH} width={Math.max(barW - 1, 1)} height={barH} fill="var(--warning-strong)" opacity={0.7} />
          )
        })}
        {oneX != null && oneX >= px && oneX <= px + chartW && (
          <line x1={oneX} y1={py} x2={oneX} y2={py + chartH} stroke="var(--danger)" strokeWidth={1} strokeDasharray="3,2" />
        )}
      </svg>
      <div className="flex gap-3 mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
        <span>{eMin.toFixed(2)}</span>
        <span className="flex-1" />
        {oneX != null && <span><span style={{ color: "var(--danger)" }}>|</span> 1.0</span>}
        <span className="flex-1" />
        <span>{eMax.toFixed(2)}</span>
      </div>
    </>
  )
}

/** The chosen scenario values' statistics, captioned with whose they are. */
function ScenarioValueStatsGrid({ stats, label }: { stats: OptimiserScenarioValueStats; label: string }) {
  return (
    <div role="group" aria-label={`Scenario value statistics: ${label}`} className="mt-2">
      <div className="text-[11px]" style={{ color: "var(--text-secondary)" }}>{label}</div>
      <div className="mt-0.5 grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs font-mono">
        <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Mean</span><span style={{ color: "var(--text-primary)" }}>{stats.mean.toFixed(4)}</span></div>
        <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Std</span><span style={{ color: "var(--text-primary)" }}>{stats.std.toFixed(4)}</span></div>
        <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>P5-P95</span><span style={{ color: "var(--text-primary)" }}>{stats.p5.toFixed(3)}-{stats.p95.toFixed(3)}</span></div>
        <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Min-Max</span><span style={{ color: "var(--text-primary)" }}>{stats.min.toFixed(3)}-{stats.max.toFixed(3)}</span></div>
        <div className="flex justify-between"><span style={{ color: "var(--success)" }}>Increase</span><span style={{ color: "var(--success)" }}>{(stats.pct_increase * 100).toFixed(1)}%</span></div>
        <div className="flex justify-between"><span style={{ color: "var(--danger)" }}>Decrease</span><span style={{ color: "var(--danger)" }}>{(stats.pct_decrease * 100).toFixed(1)}%</span></div>
      </div>
    </div>
  )
}

function RatebookImpactStatus({ detail }: { detail: RatebookRatesLoadState }) {
  const isError = detail.status === "error"
  return (
    <section
      className="min-w-[280px] flex-1 rounded px-3 py-2 text-xs"
      style={{
        background: isError ? "var(--danger-soft)" : "var(--bg-input)",
        border: `1px solid ${isError ? "var(--danger-border)" : "var(--border)"}`,
        color: isError ? "var(--danger)" : "var(--text-muted)",
      }}
    >
      <div className="mb-1 text-[11px] font-bold uppercase tracking-[0.08em]">
        Mechanical Price Effect
      </div>
      <div className="flex items-center gap-2">
        {!isError && <Loader2 size={14} className="animate-spin shrink-0" />}
        <span>
          {isError
            ? `Rate table load failed: ${detail.error}`
            : "Materialising selected point rates..."}
        </span>
      </div>
    </section>
  )
}
