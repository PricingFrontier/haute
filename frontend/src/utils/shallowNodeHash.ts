/**
 * Graph structural fingerprint helper — shallow hash over input-identity keys.
 *
 * The App-level graph-version bump invalidates the preview cache.  Previously
 * the fingerprint was built by JSON.stringify()-ing every node's entire
 * ``data`` blob, which included result keys (_columns, _availableColumns,
 * _schemaWarnings, _status, and trace/hover flags).  Those keys are
 * downstream products of a preview — including them in the fingerprint
 * creates a feedback loop where every preview completion bumps graphVersion
 * and invalidates the cache it just filled.
 *
 * ``INPUT_KEYS`` is the minimal set of keys that — if all unchanged —
 * means the downstream preview work does not need to rerun.  Do NOT add
 * result-only keys here; do NOT remove input keys.
 */

const INPUT_KEYS = ["nodeType", "label", "description", "config", "code", "func_name"] as const

/**
 * Shallow hash of a node's data — only input-identity keys contribute.
 *
 * Primitive-valued keys (label, nodeType, code, func_name, description)
 * are String()-coerced; the ``config`` object (nested rules / code /
 * scoring parameters) is JSON.stringify()'d because its content genuinely
 * matters.  Result-only keys (_columns, _availableColumns, _schemaWarnings,
 * _status, _traceActive, _traceDimmed, _hoverDimmed, _traceValue) are
 * ignored.
 *
 * Keys are joined with a non-empty delimiter (``\u0001``) to avoid
 * collisions between adjacent values like label="abc" + nodeType="def"
 * vs. label="ab" + nodeType="cdef".
 */
export function shallowNodeDataHash(data: Record<string, unknown>): string {
  const parts: string[] = []
  for (const key of INPUT_KEYS) {
    const v = data[key]
    if (v === undefined) {
      parts.push("")
      continue
    }
    parts.push(typeof v === "object" ? JSON.stringify(v) : String(v))
  }
  return parts.join("\u0001")
}
