/** Format a Unix timestamp (seconds) into a short locale time string (HH:MM). */
export function formatTime(ts: number): string {
  if (ts == null || Number.isNaN(ts)) return ""
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}

/**
 * Render a coarse relative-time label for a Unix timestamp (seconds), relative
 * to `now`. Thresholds chosen to stay readable at a glance:
 *   < 60s    -> "just now"
 *   < 60 min -> "{m} min ago"
 *   < 24 h   -> "{h} h ago"
 *   else     -> locale-formatted absolute timestamp
 */
export function formatRelativeTime(tsSeconds: number, now: Date): string {
  const tsMs = tsSeconds * 1000
  const diffSeconds = Math.floor((now.getTime() - tsMs) / 1000)
  if (diffSeconds < 60) return "just now"

  const diffMinutes = Math.floor(diffSeconds / 60)
  if (diffMinutes < 60) return `${diffMinutes} min ago`

  const diffHours = Math.floor(diffMinutes / 60)
  if (diffHours < 24) return `${diffHours} h ago`

  return new Date(tsMs).toLocaleString()
}
