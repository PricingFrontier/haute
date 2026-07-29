import { CHART_COLORS } from "../../theme/colors"
import type { EvaluationPreview } from "../../api/types"
import { safeParseInt } from "../../utils/configField"

type Column = { name: string; dtype: string }
type Evaluation = Record<string, unknown>
type ValidationMethod = "single" | "cross_validation" | "none"
type EvaluationStrategy = "random" | "group" | "temporal"

export type SplitAndMetricsConfigProps = {
  columns: Column[]
  evaluation: Evaluation
  onEvaluationChange: (evaluation: Evaluation) => void
  preview: EvaluationPreview | null
}

const inputStyle = {
  background: "var(--bg-input)",
  border: "1px solid var(--border)",
  color: "var(--text-primary)",
}

const strategyOptions: readonly {
  value: EvaluationStrategy
  label: string
}[] = [
  { value: "random", label: "Random rows" },
  { value: "group", label: "Keep entities together" },
  { value: "temporal", label: "Respect time order" },
]

const validationOptions: readonly {
  value: ValidationMethod
  label: string
}[] = [
  { value: "single", label: "Single validation" },
  { value: "cross_validation", label: "Cross-validation" },
  { value: "none", label: "No validation" },
]

function objectField(
  value: unknown,
): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function numberField(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback
}

function stringField(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback
}

function canonicalValidation(
  strategy: EvaluationStrategy,
  method: ValidationMethod,
  current: Record<string, unknown>,
): Record<string, unknown> {
  if (method === "none") return { method }
  if (method === "cross_validation") {
    return strategy === "temporal"
      ? {
          method,
          fold_count: Math.max(2, Math.min(10, numberField(current.fold_count, 5))),
          window: "expanding",
        }
      : {
          method,
          fold_count: Math.max(2, Math.min(10, numberField(current.fold_count, 5))),
        }
  }
  return strategy === "temporal"
    ? { method, start: stringField(current.start) }
    : { method, size: numberField(current.size, 0.2) }
}

