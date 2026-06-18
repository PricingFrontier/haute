/**
 * Produce a translucent CSS colour from a base colour and an alpha value.
 *
 * For hex input (3- or 6-digit, with or without `#`) it returns an exact
 * `rgba(r,g,b,alpha)` string. For any other colour form — notably a CSS custom
 * property like `var(--node-group-model)`, which can't be parsed to rgb at JS
 * time — it falls back to `color-mix(in srgb, <colour> <alpha%>, transparent)`,
 * which the browser resolves to the same translucent colour. This lets themed
 * `var(...)` accents flow through the same call sites that take hex.
 *
 * @example
 *   withAlpha("f97316", 0.1)            // "rgba(249,115,22,0.1)"
 *   withAlpha("14b8a6", 0.3)            // "rgba(20,184,166,0.3)"
 *   withAlpha("var(--accent)", 0.3)     // "color-mix(in srgb, var(--accent) 30%, transparent)"
 */
export function withAlpha(color: string, alpha: number): string {
  const h = color.replace("#", "")
  const isHex = /^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$/.test(h)
  if (!isHex) {
    // Non-hex (CSS var, named colour, …): defer the blend to the browser.
    const pct = +(alpha * 100).toFixed(4)
    return `color-mix(in srgb, ${color} ${pct}%, transparent)`
  }
  const full = h.length === 3
    ? h[0] + h[0] + h[1] + h[1] + h[2] + h[2]
    : h
  const r = parseInt(full.slice(0, 2), 16)
  const g = parseInt(full.slice(2, 4), 16)
  const b = parseInt(full.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}
