import { ConfigSection } from "../../components/form"
import type { EvaluationPreview } from "../../api/types"
import { evaluationConfigurationIssues } from "../../utils/trainingObjective"
import { NumberField } from "./NumberField"
import { EvaluationAllocation } from "./EvaluationAllocation"
import type { EvaluationStrategy, ValidationMethod } from "./evaluationPreview"
import { MODELLING_INPUT_STYLE, toggleButtonStyle } from "./styles"

type Column = { name: string; dtype: string }
type Evaluation = Record<string, unknown>
type Method = ValidationMethod
type Strategy = EvaluationStrategy
export type SplitAndMetricsConfigProps = {
  columns: Column[]
  rowLimit: number | null
  onRowLimitChange: (value: number | null) => void
  evaluation: Evaluation
  onEvaluationChange: (value: Evaluation) => void
  refitOnDevelopment: boolean
  onRefitOnDevelopmentChange: (value: boolean) => void
  tuningEnabled: boolean
  preview: EvaluationPreview | null
  previewError?: string | null
}

const strategies: { value: Strategy; label: string }[] = [
  { value: "random", label: "Random split" },
  { value: "group", label: "Group split" },
  { value: "temporal", label: "Time-based split" },
]
const methods: { value: Method; label: string }[] = [
  { value: "single", label: "Holdout validation" },
  { value: "cross_validation", label: "Cross-validation" },
  { value: "none", label: "No validation" },
]
const field = "mt-2 flex flex-col gap-0.5 text-[13px]"
const obj = (v: unknown): Record<string, unknown> =>
  v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : {}
const number = (v: unknown, fallback: number) =>
  typeof v === "number" && Number.isFinite(v) ? v : fallback
const text = (v: unknown) => (typeof v === "string" ? v : "")

