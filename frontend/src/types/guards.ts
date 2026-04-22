/**
 * Runtime type guards for values that cross the JSON / DOM boundary.
 *
 * These replace ad-hoc `as Record<string, unknown>` and `as Node` casts at
 * call sites with parse-time narrowing that fails loudly when the backend
 * contract drifts or when a raw object literal does not satisfy the
 * ReactFlow shape.  Centralising the guards here keeps the narrowing
 * logic testable in isolation and lets consumers narrow once at the
 * ingestion point rather than scattering casts downstream.
 *
 * Audit context (Phase 5 Wave 10C):
 *   - #116 — `usePipelineAPI.ts` previously punched through `node.data`
 *            with `as Record<string, unknown>` casts.  Those casts
 *            disappear once the top-level API response has been narrowed
 *            once via `parsePipelineResponse`.
 *   - #117 — `useSubmodelNavigation.ts` constructed port-node literals
 *            with `as Node` casts.  `validateReactFlowNode` narrows them
 *            at construction time.
 */

import type { Edge, Node } from "@xyflow/react"

// ---------------------------------------------------------------------------
// Pipeline response shape — the value returned by `loadPipeline()`.
// ---------------------------------------------------------------------------

/**
 * The narrowed shape of the JSON returned by the pipeline endpoints.
 *
 * `nodes` and `edges` are required; everything else is optional because
 * the backend may omit fields for fresh / empty pipelines.  The element
 * types are intentionally unknown arrays — further narrowing (e.g.
 * ReactFlow Node validation) happens at a lower layer via
 * `validateReactFlowNode`.
 */
export interface PipelineResponse {
  nodes: Node[]
  edges: Edge[]
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  source_file?: string | null
  submodels?: Record<string, unknown> | null
  warning?: string | null
  sources?: string[]
  active_source?: string
}

/** Narrow helper — any non-null object (but not an array). */
function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v)
}

function typeName(v: unknown): string {
  if (v === null) return "null"
  if (Array.isArray(v)) return "array"
  return typeof v
}

function isOptionalNullableString(v: unknown): v is string | null | undefined {
  return v === undefined || v === null || typeof v === "string"
}

function assertOptionalNullableString(
  value: unknown,
  field: string,
): asserts value is string | null | undefined {
  if (!isOptionalNullableString(value)) {
    throw new Error(
      `parsePipelineResponse: expected field \`${field}\` to be a string or null, got ${typeName(value)}`,
    )
  }
}

/**
 * Predicate form: returns true iff `v` matches the pipeline response
 * contract.  Never throws — use `parsePipelineResponse` for the throwing
 * variant that names the offending field.
 */
export function isPipelineResponse(v: unknown): v is PipelineResponse {
  if (!isPlainObject(v)) return false
  if (!Array.isArray(v.nodes)) return false
  if (!Array.isArray(v.edges)) return false
  // Optional fields — if present, they must match their declared type.
  // Any mismatch is a hard fail, not a coerce-to-default silent bug.
  if (!isOptionalNullableString(v.pipeline_name)) return false
  if (!isOptionalNullableString(v.pipeline_description)) return false
  if (!isOptionalNullableString(v.preamble)) return false
  if (!isOptionalNullableString(v.source_file)) return false
  if (!isOptionalNullableString(v.warning)) return false
  if (v.active_source !== undefined && typeof v.active_source !== "string") return false
  if (v.submodels !== undefined && v.submodels !== null && !isPlainObject(v.submodels)) return false
  if (v.sources !== undefined) {
    if (!Array.isArray(v.sources)) return false
    for (const s of v.sources) if (typeof s !== "string") return false
  }
  return true
}

/**
 * Parse + narrow, or throw a named Error.  The thrown message always
 * mentions the offending field (`nodes`, `edges`, …) so a drifting
 * backend surfaces in the browser console with a direct pointer at the
 * contract violation instead of an opaque "undefined is not iterable".
 */
