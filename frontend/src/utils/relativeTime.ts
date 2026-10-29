/**
 * How long ago `when` was, by the one rule every panel uses (specs/frontend-shared): "just
 * now" below a minute, a time ahead of `now` included; then whole minutes, hours and days
 * ago ("5m ago", "3h ago", "2d ago") below seven days; then the day and short month, with
 * the year when it is not `now`'s. An invalid date reads as nothing. Callers convert their
 * ISO string or Unix seconds to a `Date`, so one instant reads the same wherever it shows.
 */
export function relativeTime(when: Date, now: Date = new Date()): string {
  if (Number.isNaN(when.getTime())) return ""
  const minutes = Math.floor((now.getTime() - when.getTime()) / 60_000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return when.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: when.getFullYear() === now.getFullYear() ? undefined : "numeric",
  })
}
