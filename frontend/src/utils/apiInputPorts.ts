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
import {
  isSubmodelDefinition,
  isSubmodelInstanceConfig,
  type SubmodelDefinition,
  type SubmodelPortData,
} from "../types/node"
import { NODE_TYPES } from "./nodeTypes"

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
      // Mirror the backend runtime (`_json_shred._cache.load_v2_api_source`): a table
      // is a frame only if it ALSO has at least one selected column. An
      // emit-true table with no selected columns is NOT emitted at runtime, so
      // rendering a bindable Handle for it would let an edge bind to a frame the
      // executor then KeyErrors on — the very silent-orphan failure this module
      // exists to prevent.
      hasSelectedColumn(t),
  )
}

/**
 * Whether an apiInput config contains a table that contributes a runtime
 * frame. This shares the exact eligibility path used for rendered handles and
 * mirrors backend `_json_shred._shred.table_is_emitting`.
 */
export function apiInputHasEmittingTable(config: ConfigLike): boolean {
  return emitTables(config).length > 0
}

/** The exact label string of a table, or null when missing/non-string. */
function rawLabel(table: Record<string, unknown>): string | null {
  const raw = (table as { label?: unknown }).label
  return typeof raw === "string" ? raw : null
}

export type ApiInputFrameColumn = { name: string; dtype: string }

/**
 * The selected columns of one runtime-eligible apiInput frame, addressed by an
 * edge's `sourceHandle` (the frame label). This is the single frontend
 * derivation of a frame's column set and mirrors the backend runtime: only
 * `emit: true` tables with at least one selected column are frames, and only
 * `selected: true` columns are emitted. A null or unknown handle yields no
 * columns — an unresolved edge must never impersonate a frame.
 */
export function apiInputFrameColumns(
  config: ConfigLike,
  sourceHandle: string | null | undefined,
): ApiInputFrameColumn[] {
  if (typeof sourceHandle !== "string") return []
  const table = emitTables(config).find((t) => rawLabel(t) === sourceHandle)
  const columns = table ? (table as { columns?: unknown }).columns : undefined
  if (!Array.isArray(columns)) return []
  return columns.flatMap((candidate): ApiInputFrameColumn[] => {
    if (!candidate || typeof candidate !== "object") return []
    const column = candidate as { name?: unknown; selected?: unknown; dtype?: unknown; type?: unknown }
    if (column.selected !== true || typeof column.name !== "string") return []
    const dtype = typeof column.dtype === "string"
      ? column.dtype
      : typeof column.type === "string" ? column.type : ""
    return [{ name: column.name, dtype }]
  })
}

const ASCII_IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

