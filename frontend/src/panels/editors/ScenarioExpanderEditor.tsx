import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { configField } from "../../utils/configField"
import { CommittedTextField, NumberField, SavedValueOption } from "../../components/form"
import { readTypedNumber } from "../../utils/typedNumbers"

/** A stored range bound or step count as a number: a JSON number, or text by the typed-number grammar. */
function storedScenarioNumber(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null
  return typeof value === "string" ? readTypedNumber(value) : null
}

const PROBLEM_CLASS = "mt-0.5 block px-1.5 py-0.5 rounded text-[10px] leading-snug"

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
    <div>
      <NumberField
        data-testid={`scenario-${field}`}
        aria-label={label}
        className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
        style={INPUT_STYLE}
        value={storedScenarioNumber(value)}
        onCommit={(next) => onUpdate(field, next)}
        problemTestId={`scenario-${field}-error`}
        problemClassName={PROBLEM_CLASS}
      />
    </div>
  )
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
  // The backend's `scenario_step_count` rule: a whole number of at least 1.
  return (
    <div>
      <NumberField
        data-testid="scenario-steps"
        aria-label={ariaLabel}
        className={className}
        style={INPUT_STYLE}
        integer
        min={1}
        required
        unit="steps"
        value={storedScenarioNumber(steps)}
        onCommit={(next) => onUpdate("step_count", next)}
        problemTestId="scenario-steps-error"
        problemClassName={PROBLEM_CLASS}
      />
    </div>
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
  const steps: unknown = config.step_count
  const stepCount = typeof steps === "number" && Number.isInteger(steps) && steps >= 1 ? steps : null
  const stepColumn = configField(config, "step_column", "")
  const parsedMin = storedScenarioNumber(minValue)
  const parsedMax = storedScenarioNumber(maxValue)

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
