import type { BandingNodeDetail } from "../types/trace"
import { formatTraceValue } from "./traceFormatting"
import {
  TraceDetailAlert,
  TraceDetailChip,
  TraceDetailPanel,
  TraceDetailTable,
  TraceDetailTableRow,
} from "./TraceDetail"
import {
  bandingRowsForDisplay,
  formatBandingRange,
  formatBandingTransform,
} from "./bandingRows"

const formatValue = formatTraceValue

export function BandingDetailBlock({
  detail,
  tracedColumn,
  showBandingSummary = true,
}: {
  detail: BandingNodeDetail
  tracedColumn?: string | null
  showBandingSummary?: boolean
}) {
  const banding = detail
  const errorDetail = banding as unknown as Record<string, unknown>
  const error = errorDetail.error
  const errorType = errorDetail.error_type
  if (typeof error === "string") {
    return (
      <TraceDetailPanel title="Banding">
        <TraceDetailAlert>
          {typeof errorType === "string" ? `${errorType}: ` : ""}{error}
        </TraceDetailAlert>
      </TraceDetailPanel>
    )
  }
  const rows = bandingRowsForDisplay(banding, tracedColumn)
  const singleRow = rows.length === 1 ? rows[0] : null
  const rangeSummary = singleRow ? formatBandingRange(singleRow) : null
  const bandingGridClass = "grid grid-cols-[minmax(8rem,1fr)_minmax(8rem,1fr)_minmax(5rem,.65fr)_minmax(5rem,.65fr)] gap-1.5"
  return (
    <TraceDetailPanel
      title="Banding"
      summary={(
        <>
          {singleRow?.outputColumn && (
            <TraceDetailChip tone="accent">{singleRow.outputColumn}</TraceDetailChip>
          )}
          {showBandingSummary && singleRow && (
            <TraceDetailChip>{formatBandingTransform(singleRow)}</TraceDetailChip>
          )}
          {showBandingSummary && !singleRow && rows.length > 0 && (
            <TraceDetailChip>{rows.length} banded output{rows.length === 1 ? "" : "s"}</TraceDetailChip>
          )}
          {rangeSummary && <TraceDetailChip tone="muted">{rangeSummary}</TraceDetailChip>}
          {singleRow?.isDefault && <TraceDetailChip tone="warning" mono={false}>default</TraceDetailChip>}
        </>
      )}
    >
      {!singleRow && rows.length > 0 && (
        <TraceDetailTable
          ariaLabel="Banding outputs"
          gridClass={bandingGridClass}
          headers={["Output", "Source", "Band", "Rule"]}
        >
          {rows.map((row) => {
            const range = formatBandingRange(row)
            return (
              <TraceDetailTableRow key={row.key} gridClass={bandingGridClass}>
                <span style={{ overflowWrap: "anywhere", color: "var(--accent)" }}>
                  {row.outputColumn ?? ""}
                </span>
                <span className="text-center" style={{ overflowWrap: "anywhere", color: "var(--text-secondary)" }}>
                  {row.inputColumn ? `${row.inputColumn}=${formatValue(row.inputValue)}` : formatValue(row.inputValue)}
                </span>
                <span className="text-center" style={{ color: "var(--text-primary)" }}>
                  {formatValue(row.matchedBand)}
                </span>
                <span className="text-center" style={{ color: "var(--text-muted)" }}>
                  {row.isDefault ? "default" : range ?? row.status ?? ""}
                </span>
              </TraceDetailTableRow>
            )
          })}
        </TraceDetailTable>
      )}
    </TraceDetailPanel>
  )
}
