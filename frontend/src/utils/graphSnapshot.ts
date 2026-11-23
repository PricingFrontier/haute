/**
 * Canonical serialization + dirty-derivation helpers for graph snapshots.
 *
 * Dirty tracking now shares a single serializer.  This avoids split behaviour where one
 * code path stripped presentation fields (`selected`, `dragging`, …)
 * before comparing but another did not — the latter would have flagged
 * the workspace dirty every time a node was selected.
 *
 * These helpers are **pure** — they operate on the raw inputs supplied
 * by the caller (nodes/edges/preamble/submodels or just the lastSaved string) and
 * do not read from any store.  Consumers compose them with whatever
 * source of graph state they already hold.
 */
import type { Node } from "@xyflow/react"
import type { PipelineEdge } from "../types/node"
import { canonicalJson } from "./canonicalJson"
import { cachedDigest, contentDigest } from "./contentDigest"
import type { GlobalConstant, GlobalConstantDraft } from "./globalConstants"
import { authoredPolarsConfig, isSteppedConfig } from "./polarsStepInputs"

// ---------------------------------------------------------------------------
// Field stripping
//
// Fields that React Flow manages for presentation only — not part of
// the on-disk pipeline.  Stripped before serialization so that
// selecting a node or mid-drag flagging doesn't flip the unsaved-changes
// indicator.
// ---------------------------------------------------------------------------

const REACT_FLOW_NODE_UI_FIELDS = [
  "selected",
  "dragging",
  "positionAbsolute",
  "measured",
  "resizing",
  "computed",
] as const

const REACT_FLOW_EDGE_UI_FIELDS = ["selected"] as const

const LIVE_HISTORY_NODE_METADATA_FIELDS = new Set([
  "editor",
  "_functionName",
  "_recoveryId",
  "_sourceFile",
  "_savedAddress",
  "_savedOrigins",
  "_defaultInputName",
  "_sourceHandleInputNames",
  "_configReference",
])

const LIVE_HISTORY_EDGE_IDENTITY_FIELDS = new Set(["_inputName"])
const NO_RETAINED_METADATA = new Set<string>()
/** A request carries the server's editor state back, so a transform of the graph echoes it. */
const REQUEST_NODE_METADATA_FIELDS = new Set(["editor"])
function stripNodeUiFields(n: Node, forFingerprint = false): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(k)) continue
    out[k] = k === "data"
      ? stripNodeDataMetadataFields(v, forFingerprint ? NO_RETAINED_METADATA : REQUEST_NODE_METADATA_FIELDS)
      : v
  }
  const data = out.data as Record<string, unknown> | undefined
  const config = data?.config as Record<string, unknown> | undefined
  if (forFingerprint && data !== undefined && config !== undefined && isSteppedConfig(String(data.nodeType), config)) {
    // Steps are authored; their generated body is a cache.
    // Keep both in requests/history, but do not count re-materialisation as an edit.
    data.config = authoredPolarsConfig(config)
  }
  return out
}

function stripEdgeUiFields(e: PipelineEdge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(k)) continue
    if (k === "data") {
      const stripped = stripNodeDataMetadataFields(v)
      if (
        typeof stripped === "object"
        && stripped !== null
        && !Array.isArray(stripped)
        && Object.keys(stripped).length === 0
      ) continue
      out[k] = stripped
      continue
    }
    out[k] = v
  }
  return out
}

function stripNodeHistoryFields(n: Node): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(n as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_NODE_UI_FIELDS as readonly string[]).includes(key)) continue
    out[key] = key === "data"
      ? stripNodeDataMetadataFields(value, LIVE_HISTORY_NODE_METADATA_FIELDS)
      : value
  }
  return out
}

function stripEdgeHistoryFields(e: PipelineEdge): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(e as unknown as Record<string, unknown>)) {
    if ((REACT_FLOW_EDGE_UI_FIELDS as readonly string[]).includes(key)) continue
    if (key === "data") {
      const stripped = stripNodeDataMetadataFields(
        value,
        LIVE_HISTORY_EDGE_IDENTITY_FIELDS,
      )
      if (
        typeof stripped === "object"
        && stripped !== null
        && !Array.isArray(stripped)
        && Object.keys(stripped).length === 0
      ) continue
      out[key] = stripped
      continue
    }
    out[key] = value
  }
  return out
}

