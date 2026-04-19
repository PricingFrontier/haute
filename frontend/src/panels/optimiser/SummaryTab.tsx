/**
 * Summary tab for the optimiser preview.
 *
 * Renders objective, constraints, lambdas, scenario-value histogram,
 * and factor tables (ratebook mode).  Extracted from OptimiserPreview
 * as part of the god-component split.
 */

import { formatNumber } from "../../utils/formatValue"
import type { SolveResult } from "../OptimiserPreview"
import { isConstraintMet } from "./optimiserHelpers"

interface SummaryTabProps {
  result: SolveResult
  constraints: Record<string, Record<string, number>>
}

export default function SummaryTab({ result, constraints }: SummaryTabProps) {
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
            <div className="flex justify-between text-xs font-mono gap-4">
              <span style={{ color: "var(--text-secondary)" }}>Baseline</span>
              <span style={{ color: "var(--text-muted)" }}>{formatNumber(result.baseline_objective)}</span>
            </div>
            {result.baseline_objective !== 0 && (
              <div className="flex justify-between text-xs font-mono gap-4">
                <span style={{ color: "var(--text-secondary)" }}>Uplift</span>
                <span style={{ color: "#f59e0b" }}>
                  {((result.total_objective / result.baseline_objective - 1) * 100).toFixed(2)}%
                </span>
              </div>
            )}
          </div>
        </div>

        {/* Constraints with binding indicators */}
        {Object.keys(result.constraints).length > 0 && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Constraints</label>
            <div className="mt-1 space-y-0.5">
              {Object.entries(result.constraints).map(([name, value]) => {
                const baseline = result.baseline_constraints[name]
                const ratio = baseline ? value / baseline : 0
                const spec = constraints[name] || {}
                const thresholdType = Object.keys(spec)[0]
                const thresholdVal = spec[thresholdType] ?? 0
                const met = isConstraintMet(thresholdType, ratio, value, thresholdVal)
                return (
                  <div key={name} className="flex items-center justify-between text-xs font-mono gap-4">
                    <span className="flex items-center gap-1.5">
                      <span className="inline-block w-1.5 h-1.5 rounded-full shrink-0" style={{ background: met ? "#22c55e" : "#ef4444" }} />
                      <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                    </span>
                    <span>
                      <span style={{ color: "var(--text-primary)" }}>{formatNumber(value)}</span>
                      {baseline !== undefined && (
                        <span style={{ color: "var(--text-muted)" }}> ({(ratio * 100).toFixed(1)}%)</span>
                      )}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Lambdas (online) / Factor tables (ratebook) */}
        {result.mode !== "ratebook" && Object.keys(result.lambdas).length > 0 && (
          <div>
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Lambdas</label>
            <div className="mt-1 space-y-0.5">
              {Object.entries(result.lambdas).map(([name, value]) => (
                <div key={name} className="flex justify-between text-xs font-mono gap-4">
                  <span style={{ color: "var(--text-secondary)" }}>{name}</span>
                  <span style={{ color: "var(--text-primary)" }}>{value.toFixed(6)}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {result.mode === "ratebook" && result.clamp_rate != null && (
          <div className="flex justify-between text-xs font-mono">
            <span style={{ color: "var(--text-muted)" }}>Clamp rate</span>
            <span style={{ color: "#f59e0b" }}>{(result.clamp_rate * 100).toFixed(1)}%</span>
          </div>
        )}

      </div>

      {/* Middle column: histogram + stats */}
      {result.scenario_value_histogram && (() => {
        const { counts, edges } = result.scenario_value_histogram
        if (!counts || counts.length === 0) return null
        const maxCount = Math.max(...counts)
        const w = 320, h = 100, px = 2, py = 2
        const chartW = w - px * 2, chartH = h - py * 2
        const barW = chartW / counts.length
        const eMin = edges[0], eMax = edges[edges.length - 1]
        const oneX = eMax > eMin ? px + ((1.0 - eMin) / (eMax - eMin)) * chartW : null
        return (
          <div className="min-w-[200px]">
            <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Scenario Value Distribution</label>
            <svg width={w} height={h} className="mt-1" style={{ background: "var(--bg-input)", borderRadius: 6, border: "1px solid var(--border)" }}>
              {counts.map((c, i) => {
                const barH = maxCount > 0 ? (c / maxCount) * chartH : 0
                return (
                  <rect key={i} x={px + i * barW + 0.5} y={py + chartH - barH} width={Math.max(barW - 1, 1)} height={barH} fill="#f59e0b" opacity={0.7} />
                )
              })}
              {oneX != null && oneX >= px && oneX <= px + chartW && (
                <line x1={oneX} y1={py} x2={oneX} y2={py + chartH} stroke="#ef4444" strokeWidth={1} strokeDasharray="3,2" />
              )}
            </svg>
            <div className="flex gap-3 mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
              <span>{eMin.toFixed(2)}</span>
              <span className="flex-1" />
              {oneX != null && <span><span style={{ color: "#ef4444" }}>|</span> 1.0</span>}
              <span className="flex-1" />
              <span>{eMax.toFixed(2)}</span>
            </div>

            {/* Stats grid */}
            {result.scenario_value_stats && (
              <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs font-mono">
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Mean</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.mean.toFixed(4)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Std</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.std.toFixed(4)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>P5-P95</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.p5.toFixed(3)}-{result.scenario_value_stats.p95.toFixed(3)}</span></div>
                <div className="flex justify-between"><span style={{ color: "var(--text-muted)" }}>Min-Max</span><span style={{ color: "var(--text-primary)" }}>{result.scenario_value_stats.min.toFixed(3)}-{result.scenario_value_stats.max.toFixed(3)}</span></div>
                <div className="flex justify-between"><span style={{ color: "#22c55e" }}>Increase</span><span style={{ color: "#22c55e" }}>{(result.scenario_value_stats.pct_increase * 100).toFixed(1)}%</span></div>
                <div className="flex justify-between"><span style={{ color: "#ef4444" }}>Decrease</span><span style={{ color: "#ef4444" }}>{(result.scenario_value_stats.pct_decrease * 100).toFixed(1)}%</span></div>
              </div>
            )}
          </div>
        )
      })()}

      {/* Factor tables (ratebook) */}
      {result.mode === "ratebook" && result.factor_tables && (
        <div className="min-w-[180px]">
          <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: "var(--text-muted)" }}>Factor Tables</label>
          {Object.entries(result.factor_tables).map(([factorName, rows]) => (
            <div key={factorName} className="mt-1.5">
              <div className="text-[11px] font-medium mb-1" style={{ color: "var(--text-secondary)" }}>{factorName}</div>
              <div className="space-y-0.5">
                {rows.map((row, i) => {
                  const levelName = row.__factor_group__ as string ?? row[Object.keys(row)[0]] as string ?? `Level ${i}`
                  const mult = row.optimal_scenario_value as number
                  return (
                    <div key={i} className="flex justify-between text-xs font-mono gap-4">
                      <span style={{ color: "var(--text-secondary)" }}>{levelName}</span>
                      <span style={{ color: "var(--text-primary)" }}>{typeof mult === "number" ? mult.toFixed(2) : "?"}</span>
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
