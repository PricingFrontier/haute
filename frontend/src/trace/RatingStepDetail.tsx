import type { TraceNodeDetail } from "../types/trace"
import { formatTraceValue } from "./traceFormatting"
import {
  TraceDetailChip,
  TraceDetailPanel,
  TraceDetailSection,
} from "./TraceDetail"
import {
  asRatingStepCombinedOutputs,
  asRatingStepTables,
  formatRatingStatus,
  ratingTableStatus,
} from "./ratingStepHelpers"

const formatValue = formatTraceValue

const traceDetailValueStyle = {
  color: "var(--text-secondary)",
  fontSize: 11,
  fontFamily: "var(--font-mono, monospace)",
}

export function RatingStepDetailBlock({
  detail,
  tracedColumn,
}: {
  detail: TraceNodeDetail
  tracedColumn?: string | null
}) {
  const valueStyle = traceDetailValueStyle
  const tables = asRatingStepTables(detail)
  const combinedOutputs = asRatingStepCombinedOutputs(detail)

  return (
    <TraceDetailPanel title="Rating Step">
      {tables.length > 0 && (
        <TraceDetailSection title="Rating Tables">
          {tables.map((table, tableIndex) => {
            const title = table.name || table.output_column || `table ${tableIndex + 1}`
            const status = ratingTableStatus(table)
            const isTracedTable = tracedColumn != null &&
              (table.output_column === tracedColumn || table.name === tracedColumn)
            return (
              <div
                key={`${title}-${tableIndex}`}
                className="space-y-1 py-1.5"
                style={{
                  borderTop: tableIndex === 0 ? "none" : "1px solid var(--border)",
                  borderLeft: isTracedTable ? "2px solid var(--accent)" : "2px solid transparent",
                  paddingLeft: isTracedTable ? 8 : 0,
                }}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                    {title}
                  </span>
                  {isTracedTable && (
                    <TraceDetailChip tone="accent" mono={false}>traced column</TraceDetailChip>
                  )}
                  {status && (
                    <TraceDetailChip
                      tone={status === "matched" ? "success" : status === "default" ? "warning" : "danger"}
                      mono={false}
                    >
                      status: {formatRatingStatus(status)}
                    </TraceDetailChip>
                  )}
                  {table.selected_value !== undefined && (
                    <TraceDetailChip tone="accent">selected: {formatValue(table.selected_value)}</TraceDetailChip>
                  )}
                  {table.post_code_output_value !== undefined && (
                    <TraceDetailChip tone="warning">
                      after node code: {formatValue(table.post_code_output_value)}
                    </TraceDetailChip>
                  )}
                  {table.default_value !== undefined && (
                    <TraceDetailChip tone="muted">default: {formatValue(table.default_value)}</TraceDetailChip>
                  )}
                  {table.default_used && (
                    <TraceDetailChip tone="warning" mono={false}>default used</TraceDetailChip>
                  )}
                </div>
                {table.factors && table.factors.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {table.factors.map((factor) => (
                      <TraceDetailChip key={`${factor.column}-${String(factor.value)}`}>
                        {factor.column}: {formatValue(factor.value)}
                      </TraceDetailChip>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </TraceDetailSection>
      )}

      {combinedOutputs.length > 0 && (
        <TraceDetailSection title="Combined Outputs">
          {combinedOutputs.map((combined) => {
            const isTracedCombined = tracedColumn != null && combined.column === tracedColumn
            return (
              <div
                key={combined.column}
                className="space-y-1 py-1.5"
                style={{
                  borderTop: "1px solid var(--border)",
                  borderLeft: isTracedCombined ? "2px solid var(--accent)" : "2px solid transparent",
                  paddingLeft: isTracedCombined ? 8 : 0,
                }}
              >
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-mono font-semibold" style={{ color: "var(--text-primary)" }}>
                    {combined.column} = {formatValue(combined.value)}
                  </span>
                  {isTracedCombined && (
                    <TraceDetailChip tone="accent" mono={false}>traced column</TraceDetailChip>
                  )}
                </div>
                <div style={valueStyle}>
                  {combined.operation} from base {formatValue(combined.base_value)}
                </div>
                {Object.keys(combined.input_values).length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {Object.entries(combined.input_values).map(([column, value]) => (
                      <TraceDetailChip key={column}>{column}: {formatValue(value)}</TraceDetailChip>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </TraceDetailSection>
      )}
    </TraceDetailPanel>
  )
}
