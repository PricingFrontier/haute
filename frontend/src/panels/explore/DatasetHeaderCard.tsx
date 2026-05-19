/**
 * Dataset header card for the Explore preview's Overview pane.
 *
 * Presents the cached dataset's row count, column count, source, and
 * cached-at relative time in a compact 4-cell stat grid. Purely
 * presentational — receives a validated `ExploreCacheReport` via props.
 */

import { Database } from "lucide-react"
import type { ExploreCacheReport } from "../../api/types"
import { NODE_GROUP_COLORS } from "../../theme/colors"

interface DatasetHeaderCardProps {
  report: ExploreCacheReport
}

/**
 * Render a coarse relative-time label (e.g. "5 min ago") for a unix
 * timestamp expressed in seconds, relative to `now`.
 *
 * Thresholds (chosen to keep the card readable at a glance):
 *   < 60s          -> "just now"
 *   < 60 min       -> "{m} min ago"
 *   < 24 h         -> "{h} h ago"
 *   otherwise      -> locale-formatted absolute timestamp
 */
function formatRelativeTime(generatedAtSeconds: number, now: Date): string {
  const generatedMs = generatedAtSeconds * 1000
  const diffMs = now.getTime() - generatedMs
  const diffSeconds = Math.floor(diffMs / 1000)

  if (diffSeconds < 60) return "just now"

  const diffMinutes = Math.floor(diffSeconds / 60)
  if (diffMinutes < 60) return `${diffMinutes} min ago`

  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours} h ago`

  return new Date(generatedMs).toLocaleString()
}

const STAT_LABEL_CLASS = "text-[10px] font-bold uppercase tracking-[0.08em]"
const STAT_LABEL_STYLE = { color: "var(--text-secondary)" } as const
const STAT_VALUE_CLASS = "text-base font-semibold"
const STAT_VALUE_STYLE = { color: "var(--text-primary)" } as const

export default function DatasetHeaderCard({ report }: DatasetHeaderCardProps) {
  const accent = NODE_GROUP_COLORS.explore
  const generatedDate = new Date(report.generated_at * 1000)
  const generatedIso = generatedDate.toISOString()
  const cachedRelative = formatRelativeTime(report.generated_at, new Date())

  return (
    <div
      data-testid="explore-dataset-header-card"
      className="rounded-lg p-3 space-y-3"
      style={{
        background: "var(--bg-elevated)",
        border: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-1.5">
        <Database size={14} className="shrink-0" style={{ color: accent }} />
        <span className="text-[11px] font-bold" style={{ color: accent }}>
          Dataset
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <div className={STAT_LABEL_CLASS} style={STAT_LABEL_STYLE}>Rows</div>
          <div className={`${STAT_VALUE_CLASS} mt-0.5`} style={STAT_VALUE_STYLE}>
            {report.row_count.toLocaleString()}
          </div>
        </div>
        <div>
          <div className={STAT_LABEL_CLASS} style={STAT_LABEL_STYLE}>Columns</div>
          <div className={`${STAT_VALUE_CLASS} mt-0.5`} style={STAT_VALUE_STYLE}>
            {report.column_count.toLocaleString()}
          </div>
        </div>
        <div>
          <div className={STAT_LABEL_CLASS} style={STAT_LABEL_STYLE}>Source</div>
          <div className={`${STAT_VALUE_CLASS} mt-0.5 font-mono`} style={STAT_VALUE_STYLE}>
            {report.source}
          </div>
        </div>
        <div data-testid="explore-dataset-header-cached" title={generatedIso}>
          <div className={STAT_LABEL_CLASS} style={STAT_LABEL_STYLE}>Cached</div>
          <div className={`${STAT_VALUE_CLASS} mt-0.5`} style={STAT_VALUE_STYLE}>
            {cachedRelative}
          </div>
        </div>
      </div>
    </div>
  )
}
