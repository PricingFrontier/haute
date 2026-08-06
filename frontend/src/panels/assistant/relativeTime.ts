/** Render a conversation's last-used time the way a chat list reads it. */
export function relativeTime(seconds: number, now: number = Date.now()): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return ""
  const elapsed = Math.max(0, now - seconds * 1000)
  const minutes = Math.floor(elapsed / 60_000)
  if (minutes < 1) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  return new Date(seconds * 1000).toLocaleDateString()
}