export function parsePipelineResponse(v: unknown): PipelineResponse {
  if (!isPlainObject(v)) {
    throw new Error(
      `parsePipelineResponse: expected object, got ${v === null ? "null" : typeof v}`,
    )
  }
  if (!Array.isArray(v.nodes)) {
    throw new Error(
      `parsePipelineResponse: expected field \`nodes\` to be an array, got ${
        v.nodes === undefined ? "missing" : typeName(v.nodes)
      }`,
    )
  }
  if (!Array.isArray(v.edges)) {
    throw new Error(
      `parsePipelineResponse: expected field \`edges\` to be an array, got ${
        v.edges === undefined ? "missing" : typeName(v.edges)
      }`,
    )
  }
  assertOptionalNullableString(v.pipeline_name, "pipeline_name")
  assertOptionalNullableString(v.pipeline_description, "pipeline_description")
  assertOptionalNullableString(v.preamble, "preamble")
  assertOptionalNullableString(v.source_file, "source_file")
  assertOptionalNullableString(v.warning, "warning")
  if (v.active_source !== undefined && typeof v.active_source !== "string") {
    throw new Error(
      `parsePipelineResponse: expected field \`active_source\` to be a string, got ${typeName(v.active_source)}`,
    )
  }
  if (v.submodels !== undefined && v.submodels !== null && !isPlainObject(v.submodels)) {
    throw new Error(
      `parsePipelineResponse: expected field \`submodels\` to be an object or null, got ${typeName(v.submodels)}`,
    )
  }
  if (v.sources !== undefined) {
    if (!Array.isArray(v.sources)) {
      throw new Error(
        `parsePipelineResponse: expected field \`sources\` to be an array, got ${typeName(v.sources)}`,
      )
    }
    for (let i = 0; i < v.sources.length; i++) {
      if (typeof v.sources[i] !== "string") {
        throw new Error(
          `parsePipelineResponse: expected field \`sources[${i}]\` to be a string, got ${typeName(v.sources[i])}`,
        )
      }
    }
  }
  // All fields validated — the input already has the right shape, and we
  // return it unchanged (reference pass-through).  Downstream consumers
  // receive the same object they would have received from the raw JSON
  // parse, just with a sharper TypeScript type.
  return v as unknown as PipelineResponse
}

// ---------------------------------------------------------------------------
// ReactFlow node shape — validates object literals before they enter the
// graph.  Without this, `useSubmodelNavigation` would push malformed
// nodes (e.g. with a missing `data` field) into ReactFlow, which then
// throws deep inside its render path with an unhelpful stack.
// ---------------------------------------------------------------------------

/**
 * Narrow any value to a ReactFlow `Node`, throwing a field-naming Error
 * on malformed input.
 *
 * Required fields:
 *   - `id`: non-empty string.  Empty strings make ReactFlow silently drop
 *     edges that reference them — a classic "looks fine, no errors, but
 *     edges don't render" bug.
 *   - `position`: `{ x: number, y: number }`.  Missing or non-numeric
 *     coordinates make ReactFlow render the node at NaN offsets.
 *   - `data`: a plain object.  Arrays and primitives are rejected —
 *     ReactFlow indexes into `data` by string key.
 *
 * Optional fields (`type`, `width`, `height`, etc.) pass through
 * untouched.  We do not validate them because ReactFlow accepts many
 * optional fields and enumerating them here would bit-rot every time the
 * ReactFlow types evolve.
 */
export function validateReactFlowNode(v: unknown): Node {
  if (!isPlainObject(v)) {
    throw new Error(
      `validateReactFlowNode: expected object, got ${v === null ? "null" : typeof v}`,
    )
  }
  if (typeof v.id !== "string") {
    throw new Error(
      `validateReactFlowNode: expected field \`id\` to be a string, got ${
        v.id === undefined ? "missing" : typeof v.id
      }`,
    )
  }
  if (v.id === "") {
    throw new Error(
      "validateReactFlowNode: field `id` must not be an empty string (ReactFlow silently drops edges that reference empty ids)",
    )
  }
  if (!isPlainObject(v.position)) {
    throw new Error(
      `validateReactFlowNode: expected field \`position\` to be an object with numeric x and y, got ${
        v.position === undefined
          ? "missing"
          : v.position === null
            ? "null"
            : typeof v.position
      }`,
    )
  }
  if (typeof v.position.x !== "number") {
    throw new Error(
      `validateReactFlowNode: expected field \`position.x\` to be a number, got ${
        v.position.x === undefined ? "missing" : typeof v.position.x
      }`,
    )
  }
  if (typeof v.position.y !== "number") {
    throw new Error(
      `validateReactFlowNode: expected field \`position.y\` to be a number, got ${
        v.position.y === undefined ? "missing" : typeof v.position.y
      }`,
    )
  }
  if (v.data === undefined) {
    throw new Error(
      "validateReactFlowNode: expected field `data` to be a plain object, got missing",
    )
  }
  if (v.data === null) {
    throw new Error(
      "validateReactFlowNode: expected field `data` to be a plain object, got null",
    )
  }
  if (Array.isArray(v.data)) {
    throw new Error(
      "validateReactFlowNode: expected field `data` to be a plain object, got array",
    )
  }
  if (typeof v.data !== "object") {
    throw new Error(
      `validateReactFlowNode: expected field \`data\` to be a plain object, got ${typeof v.data}`,
    )
  }
  return v as unknown as Node
}
