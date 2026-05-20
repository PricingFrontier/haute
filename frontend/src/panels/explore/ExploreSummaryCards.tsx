import { AlertTriangle, ChevronDown, ChevronRight, Database, Hash, ListTree } from "lucide-react"
import { Fragment, useMemo, useState, type ReactNode } from "react"
import type { ExploreCacheReport, ExploreColumnStat } from "../../api/types"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { formatNullPct } from "../../utils/formatValue"
import { formatRelativeTime } from "../../utils/formatTime"
import { StatValueCell } from "./StatValueCell"

interface SummaryCardProps {
  report: ExploreCacheReport
}

const CARD_CLASS = "rounded-lg p-3 space-y-3"
const LABEL_CLASS = "text-[10px] font-bold uppercase tracking-[0.08em]"
const VALUE_CLASS = "text-base font-semibold"
const MUTED_STYLE = { color: "var(--text-muted)" } as const
const PRIMARY_STYLE = { color: "var(--text-primary)" } as const
const SECONDARY_STYLE = { color: "var(--text-secondary)" } as const
const ROW_BORDER_STYLE = { borderBottom: "1px solid var(--border)" } as const
const CELL_CLASS = "px-2 py-1.5"
const CATEGORICAL_TABLE_COLUMN_COUNT = 5

const CARD_STYLE = {
  background: "var(--bg-elevated)",
  border: "1px solid var(--border)",
} as const

function CardHeading({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="flex items-center gap-1.5">
      {icon}
      <span className="text-[11px] font-bold" style={{ color: NODE_GROUP_COLORS.explore }}>
        {title}
      </span>
    </div>
  )
}

function Metric({
  label,
  value,
  title,
}: {
  label: string
  value: ReactNode
  title?: string
}) {
  return (
    <div title={title} className="min-w-0">
      <div className={LABEL_CLASS} style={SECONDARY_STYLE}>
        {label}
      </div>
      <div className={`${VALUE_CLASS} mt-0.5 break-words`} style={PRIMARY_STYLE}>
        {value}
      </div>
    </div>
  )
}

function isNumericColumn(column: ExploreColumnStat): boolean {
  return column.kind === "Numeric"
}

function formatOptionalNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "-" : value.toLocaleString()
}

export function DatasetSnapshotCard({ report }: SummaryCardProps) {
  const generatedDate = new Date(report.generated_at * 1000)
  const generatedIso = generatedDate.toISOString()

  return (
    <div data-testid="explore-dataset-snapshot-card" className={CARD_CLASS} style={CARD_STYLE}>
      <CardHeading
        icon={<Database size={14} className="shrink-0" style={{ color: NODE_GROUP_COLORS.explore }} />}
        title="Dataset Snapshot"
      />
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Metric label="Rows" value={report.row_count.toLocaleString()} />
        <Metric
          label="Source"
          value={<span className="font-mono">{report.source}</span>}
          title={report.source}
        />
        <Metric
          label="Upstream"
          value={<span className="font-mono">{report.upstream_node_id}</span>}
          title={report.upstream_node_id}
        />
        <Metric
          label="Cached"
          value={formatRelativeTime(report.generated_at, new Date())}
          title={generatedIso}
        />
      </div>
    </div>
  )
}

