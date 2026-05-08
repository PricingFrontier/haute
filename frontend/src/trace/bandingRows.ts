import type {
  BandingFactorDetail,
  BandingNodeDetail,
  TraceNodeDetail,
} from "../types/trace"
import { formatValue as _formatValue } from "../utils/formatValue"

const formatValue = (v: unknown) => _formatValue(v, 2)

export interface BandingTraceRow {
  key: string
  inputColumn?: string
  outputColumn?: string
  inputValue?: unknown
  matchedBand?: unknown
  lowerBound?: unknown
  upperBound?: unknown
  lowerInclusive?: boolean | null
  upperInclusive?: boolean | null
  isDefault?: boolean
  status?: string
}

export function asBandingDetail(detail: TraceNodeDetail): BandingNodeDetail {
  return detail as BandingNodeDetail
}

export function bandingRowFromFactor(factor: BandingFactorDetail, index: number): BandingTraceRow {
  const inputColumn = factor.input_column ?? factor.column
  const outputColumn = factor.output_column
  const matchedBand = factor.matched_band ?? factor.selected_band
  return {
    key: `${outputColumn ?? "output"}-${inputColumn ?? "input"}-${index}`,
    inputColumn,
    outputColumn,
    inputValue: factor.input_value,
    matchedBand,
    lowerBound: factor.lower_bound,
    upperBound: factor.upper_bound,
    lowerInclusive: factor.lower_inclusive,
    upperInclusive: factor.upper_inclusive,
    isDefault: factor.is_default,
    status: factor.status,
  }
}

export function bandingRowFromDetail(detail: BandingNodeDetail): BandingTraceRow | null {
  const inputColumn = detail.input_column ?? detail.column
  const outputColumn = detail.output_column
  const matchedBand = detail.matched_band ?? detail.selected_band
  if (
    inputColumn == null &&
    outputColumn == null &&
    detail.input_value === undefined &&
    matchedBand === undefined
  ) {
    return null
  }

  return {
    key: `${outputColumn ?? "output"}-${inputColumn ?? "input"}-summary`,
    inputColumn,
    outputColumn,
    inputValue: detail.input_value,
    matchedBand,
    lowerBound: detail.lower_bound,
    upperBound: detail.upper_bound,
    lowerInclusive: detail.lower_inclusive,
    upperInclusive: detail.upper_inclusive,
    isDefault: detail.is_default,
    status: detail.status,
  }
}

export function bandingRows(detail: BandingNodeDetail): BandingTraceRow[] {
  const rows = Array.isArray(detail.factors)
    ? detail.factors.map((factor, index) => bandingRowFromFactor(factor, index))
    : []
  const summaryRow = bandingRowFromDetail(detail)
  if (!summaryRow) return rows
  if (rows.some((row) => row.outputColumn === summaryRow.outputColumn && row.inputColumn === summaryRow.inputColumn)) {
    return rows
  }
  return [summaryRow, ...rows]
}

export function bandingRowsForDisplay(detail: BandingNodeDetail, tracedColumn?: string | null): BandingTraceRow[] {
  const rows = bandingRows(detail)
  if (!tracedColumn) return rows
  const tracedRow = rows.find((row) => row.outputColumn === tracedColumn)
  return tracedRow ? [tracedRow] : rows
}

export function hasRenderableBandingRows(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" && bandingRows(asBandingDetail(detail)).length > 0
}

export function formatBandingTransform(row: BandingTraceRow): string {
  const source = row.inputColumn
    ? `${row.inputColumn}=${formatValue(row.inputValue)}`
    : formatValue(row.inputValue)
  return `${source} -> ${formatValue(row.matchedBand)}`
}

export function formatBandingRange(row: BandingTraceRow): string | null {
  if (row.lowerBound == null && row.upperBound == null) return null
  const lower = row.lowerBound != null ? formatValue(row.lowerBound) : ""
  const upper = row.upperBound != null ? formatValue(row.upperBound) : ""
  const lowerBracket = row.lowerInclusive === false ? "(" : "["
  const upperBracket = row.upperInclusive === false ? ")" : "]"
  return `${lowerBracket}${lower}, ${upper}${upperBracket}`
}

export function hasBandingSecondaryDetail(detail: TraceNodeDetail | null | undefined): boolean {
  return detail?.detail_type === "banding" &&
    (detail.lower_bound != null || detail.upper_bound != null || detail.is_default === true)
}
