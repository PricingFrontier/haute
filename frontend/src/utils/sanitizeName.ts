/**
 * Convert a human label to a valid Python function name (preserves casing).
 *
 * This MUST stay in sync with the backend implementation:
 *   src/haute/_graph_utils.py → _sanitize_func_name()
 *
 * Both implementations follow the same rules:
 *   1. Trim whitespace.
 *   2. Replace spaces and hyphens with underscores.
 *   3. ASCII alnum / ``_`` survive; ASCII punctuation / control chars are
 *      stripped.
 *   4. Non-ASCII characters are reversibly encoded as ``_x<hex>_`` so
 *      distinct labels (e.g. ``café`` vs ``caf``) produce distinct
 *      identifiers.
 *   5. Prefix with "node_" if the result starts with a digit.
 *   6. Fall back to "unnamed_node" if the result is empty.
 */
export function sanitizeName(label: string): string {
  let name = label.trim().replace(/[\s-]/g, "_")
  const encoded: string[] = []
  for (const c of name) {
    const code = c.codePointAt(0) ?? 0
    if (code < 128) {
      // ASCII: keep alnum/underscore, drop everything else (punctuation,
      // control chars).  Matches the backend's ``c.isascii() and
      // (c.isalnum() or c == "_")`` filter.
      if (/^[a-zA-Z0-9_]$/.test(c)) {
        encoded.push(c)
      }
    } else {
      // Non-ASCII: reversibly encode as ``_x<hex>_``.
      encoded.push(`_x${code.toString(16)}_`)
    }
  }
  name = encoded.join("")
  if (name && /^[0-9]/.test(name)) name = `node_${name}`
  return name || "unnamed_node"
}
