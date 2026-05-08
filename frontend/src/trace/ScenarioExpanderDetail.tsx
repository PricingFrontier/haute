import type { ScenarioExpanderNodeDetail } from "../types/trace"
import { formatValue as _formatValue } from "../utils/formatValue"
import {
  TraceDetailAlert,
  TraceDetailChip,
  TraceDetailPanel,
} from "./TraceDetail"

const formatValue = (v: unknown) => _formatValue(v, 2)

export function ScenarioExpanderDetailBlock({ detail }: {
  detail: ScenarioExpanderNodeDetail
}) {
  const expander = detail
  const scenarioColumn = expander.scenario_column ??
    (typeof expander.step === "string" && expander.step.length > 0 ? expander.step : "scenario")
  const scenarioValue = expander.scenario_value ?? expander.multiplier
  const scenarioIndex = expander.scenario_index
  const minValue = expander.parameters?.min_value ?? expander.range?.min
  const maxValue = expander.parameters?.max_value ?? expander.range?.max
  const stepCount = expander.parameters?.steps
  const hasGridSettings = minValue !== undefined || maxValue !== undefined || stepCount !== undefined
  return (
    <TraceDetailPanel
      title="Scenario Expander"
      summary={(
        <>
        {scenarioValue !== undefined && (
          <TraceDetailChip>{scenarioColumn}: {formatValue(scenarioValue)}</TraceDetailChip>
        )}
        {scenarioIndex !== undefined && (
          <TraceDetailChip tone="muted">index: {formatValue(scenarioIndex)}</TraceDetailChip>
        )}
        </>
      )}
    >
      {hasGridSettings && (
        <div className="flex flex-wrap gap-1">
          {minValue !== undefined && (
            <TraceDetailChip tone="muted">min: {formatValue(minValue)}</TraceDetailChip>
          )}
          {maxValue !== undefined && (
            <TraceDetailChip tone="muted">max: {formatValue(maxValue)}</TraceDetailChip>
          )}
          {stepCount !== undefined && (
            <TraceDetailChip tone="muted">steps: {formatValue(stepCount)}</TraceDetailChip>
          )}
        </div>
      )}
      {expander.error && (
        <TraceDetailAlert>
          Trace detail failed: {expander.error}
        </TraceDetailAlert>
      )}
    </TraceDetailPanel>
  )
}
