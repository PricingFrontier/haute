import { useCallback } from "react"
import { Loader2, RefreshCw, X } from "lucide-react"
import type { GraphPayload } from "../../api/types"
import ExecutionDiagnosticsSummary from "../../components/ExecutionDiagnosticsSummary"
import { CommittedTextField } from "../../components/form"
import { safeParseInt } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import type { OnUpdateConfig } from "../editors"
import { useOptimiserAutoRange } from "./useOptimiserAutoRange"

export type FrontierRangeConfig = { min?: number; max?: number }
type ConstraintConfig = Record<string, Record<string, number>>
type DataInputColumn = { name: string; dtype: string }

type OptimiserConstraintSettingsProps = {
  constraints: ConstraintConfig
  frontierRanges: Record<string, FrontierRangeConfig>
  frontierEnabled: boolean
  frontierSteps: number
  dataInputColumns: DataInputColumn[]
  objective: string
  /** The setup is solvable apart from the frontier ranges Auto range fills. */
  canAutoRange: boolean
  accentColor: string
  buildGraph: () => GraphPayload
  nodeId: string
  onUpdate: OnUpdateConfig
  onRemoveConstraint: (name: string) => void
  onConstraintColumnChange: (oldName: string, newName: string) => void
  onConstraintValueChange: (name: string, type: string, value: number) => void
}

const CONSTRAINT_TYPES = [
  { value: "min", label: "Minimum" },
  { value: "max", label: "Maximum" },
]

