/**
 * Emit-frame helpers for apiInput nodes.
 *
 * An apiInput node shreds one JSON file into multiple "tables". Each
 * `emit: true` table renders as a labelled source `<Handle>` on the
 * node's right edge (see `PipelineNode._SourceHandles`). Downstream
 * edges bind to a frame via `edge.sourceHandle` = the table's label.
 *
 * ── Frame identity contract (W1.3 / W1.4) ───────────────────────────
 *
 * The handle id IS the raw table label, end to end:
 *
 *  - runtime frames are keyed by raw label (`_json_shred.shred_v2`);
 *  - the executor resolves `edge.sourceHandle` against those keys and
 *    KeyErrors on a miss (`_execute_lazy._resolve_source_frame`);
 *  - codegen saves the handle verbatim as
 *    `pipeline.connect(..., source_port=<sourceHandle>)`;
 *  - the parser restores `sourceHandle = source_port` on reload.
 *
 * Two consequences this module enforces:
 *
 *  1. The frontend NEVER synthesizes handle ids. Blank labels used to
 *     fall back to `port_<idx>` and duplicates to `label__<idx>` —
 *     handles the backend can never emit (it hard-rejects blank and
 *     duplicate labels in `validate_v2_schema`), so edges bound to them
 *     were guaranteed executor KeyErrors. A table with an invalid label
 *     now has NO frame; the editor surfaces the validation error
 *     (`apiInputLabelIssue`) instead of papering over it.
 *
 *  2. Renaming a frame is handle MIGRATION, never edge loss. Because the
 *     label is the handle id, a committed rename changes the id — so
 *     the same state update that commits the rename must rebind the
 *     edges bound to the old id (`migrateApiInputEdges`). Only edges
 *     whose frame genuinely no longer exists are pruned
 *     (`reconcileApiInputEdges`), with a visible toast at the call
 *     site. `applyApiInputConfigChange` composes the two.
 */
import type { SimpleEdge, SimpleNode } from "../panels/editors/_shared"
import { NODE_TYPES } from "./nodeTypes"
import { sanitizeName } from "./sanitizeName"

type ConfigLike = Record<string, unknown> | undefined | null

function hasSelectedColumn(table: Record<string, unknown>): boolean {
  const cols = (table as { columns?: unknown }).columns
  if (!Array.isArray(cols)) return false
  return cols.some(
    (c) => !!c && typeof c === "object" && (c as { selected?: unknown }).selected === true,
  )
}

function emitTables(config: ConfigLike): Array<Record<string, unknown>> {
  const tables = (config as { tables?: unknown } | null | undefined)?.tables
  if (!Array.isArray(tables)) return []
  return tables.filter(
    (t): t is Record<string, unknown> =>
      !!t &&
      typeof t === "object" &&
      (t as { emit?: unknown }).emit === true &&
      // Mirror the backend runtime (`_json_shred.load_v2_api_source`): a table
      // is a frame only if it ALSO has at least one selected column. An
      // emit-true table with no selected columns is NOT emitted at runtime, so
      // rendering a bindable Handle for it would let an edge bind to a frame the
      // executor then KeyErrors on — the very silent-orphan failure this module
      // exists to prevent.
      hasSelectedColumn(t),
  )
}

/** The exact label string of a table, or null when missing/non-string. */
function rawLabel(table: Record<string, unknown>): string | null {
  const raw = (table as { label?: unknown }).label
  return typeof raw === "string" ? raw : null
}

const ASCII_IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const PYTHON_HARD_KEYWORDS = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await",
  "break", "class", "continue", "def", "del", "elif", "else", "except",
  "finally", "for", "from", "global", "if", "import", "in", "is",
  "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
  "while", "with", "yield",
])

function isValidFrameLabel(label: string | null): label is string {
  return label !== null && ASCII_IDENTIFIER_RE.test(label) && !PYTHON_HARD_KEYWORDS.has(label)
}

