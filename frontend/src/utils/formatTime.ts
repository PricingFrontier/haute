/** Format a Unix timestamp (seconds) into a short locale time string (HH:MM). */
export function formatTime(ts: number): string {
  if (ts == null || Number.isNaN(ts)) return ""
  const d = new Date(ts * 1000)
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
}