/** One canonical development/validation/final-test workflow. */
export function SplitAndMetricsConfig({
  columns,
  evaluation,
  onEvaluationChange,
  preview,
}: SplitAndMetricsConfigProps) {
  const strategy = (
    evaluation.strategy === "group" || evaluation.strategy === "temporal"
      ? evaluation.strategy
      : "random"
  ) as EvaluationStrategy
  const validation = objectField(evaluation.validation)
  const validationMethod = (
    validation.method === "none" || validation.method === "cross_validation"
      ? validation.method
      : "single"
  ) as ValidationMethod
  const test = evaluation.test === undefined
    ? null
    : objectField(evaluation.test)
  const validationSize = numberField(validation.size, 0.2)
  const testSize = test === null ? 0 : numberField(test.size, 0.2)
  const developmentSize = Math.max(0, 1 - testSize)
  const selectionBounds = (
    label: string,
    minimum: number | undefined,
    maximum: number | undefined,
  ) => {
    if (minimum === undefined && maximum === undefined) return null
    const bounds = [
      minimum === undefined ? null : `min ${minimum}`,
      maximum === undefined ? null : `max ${maximum}`,
    ].filter((value): value is string => value !== null).join(", ")
    return <div>{label}: {bounds}</div>
  }

  const changeStrategy = (next: EvaluationStrategy) => {
    const nextValidation = canonicalValidation(
      next,
      validationMethod,
      validation,
    )
    const nextEvaluation: Evaluation = {
      schema_version: 1,
      strategy: next,
      validation: nextValidation,
    }
    if (next === "temporal") {
      nextEvaluation.date_column = stringField(evaluation.date_column)
      if (test !== null) nextEvaluation.test = { start: stringField(test.start) }
    } else {
      nextEvaluation.seed = numberField(evaluation.seed, 42)
      if (next === "group") {
        nextEvaluation.group_column = stringField(evaluation.group_column)
      }
      if (test !== null) nextEvaluation.test = { size: numberField(test.size, 0.2) }
    }
    onEvaluationChange(nextEvaluation)
  }

  const update = (fields: Evaluation) => {
    onEvaluationChange({ ...evaluation, ...fields })
  }

  return (
    <div className="space-y-4">
      <section aria-labelledby="evaluation-structure-heading">
        <h3
          id="evaluation-structure-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          How is the data structured?
        </h3>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {strategyOptions.map((option) => {
            const active = strategy === option.value
            return (
              <button
                type="button"
                key={option.value}
                aria-pressed={active}
                onClick={() => changeStrategy(option.value)}
                className="rounded-md px-3 py-1 text-xs font-medium"
                style={{
                  background: active ? "var(--accent-soft)" : "var(--bg-input)",
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                  color: active ? "var(--accent)" : "var(--text-secondary)",
                }}
              >
                {option.label}
              </button>
            )
          })}
        </div>

        {strategy === "group" && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            Entity column
            <select
              aria-label="Entity column"
              value={stringField(evaluation.group_column)}
              onChange={(event) => update({ group_column: event.target.value })}
              className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
              style={inputStyle}
            >
              <option value="">Select...</option>
              {columns.map((column) => (
                <option key={column.name} value={column.name}>{column.name}</option>
              ))}
            </select>
          </label>
        )}

        {strategy === "temporal" && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            Date column
            <select
              aria-label="Date column"
              value={stringField(evaluation.date_column)}
              onChange={(event) => update({ date_column: event.target.value })}
              className="mt-0.5 w-full rounded-lg px-2.5 py-1.5 font-mono text-xs"
              style={inputStyle}
            >
              <option value="">Select...</option>
              {columns.map((column) => (
                <option key={column.name} value={column.name}>{column.name}</option>
              ))}
            </select>
          </label>
        )}
      </section>

      <section aria-labelledby="evaluation-validation-heading">
        <h3
          id="evaluation-validation-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          How should candidates be validated?
        </h3>
        <div className="mt-1.5 flex flex-wrap gap-2">
          {validationOptions.map((option) => {
            const active = validationMethod === option.value
            return (
              <button
                type="button"
                key={option.value}
                aria-pressed={active}
                onClick={() => update({
                  validation: canonicalValidation(
                    strategy,
                    option.value,
                    validation,
                  ),
                })}
                className="rounded-md px-3 py-1 text-xs font-medium"
                style={{
                  background: active ? "var(--accent-soft)" : "var(--bg-input)",
                  border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                  color: active ? "var(--accent)" : "var(--text-secondary)",
                }}
              >
                {option.label}
              </button>
            )
          })}
        </div>

        {validationMethod === "single" && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            {strategy === "temporal" ? "Validation starts" : "Validation fraction"}
            <input
              aria-label={strategy === "temporal" ? "Validation starts" : "Validation fraction"}
              type={strategy === "temporal" ? "date" : "number"}
              min={strategy === "temporal" ? undefined : 0}
              max={strategy === "temporal" ? undefined : 0.9}
              step={strategy === "temporal" ? undefined : 0.05}
              value={strategy === "temporal" ? stringField(validation.start) : validationSize}
              onChange={(event) => update({
                validation: strategy === "temporal"
                  ? { method: "single", start: event.target.value }
                  : {
                      method: "single",
                      size: Number.parseFloat(event.target.value) || 0,
                    },
              })}
              className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
              style={inputStyle}
            />
          </label>
        )}

        {validationMethod === "cross_validation" && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            Fold count
            <input
              aria-label="Fold count"
              type="number"
              min={2}
              max={10}
              value={numberField(validation.fold_count, 5)}
              onChange={(event) => update({
                validation: {
                  method: "cross_validation",
                  fold_count: Math.max(
                    2,
                    Math.min(10, safeParseInt(event.target.value, 5)),
                  ),
                  ...(strategy === "temporal" ? { window: "expanding" } : {}),
                },
              })}
              className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
              style={inputStyle}
            />
          </label>
        )}
      </section>

      <section aria-labelledby="evaluation-test-heading">
        <h3
          id="evaluation-test-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Reserve an untouched final test?
        </h3>
        <label className="mt-1.5 flex items-center gap-2 text-[11px]">
          <input
            type="checkbox"
            aria-label="Reserve final test"
            checked={test !== null}
            onChange={(event) => {
              if (!event.target.checked) {
                const { test: _test, ...withoutTest } = evaluation
                onEvaluationChange(withoutTest)
              } else {
                update({
                  test: strategy === "temporal"
                    ? { start: "" }
                    : { size: 0.2 },
                })
              }
            }}
          />
          <span style={{ color: "var(--text-primary)" }}>
            Keep final test data unseen until the selected model is fixed
          </span>
        </label>

        {test !== null && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            {strategy === "temporal" ? "Final test starts" : "Final test fraction"}
            <input
              aria-label={strategy === "temporal" ? "Final test starts" : "Final test fraction"}
              type={strategy === "temporal" ? "date" : "number"}
              min={strategy === "temporal" ? undefined : 0}
              max={strategy === "temporal" ? undefined : 0.9}
              step={strategy === "temporal" ? undefined : 0.05}
              value={strategy === "temporal" ? stringField(test.start) : testSize}
              onChange={(event) => update({
                test: strategy === "temporal"
                  ? { start: event.target.value }
                  : { size: Number.parseFloat(event.target.value) || 0 },
              })}
              className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
              style={inputStyle}
            />
          </label>
        )}

        {strategy !== "temporal" && (
          <div
            aria-label="Evaluation allocation"
            className="mt-2 flex h-1.5 gap-0.5 overflow-hidden rounded-full"
            style={{ background: "var(--chrome-hover)" }}
          >
            <div
              style={{
                width: `${developmentSize * 100}%`,
                background: CHART_COLORS.train,
              }}
              title={`Development data: ${(developmentSize * 100).toFixed(0)}%`}
            />
            {testSize > 0 && (
              <div
                style={{
                  width: `${testSize * 100}%`,
                  background: "var(--signif-high)",
                }}
                title={`Final test: ${(testSize * 100).toFixed(0)}%`}
              />
            )}
          </div>
        )}

        {strategy !== "temporal" && (
          <label className="mt-2 block text-[11px]" style={{ color: "var(--text-muted)" }}>
            Seed
            <input
              aria-label="Evaluation seed"
              type="number"
              value={numberField(evaluation.seed, 42)}
              onChange={(event) => update({
                seed: safeParseInt(event.target.value, 42),
              })}
              className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
              style={inputStyle}
            />
          </label>
        )}
      </section>

      {preview !== null && (
        <section
          aria-label="Exact evaluation preview"
          className="rounded-lg border px-3 py-2 text-[11px]"
          style={{ borderColor: "var(--border)", background: "var(--bg-input)", color: "var(--text-secondary)" }}
        >
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>Exact evaluation</div>
          <div className="mt-1 space-y-0.5">
            <div>Development rows: {preview.development_rows}</div>
            <div>Final-test rows: {preview.final_test_rows}</div>
            <div>Validation fits: {preview.validation_fit_count}</div>
            {selectionBounds("Selection train rows", preview.min_selection_train_rows, preview.max_selection_train_rows)}
            {selectionBounds("Selection validation rows", preview.min_selection_validation_rows, preview.max_selection_validation_rows)}
            {preview.strategy === "group" && (
              <>
                {preview.development_group_count !== undefined && <div>Development groups: {preview.development_group_count}</div>}
                {preview.final_test_group_count !== undefined && <div>Final-test groups: {preview.final_test_group_count}</div>}
              </>
            )}
            {preview.strategy === "temporal" && (
              <>
                {preview.development_date_range !== undefined && <div>Development dates: {preview.development_date_range.start} to {preview.development_date_range.end}</div>}
                {preview.final_test_date_range !== undefined && <div>Final-test dates: {preview.final_test_date_range.start} to {preview.final_test_date_range.end}</div>}
              </>
            )}
          </div>
        </section>
      )}
    </div>
  )
}