/**
 * Derive the ordered, unique raw labels from runtime-eligible tables.
 *
 * This is shared by the visible-frame list and the multi-port handle-mode
 * helper so both consumers agree on which labelled frames are bindable:
 *  - only tables already eligible at runtime (`emit: true` and at least one
 *    selected column) are considered;
 *  - the frame id is the table's RAW label — never a synthesized stand-in;
 *  - missing / non-string / blank / non-identifier / keyword labels yield no
 *    frame;
 *  - duplicate labels are compared case-insensitively and only the first
 *    occurrence remains bindable.
 *
 * This list is consumed unchanged by visible rows, source handles, edge
 * reconciliation, and derived input-name surfaces.
 */
function eligibleFrameLabels(emit: readonly Record<string, unknown>[]): string[] {
  const seen = new Set<string>()
  const labels: string[] = []
  for (const t of emit) {
    const label = rawLabel(t)
    if (!isValidFrameLabel(label)) continue
    const folded = label.toLowerCase()
    if (seen.has(folded)) continue
    seen.add(folded)
    labels.push(label)
  }
  return labels
}

/**
 * Ordered list of every runtime-eligible apiInput frame label.
 *
 * This is the only frontend frame-label derivation. A single eligible frame
 * is labelled exactly like a multi-frame apiInput; only zero eligible frames
 * use the legacy null-id handle.
 */
export function apiInputFrameLabels(config: ConfigLike): string[] {
  return eligibleFrameLabels(emitTables(config))
}

type SubmodelsLike = Record<string, unknown> | undefined

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export type SubmodelGraphLike = {
  nodes: SimpleNode[]
  edges: SimpleEdge[]
  submodels: Record<string, unknown>
}

/** Read either supported submodel metadata shape without inventing a graph. */
export function submodelGraphFromMetadata(value: unknown): SubmodelGraphLike | undefined {
  const metadata = isRecord(value) ? value : undefined
  const graph = metadata && isRecord(metadata.graph) ? metadata.graph : metadata
  if (!graph || !Array.isArray(graph.nodes)) return undefined
  return {
    nodes: graph.nodes as SimpleNode[],
    edges: Array.isArray(graph.edges) ? graph.edges as SimpleEdge[] : [],
    submodels: isRecord(graph.submodels) ? graph.submodels : {},
  }
}

/** Resolve one synthetic submodel boundary handle to its actual child node. */
export function resolveSubmodelBoundaryNode(
  boundaryNode: SimpleNode,
  handle: string | null | undefined,
  direction: "in" | "out",
  submodels: SubmodelsLike,
): SimpleNode | undefined {
  const prefix = `${direction}__`
  if (!boundaryNode.id.startsWith("submodel__") || !handle?.startsWith(prefix)) {
    return undefined
  }

  const submodelName = boundaryNode.id.slice("submodel__".length)
  const graph = submodelGraphFromMetadata(submodels?.[submodelName])
  if (!graph) {
    throw new Error(
      `Cannot resolve ${direction}put handle ${handle} for submodel ${submodelName}: graph nodes are missing`,
    )
  }
  const childId = handle.slice(prefix.length)
  const child = graph.nodes.find((node) => node.id === childId)
  if (!child) {
    throw new Error(
      `Cannot resolve ${direction}put handle ${handle} for submodel ${submodelName}: child ${childId} is missing`,
    )
  }
  return child
}

function submodelChildNode(
  edge: SimpleEdge,
  sourceNode: SimpleNode,
  submodels: SubmodelsLike,
): SimpleNode | undefined {
  return resolveSubmodelBoundaryNode(sourceNode, edge.sourceHandle, "out", submodels)
}

/**
 * Derive the executable input name for one edge.
 *
 * API-input frame handles are already canonical names and are returned
 * verbatim, including a stale non-null handle so the UI can identify the
 * unresolved edge. Ordinary sources use the shared backend-compatible
 * sanitizer. A flattened submodel output resolves its `out__` child before
 * sanitizing, matching code generation's boundary resolution.
 */