function isValidFrameLabel(
  label: string | null,
  reservedLabels: ReadonlySet<string>,
): label is string {
  return label !== null && ASCII_IDENTIFIER_RE.test(label) && !reservedLabels.has(label)
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
function eligibleFrameLabels(
  emit: readonly Record<string, unknown>[],
  reservedLabels: ReadonlySet<string>,
): string[] {
  const seen = new Set<string>()
  const labels: string[] = []
  for (const t of emit) {
    const label = rawLabel(t)
    if (!isValidFrameLabel(label, reservedLabels)) continue
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
 * This is the only frontend frame-label derivation. Every eligible frame uses
 * its label as its handle.
 */
export function apiInputFrameLabels(
  config: ConfigLike,
  reservedLabels: ReadonlySet<string>,
): string[] {
  return eligibleFrameLabels(emitTables(config), reservedLabels)
}

/** Ordered handles whose executable identities were supplied by the server. */
export function authoritativeSourceHandles(
  node: { id: string; data: Record<string, unknown> },
): string[] {
  const value = node.data._sourceHandleInputNames
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`Node ${node.id} has no authoritative source-handle identities`)
  }
  const entries = Object.entries(value)
  if (entries.some(([handle, inputName]) => handle.length === 0 || typeof inputName !== "string" || inputName.length === 0)) {
    throw new Error(`Node ${node.id} has malformed source-handle identities`)
  }
  return entries.map(([handle]) => handle)
}

type SubmodelsLike = Record<string, unknown> | undefined

export type SubmodelGraphLike = {
  nodes: SimpleNode[]
  edges: SimpleEdge[]
}

type CanonicalSubmodelBoundary = {
  definition: SubmodelDefinition
  graph: SubmodelGraphLike
}

/** Read a graph only from a complete canonical submodel definition. */
export function submodelGraphFromMetadata(value: unknown): SubmodelGraphLike | undefined {
  if (!isSubmodelDefinition(value)) return undefined
  return {
    nodes: value.graph.nodes as unknown as SimpleNode[],
    edges: value.graph.edges as SimpleEdge[],
  }
}

function canonicalSubmodelBoundary(
  boundaryNode: SimpleNode,
  submodels: SubmodelsLike,
): CanonicalSubmodelBoundary {
  const config = boundaryNode.data.config
  if (!isSubmodelInstanceConfig(config)) {
    throw new Error(
      "Cannot resolve submodel instance " + boundaryNode.id
      + ": canonical occurrence config is malformed",
    )
  }
  const definition = submodels?.[config.definitionId]
  if (!isSubmodelDefinition(definition, config.definitionId)) {
    throw new Error(
      "Cannot resolve submodel instance " + boundaryNode.id
      + ": definition " + config.definitionId + " is missing or malformed",
    )
  }
  return {
    definition,
    graph: {
      nodes: definition.graph.nodes as unknown as SimpleNode[],
      edges: definition.graph.edges as SimpleEdge[],
    },
  }
}

/**
 * Resolve a synthetic submodel boundary handle to every declared child
 * endpoint. Canonical input ports may fan out.
 */
export function resolveSubmodelBoundaryNodes(
  boundaryNode: SimpleNode,
  handle: string | null | undefined,
  direction: "in" | "out",
  submodels: SubmodelsLike,
): SimpleNode[] {
  if (boundaryNode.data.nodeType !== NODE_TYPES.SUBMODEL) return []

  const prefix = direction + "__"
  const canonical = canonicalSubmodelBoundary(boundaryNode, submodels)
  if (!handle?.startsWith(prefix) || handle.length === prefix.length) {
    throw new Error(
      "Cannot resolve " + direction + "put handle " + String(handle)
      + " for submodel instance " + boundaryNode.id
      + ": expected " + prefix + "<portId>",
    )
  }
  const portId = handle.slice(prefix.length)
  if (direction === "in") {
    const port = canonical.definition.inputPorts.find(
      (candidate) => candidate.portId === portId,
    )
    if (!port) {
      throw new Error(
        "Cannot resolve input handle " + handle + " for submodel instance "
        + boundaryNode.id + ": public port " + portId + " is missing",
      )
    }
    return port.targets.map((endpoint) => {
      const child = canonical.graph.nodes.find((node) => node.id === endpoint.nodeId)
      if (!child) {
        throw new Error(
          "Cannot resolve input handle " + handle + " for submodel instance "
          + boundaryNode.id + ": child " + endpoint.nodeId + " is missing",
        )
      }
      return child
    })
  }

  const port = canonical.definition.outputPorts.find(
    (candidate) => candidate.portId === portId,
  )
  if (!port) {
    throw new Error(
      "Cannot resolve output handle " + handle + " for submodel instance "
      + boundaryNode.id + ": public port " + portId + " is missing",
    )
  }
  const child = canonical.graph.nodes.find(
    (node) => node.id === port.source.nodeId,
  )
  if (!child) {
    throw new Error(
      "Cannot resolve output handle " + handle + " for submodel instance "
      + boundaryNode.id + ": child " + port.source.nodeId + " is missing",
    )
  }
  return [child]
}
/** Resolve a boundary handle only when it has exactly one child endpoint. */
export function resolveSubmodelBoundaryNode(
  boundaryNode: SimpleNode,
  handle: string | null | undefined,
  direction: "in" | "out",
  submodels: SubmodelsLike,
): SimpleNode | undefined {
  const children = resolveSubmodelBoundaryNodes(
    boundaryNode,
    handle,
    direction,
    submodels,
  )
  if (children.length > 1) {
    throw new Error(
      "Cannot resolve " + direction + "put handle " + String(handle)
      + " for submodel instance " + boundaryNode.id
      + ": public port fans out to " + children.length + " children",
    )
  }
  return children[0]
}

/**
 * Derive the executable input name for one edge.
 *
 * API-input frame handles are already canonical names and are returned
 * verbatim, including a stale non-null handle so the UI can identify the
 * unresolved edge. Ordinary sources consume authoritative backend identity
 * metadata. A submodel output is named from its stable alias plus public port
 * id.
 */
export const UNRESOLVED_INPUT_NAME = "<unresolved>"

function sourceHandleInputName(
  edge: SimpleEdge,
  sourceNode: SimpleNode,
): string {
  const handle = edge.sourceHandle
  if (handle === null || handle === undefined) return UNRESOLVED_INPUT_NAME
  const mappings = sourceNode.data._sourceHandleInputNames
  if (typeof mappings !== "object" || mappings === null || Array.isArray(mappings)) {
    throw new Error(
      `Cannot resolve input name for edge ${edge.id}: source ${sourceNode.id} has no authoritative handle identities`,
    )
  }
  const inputName = (mappings as Record<string, unknown>)[handle]
  if (typeof inputName !== "string" || inputName.length === 0) {
    return UNRESOLVED_INPUT_NAME
  }
  return inputName
}

export function edgeInputName(
  edge: SimpleEdge,
  sourceNode: SimpleNode,
  submodels: SubmodelsLike = undefined,
): string {
  const edgeInput = edge.data?._inputName
  if (typeof edgeInput === "string" && edgeInput.length > 0) return edgeInput
  if (sourceNode.data.nodeType === NODE_TYPES.API_INPUT) {
    return sourceHandleInputName(edge, sourceNode)
  }
  if (sourceNode.data.nodeType === NODE_TYPES.SUBMODEL_PORT) {
    const boundary = sourceNode.data as Partial<SubmodelPortData>
    if (boundary.portDirection !== "input" || !Array.isArray(boundary.ports)) {
      throw new Error(
        "Cannot derive input name for edge " + edge.id + ": source "
        + sourceNode.id + " is not a valid submodel Input",
      )
    }
    const port = boundary.ports.find(
      (candidate) => candidate.id === edge.sourceHandle,
    )
    if (!port || typeof port.label !== "string" || typeof port.id !== "string") {
      throw new Error(
        "Cannot derive input name for edge " + edge.id + ": submodel Input row "
        + String(edge.sourceHandle) + " is missing",
      )
    }
    if (
      typeof boundary.instanceId !== "string"
      || boundary.instanceId.length === 0
      || boundary.instanceId.trim() !== boundary.instanceId
      || typeof boundary.definitionId !== "string"
      || boundary.definitionId.length === 0
      || boundary.definitionId.trim() !== boundary.definitionId
    ) {
      throw new Error(
        "Cannot derive input name for edge " + edge.id
        + ": canonical submodel Input identity is malformed",
      )
    }
    return sourceHandleInputName(edge, sourceNode)
  }
  if (sourceNode.data.nodeType === NODE_TYPES.SUBMODEL) {
    if (!isSubmodelInstanceConfig(sourceNode.data.config)) {
      throw new Error(
        "Cannot derive input name for edge " + edge.id
        + ": submodel instance " + sourceNode.id + " has malformed identity config",
      )
    }
    resolveSubmodelBoundaryNodes(sourceNode, edge.sourceHandle, "out", submodels)
    return sourceHandleInputName(edge, sourceNode)
  }
  const defaultInputName = sourceNode.data._defaultInputName
  if (typeof defaultInputName !== "string" || defaultInputName.length === 0) {
    throw new Error(
      `Cannot resolve input name for edge ${edge.id}: source ${sourceNode.id} has no authoritative default input identity`,
    )
  }
  return defaultInputName
}
/**
 * Derive every incoming input name for one executable target. `boundaryNodeId`
 * identifies the visible target when the executable target is a child behind
 * a public submodel input handle. Definitions resolve the public port to an
 * internal endpoint. The same helper is used by drag-time and rename preflight
 * so boundary and ordinary edges cannot drift apart.
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
    let canonicalPortId: string | undefined
    if (edge.target !== targetNodeId) {
      if (
        edge.target !== boundaryNodeId
        || boundaryNode?.data.nodeType !== NODE_TYPES.SUBMODEL
      ) continue
      const targets = resolveSubmodelBoundaryNodes(
        boundaryNode,
        edge.targetHandle,
        "in",
        submodels,
      )
      if (!targets.some((target) => target.id === targetNodeId)) continue
      canonicalPortId = edge.targetHandle?.slice("in__".length)
    }
    if (canonicalPortId) {
      const instanceConfig = boundaryNode?.data.config
      if (!isSubmodelInstanceConfig(instanceConfig)) {
        throw new Error(`Cannot resolve input name: submodel ${boundaryNodeId} has malformed identity`)
      }
      const definition = submodels?.[instanceConfig.definitionId]
      if (!isSubmodelDefinition(definition, instanceConfig.definitionId)) {
        throw new Error(`Cannot resolve input name: submodel definition ${instanceConfig.definitionId} is unavailable`)
      }
      const inputName = definition._inputPortInputNames?.[canonicalPortId]
      if (typeof inputName !== "string" || inputName.length === 0) {
        throw new Error(
          `Cannot resolve input name: public input ${canonicalPortId} has no authoritative identity`,
        )
      }
      names.push(inputName)
      continue
    }
    const sourceNode = nodesById.get(edge.source)
    if (!sourceNode) {
      throw new Error("Cannot derive input name: source node " + edge.source + " is missing")
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
// load-bearing for backend compatibility: Python regexes operate on code points, and
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
  reservedLabels: ReadonlySet<string>,
): ApiInputLabelIssue | null {
  if (!candidate.trim()) return { kind: "blank" }
  if (!ASCII_IDENTIFIER_RE.test(candidate)) {
    return { kind: "identifier", reason: "ascii" }
  }
  if (reservedLabels.has(candidate)) {
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
 * legitimately carry, given its config.
 */
export function validSourceHandleKeys(
  config: ConfigLike,
  reservedLabels: ReadonlySet<string>,
): Set<string> {
  return new Set(apiInputFrameLabels(config, reservedLabels))
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
  reservedLabels,
}: {
  nodeId: string
  config: ConfigLike
  edges: E[]
  reservedLabels: ReadonlySet<string>
}): ReconcileApiInputEdgesResult<E> {
  const validKeys = validSourceHandleKeys(config, reservedLabels)
  const removed: ReconciledApiInputEdge[] = []
  const kept: E[] = []
  for (const edge of edges) {
    if (edge.source !== nodeId) {
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
function portOwnership(
  config: ConfigLike,
  reservedLabels: ReadonlySet<string>,
): {
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
    if (!isValidFrameLabel(label, reservedLabels)) return
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
  reservedLabels,
}: {
  nodeId: string
  prevConfig: ConfigLike
  nextConfig: ConfigLike
  edges: E[]
  reservedLabels: ReadonlySet<string>
}): MigrateApiInputEdgesResult<E> {
  const prevTables = rawTables(prevConfig)
  const nextTables = rawTables(nextConfig)
  if (prevTables.length === 0 || prevTables.length !== nextTables.length) {
    return { edges, rebound: [] }
  }

  const prevOwnership = portOwnership(prevConfig, reservedLabels)
  const nextOwnership = portOwnership(nextConfig, reservedLabels)

  const renameByOldLabel = new Map<string, string>()
  for (let i = 0; i < prevTables.length; i++) {
    const prevTable = tableAt(prevTables, i)
    const nextTable = tableAt(nextTables, i)
    if (!prevTable || !nextTable) continue
    const prevLabel = rawLabel(prevTable)
    const nextLabel = rawLabel(nextTable)
    if (
      !isValidFrameLabel(prevLabel, reservedLabels)
      || !isValidFrameLabel(nextLabel, reservedLabels)
    ) continue
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
    return {
      ...edge,
      sourceHandle: to,
      data: { ...edge.data, _inputName: to },
    }
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
  reservedLabels,
}: {
  nodeId: string
  prevConfig: ConfigLike
  nextConfig: ConfigLike
  edges: E[]
  reservedLabels: ReadonlySet<string>
}): ApplyApiInputConfigChangeResult<E> {
  const migration = migrateApiInputEdges({
    nodeId,
    prevConfig,
    nextConfig,
    edges,
    reservedLabels,
  })
  const { edges: pruned, removed } = reconcileApiInputEdges({
    nodeId,
    config: nextConfig,
    edges: migration.edges,
    reservedLabels,
  })
  return { edges: pruned, rebound: migration.rebound, removed }
}
