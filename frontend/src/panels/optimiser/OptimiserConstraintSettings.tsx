import { useCallback, useEffect, useRef } from "react"
import { Loader2, RefreshCw, X } from "lucide-react"
import type { GraphPayload } from "../../api/types"
import ExecutionDiagnosticsSummary from "../../components/ExecutionDiagnosticsSummary"
import { CommittedTextField } from "../../components/form"
import { safeParseInt } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import type { OnUpdateConfig } from "../editors/_shared"
import { useOptimiserAutoRange, type FrontierRange } from "./useOptimiserAutoRange"
import { sweptConstraintNames } from "./solveReadiness"
import { Select } from "../../haute-ui"
import {
  boundFromDisplay,
  boundToDisplay,
  CONSTRAINT_BOUND_KINDS,
  CONSTRAINT_BOUND_LABELS,
  constraintBoundKind,
  constraintBoundValue,
  isPercentBound,
  type ConstraintBoundKind,
} from "./constraintBounds"

export type FrontierRangeConfig = { min?: number; max?: number }
type ConstraintConfig = Record<string, Record<string, number>>
type DataInputColumn = { name: string; dtype: string }

type OptimiserConstraintSettingsProps = {
  constraints: ConstraintConfig
  /** One entry per swept constraint; a constraint without one is fixed at its bound. */
  frontierRanges: Record<string, FrontierRangeConfig>
  frontierSteps: number
  dataInputColumns: DataInputColumn[]
  objective: string
  /** The setup is solvable apart from the frontier ranges Auto range fills. */
  canAutoRange: boolean
  accentColor: string
  labelClassName: string
  buildGraph: () => GraphPayload
  nodeId: string
  onUpdate: OnUpdateConfig
  onRemoveConstraint: (name: string) => void
  onConstraintColumnChange: (oldName: string, newName: string) => void
  onConstraintValueChange: (name: string, kind: ConstraintBoundKind, value: number) => void
  onConstraintKindChange: (name: string, kind: ConstraintBoundKind) => void
}

const BOUND_MODES = [
  { swept: false, label: "Fixed" },
  { swept: true, label: "Sweep" },
] as const

function parseOptionalNumber(raw: string): number | undefined {
  const trimmed = raw.trim()
  if (trimmed === "") return undefined
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : undefined
}

/**
 * The Constraints pane's cards and frontier settings. Each card reads as a
 * sentence: the column must be at least / at most a fixed value, or swept
 * from one value to another, as an absolute total or a percentage of its
 * baseline. With nothing swept the solve is a single point.
 */