export function edgeInputName(
  edge: SimpleEdge,
  sourceNode: SimpleNode,
  submodels: SubmodelsLike = undefined,
): string {
  if (sourceNode.data.nodeType === NODE_TYPES.API_INPUT) {
    if (edge.sourceHandle === null || edge.sourceHandle === undefined) {
      throw new Error(`apiInput edge ${edge.id} has no sourceHandle/frame label`)
    }
    return edge.sourceHandle
  }
  const resolvedChild = submodelChildNode(edge, sourceNode, submodels)
  return sanitizeName(resolvedChild?.data.label ?? sourceNode.data.label)
}

/**
 * Derive every incoming input name for one executable target. `boundaryNodeId`
 * identifies the visible target when the executable target is a child behind
 * an `in__<child>` submodel handle. The same helper is used by drag-time and
 * rename preflight so boundary and ordinary edges cannot drift apart.
 */
export function incomingEdgeInputNames({
  targetNodeId,
  boundaryNodeId = targetNodeId,
  nodes,
  edges,
  submodels,
}: {
  targetNodeId: string
  boundaryNodeId?: string
  nodes: readonly SimpleNode[]
  edges: readonly SimpleEdge[]
  submodels?: SubmodelsLike
}): string[] {
  const nodesById = new Map(nodes.map((node) => [node.id, node]))
  const boundaryNode = nodesById.get(boundaryNodeId)
  const names: string[] = []
  for (const edge of edges) {
    if (edge.target !== targetNodeId) {
      if (
        edge.target !== boundaryNodeId
        || boundaryNode?.data.nodeType !== NODE_TYPES.SUBMODEL
        || resolveSubmodelBoundaryNode(boundaryNode, edge.targetHandle, "in", submodels)?.id !== targetNodeId
      ) continue
    }
    const sourceNode = nodesById.get(edge.source)
    if (!sourceNode) {
      throw new Error(`Cannot derive input name: source node ${edge.source} is missing`)
    }
    names.push(edgeInputName(edge, sourceNode, submodels))
  }
  return names
}

// ─── Label validation (mirrors backend `validate_v2_schema`) ─────────

// Filesystem-safe label form — exact mirror of
// `haute._api_input_schema._FILESYSTEM_SAFE_RE`. The backend derives
// each frame's parquet filename from the sanitised label and rejects
// (B2) any two labels whose sanitised forms collide. The `u` flag is
// load-bearing for parity: Python regexes operate on code points, and
// without it JavaScript matches UTF-16 units — an astral char (e.g. an
// emoji) would become TWO underscores here but ONE on the backend,
// silently desynchronising the collision check.
const FILESYSTEM_SAFE_RE = /[^a-zA-Z0-9_-]/gu

/**
 * Map a table label to its filesystem-safe parquet-filename stem,
 * byte-for-byte like `haute._api_input_schema.sanitise_label_for_filesystem`.
 */
export function sanitiseLabelForFilesystem(label: string): string {
  if (!label) return "_unnamed"
  return label.replace(FILESYSTEM_SAFE_RE, "_")
}

export type ApiInputLabelIssue =
  | { kind: "blank" }
  | { kind: "identifier"; reason: "ascii" | "keyword" }
  | { kind: "duplicate"; other: string }
  | { kind: "sanitised-collision"; other: string; sanitised: string }

/**
 * Validate a candidate table label against the labels of the node's
 * OTHER tables. Mirrors the backend's save-time rules
 * (`validate_v2_schema`): blank labels, exact duplicates, and
 * sanitised-form collisions (B2) are all rejected there with a 422 —
 * the editor must surface the same verdict before commit, because a
 * committed invalid label would otherwise only fail at save/run time.
 *
 * The blank check uses `trim()` (slightly stricter than the backend's
 * non-empty floor): a whitespace-only frame name is never intentional.
 */