export function DataQualityCard({ report }: SummaryCardProps) {
  const summary = report.overview_summary.data_quality

  return (
    <div data-testid="explore-data-quality-card" className={CARD_CLASS} style={CARD_STYLE}>
      <CardHeading
        icon={<AlertTriangle size={14} className="shrink-0" style={{ color: NODE_GROUP_COLORS.explore }} />}
        title="Data Quality"
      />
      {summary.issues.length === 0 ? (
        <div
          className="rounded-md px-2.5 py-2 text-[11px] leading-relaxed"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          No obvious missing, constant, negative, or mostly-zero fields.
        </div>
      ) : (
        <div className="space-y-2">
          {summary.issues.map((issue) => (
            <div
              key={`${issue.label}:${issue.detail}`}
              className="rounded-md px-2.5 py-2"
              style={{
                background: issue.severity === "danger" ? "var(--danger-soft-faint)" : "var(--bg-input)",
                border: `1px solid ${issue.severity === "danger" ? "var(--danger-border)" : "var(--border)"}`,
              }}
            >
              <div className="text-[11px] font-semibold" style={PRIMARY_STYLE}>
                {issue.label}
              </div>
              <div className="mt-0.5 text-[11px] leading-relaxed" style={MUTED_STYLE}>
                {issue.detail}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function NumericSummaryCard({ report }: SummaryCardProps) {
  const numericColumns = useMemo(() => report.columns.filter(isNumericColumn), [report.columns])
  const fieldCount = `${numericColumns.length.toLocaleString()} ${
    numericColumns.length === 1 ? "field" : "fields"
  }`

  return (
    <div data-testid="explore-numeric-summary-card" className={CARD_CLASS} style={CARD_STYLE}>
      <div className="flex items-center gap-1.5">
        <CardHeading
          icon={<Hash size={14} className="shrink-0" style={{ color: NODE_GROUP_COLORS.explore }} />}
          title="Numeric Summary"
        />
        <span className="text-[11px]" style={MUTED_STYLE}>
          {fieldCount}
        </span>
      </div>
      {numericColumns.length === 0 ? (
        <div
          className="rounded-md px-2.5 py-2 text-[11px] leading-relaxed"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          No numeric fields in this dataset.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]" aria-label="Numeric field statistics">
            <thead>
              <tr>
                {[
                  "Field",
                  "Type",
                  "Null %",
                  "Distinct",
                  "Min",
                  "P25",
                  "Median",
                  "Mean",
                  "P75",
                  "Max",
                  "Std",
                  "Zeros",
                  "Negatives",
                ].map((label) => (
                  <th
                    key={label}
                    className="text-[10px] font-bold uppercase tracking-[0.08em] text-left px-2 py-1.5"
                    style={SECONDARY_STYLE}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {numericColumns.map((column) => (
                <tr key={column.name} data-testid="explore-numeric-summary-row" style={ROW_BORDER_STYLE}>
                  <td className={`${CELL_CLASS} font-mono max-w-[24ch] truncate`} style={PRIMARY_STYLE} title={column.name}>
                    {column.name}
                  </td>
                  <td className={`${CELL_CLASS} font-mono`} style={SECONDARY_STYLE}>
                    {column.dtype}
                  </td>
                  <td className={CELL_CLASS} style={column.null_count === 0 ? MUTED_STYLE : PRIMARY_STYLE}>
                    {formatNullPct(column.null_count, report.row_count) ?? "-"}
                  </td>
                  <td className={CELL_CLASS} style={column.distinct_count === null ? MUTED_STYLE : PRIMARY_STYLE}>
                    {formatOptionalNumber(column.distinct_count)}
                  </td>
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.min_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.p25_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.median_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.mean_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.p75_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.max_value} />
                  <StatValueCell maxWidthClass="max-w-[16ch]" value={column.std_value} />
                  <td className={CELL_CLASS} style={column.zero_count ? PRIMARY_STYLE : MUTED_STYLE}>
                    {formatOptionalNumber(column.zero_count)}
                  </td>
                  <td className={CELL_CLASS} style={column.negative_count ? PRIMARY_STYLE : MUTED_STYLE}>
                    {formatOptionalNumber(column.negative_count)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function CategoricalSummaryCard({ report }: SummaryCardProps) {
  const [expandedFields, setExpandedFields] = useState<Set<string>>(() => new Set())
  const profiles = report.overview_summary.categorical_summary
  const columnByName = useMemo(
    () => new Map(report.columns.map((column) => [column.name, column])),
    [report.columns],
  )
  const fieldCount = `${profiles.length.toLocaleString()} ${profiles.length === 1 ? "field" : "fields"}`
  const toggleField = (field: string) => {
    setExpandedFields((current) => {
      const next = new Set(current)
      if (next.has(field)) {
        next.delete(field)
      } else {
        next.add(field)
      }
      return next
    })
  }

  return (
    <div data-testid="explore-categorical-summary-card" className={CARD_CLASS} style={CARD_STYLE}>
      <div className="flex items-center gap-1.5">
        <CardHeading
          icon={<ListTree size={14} className="shrink-0" style={{ color: NODE_GROUP_COLORS.explore }} />}
          title="Categorical Summary"
        />
        <span className="text-[11px]" style={MUTED_STYLE}>
          {fieldCount}
        </span>
      </div>
      {profiles.length === 0 ? (
        <div
          className="rounded-md px-2.5 py-2 text-[11px] leading-relaxed"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        >
          No non-numeric fields in this dataset.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]" aria-label="Categorical field distinct values">
            <thead>
              <tr>
                {["", "Field", "Type", "Null %", "Distinct"].map((label) => (
                  <th
                    key={label || "expand"}
                    scope={label ? "col" : undefined}
                    aria-hidden={label ? undefined : true}
                    className="text-[10px] font-bold uppercase tracking-[0.08em] text-left px-2 py-1.5"
                    style={SECONDARY_STYLE}
                  >
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile, index) => {
                const column = columnByName.get(profile.field)
                const isExpanded = expandedFields.has(profile.field)
                const detailId = `explore-categorical-values-${index}`
                return (
                  <Fragment key={profile.field}>
                    <tr data-testid="explore-categorical-summary-row" style={ROW_BORDER_STYLE}>
                      <td className="w-8 px-1 py-1.5">
                        {profile.expandable ? (
                          <button
                            type="button"
                            aria-label={`${isExpanded ? "Collapse" : "Expand"} ${profile.field}`}
                            aria-expanded={isExpanded}
                            aria-controls={detailId}
                            className="focus-ring inline-flex h-6 w-6 items-center justify-center rounded"
                            style={{ color: NODE_GROUP_COLORS.explore }}
                            onClick={() => toggleField(profile.field)}
                          >
                            {isExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                          </button>
                        ) : null}
                      </td>
                      <td className={`${CELL_CLASS} font-mono max-w-[24ch] truncate`} style={PRIMARY_STYLE} title={profile.field}>
                        {profile.field}
                      </td>
                      <td className={`${CELL_CLASS} font-mono`} style={column ? SECONDARY_STYLE : MUTED_STYLE}>
                        {column?.dtype ?? "-"}
                      </td>
                      <td className={CELL_CLASS} style={column && column.null_count > 0 ? PRIMARY_STYLE : MUTED_STYLE}>
                        {column ? (formatNullPct(column.null_count, report.row_count) ?? "-") : "-"}
                      </td>
                      <td
                        className={CELL_CLASS}
                        style={profile.distinct_count === null ? MUTED_STYLE : PRIMARY_STYLE}
                        title={profile.values_truncated ? "Expanded details are limited to the top 50 groups" : undefined}
                      >
                        {formatOptionalNumber(profile.distinct_count)}
                      </td>
                    </tr>
                    {profile.expandable && isExpanded ? (
                      <tr id={detailId}>
                        <td
                          colSpan={CATEGORICAL_TABLE_COLUMN_COUNT}
                          className="px-2 pb-2"
                          data-testid="explore-categorical-values-detail"
                        >
                          <div
                            className="rounded-md px-2.5 py-2"
                            style={{
                              background: "var(--bg-input)",
                              border: "1px solid var(--border)",
                            }}
                          >
                            <div className="mb-1.5 flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-[0.08em]">
                              <span style={SECONDARY_STYLE}>
                                {profile.values_truncated ? "Top 50 groups" : "Top values"}
                              </span>
                              <span className="font-mono normal-case tracking-normal" style={MUTED_STYLE}>
                                {profile.field}
                              </span>
                            </div>
                            <div
                              role="list"
                              aria-label={`${profile.field} value counts`}
                              className="flex flex-wrap gap-1.5"
                            >
                              {profile.values.map((item) => (
                                <span
                                  key={`${profile.field}:${item.value ?? "__null__"}`}
                                  role="listitem"
                                  aria-label={`${item.value ?? "Missing"}, count ${item.count.toLocaleString()}`}
                                  className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-mono"
                                  style={{
                                    background: "var(--bg-elevated)",
                                    border: "1px solid var(--border)",
                                    color: "var(--text-primary)",
                                  }}
                                >
                                  <span className="max-w-[18ch] truncate" title={item.value ?? "Missing"}>
                                    {item.value ?? "Missing"}
                                  </span>
                                  <span style={MUTED_STYLE}>{item.count.toLocaleString()}</span>
                                </span>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