export default function OptimiserConstraintSettings({
  constraints,
  frontierRanges,
  frontierSteps,
  dataInputColumns,
  objective,
  canAutoRange,
  accentColor,
  labelClassName,
  buildGraph,
  nodeId,
  onUpdate,
  onRemoveConstraint,
  onConstraintColumnChange,
  onConstraintValueChange,
  onConstraintKindChange,
}: OptimiserConstraintSettingsProps) {
  const constraintEntries = Object.entries(constraints)
  const swept = sweptConstraintNames(constraints, frontierRanges)
  const frontierSolves = frontierSteps ** swept.length
  // Auto range fills one constraint at a time; its result lands on the
  // ranges as they are when it finishes, not as they were when it started.
  const frontierRangesRef = useRef(frontierRanges)
  useEffect(() => {
    frontierRangesRef.current = frontierRanges
  }, [frontierRanges])
  const writeRanges = useCallback(
    (ranges: Record<string, FrontierRange>) =>
      onUpdate({ frontier_ranges: { ...frontierRangesRef.current, ...ranges } }),
    [onUpdate],
  )
  const {
    autoRangeTargets,
    autoRangeLoading,
    autoRangeError,
    autoRangeTerminalMetrics,
    autoRangeTerminalStatus,
    autoRangeTerminalReason,
    autoRangeTerminalErrorCode,
    run: handleAutoRange,
  } = useOptimiserAutoRange({
    nodeId,
    buildGraph,
    writeRanges,
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

  // Sweeping starts from the fixed value; going back to Fixed drops the range
  // and the saved bound applies again.
  const setSweep = useCallback(
    (name: string, on: boolean, fixedValue: number | undefined) => {
      const nextRanges = { ...frontierRanges }
      if (on) nextRanges[name] = fixedValue === undefined ? {} : { min: fixedValue }
      else delete nextRanges[name]
      onUpdate({ frontier_ranges: nextRanges })
    },
    [frontierRanges, onUpdate],
  )

  // A swept constraint keeps its entry with either end cleared: clearing
  // the range never stops the sweep.
  const handleFrontierRangeChange = useCallback(
    (name: string, key: keyof FrontierRangeConfig, value: number | undefined) => {
      const nextRange: FrontierRangeConfig = { ...rangeForConstraint(name) }
      if (value === undefined) delete nextRange[key]
      else nextRange[key] = value
      onUpdate({ frontier_ranges: { ...frontierRanges, [name]: nextRange } })
    },
    [frontierRanges, onUpdate, rangeForConstraint],
  )

  const valueClass = "w-full font-mono text-right"

  if (constraintEntries.length === 0) {
    return (
      <p className="mt-1.5 text-[11px]" style={{ color: "var(--text-muted)" }}>
        No constraints: the optimiser maximises the objective alone. Add one to bound a column&apos;s total.
      </p>
    )
  }

  return (
    <div className="mt-1.5 space-y-3" data-testid="constraints-settings">
      <p data-testid="constraints-result" className="text-[11px]" style={{ color: "var(--text-secondary)" }}>
        <span style={{ color: "var(--text-muted)" }}>Result: </span>
        {swept.length === 0
          ? "single point"
          : `frontier over ${swept.join(", ")} · ${frontierSolves.toLocaleString()} ${frontierSolves === 1 ? "solve" : "solves"}`}
      </p>

      <div className="space-y-1.5">
        {constraintEntries.map(([name, spec]) => {
          // A spec without exactly one bound shows no kind until one is chosen;
          // the Solve issue list names it.
          const boundKind = constraintBoundKind(spec)
          const storedValue = boundKind === null ? undefined : constraintBoundValue(spec, boundKind)
          const percent = boundKind !== null && isPercentBound(boundKind)
          const shown = (stored: number | undefined) =>
            stored === undefined || boundKind === null ? "" : String(boundToDisplay(boundKind, stored))
          const stored = (raw: string) => {
            const parsed = parseOptionalNumber(raw)
            return parsed === undefined || boundKind === null ? undefined : boundFromDisplay(boundKind, parsed)
          }
          const unit = percent
            ? <span aria-hidden="true" style={{ color: "var(--text-muted)" }}>%</span>
            : null
          const isSwept = swept.includes(name)
          const range = rangeForConstraint(name)
          const minMissing = range.min === undefined
          const maxMissing = range.max === undefined
          const autoRangeRunning = autoRangeLoading && autoRangeTargets.includes(name)
          const autoRangeNoteId = `${nodeId}-${name}-auto-range-note`
          return (
            <div
              key={name}
              data-testid="constraint-card"
              role="group"
              aria-label={`${name} constraint`}
              className="p-2 rounded-lg space-y-1.5"
              style={{
                background: "var(--bg-panel)",
                border: `1px solid ${isSwept ? withAlpha(accentColor, 0.35) : "var(--border)"}`,
              }}
            >
              <div data-testid="constraint-row" className="flex items-center gap-1.5">
                <Select
                  aria-label={`${name} constraint column`}
                  value={name}
                  onChange={(event) => onConstraintColumnChange(name, event.target.value)}
                  className="flex-1 font-mono"
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
                </Select>
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

              <div className="flex flex-wrap items-start gap-1.5">
                <Select
                  aria-label={`${name} constraint bound type`}
                  aria-invalid={boundKind === null || undefined}
                  value={boundKind ?? ""}
                  onChange={(event) =>
                    onConstraintKindChange(name, event.target.value as ConstraintBoundKind)}
                  className="shrink-0"
                >
                  {boundKind === null && <option value="" disabled>choose a bound</option>}
                  {CONSTRAINT_BOUND_KINDS.map((kind) => (
                    <option key={kind} value={kind}>{CONSTRAINT_BOUND_LABELS[kind]}</option>
                  ))}
                </Select>
                <div
                  role="radiogroup"
                  aria-label={`${name} bound`}
                  className="flex shrink-0 rounded p-0.5"
                  style={{ background: "var(--chrome-hover)" }}
                >
                  {BOUND_MODES.map((mode) => {
                    const selected = isSwept === mode.swept
                    return (
                      <button
                        key={mode.label}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        onClick={() => {
                          if (!selected) setSweep(name, mode.swept, storedValue)
                        }}
                        className="px-2.5 py-0.5 rounded text-[11px] font-medium transition-colors"
                        style={{
                          background: selected ? withAlpha(accentColor, 0.15) : "transparent",
                          color: selected ? accentColor : "var(--text-muted)",
                        }}
                      >
                        {mode.label}
                      </button>
                    )
                  })}
                </div>
                <div className="flex-1 basis-40 min-w-0">
                  {isSwept ? (
                    <div className={`grid ${percent ? "grid-cols-[2rem_minmax(0,1fr)_auto]" : "grid-cols-[2rem_minmax(0,1fr)]"} items-center gap-x-1.5 gap-y-1 text-[11px]`}>
                      <span style={{ color: "var(--text-muted)" }}>from</span>
                      <CommittedTextField
                        type="number"
                        step="any"
                        value={shown(range.min)}
                        aria-label={`${name} sweep from${percent ? " (% of baseline)" : ""}`}
                        aria-invalid={minMissing || undefined}
                        placeholder="Required"
                        onCommit={(raw) => handleFrontierRangeChange(name, "min", stored(raw))}
                        field="regular"
                        className={valueClass}
                      />
                      {unit}
                      <span style={{ color: "var(--text-muted)" }}>to</span>
                      <CommittedTextField
                        type="number"
                        step="any"
                        value={shown(range.max)}
                        aria-label={`${name} sweep to${percent ? " (% of baseline)" : ""}`}
                        aria-invalid={maxMissing || undefined}
                        placeholder="Required"
                        onCommit={(raw) => handleFrontierRangeChange(name, "max", stored(raw))}
                        field="regular"
                        className={valueClass}
                      />
                      {unit}
                      <div className="col-start-2 flex items-center justify-end gap-1.5">
                        {/* Auto range fills absolute totals; a percentage range is typed. */}
                        {percent && (
                          <span id={autoRangeNoteId} className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                            Auto range sets absolute totals
                          </span>
                        )}
                        <button
                          type="button"
                          aria-label={`${autoRangeRunning ? "Restart auto range" : "Auto range"} ${name}`}
                          aria-describedby={percent ? autoRangeNoteId : undefined}
                          onClick={() => handleAutoRange([name])}
                          disabled={!canAutoRange || percent}
                          className="flex items-center gap-1 px-2 py-1 rounded text-[10px] font-medium disabled:opacity-50"
                          style={{
                            background: withAlpha(accentColor, 0.12),
                            color: accentColor,
                          }}
                        >
                          {autoRangeRunning ? (
                            <Loader2 size={10} className="animate-spin" />
                          ) : (
                            <RefreshCw size={10} />
                          )}
                          {autoRangeRunning ? "Restart auto range" : "Auto range"}
                        </button>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-1.5 text-[11px]">
                      <CommittedTextField
                        aria-label={`${name} constraint value${percent ? " (% of baseline)" : ""}`}
                        type="number"
                        step="any"
                        value={shown(storedValue)}
                        disabled={boundKind === null}
                        onCommit={(raw) => {
                          // A cleared or unreadable bound keeps its stored value:
                          // committing 0 would silently relax the constraint.
                          const value = stored(raw)
                          if (value !== undefined && boundKind !== null) onConstraintValueChange(name, boundKind, value)
                        }}
                        field="regular"
                        className={valueClass}
                      />
                      {unit}
                    </div>
                  )}
                </div>
              </div>
              {isSwept && autoRangeError && autoRangeTargets.includes(name) && (
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
          )
        })}
      </div>

      {swept.length > 0 && (
        <section data-testid="frontier-settings" className="space-y-2 pt-2" style={{ borderTop: "1px solid var(--border)" }} aria-labelledby={`${nodeId}-frontier-heading`}>
          <h3 id={`${nodeId}-frontier-heading`} className={labelClassName} style={{ color: "var(--text-muted)" }}>
            Frontier
          </h3>
          <div>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }} htmlFor={`${nodeId}-frontier-steps`}>
              Points per swept constraint
            </label>
            <CommittedTextField
              id={`${nodeId}-frontier-steps`}
              type="number"
              min={2}
              step={1}
              value={String(frontierSteps)}
              onCommit={(value) => onUpdate("frontier_steps", safeParseInt(value, 15))}
              field="regular"
              className="w-full mt-0.5 font-mono"
            />
          </div>
        </section>
      )}
    </div>
  )
}