function stripNodeDataMetadataFields(
  value: unknown,
  retainedMetadata: ReadonlySet<string> = NO_RETAINED_METADATA,
): unknown {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return value

  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
    // `_`-prefixed fields and the server's `editor` state are metadata, never an edit.
    if ((k.startsWith("_") || k === "editor") && !retainedMetadata.has(k)) continue
    out[k] = v
  }
  return out
}

function isSubmodelDefinitionRecord(record: Record<string, unknown>): boolean {
  return typeof record.definitionId === "string"
    && typeof record.file === "string"
    && typeof record.graph === "object"
    && record.graph !== null
    && Array.isArray(record.inputPorts)
    && Array.isArray(record.outputPorts)
}

function stripGraphMetadataTransientFields(value: unknown, forFingerprint = false): unknown {
  if (value === null || typeof value !== "object") return value
  if (Array.isArray(value)) return value.map((child) => stripGraphMetadataTransientFields(child, forFingerprint))

  const record = value as Record<string, unknown>
  if (isSubmodelDefinitionRecord(record)) {
    return Object.fromEntries(
      Object.entries(record)
        .filter(([key]) => !key.startsWith("_"))
        .map(([key, child]) => [key, stripGraphMetadataTransientFields(child, forFingerprint)]),
    )
  }
  if (typeof record.source === "string" && typeof record.target === "string") {
    return stripEdgeUiFields(record as unknown as PipelineEdge)
  }
  if (typeof record.id === "string" && "data" in record) {
    return stripNodeUiFields(record as unknown as Node, forFingerprint)
  }

  return Object.fromEntries(
    Object.entries(record).map(([key, child]) => [key, stripGraphMetadataTransientFields(child, forFingerprint)]),
  )
}

function cloneGraphValue<T>(value: T, seen = new WeakMap<object, unknown>()): T {
  if (value === null || typeof value !== "object") return value
  const objectValue = value as object
  const existing = seen.get(objectValue)
  if (existing !== undefined) return existing as T

  if (Array.isArray(value)) {
    const clone: unknown[] = []
    seen.set(objectValue, clone)
    for (const item of value) clone.push(cloneGraphValue(item, seen))
    return clone as T
  }

  const clone: Record<string, unknown> = {}
  seen.set(objectValue, clone)
  for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
    const clonedChild = cloneGraphValue(child, seen)
    // JSON keys are data, including the legacy Object prototype setter name.
    if (key === "__proto__") {
      Object.defineProperty(clone, key, {
        value: clonedChild, enumerable: true, configurable: true, writable: true,
      })
    } else {
      clone[key] = clonedChild
    }
  }
  return clone as T
}

// ---------------------------------------------------------------------------
// Canonicalisation
//
// Deterministic stringifier — same object shape and contents always
// produce the same string regardless of `JSON.stringify` key-order
// quirks.  Arrays retain their original order (intentional: the order
// of nodes/edges in the graph is user-meaningful).
//
// Safe for JSON-like tree structures with no cycles (the graph shapes
// we serialize).  A cyclic object would throw in `JSON.stringify` —
// correct loud-failure behaviour.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Clone a live editor graph into the strict canonical payload accepted by
 * backend graph schemas. Presentation fields and server-owned editor identity
 * metadata are removed recursively from root and embedded definition graphs.
 * A request builds its graph with `utils/documentRequest.documentRequestGraph`,
 * which gives each node exactly the saved forms its baseline holds for it.
 */
