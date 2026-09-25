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

import { exportConfigKeysFor } from "./modellingExportConfig"
import { authoredPolarsConfig, steppedSurfaceFor } from "./polarsStepInputs"

const INPUT_KEYS = ["nodeType", "label", "description", "config", "code", "func_name"] as const
type InputKey = (typeof INPUT_KEYS)[number]
const EXPLORE_NODE_TYPE = "explore"

// Structural edits replace data/config objects; WeakMaps keep visual churn cheap
// without retaining old graph payloads after React releases them.
const objectInputHashCache = new WeakMap<object, string>()
const exploreConfigInputHashCache = new WeakMap<object, string>()
const publishConfigInputHashCache = new WeakMap<object, string>()
const polarsConfigInputHashCache = new WeakMap<object, string>()
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

function stringifyExploreConfig(value: unknown): string {
  if (value === undefined) return ""
  if (value === null || typeof value !== "object") return String(value)

  const cached = exploreConfigInputHashCache.get(value)
  if (cached !== undefined) return cached

  const dataConfig = Array.isArray(value)
    ? value
    : (() => {
        const {
          overview: _overview,
          pivot_formulas: _pivotFormulas,
          pivots: _pivots,
          charts: _charts,
          ...rest
        } = value as Record<string, unknown>
        void _overview
        void _pivotFormulas
        void _pivots
        void _charts
        // Steps are authored; their generated body and validation result are caches.
        return authoredPolarsConfig(rest)
      })()
  const serialized = JSON.stringify(dataConfig)
  if (serialized === undefined) {
    throw new TypeError(`Cannot hash object-valued node input "config"`)
  }
  exploreConfigInputHashCache.set(value, serialized)
  return serialized
}

function stringifyConfigWithoutExportKeys(value: unknown, exportKeys: readonly string[]): string {
  if (value === undefined) return ""
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    return stringifyInputValue("config", value)
  }

  const cached = publishConfigInputHashCache.get(value)
  if (cached !== undefined) return cached

  const dataConfig = { ...(value as Record<string, unknown>) }
  for (const key of exportKeys) delete dataConfig[key]
  const serialized = JSON.stringify(dataConfig)
  if (serialized === undefined) {
    throw new TypeError(`Cannot hash object-valued node input "config"`)
  }
  publishConfigInputHashCache.set(value, serialized)
  return serialized
}

function stringifyNodeInputValue(data: Record<string, unknown>, key: InputKey): string {
  // Explore first: it is a stepped surface too, but its presentation fields
  // must stay out of the hash as well as its generated step caches.
  if (key === "config" && data.nodeType === EXPLORE_NODE_TYPE) {
    return stringifyExploreConfig(data[key])
  }
  if (key === "config" && steppedSurfaceFor(String(data.nodeType)) !== undefined) {
    const config = data.config
    if (config !== null && typeof config === "object" && !Array.isArray(config)) {
      const cached = polarsConfigInputHashCache.get(config)
      if (cached !== undefined) return cached
      const hash = stringifyInputValue(key, authoredPolarsConfig(config as Record<string, unknown>))
      polarsConfigInputHashCache.set(config, hash)
      return hash
    }
  }
  const exportKeys = key === "config" ? exportConfigKeysFor(data.nodeType) : []
  if (exportKeys.length > 0) {
    return stringifyConfigWithoutExportKeys(data[key], exportKeys)
  }
  return stringifyInputValue(key, data[key])
}

/**
 * Shallow hash of a node's data — only input-identity keys contribute.
 *
 * Primitive-valued keys (label, nodeType, code, func_name, description)
 * are String()-coerced; the ``config`` object (nested rules / code /
 * scoring parameters) is JSON.stringify()'d because its content genuinely
 * matters. Explore ``config.overview``, ``config.pivot_formulas``,
 * ``config.pivots``, and ``config.charts`` do not affect the materialised
 * dataframe and are ignored so changing pivot calculations or presentation
 * does not invalidate cached Explore data. Modelling and optimiser export settings
 * (``exportConfigKeysFor``: MLflow destination and experiment, model file or
 * result file path) are ignored for the same reason: they say where a result is
 * published, not what the pipeline computes or how the result is produced. Stepped
 * transforms hash their authored steps, excluding generated code and its
 * validation message; refreshing those caches cannot change execution.
 * Result-only keys (_columns, _availableColumns, _schemaWarnings,
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
    parts.push(stringifyNodeInputValue(data, key))
  }
  const hash = parts.join("\u0001")
  nodeDataHashCache.set(data, hash)
  return hash
}