function validation(
  strategy: Strategy,
  method: Method,
  current: Record<string, unknown>,
) {
  if (method === "none") return { method }
  if (method === "cross_validation")
    return {
      method,
      fold_count: number(current.fold_count, 5),
      ...(strategy === "temporal" ? { window: "expanding" } : {}),
    }
  return strategy === "temporal"
    ? { method, start: text(current.start) }
    : { method, size: number(current.size, 0.2) }
}
export function SplitAndMetricsConfig({
  columns,
  rowLimit,
  onRowLimitChange,
  evaluation,
  onEvaluationChange,
  refitOnDevelopment,
  onRefitOnDevelopmentChange,
  tuningEnabled,
  preview,
  previewError = null,
}: SplitAndMetricsConfigProps) {
  const strategy = (
    evaluation.strategy === "group" || evaluation.strategy === "temporal"
      ? evaluation.strategy
      : "random"
  ) as Strategy
  const current = obj(evaluation.validation)
  const method = (
    current.method === "none" || current.method === "cross_validation"
      ? current.method
      : "single"
  ) as Method
  const test = evaluation.test == null ? null : obj(evaluation.test)
  const validationFraction = number(current.size, 0.2)
  const testFraction = number(test?.size, 0)
  const issues = evaluationConfigurationIssues(evaluation)
  const sumIssue = issues.find((x) =>
    /total below 100%|fraction|sum/i.test(x.message),
  )
  const update = (x: Evaluation) => onEvaluationChange({ ...evaluation, ...x })
  const updateTest = (value: Evaluation | null) => {
    if (value === null) {
      const next = { ...evaluation }
      delete next.test
      onEvaluationChange(next)
    } else {
      update({ test: value })
    }
  }
  const changeStrategy = (next: Strategy) => {
    const e: Evaluation = {
      schema_version: 1,
      strategy: next,
      validation: validation(next, method, current),
    }
    if (next === "temporal") {
      e.date_column = text(evaluation.date_column)
      const start = text(test?.start)
      if (start) e.test = { start }
    } else {
      e.seed = number(evaluation.seed, 42)
      if (next === "group") e.group_column = text(evaluation.group_column)
      if (typeof test?.size === "number") e.test = { size: test.size }
    }
    onEvaluationChange(e)
  }
  return (
    <div className="space-y-3">
      <ConfigSection title="Row limit">
        <div className="flex items-center gap-2">
          <input
            aria-label="Row limit"
            type="number"
            min={0}
            step={100000}
            value={rowLimit ?? ""}
            onChange={(event) =>
              onRowLimitChange(event.target.value === "" ? null : Math.max(0, Number(event.target.value)))
            }
            placeholder="All rows"
            className="h-8 w-full min-w-0 rounded px-2 font-mono text-[13px]"
            style={MODELLING_INPUT_STYLE}
          />
          {rowLimit !== null && rowLimit > 0 && (
            <span className="shrink-0 text-xs font-mono" style={{ color: "var(--text-muted)" }}>
              {rowLimit.toLocaleString()} rows
            </span>
          )}
        </div>
      </ConfigSection>
      {issues
        .filter((x) => x !== sumIssue)
        .map((x) => (
          <p
            key={x.code}
            role="alert"
            className="rounded border px-2 py-1 text-xs"
            style={{ color: "var(--danger)", borderColor: "var(--danger)" }}
          >
            {x.message}
          </p>
        ))}
      <EvaluationAllocation
        strategy={strategy}
        method={method}
        validationSize={
          method === "cross_validation"
            ? number(current.fold_count, 5)
            : validationFraction
        }
        testSize={testFraction}
        hasTest={test !== null}
        preview={preview}
        invalid={issues.length > 0}
      />
      <ConfigSection title="Split strategy">
        <div className="flex flex-wrap gap-2">
          {strategies.map((x) => (
            <button
              type="button"
              key={x.value}
              aria-pressed={strategy === x.value}
              onClick={() => changeStrategy(x.value)}
              className="rounded-md px-3 py-1 text-[13px] font-medium"
              style={toggleButtonStyle(strategy === x.value)}
            >
              {x.label}
            </button>
          ))}
        </div>
        {strategy === "group" && (
          <label className={field}>
            Group column
            <select
              aria-label="Group column"
              value={text(evaluation.group_column)}
              onChange={(e) => update({ group_column: e.target.value })}
              className="h-8 rounded px-2 font-mono text-[13px]"
              style={MODELLING_INPUT_STYLE}
            >
              <option value="">Select...</option>
              {columns.map((x) => (
                <option key={x.name} value={x.name}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {strategy === "temporal" && (
          <label className={field}>
            Date column
            <select
              aria-label="Date column"
              value={text(evaluation.date_column)}
              onChange={(e) => update({ date_column: e.target.value })}
              className="h-8 rounded px-2 font-mono text-[13px]"
              style={MODELLING_INPUT_STYLE}
            >
              <option value="">Select...</option>
              {columns.map((x) => (
                <option key={x.name} value={x.name}>
                  {x.name}
                </option>
              ))}
            </select>
          </label>
        )}
      </ConfigSection>
      <ConfigSection title="Validation strategy">
        <div className="flex flex-wrap gap-2">
          {methods.map((x) => (
            <button
              type="button"
              key={x.value}
              aria-pressed={method === x.value}
              onClick={() =>
                update({ validation: validation(strategy, x.value, current) })
              }
              className="rounded-md px-3 py-1 text-[13px] font-medium"
              style={toggleButtonStyle(method === x.value)}
            >
              {x.label}
            </button>
          ))}
        </div>
        {method === "single" &&
          (strategy === "temporal" ? (
            <label className={field}>
              Validation starts
              <input
                aria-label="Validation starts"
                type="date"
                value={text(current.start)}
                onChange={(e) =>
                  update({
                    validation: { method: "single", start: e.target.value },
                  })
                }
                className="h-8 rounded px-2 font-mono text-[13px]"
                style={MODELLING_INPUT_STYLE}
              />
            </label>
          ) : (
            <div className={field}>
              <span>Validation set (%)</span>
              <NumberField
                label="Validation set (%)"
                value={validationFraction * 100}
                min={0}
                max={100}
                exclusiveMin
                exclusiveMax
                integer={false}
                required
                step={1}
                className="h-8 w-full rounded px-2 font-mono text-[13px]"
                onCommit={(v) =>
                  v !== undefined &&
                  update({ validation: { method: "single", size: v / 100 } })
                }
              />
            </div>
          ))}
        {method === "cross_validation" && (
          <div className={field}>
            <span>Fold count</span>
            <NumberField
              label="Fold count"
              value={number(current.fold_count, 5)}
              min={2}
              max={10}
              integer
              required
              step={1}
              className="h-8 w-full rounded px-2 font-mono text-[13px]"
              onCommit={(v) =>
                v !== undefined &&
                update({
                  validation: {
                    method: "cross_validation",
                    fold_count: v,
                    ...(strategy === "temporal" ? { window: "expanding" } : {}),
                  },
                })
              }
            />
          </div>
        )}
        {method === "single" && (
          <label className="flex items-center gap-2 text-[13px] cursor-pointer select-none">
            <input
              type="checkbox"
              checked={refitOnDevelopment}
              disabled={tuningEnabled && refitOnDevelopment}
              className="accent-purple-500"
              onChange={(event) => onRefitOnDevelopmentChange(event.target.checked)}
            />
            Refit on training + validation
          </label>
        )}
        {sumIssue && (
          <p
            role="alert"
            className="mt-2 text-xs"
            style={{ color: "var(--danger)" }}
          >
            {sumIssue.message}
          </p>
        )}
      </ConfigSection>
      <ConfigSection title="Test set">
        {strategy === "temporal" ? (
          <label className={field}>
            Test starts
            <input
              aria-label="Test starts"
              type="date"
              value={text(test?.start)}
              onChange={(e) =>
                updateTest(e.target.value ? { start: e.target.value } : null)
              }
              className="h-8 rounded px-2 font-mono text-[13px]"
              style={MODELLING_INPUT_STYLE}
            />
          </label>
        ) : (
          <div className={field}>
            <span>Test set (%)</span>
            <NumberField
              label="Test set (%)"
              value={testFraction * 100}
              min={0}
              max={100}
              exclusiveMax
              integer={false}
              required
              step={1}
              className="h-8 w-full rounded px-2 font-mono text-[13px]"
              onCommit={(v) =>
                v !== undefined && updateTest(v === 0 ? null : { size: v / 100 })
              }
            />
          </div>
        )}
      </ConfigSection>
      {previewError && (
        <p role="alert" className="text-xs" style={{ color: "var(--danger)" }}>
          {previewError.startsWith("Evaluation preview failed:")
            ? previewError
            : `Evaluation preview failed: ${previewError}`}
        </p>
      )}
    </div>
  )
}