export function toCanonicalGraphPayload(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  submodels?: Record<string, unknown>
  preamble?: string
  global_constants?: readonly GlobalConstant[]
  global_constants_error?: string | null
}): {
  nodes: Node[]
  edges: PipelineEdge[]
  submodels: Record<string, unknown> | undefined
  preamble: string | undefined
  global_constants?: GlobalConstant[]
  global_constants_error?: string | null
} {
  return {
    ...(input.global_constants === undefined
      ? {}
      : { global_constants: structuredClone([...input.global_constants]) }),
    ...(input.global_constants_error === undefined
      ? {}
      : { global_constants_error: input.global_constants_error }),
    nodes: input.nodes.map(
      (node) => cloneGraphValue(stripNodeUiFields(node)) as Node,
    ),
    edges: input.edges.map(
      (edge) => cloneGraphValue(stripEdgeUiFields(edge)) as PipelineEdge,
    ),
    submodels: input.submodels === undefined
      ? undefined
      : cloneGraphValue(
        stripGraphMetadataTransientFields(input.submodels),
      ) as Record<string, unknown>,
    preamble: input.preamble,
  }
}

/**
 * Canonical serialization of a graph snapshot used for dirty-derivation.
 *
 * Scope: `{nodes, edges, preamble, submodels, globalConstants}` — the complete
 * persisted, user-editable graph surface. Preserved blocks remain out of scope because
 * they round-trip outside the graph store, while submodels are editable in
 * the GUI and must participate in dirty detection.
 * A stepped Polars node's code and validation result are generated caches;
 * they remain in requests/history but do not contribute to this fingerprint.
 *
 * Determinism: equal inputs produce equal strings even if the caller
 * constructed the object with keys in a different order.
 */
export function serializeSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
  globalConstants?: readonly GlobalConstantDraft[]
}): string {
  return canonicalJson({
    nodes: input.nodes.map((node) => stripNodeUiFields(node, true)),
    edges: input.edges.map(stripEdgeUiFields),
    preamble: input.preamble,
    submodels: stripGraphMetadataTransientFields(input.submodels, true),
    globalConstants: input.globalConstants ?? [],
  })
}

// ---------------------------------------------------------------------------
// The persisted fingerprint
//
// What `serializeSnapshot` reads, as one digest per node, edge, preamble and
// constant list, and the definitions' digest composed of one per definition and
// per child, each kept for its object's identity. The graph replaces what it
// changes and the store freezes what it holds (`freezeGraphValue`), so an edit
// to one node digests that node alone, and two fingerprints compare in time that
// follows the node count, never the size of a node's settings (FEG-01).
// ---------------------------------------------------------------------------

const persistedNodeDigests = new WeakMap<Node, string>()
const persistedEdgeDigests = new WeakMap<PipelineEdge, string>()
const persistedValueDigests = new WeakMap<object, string>()
const persistedConstantsDigests = new WeakMap<readonly GlobalConstantDraft[], string>()
const NO_CONSTANTS: readonly GlobalConstantDraft[] = Object.freeze([])

function persistedNodeDigest(node: Node): string {
  return cachedDigest(persistedNodeDigests, node, (value) => canonicalJson(stripNodeUiFields(value, true)))
}

function persistedEdgeDigest(edge: PipelineEdge): string {
  return cachedDigest(persistedEdgeDigests, edge, (value) => canonicalJson(stripEdgeUiFields(value)))
}

/** What a value adds to the digest of the value holding it: an object, its own digest. */
function persistedPart(value: unknown): string {
  return value !== null && typeof value === "object" ? `o:${persistedValueDigest(value)}` : `p:${canonicalJson(value)}`
}

/** A record's fields `keep` admits, as JSON writes them (sorted, `undefined` left out), each by its part. */
function persistedRecordParts(record: Record<string, unknown>, keep: (key: string) => boolean = () => true): string {
  return Object.keys(record)
    .filter((key) => keep(key) && record[key] !== undefined && typeof record[key] !== "function")
    .sort()
    .map((key) => `${JSON.stringify(key)}:${persistedPart(record[key])}`)
    .join(",")
}

/**
 * The digest of a value of the definitions, read as `stripGraphMetadataTransientFields`
 * reads it for `serializeSnapshot`: a node or an edge by its serialisation, anything else
 * by its fields' own digests. Each is kept for its object, so a change to one child of one
 * definition reads that child alone.
 */