function parseOptionalNumber(raw: string): number | undefined {
  const trimmed = raw.trim()
  if (trimmed === "") return undefined
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

export default function OptimiserConstraintSettings({
  constraints,
  frontierRanges,
  frontierEnabled,
  frontierSteps,
  dataInputColumns,
  objective,
  canAutoRange,
  accentColor,
  buildGraph,
  nodeId,
  onUpdate,
  onRemoveConstraint,
  onConstraintColumnChange,
  onConstraintValueChange,
}: OptimiserConstraintSettingsProps) {
  const constraintEntries = Object.entries(constraints)
  const constraintCount = constraintEntries.length
  const {
    autoRangeLoading,
    autoRangeError,
    autoRangeTerminalMetrics,
    autoRangeTerminalStatus,
    autoRangeTerminalReason,
    autoRangeTerminalErrorCode,
    run: handleAutoRange,
  } = useOptimiserAutoRange({
    nodeId,
    constraintNames: constraintEntries.map(([name]) => name),
    buildGraph,
    onUpdate,
  })

  const rangeForConstraint = useCallback(
    (name: string): FrontierRangeConfig => {
      const configured = frontierRanges[name]
      return {
        min: typeof configured?.min === "number" && Number.isFinite(configured.min) ? configured.min : undefined,
        max: typeof configured?.max === "number" && Number.isFinite(configured.max) ? configured.max : undefined,
      }
    },
    [frontierRanges],
  )

  const handleFrontierRangeChange = useCallback(
    (name: string, key: keyof FrontierRangeConfig, value: number | undefined) => {
      const nextRange: FrontierRangeConfig = { ...rangeForConstraint(name) }
      if (value === undefined) delete nextRange[key]
      else nextRange[key] = value
      const nextRanges = { ...frontierRanges }
      if (nextRange.min === undefined && nextRange.max === undefined) delete nextRanges[name]
      else nextRanges[name] = nextRange
      onUpdate({ frontier_ranges: nextRanges })
    },
    [frontierRanges, onUpdate, rangeForConstraint],
  )

  const inputStyle = {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  }
  const rangeInputStyle = (missing: boolean) => ({
    background: missing ? "var(--warning-soft)" : "var(--bg-input)",
    border: `1px solid ${missing ? "var(--warning-border-strong)" : "var(--border)"}`,
    color: "var(--text-primary)",
  })

  return (
    <div className="mt-1.5 space-y-2" data-testid="constraints-settings">
      {constraintCount > 0 && (
        <>
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Result type
            </label>
            <div className="mt-1 flex gap-1">
              {[
                { enabled: false, label: "Individual point" },
                { enabled: true, label: "Efficient frontier" },
              ].map((option) => {
                const selected = frontierEnabled === option.enabled
                return (
                  <button
                    key={option.label}
                    onClick={() => onUpdate("frontier_enabled", option.enabled)}
                    className="flex-1 px-2 py-1 rounded text-[11px] font-medium transition-colors"
                    style={{
                      background: selected
                        ? withAlpha(accentColor, 0.15)
                        : "var(--chrome-hover)",
                      color: selected ? accentColor : "var(--text-muted)",
                      border: `1px solid ${
                        selected ? withAlpha(accentColor, 0.3) : "transparent"
                      }`,
                    }}
                  >
                    {option.label}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="space-y-1.5">
            {constraintEntries.map(([name, spec]) => {
              const constraintType = Object.keys(spec)
                .find((key) => key === "min" || key === "max") ?? "min"
              const constraintValue = spec[constraintType] ?? 0
              const range = rangeForConstraint(name)
              const minMissing = range.min === undefined
              const maxMissing = range.max === undefined
              return (
                <div
                  key={name}
                  data-testid="constraint-card"
                  className="p-2 rounded-lg space-y-1.5"
                  style={{
                    background: "var(--bg-panel)",
                    border: "1px solid var(--border)",
                  }}
                >
                  <div data-testid="constraint-row" className="flex items-center gap-1.5">
                    <select
                      aria-label={`${name} constraint column`}
                      value={name}
                      onChange={(event) => onConstraintColumnChange(name, event.target.value)}
                      className="flex-1 min-w-0 px-1.5 py-1 rounded text-[11px] font-mono"
                      style={inputStyle}
                    >
                      <option value={name}>{name}</option>
                      {dataInputColumns
                        .filter(
                          (column) =>
                            column.name !== name
                            && column.name !== objective
                            && !constraints[column.name],
                        )
                        .map((column) => (
                          <option key={column.name} value={column.name}>
                            {column.name}
                          </option>
                        ))}
                    </select>
                    <button
                      type="button"
                      aria-label={`Remove ${name} constraint`}
                      onClick={() => onRemoveConstraint(name)}
                      className="p-0.5 rounded transition-colors shrink-0"
                      style={{ color: "var(--text-muted)" }}
                    >
                      <X size={12} />
                    </button>
                  </div>
                  {!frontierEnabled ? (
                    <div
                      data-testid="constraint-bound-row"
                      className="grid grid-cols-[90px_minmax(0,1fr)] items-center gap-1.5"
                    >
                      <select
                        aria-label={`${name} constraint bound type`}
                        value={constraintType}
                        onChange={(event) =>
                          onConstraintValueChange(
                            name,
                            event.target.value,
                            constraintValue,
                          )}
                        className="px-1 py-1 rounded text-[10px]"
                        style={{ ...inputStyle, color: "var(--text-secondary)" }}
                      >
                        {CONSTRAINT_TYPES.map((constraintTypeOption) => (
                          <option
                            key={constraintTypeOption.value}
                            value={constraintTypeOption.value}
                          >
                            {constraintTypeOption.label}
                          </option>
                        ))}
                      </select>
                      <CommittedTextField
                        aria-label={`${name} constraint value`}
                        type="number"
                        step="any"
                        value={String(constraintValue)}
                        onCommit={(raw) => {
                          // A cleared or unreadable bound keeps its stored value:
                          // committing 0 would silently relax the constraint.
                          const parsed = parseOptionalNumber(raw)
                          if (parsed !== undefined) onConstraintValueChange(name, constraintType, parsed)
                        }}
                        className="w-full px-1.5 py-1 rounded text-[11px] font-mono text-right"
                        style={inputStyle}
                      />
                    </div>
                  ) : (
                    <div data-testid="frontier-range-row" className="grid grid-cols-2 gap-2">
                      <div>
                        <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          Min value
                        </label>
                        <CommittedTextField
                          type="number"
                          step="any"
                          value={range.min === undefined ? "" : String(range.min)}
                          aria-label={`${name} min value`}
                          aria-invalid={minMissing || undefined}
                          placeholder="Required"
                          onCommit={(raw) =>
                            handleFrontierRangeChange(
                              name,
                              "min",
                              parseOptionalNumber(raw),
                            )}
                          className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                          style={rangeInputStyle(minMissing)}
                        />
                      </div>
                      <div>
                        <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                          Max value
                        </label>
                        <CommittedTextField
                          type="number"
                          step="any"
                          value={range.max === undefined ? "" : String(range.max)}
                          aria-label={`${name} max value`}
                          aria-invalid={maxMissing || undefined}
                          placeholder="Required"
                          onCommit={(raw) =>
                            handleFrontierRangeChange(
                              name,
                              "max",
                              parseOptionalNumber(raw),
                            )}
                          className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                          style={rangeInputStyle(maxMissing)}
                        />
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>

          {frontierEnabled && (
            <div data-testid="frontier-settings" className="space-y-2">
              <div className="grid grid-cols-[minmax(0,1fr)_auto] items-end gap-2">
                <div>
                  <label className="text-[11px]" style={{ color: "var(--text-muted)" }} htmlFor={`${nodeId}-frontier-steps`}>
                    Steps per constraint
                  </label>
                  <CommittedTextField
                    id={`${nodeId}-frontier-steps`}
                    type="number"
                    min={2}
                    step={1}
                    value={String(frontierSteps)}
                    onCommit={(value) =>
                      onUpdate("frontier_steps", safeParseInt(value, 15))}
                    className="w-full mt-0.5 px-2 py-1 rounded text-xs font-mono"
                    style={inputStyle}
                  />
                  <div className="mt-0.5 text-[10px]" style={{ color: "var(--text-muted)" }}>
                    {`${(frontierSteps ** constraintCount).toLocaleString()} solves in total`}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleAutoRange}
                  disabled={!canAutoRange}
                  className="flex items-center gap-1 px-2 py-1.5 rounded text-[10px] font-medium disabled:opacity-50"
                  style={{
                    background: withAlpha(accentColor, 0.12),
                    color: accentColor,
                  }}
                >
                  {autoRangeLoading ? (
                    <Loader2 size={10} className="animate-spin" />
                  ) : (
                    <RefreshCw size={10} />
                  )}
                  {autoRangeLoading ? "Restart auto range" : "Auto range"}
                </button>
              </div>
              {autoRangeError && (
                <div className="space-y-1">
                  <div className="text-[11px]" style={{ color: "var(--warning)" }}>
                    {autoRangeError}
                  </div>
                  <ExecutionDiagnosticsSummary
                    metrics={autoRangeTerminalMetrics}
                    status={autoRangeTerminalStatus}
                    terminalReason={autoRangeTerminalReason}
                    errorCode={autoRangeTerminalErrorCode}
                  />
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}