export function apiInputLabelIssue(
  candidate: string,
  otherLabels: readonly string[],
): ApiInputLabelIssue | null {
  if (!candidate.trim()) return { kind: "blank" }
  if (!ASCII_IDENTIFIER_RE.test(candidate)) {
    return { kind: "identifier", reason: "ascii" }
  }
  if (PYTHON_HARD_KEYWORDS.has(candidate)) {
    return { kind: "identifier", reason: "keyword" }
  }
  for (const other of otherLabels) {
    if (other === candidate) return { kind: "duplicate", other }
  }
  // Compared case-insensitively, mirroring the backend's B2: sanitised
  // stems are pure ASCII, and `Foo.parquet` / `foo.parquet` are the SAME
  // file on the case-insensitive filesystems macOS and Windows default
  // to — case-variant labels would silently clobber one parquet.
  const sanitised = sanitiseLabelForFilesystem(candidate)
  const folded = sanitised.toLowerCase()
  for (const other of otherLabels) {
    if (sanitiseLabelForFilesystem(other).toLowerCase() === folded) {
      return { kind: "sanitised-collision", other, sanitised }
    }
  }
  return null
}

/** User-facing message for a label issue; null passes through. */
export function apiInputLabelIssueMessage(issue: ApiInputLabelIssue | null): string | null {
  if (issue === null) return null
  switch (issue.kind) {
    case "blank":
      return "A label is required — it names this table's frame."
    case "identifier":
      return issue.reason === "keyword"
        ? "A frame label cannot be a Python hard keyword."
        : "A frame label must be an ASCII identifier (letters, digits, and underscores only)."
    case "duplicate":
      return `Duplicate label: "${issue.other}" is already used by another table.`
    case "sanitised-collision":
      return `Label collides with "${issue.other}": both become "${issue.sanitised}" on disk (case-insensitive — macOS/Windows treat case-variant filenames as one file).`
  }
}

// ─── Edge reconciliation ─────────────────────────────────────────────

/**
 * The set of `sourceHandle` values an apiInput's outgoing edges may
 * legitimately carry, given its config. One or more eligible frames expose
 * their raw labels; zero eligible frames expose no valid source keys because
 * their legacy default handle is rendered non-connectable.
 */
export function validSourceHandleKeys(config: ConfigLike): Set<string> {
  return new Set(apiInputFrameLabels(config))
}

export type ReconciledApiInputEdge = {
  edge: SimpleEdge
  /** The stale `sourceHandle` the edge was bound to (null shown as null). */
  sourceHandle: string | null
}

export type ReconcileApiInputEdgesResult<E extends SimpleEdge> = {
  /** Edges with orphaned ones removed. Same reference as the input when nothing changed. */
  edges: E[]
  /** The removed edges + the stale frame they pointed at. Empty when nothing changed. */
  removed: ReconciledApiInputEdge[]
}

/**
 * Prune outgoing edges of `nodeId` whose `sourceHandle` no longer maps
 * to a rendered frame under `config`.
 *
 * Pure: it computes the result, never mutates. The caller is
 * responsible for committing the new edge list and surfacing the
 * removal to the user (a toast naming what was disconnected) — pruning
 * silently would just trade one invisible failure for another.
 *
 * NOTE: renames must be migrated BEFORE pruning (see
 * `migrateApiInputEdges` / `applyApiInputConfigChange`) — this function
 * alone cannot distinguish "frame renamed" from "frame deleted".
 *
 * Returns the original `edges` array reference untouched when nothing
 * is orphaned, so callers can cheaply skip a state update.
 */