function persistedValueDigest(value: object): string {
  return cachedDigest(persistedValueDigests, value, (object) => {
    if (Array.isArray(object)) return `[${object.map(persistedPart).join(",")}]`
    const record = object as Record<string, unknown>
    if (isSubmodelDefinitionRecord(record)) return `definition{${persistedRecordParts(record, (key) => !key.startsWith("_"))}}`
    if (typeof record.source === "string" && typeof record.target === "string") {
      return `edge:${canonicalJson(stripEdgeUiFields(record as unknown as PipelineEdge))}`
    }
    if (typeof record.id === "string" && "data" in record) {
      return `node:${canonicalJson(stripNodeUiFields(record as unknown as Node, true))}`
    }
    return `{${persistedRecordParts(record)}}`
  })
}

/**
 * The fingerprint of the document `serializeSnapshot` writes: equal for two documents
 * exactly when their serialisations are equal, and cheap to recompute after an edit.
 * The dirty flag and whether a change moves history are decided on it.
 */
export function persistedFingerprint(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
  globalConstants?: readonly GlobalConstantDraft[]
}): string {
  const constants = cachedDigest(persistedConstantsDigests, input.globalConstants ?? NO_CONSTANTS, canonicalJson)
  return `nodes:${input.nodes.map(persistedNodeDigest).join(",")}`
    + `|edges:${input.edges.map(persistedEdgeDigest).join(",")}`
    + `|preamble:${contentDigest(input.preamble)}|submodels:${persistedValueDigest(input.submodels)}|constants:${constants}`
}

// ---------------------------------------------------------------------------
// History forms
//
// A history entry holds each node, edge and definition in its history form, kept
// for the object it is the form of and frozen: the entries share everything a
// change did not touch, so one hundred edits beside a large table hold the table
// once, in the pipeline or in a submodel (FEG-01).
// ---------------------------------------------------------------------------

const historyForms = new WeakMap<object, object>()
const deepFrozen = new WeakSet<object>()

/**
 * Freeze `value` and everything it holds, once per object. The graph store freezes each
 * value it accepts (`stores/useGraphStore.ts`), so a write into a committed node in place
 * throws instead of going round the digests and history forms kept for its identity.
 */
export function freezeGraphValue<T>(value: T): T {
  if (value === null || typeof value !== "object" || deepFrozen.has(value)) return value
  deepFrozen.add(value)
  for (const child of Object.values(value)) freezeGraphValue(child)
  return Object.freeze(value)
}

/** `value`'s history form, made once and shared by every entry that holds it. */
function historyForm<T extends object>(value: T, strip: (value: T) => object): T {
  const known = historyForms.get(value)
  if (known !== undefined) return known as T
  const form = freezeGraphValue(strip(value)) as T
  historyForms.set(value, form)
  historyForms.set(form, form)
  return form
}

function nodeHistoryForm(node: Node): Node {
  const form = historyForm(node, stripNodeHistoryFields)
  // The form leaves out only what the persisted fingerprint leaves out too.
  if (form !== node && !persistedNodeDigests.has(form)) persistedNodeDigests.set(form, persistedNodeDigest(node))
  return form
}

function edgeHistoryForm(edge: PipelineEdge): PipelineEdge {
  const form = historyForm(edge, stripEdgeHistoryFields)
  if (form !== edge && !persistedEdgeDigests.has(form)) persistedEdgeDigests.set(form, persistedEdgeDigest(edge))
  return form
}

/**
 * A value of the definitions in its history form, kept for each object at every level: a
 * node and an edge without their presentation and execution fields, anything else field by
 * field. An unchanged definition, and an unchanged child of a changed one, is shared by
 * every entry holding it.
 */
