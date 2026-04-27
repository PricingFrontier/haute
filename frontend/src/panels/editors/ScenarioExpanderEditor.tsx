import { InputSourcesBar, INPUT_STYLE } from "./_shared"
import type { InputSource, OnUpdateConfig } from "./_shared"
import { CodeEditor } from "./CodeEditor"
import { configField } from "../../utils/configField"

export default function ScenarioExpanderEditor({
  config,
  onUpdate,
  inputSources,
  onDeleteInput,
  upstreamColumns,
  errorLine,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  inputSources: InputSource[]
  onDeleteInput?: (edgeId: string) => void
  upstreamColumns: { name: string; dtype: string }[]
  errorLine?: number | null
}) {
  const quoteId = configField(config, "quote_id", "")
  const columnName = configField(config, "column_name", "")
  const minValue = configField(config, "min_value", "")
  const maxValue = configField(config, "max_value", "")
  const steps = configField(config, "steps", "")
  const stepColumn = configField(config, "step_column", "")

  return (
    <>
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
            {upstreamColumns.map((c) => (
              <option key={c.name} value={c.name}>{c.name}</option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
            style={INPUT_STYLE}
            value={quoteId}
            onChange={(e) => onUpdate("quote_id", e.target.value)}
          />
        )}
      </div>

      {/* Index column name */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Index Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">0-based step index column</span>
        </label>
        <input
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={stepColumn}
          onChange={(e) => onUpdate("step_column", e.target.value)}
        />
      </div>

      {/* Steps — shown standalone when no value column is set */}
      {!columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Steps
            <span className="ml-1.5 normal-case tracking-normal font-normal">rows generated per input row</span>
          </label>
          <input
            type="number"
            min={1}
            className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
            style={INPUT_STYLE}
            value={steps}
            onChange={(e) => onUpdate("steps", Math.max(1, parseInt(e.target.value) || 1))}
          />
        </div>
      )}

      {/* Value column (optional) */}
      <div>
        <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
          Value Column
          <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
        </label>
        <input
          type="text"
          className="w-full px-2.5 py-1.5 rounded-md text-[12px] font-mono"
          style={INPUT_STYLE}
          value={columnName}
          onChange={(e) => onUpdate("column_name", e.target.value)}
        />
      </div>

      {/* Value range — only shown when value column is set */}
      {columnName && (
        <div>
          <label className="text-[11px] font-bold uppercase tracking-[0.08em] block mb-1.5" style={{ color: 'var(--text-muted)' }}>
            Value Range
          </label>
          {minValue !== "" && maxValue !== "" && Number(minValue) >= Number(maxValue) && (
            <div className="mb-1.5 px-2 py-1 rounded text-[10px]" style={{ background: 'var(--warning-soft-strong)', color: 'var(--warning-strong)', border: '1px solid var(--warning-border)' }}>
              Warning: min value should be less than max value
            </div>
          )}
          <div className="grid grid-cols-4 gap-2">
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Min</label>
              <input
                type="number"
                step="any"
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={INPUT_STYLE}
                value={minValue}
                onChange={(e) => onUpdate("min_value", parseFloat(e.target.value) || 0)}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Max</label>
              <input
                type="number"
                step="any"
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={INPUT_STYLE}
                value={maxValue}
                onChange={(e) => onUpdate("max_value", parseFloat(e.target.value) || 0)}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Steps</label>
              <input
                type="number"
                min={1}
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={INPUT_STYLE}
                value={steps}
                onChange={(e) => onUpdate("steps", Math.max(1, parseInt(e.target.value) || 1))}
              />
            </div>
            <div>
              <label className="text-[10px] block mb-0.5" style={{ color: 'var(--text-muted)' }}>Step Size</label>
              <div
                className="w-full px-2 py-1.5 rounded-md text-[12px] font-mono"
                style={{ ...INPUT_STYLE, opacity: 0.7 }}
                data-testid="step-size"
              >
                {steps && Number(steps) > 1 && minValue !== "" && maxValue !== "" ? +((Number(maxValue) - Number(minValue)) / Math.max(Number(steps) - 1, 1)).toFixed(4) : "—"}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>

    <div className="px-3 py-2 flex flex-col gap-2" style={{ borderTop: '1px solid var(--border)' }}>
      <div className="flex items-center justify-between shrink-0">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>
          Polars Code
          <span className="ml-1.5 normal-case tracking-normal font-normal">(optional)</span>
        </label>
        <span className="text-[11px] font-medium" style={{ color: 'var(--text-muted)' }}>
          use <code className="px-0.5 rounded" style={{ background: 'var(--bg-hover)' }}>df</code> for expanded data
        </span>
      </div>
      <CodeEditor
        defaultValue={configField(config, "code", "")}
        onChange={(val) => onUpdate("code", val)}
        errorLine={errorLine}
        placeholder=""
      />
    </div>
    </>
  )
}