export function reconcileApiInputEdges<E extends SimpleEdge>({
  nodeId,
  config,
  edges,
  pruneNamedStale = true,
}: {
  nodeId: string
  config: ConfigLike
  edges: E[]
  /** Load reconciliation passes false to preserve named unresolved edges. */
  pruneNamedStale?: boolean
}): ReconcileApiInputEdgesResult<E> {
  const validKeys = validSourceHandleKeys(config)
  const removed: ReconciledApiInputEdge[] = []
  const kept: E[] = []
  for (const edge of edges) {
    if (edge.source !== nodeId) {
      kept.push(edge)
      continue
    }
    if (!pruneNamedStale && edge.sourceHandle !== null && edge.sourceHandle !== undefined) {
      kept.push(edge)
      continue
    }
    const handleKey = edge.sourceHandle ?? ""
    if (validKeys.has(handleKey)) {
      kept.push(edge)
      continue
    }
    removed.push({ edge, sourceHandle: edge.sourceHandle ?? null })
  }
  // Preserve referential identity when nothing was orphaned so callers
  // can short-circuit a re-render / snapshot.
  if (removed.length === 0) return { edges, removed }
  return { edges: kept, removed }
}

// ─── Rename migration (W1.3) ─────────────────────────────────────────

/** The raw tables array of a config (no filtering — index alignment matters). */
function rawTables(config: ConfigLike): unknown[] {
  const tables = (config as { tables?: unknown } | null | undefined)?.tables
  return Array.isArray(tables) ? tables : []
}

function tableAt(tables: unknown[], i: number): Record<string, unknown> | null {
  const t = tables[i]
  return t && typeof t === "object" ? (t as Record<string, unknown>) : null
}

/**
 * Which table index OWNS each frame label under `config` — the first
 * emit-eligible table carrying that label (later duplicates are not
 * frames, matching `apiInputFrameLabels`). Also counts eligible
 * tables per label so collisions can be detected.
 */
function portOwnership(config: ConfigLike): {
  ownerIndexByLabel: Map<string, number>
  eligibleCountByLabel: Map<string, number>
} {
  const tables = rawTables(config)
  const eligible = new Set(emitTables(config))
  const ownerIndexByLabel = new Map<string, number>()
  const eligibleCountByLabel = new Map<string, number>()
  tables.forEach((t, i) => {
    if (!t || typeof t !== "object" || !eligible.has(t as Record<string, unknown>)) return
    const label = rawLabel(t as Record<string, unknown>)
    if (!isValidFrameLabel(label)) return
    if (!ownerIndexByLabel.has(label)) ownerIndexByLabel.set(label, i)
    eligibleCountByLabel.set(label, (eligibleCountByLabel.get(label) ?? 0) + 1)
  })
  return { ownerIndexByLabel, eligibleCountByLabel }
}

export type ReboundApiInputEdge<E extends SimpleEdge> = {
  /** The ORIGINAL edge object (pre-rebind). */
  edge: E
  from: string
  to: string
}

export type MigrateApiInputEdgesResult<E extends SimpleEdge> = {
  /** Edges with renamed handles rebound. Same reference as the input when nothing changed. */
  edges: E[]
  rebound: ReboundApiInputEdge<E>[]
}

/**
 * Rebind outgoing edges of `nodeId` across a frame RENAME — the W1.3
 * fix. Because the handle id is the label itself (the only id space
 * that round-trips through codegen/parse), committing a rename changes
 * the id; without migration the edges bound to the old id are
 * indistinguishable from edges to a deleted frame and get pruned.
 *
 * A rename is recognised conservatively — every guard must hold:
 *  - prev/next have the SAME number of tables (the editor never
 *    reorders rows; add/remove changes the length and is not a rename);
 *  - at index i the `path` is unchanged but the label differs (a
 *    replaced table — different path — is not a rename);
 *  - the old label was the frame OWNED by table i under prev (a later
 *    duplicate never owned the bound edges);
 *  - the old label no longer names ANY frame under next (otherwise
 *    rebinding would hijack edges that still resolve);
 *  - the new label is owned by table i under next and is UNIQUE there
 *    (never rebind onto a colliding handle).
 *
 * Pure; returns the input `edges` reference when nothing was rebound.
 */