function graphHistoryForm<T>(value: T): T {
  if (value === null || typeof value !== "object") return value
  const object = value as object
  const form = historyForm(object, (each) => {
    if (Array.isArray(each)) return each.map(graphHistoryForm)
    const record = each as Record<string, unknown>
    if (typeof record.source === "string" && typeof record.target === "string") {
      return stripEdgeHistoryFields(record as unknown as PipelineEdge)
    }
    if (typeof record.id === "string" && "data" in record) return stripNodeHistoryFields(record as unknown as Node)
    return Object.fromEntries(Object.entries(record).map(([key, child]) => [key, graphHistoryForm(child)]))
  })
  // The form leaves out only what the persisted fingerprint leaves out too.
  if (form !== object && !persistedValueDigests.has(form)) persistedValueDigests.set(form, persistedValueDigest(object))
  return form as T
}

/**
 * A live history snapshot. React Flow presentation and volatile runtime
 * fields are omitted, but server-owned editor identities are retained:
 * undo/redo restores a live executable graph, not a persisted document.
 * `serializeSnapshot` remains the separate boundary that strips identity
 * metadata too. Its parts are frozen history forms shared with every other
 * snapshot of the same objects.
 */
export function historyGraphSnapshot(input: {
  nodes: readonly Node[]
  edges: readonly PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
  globalConstants?: readonly GlobalConstantDraft[]
}): {
  nodes: Node[]
  edges: PipelineEdge[]
  preamble: string
  submodels: Record<string, unknown>
  globalConstants?: GlobalConstantDraft[]
} {
  return {
    ...(input.globalConstants === undefined
      ? {}
      : { globalConstants: historyForm(input.globalConstants, (constants) => [...constants]) as GlobalConstantDraft[] }),
    nodes: input.nodes.map(nodeHistoryForm),
    edges: input.edges.map(edgeHistoryForm),
    preamble: input.preamble,
    submodels: graphHistoryForm(input.submodels),
  }
}

/**
 * A node as the document keeps it: no React Flow presentation fields and no
 * execution annotations (a preview's columns, statuses), its server-owned
 * editor identities kept. What a submodel definition holds for each child, and
 * what history restores.
 */
export function authoredNodeForm(node: Node): Node {
  return stripNodeHistoryFields(node) as unknown as Node
}

/** An edge as the document keeps it: no presentation fields, its input name kept. */
export function authoredEdgeForm(edge: PipelineEdge): PipelineEdge {
  return stripEdgeHistoryFields(edge) as unknown as PipelineEdge
}

/**
 * A node's execution annotations: the `_`-prefixed data the editor writes for
 * the view (columns, statuses, notes), never its editor identities.
 */
export function nodeExecutionAnnotations(node: Node): Record<string, unknown> {
  const data = node.data as Record<string, unknown>
  return Object.fromEntries(
    Object.entries(data).filter(([key]) => key.startsWith("_") && !LIVE_HISTORY_NODE_METADATA_FIELDS.has(key)),
  )
}

/**
 * Whether a node carries the canvas's state, which its authored form leaves out: a React
 * Flow field (a selection, a size, a drag) or an execution annotation.
 */
export function holdsCanvasState(node: Node): boolean {
  return REACT_FLOW_NODE_UI_FIELDS.some((field) => field in node)
    || Object.keys(nodeExecutionAnnotations(node)).length > 0
}

/** The fingerprint of the empty workspace, which a never-saved document is clean at. */
export const EMPTY_PERSISTED_FINGERPRINT = persistedFingerprint({
  nodes: [],
  edges: [],
  preamble: "",
  submodels: {},
})

/**
 * Pure selector: is the current graph different from the last saved one? Both
 * are `persistedFingerprint`s.
 *
 * Semantics:
 *   - `lastSavedSnapshot === null` (never saved) + empty current graph
 *     => NOT dirty.  Fresh workspace is clean.
 *   - `lastSavedSnapshot === null` + non-empty current graph => DIRTY.
 *     The user built something without saving.
 *   - `lastSavedSnapshot !== null`: string-compare against current.
 */
export function selectIsDirty(
  state: { lastSavedSnapshot: string | null },
  currentSnapshot: string,
): boolean {
  if (state.lastSavedSnapshot === null) {
    return currentSnapshot !== EMPTY_PERSISTED_FINGERPRINT
  }
  return currentSnapshot !== state.lastSavedSnapshot
}
