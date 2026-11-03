import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { CommittedTextField, SavedValueOption, ValidatedTextField } from "../../components/form"

function numberConfigText(value: unknown): string {
  return value === null || value === undefined ? "" : String(value)
}

/** A range bound as typed: blank clears it (null), otherwise a finite number. */
function parseScenarioNumber(raw: string): number | null {
  const trimmed = raw.trim()
  if (!trimmed) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function rangeBoundError(raw: string): string | null {
  return raw.trim() === "" || parseScenarioNumber(raw) !== null ? null : "Enter a number, or leave it blank."
}

function RangeBoundField({
  label,
  field,
  value,
  onUpdate,
}: {
  label: string
  field: "min_value" | "max_value"
  value: unknown
  onUpdate: OnUpdateConfig
}) {
  return (
    <ValidatedTextField
      dataTestId={`scenario-${field}`}
      ariaLabel={label}
      containerClassName=""
      className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
      style={INPUT_STYLE}
      inputMode="decimal"
      value={numberConfigText(value)}
      validate={rangeBoundError}
      onCommit={(raw) => onUpdate(field, parseScenarioNumber(raw))}
    />
  )
}

/** The backend's `scenario_step_count` rule: a whole number of at least 1. */
function stepCountError(raw: string): string | null {
  return /^\s*\d+\s*$/.test(raw) && Number(raw) >= 1 ? null : "Steps must be a whole number of at least 1."
}

function StepsField({
  steps,
  onUpdate,
  ariaLabel,
  className,
}: {
  steps: unknown
  onUpdate: OnUpdateConfig
  ariaLabel?: string
  className: string
}) {
  return (
    <ValidatedTextField
      dataTestId="scenario-steps"
      ariaLabel={ariaLabel}
      containerClassName=""
      className={className}
      style={INPUT_STYLE}
      value={steps === null || steps === undefined ? "" : String(steps)}
      validate={stepCountError}
      onCommit={(v) => onUpdate("stepCount", Number(v))}
    />
  )
}

export default function ScenarioExpanderEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  upstreamColumns,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns: { name: string; dtype: string }[]
}) {
  const quoteId = configField(config, "quote_id", "")
  const columnName = configField(config, "column_name", "")
  const minValue = configField(config, "min_value", "")
  const maxValue = configField(config, "max_value", "")
  const steps: unknown = config.stepCount
  const stepCount = typeof steps === "number" && Number.isInteger(steps) && steps >= 1 ? steps : null
  const stepColumn = configField(config, "step_column", "")
  const parsedMin = parseScenarioNumber(numberConfigText(minValue))
  const parsedMax = parseScenarioNumber(numberConfigText(maxValue))

  return (
    <div className="px-4 py-3 space-y-4">
      <InputSourcesBar inputSources={inputSources} onDeleteInput={onDeleteInput} />

      {/* Row key */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Row Key
          <span className="ml-1.5 normal-case tracking-normal font-normal">unique column per input row</span>
        </label>
        {upstreamColumns.length > 0 ? (
          <select
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono appearance-none cursor-pointer"
            style={INPUT_STYLE}
            value={quoteId}
            onChange={(e) => onUpdate("quote_id", e.target.value)}
          >
            <option value="">-- select column --</option>
            <SavedValueOption value={quoteId} options={upstreamColumns.map((c) => c.name)} />
            {upstreamColumns.map((c) => (
              <option key={c.name} value={c.name}>{c.name}</option>
            ))}
          </select>
        ) : (
          <CommittedTextField
            type="text"
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
            style={INPUT_STYLE}
            value={quoteId}
            onCommit={(v) => onUpdate("quote_id", v)}
          />
        )}
      </div>

      {/* Index column name */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Index Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">0-based step index column</span>
        </label>
        <CommittedTextField
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={stepColumn}
          onCommit={(v) => onUpdate("step_column", v)}
        />
      </div>

      {/* Steps — shown standalone when no value column is set */}
      {!columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Steps
            <span className="ml-1.5 normal-case tracking-normal font-normal">rows generated per input row</span>
          </label>
          <StepsField
            steps={steps}
            onUpdate={onUpdate}
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          />
        </div>
      )}

      {/* Value column (optional) */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Value Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
        </label>
        <CommittedTextField
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={columnName}
          onCommit={(v) => onUpdate("column_name", v)}
        />
      </div>

      {/* Value range — only shown when value column is set */}
      {columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Value Range
          </label>
          {parsedMin !== null && parsedMax !== null && parsedMin >= parsedMax && (
            <div className="mb-1.5 px-2 py-1 rounded text-[10px]" style={{ background: 'var(--warning-soft-strong)', color: 'var(--warning-strong)', border: '1px solid var(--warning-border)' }}>
              Warning: min value should be less than max value
            </div>
          )}
          <div className="grid grid-cols-4 gap-2">
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Min</label>
              <RangeBoundField label="Min" field="min_value" value={minValue} onUpdate={onUpdate} />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Max</label>
              <RangeBoundField label="Max" field="max_value" value={maxValue} onUpdate={onUpdate} />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Steps</label>
              <StepsField
                steps={steps}
                onUpdate={onUpdate}
                ariaLabel="Steps"
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Step Size</label>
              <div
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={{ ...INPUT_STYLE, opacity: 0.7 }}
                data-testid="step-size"
              >
                {stepCount !== null && stepCount > 1 && parsedMin !== null && parsedMax !== null ? +((parsedMax - parsedMin) / (stepCount - 1)).toFixed(4) : "-"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
