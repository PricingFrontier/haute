/**
 * Mint a stable browser-owned key from a human label.
 *
 * This is deliberately not an executable-language identifier. It uses
 * JavaScript trimming, has no reserved-word table, and is only suitable for
 * UI persistence keys and suggested filenames. Executable names and config
 * references must come from the editor identity API.
 */
export function portableKey(label: string): string {
  const encoded: string[] = []
  for (const character of label.trim()) {
    const codePoint = character.codePointAt(0) ?? 0
    if (character === " " || character === "-") {
      encoded.push("_")
    } else if (/^[A-Za-z0-9_]$/.test(character)) {
      encoded.push(character)
    } else if (codePoint >= 128) {
      encoded.push(`_u${codePoint.toString(16)}_`)
    }
  }
  let key = encoded.join("")
  if (/^[0-9]/.test(key)) key = `item_${key}`
  return key || "untitled"
}
