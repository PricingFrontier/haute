import type { ScenarioExpanderNodeDetail } from "../types/trace"
import { formatTraceValue } from "./traceFormatting"
import {
  TraceDetailAlert,
  TraceDetailChip,
  TraceDetailPanel,
} from "./TraceDetail"

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
          <TraceDetailChip>{scenarioColumn}: {formatTraceValue(scenarioValue)}</TraceDetailChip>
        )}
        {scenarioIndex !== undefined && (
          <TraceDetailChip tone="muted">index: {formatTraceValue(scenarioIndex)}</TraceDetailChip>
        )}
        </>
      )}
    >
      {hasGridSettings && (
        <div className="flex flex-wrap gap-1">
          {minValue !== undefined && (
            <TraceDetailChip tone="muted">min: {formatTraceValue(minValue)}</TraceDetailChip>
          )}
          {maxValue !== undefined && (
            <TraceDetailChip tone="muted">max: {formatTraceValue(maxValue)}</TraceDetailChip>
          )}
          {stepCount !== undefined && (
            <TraceDetailChip tone="muted">steps: {formatTraceValue(stepCount)}</TraceDetailChip>
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
