/**
 * Produce an `rgba(r, g, b, alpha)` string from a hex color and an alpha value.
 *
 * Accepts 3- or 6-digit hex (with or without `#`).
 *
 * @example
 *   withAlpha("f97316", 0.1)  // "rgba(249,115,22,0.1)"
 *   withAlpha("14b8a6", 0.3)  // "rgba(20,184,166,0.3)"
 */
export function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace("#", "")
  const full = h.length === 3
    ? h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
    : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}
