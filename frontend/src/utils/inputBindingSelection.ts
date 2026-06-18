import { sanitizeName } from "./sanitizeName"

/**
 * Pure model for the input binding selector — the input-domain twin of the
 * output `columnSelection.ts` (see `frontend/DESIGN_PRINCIPLES.md` §1.3). Each
 * incoming connection is one row: the "incoming name" is the upstream
 * node/var, the editable "binding name" is the local parameter name. The
 * binding name is backed by `GraphEdge.inputAlias` and round-trips through the
 * backend codegen/parser (it is NOT a frontend-only relabel) — wiring stays
 * positional via the connect() edges, so the alias only renames the emitted
 * parameter.
 *
 * Diverges from the column model deliberately: rows are keyed by an opaque
 * `edgeId` (two upstreams can sanitize to the same name), there is no
 * keep-all-natural collapse (an input is removed by deleting its edge, not by
 * unticking), and there is no dtype — inputs are whole frames.
 *
 * Framework-free so the derive/serialise rules are exhaustively unit-testable
 * without rendering.
 */

/** Minimal shape of an input source the model needs (satisfied by `InputSource`). */
export interface InputBindingSource {
  edgeId: string
  sourceNodeId: string
  sourceLabel: string
  /** Default binding name derived from the upstream label (the sanitized var). */
  varName: string
  /** User-chosen binding alias for this connection, if any. */
  inputAlias?: string | null
}

export interface InputBindingRow {
  /** Stable identity — the edge id. Used as React key, drag index, and rename key. */
  edgeId: string
  /** Upstream node id. */
  sourceNodeId: string
  /** Upstream node label — the source the binding maps FROM (read-only "From"). */
  sourceLabel: string
  /** 1-based argument position the input arrived in. */
  incomingOrder: number
  /** Default binding name derived from the upstream label (the rename maps FROM). */
  incomingName: string
  /** Effective binding (parameter) name: the user alias, else the incoming name. */
  bindingName: string
  /** True when the binding name has been overridden from its default. */
  aliased: boolean
}

/** Build the display rows from the current incoming connections. */
export function deriveInputRows(
  inputSources: readonly InputBindingSource[],
): InputBindingRow[] {
  return inputSources.map((src, index) => {
    const incomingName = src.varName
    const alias = src.inputAlias && src.inputAlias.trim() ? src.inputAlias : undefined
    const bindingName = alias ?? incomingName
    return {
      edgeId: src.edgeId,
      sourceNodeId: src.sourceNodeId,
      sourceLabel: src.sourceLabel,
      incomingOrder: index + 1,
      incomingName,
      bindingName,
      aliased: bindingName !== incomingName,
    }
  })
}

/**
 * Resolve a committed binding-name edit to the alias to persist, or `null`
 * when there is no override.
 *
 * A blank value, or one that sanitizes back to the default incoming name,
 * clears the alias. Otherwise the value is sanitised to a valid identifier so
 * the displayed binding name, the emitted parameter name, and the value that
 * survives a backend round-trip all agree (mirrors how `varName` is the
 * sanitized upstream label).
 */
export function resolveBindingAlias(rawValue: string, incomingName: string): string | null {
  const trimmed = rawValue.trim()
  if (!trimmed) return null
  const sanitized = sanitizeName(trimmed)
  if (!sanitized || sanitized === incomingName) return null
  return sanitized
}
