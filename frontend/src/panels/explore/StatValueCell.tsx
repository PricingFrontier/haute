/**
 * Shared table cell for an optional, monospaced stat value (min/max, profile
 * aggregates). Renders a muted placeholder when the value is null/undefined and
 * truncates long values with the full value available on hover. Used by both
 * the Schema and Numeric Summary cards so their value cells stay consistent.
 */

const CELL_CLASS = "px-2 py-1.5"
const MUTED_STYLE = { color: "var(--text-muted)" } as const
const PRIMARY_STYLE = { color: "var(--text-primary)" } as const

export function StatValueCell({
  value,
  testId,
  maxWidthClass = "max-w-[18ch]",
}: {
  value: string | null | undefined
  testId?: string
  maxWidthClass?: string
}) {
  const isEmpty = value === null || value === undefined
  return (
    <td
      data-testid={testId}
      className={`${CELL_CLASS} font-mono ${maxWidthClass} truncate`}
      style={isEmpty ? MUTED_STYLE : PRIMARY_STYLE}
      title={value ?? undefined}
    >
      {value ?? "-"}
    </td>
  )
}
