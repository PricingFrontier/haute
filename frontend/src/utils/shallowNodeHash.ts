/**
 * Graph structural fingerprint helper — shallow hash over input-identity keys.
 *
 * The graph structuralVersion invalidates the preview cache.  Previously
 * the fingerprint was built by JSON.stringify()-ing every node's entire
 * ``data`` blob, which included result keys (_columns, _availableColumns,
 * _schemaWarnings, _status, and trace/hover flags).  Those keys are
 * downstream products of a preview — including them in the fingerprint
 * creates a feedback loop where every preview completion changes structuralVersion
 * and invalidates the cache it just filled.
 *
 * ``INPUT_KEYS`` is the minimal set of keys that — if all unchanged —
 * means the downstream preview work does not need to rerun.  Do NOT add
 * result-only keys here; do NOT remove input keys.
 */

const INPUT_KEYS = ["nodeType", "label", "description", "config", "code", "func_name"] as const
type InputKey = (typeof INPUT_KEYS)[number]

// Structural edits replace data/config objects; WeakMaps keep visual churn cheap
// without retaining old graph payloads after React releases them.
const objectInputHashCache = new WeakMap<object, string>()
const nodeDataHashCache = new WeakMap<Record<string, unknown>, string>()

function stringifyInputValue(key: InputKey, value: unknown): string {
  if (value === undefined) return ""
  if (value !== null && typeof value === "object") {
    const cached = objectInputHashCache.get(value)
    if (cached !== undefined) return cached

    const serialized = JSON.stringify(value)
    if (serialized === undefined) {
      throw new TypeError(`Cannot hash object-valued node input "${key}"`)
    }
    objectInputHashCache.set(value, serialized)
    return serialized
  }
  return String(value)
}

/**
 * Shallow hash of a node's data — only input-identity keys contribute.
 *
 * Primitive-valued keys (label, nodeType, code, func_name, description)
 * are String()-coerced; the ``config`` object (nested rules / code /
 * scoring parameters) is JSON.stringify()'d because its content genuinely
 * matters.  Result-only keys (_columns, _availableColumns, _schemaWarnings,
 * _status, _traceActive, _traceDimmed, _hoverDimmed, _traceValue,
 * _traceMotionDisabled) are ignored.
 *
 * Keys are joined with a non-empty delimiter (``\u0001``) to avoid
 * collisions between adjacent values like label="abc" + nodeType="def"
 * vs. label="ab" + nodeType="cdef".
 */
export function shallowNodeDataHash(data: Record<string, unknown>): string {
  const cached = nodeDataHashCache.get(data)
  if (cached !== undefined) return cached

  const parts: string[] = []
  for (const key of INPUT_KEYS) {
    parts.push(stringifyInputValue(key, data[key]))
  }
  const hash = parts.join("\u0001")
  nodeDataHashCache.set(data, hash)
  return hash
}