export function migrateApiInputEdges<E extends SimpleEdge>({
  nodeId,
  prevConfig,
  nextConfig,
  edges,
}: {
  nodeId: string
  prevConfig: ConfigLike
  nextConfig: ConfigLike
  edges: E[]
}): MigrateApiInputEdgesResult<E> {
  const prevTables = rawTables(prevConfig)
  const nextTables = rawTables(nextConfig)
  if (prevTables.length === 0 || prevTables.length !== nextTables.length) {
    return { edges, rebound: [] }
  }

  const prevOwnership = portOwnership(prevConfig)
  const nextOwnership = portOwnership(nextConfig)

  const renameByOldLabel = new Map<string, string>()
  for (let i = 0; i < prevTables.length; i++) {
    const prevTable = tableAt(prevTables, i)
    const nextTable = tableAt(nextTables, i)
    if (!prevTable || !nextTable) continue
    const prevLabel = rawLabel(prevTable)
    const nextLabel = rawLabel(nextTable)
    if (!isValidFrameLabel(prevLabel) || !isValidFrameLabel(nextLabel)) continue
    if (prevLabel === nextLabel) continue
    // Same table identity: the iteration path must be unchanged.
    const prevPath = (prevTable as { path?: unknown }).path
    const nextPath = (nextTable as { path?: unknown }).path
    if (typeof prevPath !== "string" || prevPath !== nextPath) continue
    // The old label must have been THIS table's frame…
    if (prevOwnership.ownerIndexByLabel.get(prevLabel) !== i) continue
    // …and must be gone from the new frame set (otherwise ambiguous).
    if (nextOwnership.ownerIndexByLabel.has(prevLabel)) continue
    // The new label must be this table's frame and collision-free.
    if (nextOwnership.ownerIndexByLabel.get(nextLabel) !== i) continue
    if (nextOwnership.eligibleCountByLabel.get(nextLabel) !== 1) continue
    renameByOldLabel.set(prevLabel, nextLabel)
  }
  if (renameByOldLabel.size === 0) return { edges, rebound: [] }

  const rebound: ReboundApiInputEdge<E>[] = []
  const next = edges.map((edge) => {
    if (edge.source !== nodeId) return edge
    const handle = edge.sourceHandle
    if (typeof handle !== "string") return edge
    const to = renameByOldLabel.get(handle)
    if (to === undefined) return edge
    rebound.push({ edge, from: handle, to })
    return { ...edge, sourceHandle: to }
  })
  if (rebound.length === 0) return { edges, rebound }
  return { edges: next, rebound }
}

export type ApplyApiInputConfigChangeResult<E extends SimpleEdge> = {
  /** Edge list after rename migration AND orphan pruning. Same reference as the input when nothing changed. */
  edges: E[]
  rebound: ReboundApiInputEdge<E>[]
  removed: ReconciledApiInputEdge[]
}

/**
 * The single edge-maintenance step for an apiInput config commit:
 * migrate renames FIRST (so renamed frames keep their edges), then prune
 * whatever genuinely no longer resolves (emit-off, table delete,
 * single↔multi transitions). `App.onUpdateNode` applies the result in
 * the same state update that commits the config, so a rename is one
 * atomic, undoable operation — never a destroy-and-reconnect.
 */
export function applyApiInputConfigChange<E extends SimpleEdge>({
  nodeId,
  prevConfig,
  nextConfig,
  edges,
}: {
  nodeId: string
  prevConfig: ConfigLike
  nextConfig: ConfigLike
  edges: E[]
}): ApplyApiInputConfigChangeResult<E> {
  const migration = migrateApiInputEdges({ nodeId, prevConfig, nextConfig, edges })
  const { edges: pruned, removed } = reconcileApiInputEdges({
    nodeId,
    config: nextConfig,
    edges: migration.edges,
  })
  return { edges: pruned, rebound: migration.rebound, removed }
}
