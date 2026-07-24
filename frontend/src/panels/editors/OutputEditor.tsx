import {
  useMemo,
  useState,
  useCallback,
  useLayoutEffect,
  useRef,
  type CSSProperties,
} from "react"
import { ChevronRight, ChevronDown, Plus, X, Wand2, Pencil, Check, AlertTriangle } from "lucide-react"
import type { OnUpdateConfig, SimpleNode, SimpleEdge } from "./_shared"
import { EditorLabel } from "../../components/form"
import { useGraph } from "../useGraph"
import { buildGraph } from "../../utils/buildGraph"
import useSettingsStore from "../../stores/useSettingsStore"
import { outputAssembleDryRun, previewNode, ApiError } from "../../api/client"
import { FrameTableActions } from "./FrameTableActions"
import { JsonPreview } from "./JsonPreview"
import {
  substitutePrefix,
  commonRootPath,
  dropMappingHeader,
} from "./outputPathTools"
import { nonCanonicalHint, nonCanonicalNote } from "./pathCanonicalWarning"
import { NODE_TYPES } from "../../utils/nodeTypes"
import { apiInputFrameLabels, edgeInputName } from "../../utils/apiInputPorts"

// ─── Preview chunk size ───────────────────────────────────────────
//
// The OUTPUT editor's two JSON previews (the assembled-output document and each
// frame's input rows) are capped to this many rows/lines so a large source can
// never dump thousands of rows into the panel. It is the "chunk size" for
// previews: the backend dry-run + per-frame previewNode are both requested with
// this row limit, AND the rendered rows are sliced to it (with a "showing N of
// M" note when the source had more). There is no pre-existing editor-level
// preview-row constant to reuse (the settings store's `rowLimit` is the
// pipeline-execution preview limit, default 100); 50 keeps the OUTPUT preview
// cheap and legible without coupling to that user-tunable value.
const PREVIEW_ROW_LIMIT = 50
import {
  classifyConfig,
  emptyV2,
  migrateV1,
  writeV2,
  validateOutputPath,
  type OutputConfigV2,
  type OutputMappingEntryV2,
  type OutputRowStatus,
} from "./outputMappingSchema"

// ─── Frame identity + columns ─────────────────────────────────────
//
// One BLOCK per incoming edge = per source FRAME. The frame's identity is the
// edge's `sourceHandle` (the apiInput table label / per-port handle id);
// single-frame sources have a null handle, in which case the frame resolves to
// the sanitised source-node label — exactly what the backend executor uses as
// the positional frame key (`edge.sourceHandle or sanitize(node-label)`, see
// `_execute_lazy.py` build_node_fns + `_graph_utils.py::_sanitize_func_name`).
// The user-facing name still shows the raw label.

/**
 * The persisted `source_port` for an edge — the backend's frame key:
 *   - its `sourceHandle` (multi-frame apiInput / per-table handle), else
 *   - `sanitizeName(source node label)` (single-frame source, null handle).
 * NEVER "" for a resolvable edge: two distinct single-frame sources must persist
 * DISTINCT, non-empty ports so a genuine >=2-frame OUTPUT binds (the old `""`
 * fallback collapsed them and tripped `OutputMappingSchemaError`). Falls back
 * to the sanitised source-node id when the label is missing.
 */
function framePortId(
  edge: SimpleEdge,
  sourceNode: SimpleNode | undefined,
  submodels?: Record<string, unknown>,
): string {
  if (!sourceNode) {
    throw new Error(`Cannot derive output frame name for edge ${edge.id}: source node ${edge.source} is missing`)
  }
  return edgeInputName(edge, sourceNode, submodels)
}

function frameIsUnresolved(edge: SimpleEdge, sourceNode: SimpleNode | undefined): boolean {
  return sourceNode?.data.nodeType === NODE_TYPES.API_INPUT
    && (edge.sourceHandle === null
      || edge.sourceHandle === undefined
      || !apiInputFrameLabels(sourceNode.data.config).includes(edge.sourceHandle))
}

/**
 * Columns available for a frame.
 *
 * For an apiInput source with a v2 `tables` config, the frame's columns come
 * STRAIGHT from the config (no preview/run needed) — so Infer and the source
 * dropdown work the moment a table is inferred:
 *   - multi-frame: the emitted table whose `label === edge.sourceHandle`;
 *   - single-frame (null sourceHandle): the sole emit-true table.
 * Only `selected` columns are returned, matching what the frame actually emits
 * at runtime (an unselected column is not in the frame, so mapping it would
 * fail the upstream-column contract). For a NON-apiInput source (transform,
 * dataInput, …) there is no `tables` config, so fall back to the node's
 * `_columns` (populated by preview/run).
 *
 * SHAPE NOTE: this helper is deliberately the only place that derives a frame's
 * column set. A future backend per-frame schema endpoint can replace the BODY
 * here without touching any caller — callers only ever see `string[]`.
 */
function frameColumns(edge: SimpleEdge, sourceNode: SimpleNode | undefined): string[] {
  if (!sourceNode) return []
  const data = sourceNode.data as Record<string, unknown>
  const cfg = data.config as Record<string, unknown> | undefined
  const tables = cfg && Array.isArray(cfg.tables) ? (cfg.tables as unknown[]) : null

  // apiInput v2: derive from the config table for this frame.
  if (tables) {
    const objs = tables.filter(
      (t): t is Record<string, unknown> => !!t && typeof t === "object",
    )
    // Multi-frame matches by handle; single-frame (null handle) is the sole
    // emit-true table.
    const table = edge.sourceHandle
      ? objs.find((t) => t.label === edge.sourceHandle)
      : objs.find((t) => t.emit === true)
    if (table && Array.isArray(table.columns)) {
      return (table.columns as unknown[])
        .filter(
          (c): c is Record<string, unknown> =>
            !!c && typeof c === "object" && (c as Record<string, unknown>).selected !== false,
        )
        .map((c) => c.name)
        .filter((n): n is string => typeof n === "string")
    }
    // apiInput config present but no matching emit table → nothing to surface
    // (best-effort; the backend is the authority).
    return []
  }

  // Non-apiInput source: cached columns from preview/run.
  const cols = data._columns as { name: string }[] | undefined
  if (Array.isArray(cols)) {
    return cols.map((c) => c.name).filter((n): n is string => typeof n === "string")
  }
  return []
}

/**
 * The same frame column set as `frameColumns`, but carrying each column's TYPE
 * for the read-only INPUT-SCHEMA view at the top of the editor. Sources mirror
 * `frameColumns` exactly — only the shape differs (`{name, type}` here vs
 * `string` there):
 *   - apiInput v2 (`config.tables`): the matching emit table's `columns` are
 *     `[{name, type, selected, ...}]` — keep `selected !== false`, surface
 *     `name` + `type`;
 *   - non-apiInput: `_columns` are `[{name, dtype}]` — surface `name` + `dtype`.
 * A missing/unknown type renders as an empty string (the row still shows its
 * name). Like `frameColumns`, this is the single place that derives a frame's
 * typed schema; a future backend per-frame schema endpoint can replace the body.
 */
function frameSchemaColumns(
  edge: SimpleEdge,
  sourceNode: SimpleNode | undefined,
): { name: string; type: string }[] {
  if (!sourceNode) return []
  const data = sourceNode.data as Record<string, unknown>
  const cfg = data.config as Record<string, unknown> | undefined
  const tables = cfg && Array.isArray(cfg.tables) ? (cfg.tables as unknown[]) : null

  // apiInput v2: derive from the config table for this frame.
  if (tables) {
    const objs = tables.filter(
      (t): t is Record<string, unknown> => !!t && typeof t === "object",
    )
    const table = edge.sourceHandle
      ? objs.find((t) => t.label === edge.sourceHandle)
      : objs.find((t) => t.emit === true)
    if (table && Array.isArray(table.columns)) {
      return (table.columns as unknown[])
        .filter(
          (c): c is Record<string, unknown> =>
            !!c && typeof c === "object" && (c as Record<string, unknown>).selected !== false,
        )
        .map((c) => ({
          name: typeof c.name === "string" ? c.name : "",
          type: typeof c.type === "string" ? c.type : "",
        }))
        .filter((c) => c.name !== "")
    }
    return []
  }

  // Non-apiInput source: cached columns (`{name, dtype}`) from preview/run.
  const cols = data._columns as { name?: unknown; dtype?: unknown }[] | undefined
  if (Array.isArray(cols)) {
    return cols
      .map((c) => ({
        name: typeof c.name === "string" ? c.name : "",
        type: typeof c.dtype === "string" ? c.dtype : "",
      }))
      .filter((c) => c.name !== "")
  }
  return []
}

/**
 * Classify a frame's source for the per-frame DATA preview.
 *
 * `previewNode` now accepts a `port_label` (the frame's `sourceHandle`), so the
 * route returns THAT frame's rows for a multi-frame apiInput — not just the
 * first frame. `previewFrameData` passes the handle, so the common case is
 * fully resolvable and needs no caveat. The ONE case that still can't be
 * resolved: a multi-frame source where this edge's `sourceHandle` matches none
 * of the source's emit-true labels (a dangling handle). The backend then
 * degrades to the first frame, so the preview may not be this frame's rows.
 *
 * Returns:
 *   - `multiFrame`: the source emits 2+ frames (an apiInput with 2+ emit tables);
 *   - `resolvable`: this edge's frame can be selected on the source (its handle
 *     names an actual emit-true table), so the preview is genuinely this frame.
 * For a single-frame source `multiFrame` is false and `resolvable` is true.
 */
function frameSourceKind(
  edge: SimpleEdge,
  sourceNode: SimpleNode | undefined,
): { multiFrame: boolean; resolvable: boolean } {
  if (!sourceNode) return { multiFrame: false, resolvable: true }
  const cfg = (sourceNode.data as Record<string, unknown>).config as
    | Record<string, unknown>
    | undefined
  const tables = cfg && Array.isArray(cfg.tables) ? (cfg.tables as unknown[]) : null
  if (!tables) return { multiFrame: false, resolvable: true }
  const emitLabels = tables
    .filter((t): t is Record<string, unknown> => !!t && typeof t === "object")
    .filter((t) => t.emit === true)
    .map((t) => t.label)
    .filter((l): l is string => typeof l === "string")
  const multiFrame = emitLabels.length >= 2
  if (!multiFrame) return { multiFrame: false, resolvable: true }
  // Multi-frame: the frame resolves when its handle names a real emit table —
  // then `port_label` selects it and the preview is this frame's own rows. A
  // handle that names no emit table is a dangling frame; the backend falls back
  // to the first frame, so the preview may not match (keep the caveat).
  const resolvable = edge.sourceHandle != null && emitLabels.includes(edge.sourceHandle)
  return { multiFrame, resolvable }
}

// ─── Editor-only row status ───────────────────────────────────────
//
// "Inferred" vs "Confirmed" is tracked in EDITOR STATE ONLY — it is never
// persisted (writeV2 emits only the four entry fields). Auto-mapped rows start
// Inferred (pilled); editing a row's column or path flips it to Confirmed.
//
// Status is keyed by a row's ABSOLUTE index into `outputMapping` — the SAME
// coordinate the rows render and reconcile by (`key={r.index}`). Keying by the
// per-frame LOCAL index (the old bug) desynced the pill from its row whenever
// an earlier row shifted the locals. Removing a row compacts the array, so
// `removeRow` REMAPS this map (drop the removed key, slide higher keys down)
// so a surviving Inferred pill follows its row rather than smearing onto a
// neighbour. Appends land at the end (`outputMapping.length`) and need no shift.

type RowStatusMap = Record<number, OutputRowStatus>

/**
 * Re-key a status map after the entry at `removedIndex` is deleted from
 * `outputMapping` (which compacts every higher index down by one). Drops the
 * removed entry's status and slides each higher key down so status keeps
 * tracking the row it belongs to.
 */
function remapStatusAfterRemoval(prev: RowStatusMap, removedIndex: number): RowStatusMap {
  const next: RowStatusMap = {}
  for (const [k, status] of Object.entries(prev)) {
    const idx = Number(k)
    if (idx === removedIndex) continue
    next[idx > removedIndex ? idx - 1 : idx] = status
  }
  return next
}

// ─── OutputEditor ─────────────────────────────────────────────────

export default function OutputEditor({
  config,
  onUpdate,
  nodeId,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  nodeId: string
}) {
  const { allNodes, edges, submodels, preamble } = useGraph()

  // Incoming edges = the frames mapped into the response. One block each.
  const incomingEdges = useMemo(
    () => edges.filter((e) => e.target === nodeId),
    [edges, nodeId],
  )
  const nodeById = useMemo(
    () => Object.fromEntries(allNodes.map((n) => [n.id, n])),
    [allNodes],
  )

  // Per-edge resolved frame port + label, computed once so the render, the
  // duplicate-port guard, and the v1 migration all agree on one derivation.
  const frames = useMemo(
    () =>
      incomingEdges.map((edge) => {
        const sourceNode = nodeById[edge.source]
        const name = framePortId(edge, sourceNode, submodels)
        return {
          edge,
          sourceNode,
          parentLabel: sourceNode.data.label,
          port: name,
          label: name,
          frameUnresolved: frameIsUnresolved(edge, sourceNode),
          columns: frameColumns(edge, sourceNode),
        }
      }),
    [incomingEdges, nodeById, submodels],
  )

  // Per-frame INPUT schema (columns + types) for the expandable "Frames (N)"
  // overview — read-only, sourced exactly like `frames.columns` but typed (see
  // `frameSchemaColumns`). One entry per incoming frame, ordered with `frames`.
  const framesSchema = useMemo(
    () =>
      frames.map((f) => ({
        label: f.label,
        columns: frameSchemaColumns(f.edge, f.sourceNode),
      })),
    [frames],
  )

  // Two incoming frames that resolve to the SAME `source_port` would collide on
  // disk (their rows merge into one frame, and a genuine multi-frame OUTPUT
  // would mis-bind). Detect it and block with a banner — the user must
  // disambiguate (rename a source / give the apiInput tables distinct labels).
  const duplicatePorts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const f of frames) counts.set(f.port, (counts.get(f.port) ?? 0) + 1)
    return [...counts.entries()].filter(([, n]) => n > 1).map(([p]) => p)
  }, [frames])

  // Classify the on-disk config. v1 → migration banner + live working copy
  // (the migrated v2 is what we render and what the first Save persists).
  const shape = useMemo(() => classifyConfig(config), [config])
  const isV1 = shape.kind === "v1"
  const v2: OutputConfigV2 = useMemo(() => {
    if (shape.kind === "v2") return shape.v2
    if (shape.kind === "v1") {
      // v1 is the legacy single-frame shape; resolve the lone incoming edge's
      // frame id (handle, else sanitised source label) so the migrated rows
      // persist the SAME `source_port` the backend will key by. Fall back to
      // "" only when no edge is wired (the backend n==1 rescue then binds it).
      const frameId = frames.length === 1 ? frames[0].port : ""
      return migrateV1(config, frameId)
    }
    return emptyV2()
  }, [shape, config, frames])

  // Editor-only row status (Inferred pill). Never persisted. Keyed by ABSOLUTE
  // index into v2.outputMapping.
  const [rowStatus, setRowStatus] = useState<RowStatusMap>({})
  // Which frame blocks are expanded — keyed by EDGE id (stable per incoming
  // edge), not the resolved port, so two frames resolving to the same port
  // never share expand/collapse state.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  // Whether the v1→v2 migration banner has been dismissed by a Save.
  const [migrated, setMigrated] = useState(false)
  // Whether the top "Frames (N)" table is expanded to show each frame's
  // read-only input schema (columns + types). Default collapsed.
  const [framesExpanded, setFramesExpanded] = useState(false)

  const toggleExpanded = useCallback((edgeId: string) => {
    setExpanded((prev) => ({ ...prev, [edgeId]: !prev[edgeId] }))
  }, [])

  // Push the whole v2 config back through onUpdate — the same mechanism
  // ApiInputEditor uses (writeV2 of the live working copy).
  const writeBack = useCallback(
    (next: OutputConfigV2) => {
      onUpdate(writeV2(next))
      if (isV1) setMigrated(true)
    },
    [onUpdate, isV1],
  )

  // ─── Assembled-output preview ───────────────────────────────────
  //
  // POSTs the CURRENT (unsaved) editor mapping to the dry-run route and renders
  // the assembled response document. The route swaps `output_mapping` into the
  // node's config (overriding disk), so this reflects in-editor edits, not the
  // saved file. Capped to PREVIEW_ROW_LIMIT source rows; document rows beyond
  // the cap render with a "showing N of M" note rather than silently truncating.
  const [outputPreviewOpen, setOutputPreviewOpen] = useState(false)
  const [outputDoc, setOutputDoc] = useState<unknown[] | null>(null)
  // The pre-cap document length (the dry-run's `row_count`), so a capped doc can
  // show "showing N of M".
  const [outputDocTotal, setOutputDocTotal] = useState<number>(0)
  const [outputLoading, setOutputLoading] = useState(false)
  const [outputError, setOutputError] = useState<string | null>(null)
  const outputReqSeq = useRef(0)

  const runOutputPreview = useCallback(() => {
    const reqId = ++outputReqSeq.current
    setOutputLoading(true)
    setOutputError(null)
    const graph = buildGraph(allNodes, edges, submodels, preamble)
    outputAssembleDryRun({
      graph,
      nodeId,
      // The live working copy's mapping — the editor's CURRENT (unsaved) state.
      outputMapping: writeV2(v2).outputMapping as Array<Record<string, unknown>>,
      outputFormat: v2.outputFormat || "json",
      rowLimit: PREVIEW_ROW_LIMIT,
      source: useSettingsStore.getState().activeSource,
    })
      .then((res) => {
        if (outputReqSeq.current !== reqId) return
        if (res.status !== "ok") {
          // A run that completed but the node errored (200 + status:"error").
          setOutputDoc(null)
          setOutputError(res.error || "Assembly failed")
        } else {
          setOutputDoc(Array.isArray(res.document) ? res.document : [])
          setOutputDocTotal(
            typeof res.row_count === "number" && res.row_count > 0
              ? res.row_count
              : Array.isArray(res.document)
                ? res.document.length
                : 0,
          )
        }
        setOutputLoading(false)
      })
      .catch((err: unknown) => {
        if (outputReqSeq.current !== reqId) return
        // The route returns structured 422/400/404/503/504/500 — surface the
        // detail message (ApiError carries it) rather than a bare status.
        const message =
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Output preview failed"
        setOutputDoc(null)
        setOutputError(message)
        setOutputLoading(false)
      })
  }, [allNodes, edges, submodels, preamble, nodeId, v2])

  // Expanding the preview for the first time (no doc yet, not already loading)
  // kicks off a run; the refresh button re-runs on demand.
  const toggleOutputPreview = useCallback(() => {
    setOutputPreviewOpen((open) => {
      const next = !open
      if (next && outputDoc === null && !outputLoading && outputError === null) {
        runOutputPreview()
      }
      return next
    })
  }, [outputDoc, outputLoading, outputError, runOutputPreview])

  // Rows actually rendered — capped to PREVIEW_ROW_LIMIT (the doc may legally be
  // longer than the source-row cap once frames fan out).
  const outputDocRows = useMemo(
    () => (outputDoc ? outputDoc.slice(0, PREVIEW_ROW_LIMIT) : []),
    [outputDoc],
  )

  // ─── Per-frame input-data preview ───────────────────────────────
  //
  // Preview a frame's INPUT rows by previewing its upstream SOURCE node (the
  // best available data path — there is no per-frame row endpoint, see
  // `frameSourceKind`). Projects to the frame's column set. Returns the source's
  // preview rows + the pre-cap total (so JsonPreview shows "showing N of M").
  const previewFrameData = useCallback(
    async (
      edge: SimpleEdge,
      columns: string[],
    ): Promise<{ rows: Record<string, unknown>[]; total: number }> => {
      const sourceNode = nodeById[edge.source]
      if (!sourceNode) throw new ApiError("Frame source node not found", 404)
      const graph = buildGraph(allNodes, edges, submodels, preamble)
      const res = await previewNode({
        graph,
        nodeId: edge.source,
        rowLimit: PREVIEW_ROW_LIMIT,
        source: useSettingsStore.getState().activeSource,
        // Project to this frame's columns where known, so the preview shows the
        // frame's fields (best-effort; the backend tolerates a superset).
        requestedPreviewColumns: columns.length > 0 ? columns : undefined,
        // Select THIS frame from a multi-frame source via its handle, so the
        // preview shows the frame's OWN rows rather than the source's first
        // frame. A null handle (single-frame source) omits it → first frame,
        // which IS the frame. An unknown handle degrades to the first frame
        // backend-side (the `frameSourceKind` caveat covers that case).
        portLabel: edge.sourceHandle ?? undefined,
      })
      if (res.status !== "ok") {
        throw new ApiError(res.error || "Frame preview failed", 422, res.error ?? undefined)
      }
      const rows = (res.preview ?? []).slice(0, PREVIEW_ROW_LIMIT)
      const total =
        typeof res.preview_row_count === "number" && res.preview_row_count > 0
          ? res.preview_row_count
          : (res.preview ?? []).length
      return { rows, total }
    },
    [allNodes, edges, submodels, preamble, nodeById],
  )

  // Rows for a given frame, with their absolute index into v2.outputMapping so
  // mutations + row status address the right entry.
  const rowsForPort = useCallback(
    (port: string) =>
      v2.outputMapping
        .map((entry, index) => ({ entry, index }))
        .filter((r) => r.entry.source_port === port),
    [v2.outputMapping],
  )

  const setEntry = useCallback(
    (absIndex: number, patch: Partial<OutputMappingEntryV2>) => {
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: v2.outputMapping.map((e, i) => (i === absIndex ? { ...e, ...patch } : e)),
      }
      writeBack(next)
    },
    [v2, writeBack],
  )

  const markConfirmed = useCallback((absIndex: number) => {
    setRowStatus((prev) => ({ ...prev, [absIndex]: "Confirmed" }))
  }, [])

  const addRow = useCallback(
    (port: string) => {
      // The new entry is appended, so its absolute index is the current length.
      const newAbsIndex = v2.outputMapping.length
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: [
          ...v2.outputMapping,
          { source_port: port, source_column: "", output_path: "", enabled: true },
        ],
      }
      // A blank Add-row is Confirmed (the user is authoring it deliberately).
      setRowStatus((prev) => ({ ...prev, [newAbsIndex]: "Confirmed" }))
      writeBack(next)
    },
    [v2, writeBack],
  )

  const removeRow = useCallback(
    (absIndex: number) => {
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: v2.outputMapping.filter((_, i) => i !== absIndex),
      }
      // The array compacts on removal, so re-key row status to match.
      setRowStatus((prev) => remapStatusAfterRemoval(prev, absIndex))
      writeBack(next)
    },
    [v2, writeBack],
  )

  /**
   * CLEAR: drop EVERY row of the frame `port` in one writeBack (rows of other
   * frames are untouched). The array compacts to just the survivors, so re-key
   * row status the same way `pasteRowsForPort` does — carry each surviving
   * (other-frame) row's status across to its new front index; the cleared
   * frame's status keys are dropped entirely.
   */
  const clearFrame = useCallback(
    (port: string) => {
      const survivors = v2.outputMapping
        .map((e, i) => ({ e, i }))
        .filter((x) => x.e.source_port !== port)
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: survivors.map((x) => x.e),
      }
      setRowStatus((prev) => {
        const m: RowStatusMap = {}
        survivors.forEach((x, newIdx) => {
          if (prev[x.i] !== undefined) m[newIdx] = prev[x.i]
        })
        return m
      })
      writeBack(next)
    },
    [v2, writeBack],
  )

  /**
   * PATH-EDIT apply: for the frame `port`, rewrite every row whose
   * `output_path` starts with `oldPrefix` so that prefix becomes `newPrefix`.
   * One writeBack for the whole frame; row status is left untouched (a
   * mass-edit doesn't re-infer). Only rows of this port are touched.
   */
  const applyPathSubstitution = useCallback(
    (port: string, oldPrefix: string, newPrefix: string) => {
      if (oldPrefix === newPrefix) return
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: v2.outputMapping.map((e) =>
          e.source_port === port
            ? { ...e, output_path: substitutePrefix(e.output_path, oldPrefix, newPrefix) }
            : e,
        ),
      }
      writeBack(next)
    },
    [v2, writeBack],
  )

  /**
   * PASTE-IN for a frame's column table. The pasted grid is tab-separated
   * `column<TAB>output_path[<TAB>enabled]` rows (the same shape Copy emits). A
   * recognised header row (`column`/`source_column` + `path`/`output_path`) is
   * dropped. Rows REPLACE this frame's existing rows wholesale; rows for other
   * frames are untouched. Editor row-status for the frame is reset (pasted rows
   * are author-confirmed). Blank/columns-only rows are skipped.
   */
  const pasteRowsForPort = useCallback(
    (port: string, grid: string[][]) => {
      const body = dropMappingHeader(grid)
      const pasted: OutputMappingEntryV2[] = []
      for (const cells of body) {
        const source_column = (cells[0] ?? "").trim()
        const output_path = (cells[1] ?? "").trim()
        if (source_column === "" && output_path === "") continue
        const enabledCell = (cells[2] ?? "").trim().toLowerCase()
        const enabled = enabledCell === "" ? true : enabledCell !== "false" && enabledCell !== "0" && enabledCell !== "no"
        pasted.push({ source_port: port, source_column, output_path, enabled })
      }
      // Keep other frames in place; replace this frame's rows with the pasted
      // set. Preserve relative ordering: other frames first, then this frame's
      // new rows at the end (absolute indices for this frame thus reset).
      const others = v2.outputMapping
        .map((e, i) => ({ e, i }))
        .filter((x) => x.e.source_port !== port)
      const next: OutputConfigV2 = {
        ...v2,
        outputMapping: [...others.map((x) => x.e), ...pasted],
      }
      // Re-key status: `others` keep their relative order at the new front
      // indices (0..others.length-1); carry each surviving row's status across
      // from its old absolute index so sibling frames keep their Inferred pills.
      // Pasted rows default to Confirmed by omission.
      setRowStatus((prev) => {
        const m: RowStatusMap = {}
        others.forEach((x, newIdx) => {
          if (prev[x.i] !== undefined) m[newIdx] = prev[x.i]
        })
        return m
      })
      writeBack(next)
    },
    [v2, writeBack],
  )

  const autoMap = useCallback(
    (port: string, columns: string[]) => {
      const existing = new Set(
        v2.outputMapping
          .filter((e) => e.source_port === port)
          .map((e) => e.source_column),
      )
      const fresh = columns.filter((c) => !existing.has(c))
      if (fresh.length === 0) return
      // Additions are appended, so their absolute indices start at the current
      // length and run sequentially.
      const baseAbsIndex = v2.outputMapping.length
      const additions: OutputMappingEntryV2[] = fresh.map((column) => ({
        source_port: port,
        source_column: column,
        output_path: `$[:].${column}`,
        enabled: true,
      }))
      // Auto-mapped rows are Inferred (pilled).
      setRowStatus((prev) => {
        const draft = { ...prev }
        fresh.forEach((_, i) => {
          draft[baseAbsIndex + i] = "Inferred"
        })
        return draft
      })
      writeBack({ ...v2, outputMapping: [...v2.outputMapping, ...additions] })
    },
    [v2, writeBack],
  )

  return (
    <div className="px-4 py-3 space-y-3" data-testid="output-editor">
      {/* RESPONSE CONFIGURATION — its own section, above Response Mapping (a
          peer of it, not a boxed sub-panel). The output format starts at the
          "-- select output format --" placeholder (a disabled, hidden option)
          so a format is never silently chosen. JSON is the only built format
          today; jsonl/jsonseq join the list later. */}
      <div data-testid="output-response-config">
        <EditorLabel className="block" as="div">
          Response configuration
        </EditorLabel>
        <label
          className="mt-1.5 flex items-center gap-1.5 text-[11px]"
          style={{ color: "var(--text-muted)" }}
        >
          Output format
          <select
            data-testid="output-format-select"
            value={v2.outputFormat || ""}
            onChange={(e) => writeBack({ ...v2, outputFormat: e.target.value })}
            className="text-[11px] px-1.5 py-0.5 rounded font-mono"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
          >
            <option value="" disabled hidden>
              -- select output format --
            </option>
            <option value="json">JSON</option>
          </select>
        </label>
      </div>

      <EditorLabel className="block" as="div">
        Response Mapping
      </EditorLabel>

      {/* v1 → v2 migration banner. Shown while the on-disk config is v1 and the
          user hasn't yet saved (which writes migrateV1 and silences it). */}
      {isV1 && !migrated && (
        <div
          data-testid="output-migration-banner"
          className="px-2.5 py-2 rounded-md text-[11px] leading-relaxed"
          style={{
            background: "var(--warning-soft)",
            border: "1px solid var(--warning-border)",
            color: "var(--text-muted)",
          }}
        >
          This OUTPUT node uses the legacy format; saving will convert it to the
          new mapping format.
        </div>
      )}

      {/* Duplicate resolved-port guard. Two incoming frames that resolve to the
          same `source_port` would silently merge on disk; block until the user
          disambiguates. */}
      {duplicatePorts.length > 0 && (
        <div
          data-testid="output-duplicate-port-banner"
          className="px-2.5 py-2 rounded-md text-[11px] leading-relaxed"
          style={{
            background: "var(--danger-soft)",
            border: "1px solid var(--danger-border-strong)",
            color: "var(--danger-text)",
          }}
        >
          Two input frames resolve to the same name ({duplicatePorts.join(", ")})
          and would collide in the response. Give the sources distinct
          names/labels before mapping them.
        </div>
      )}

      {incomingEdges.length === 0 ? (
        <div
          data-testid="output-empty-state"
          className="text-xs py-3"
          style={{ color: "var(--text-muted)" }}
        >
          Connect input frames to map them to the response.
        </div>
      ) : (
        <>
          {/* Top-level FRAMES table: one row per frame, EXPANDABLE to show each
              frame's read-only INPUT SCHEMA (columns + types). The chevron/label
              toggles the schema view; the shared table-actions strip still does
              Copy/Share/Save of the whole frame set. Read-only here — editing
              happens per-frame below — so no Paste-in. */}
          <div
            data-testid="output-frames-table"
            className="rounded-md"
            style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}
          >
            <div className="flex items-center justify-between gap-2 px-2 py-2">
              <button
                data-testid="output-frames-toggle"
                onClick={() => setFramesExpanded((open) => !open)}
                className="flex items-center gap-1 flex-1 min-w-0 text-left"
                title={framesExpanded ? "Collapse input schema" : "Show input schema"}
              >
                {framesExpanded ? (
                  <ChevronDown size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
                ) : (
                  <ChevronRight size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
                )}
                <span className="text-[11px] font-semibold" style={{ color: "var(--text-muted)" }}>
                  Frames ({frames.length})
                </span>
              </button>
              <FrameTableActions
                testIdPrefix="output-frames"
                filename="output-frames"
                pasteable={false}
                getGrid={() => ({
                  headers: ["frame", "rows", "root_path"],
                  rows: frames.map(({ port, label }) => {
                    const portRows = v2.outputMapping.filter((e) => e.source_port === port)
                    return [label, String(portRows.length), commonRootPath(portRows.map((e) => e.output_path))]
                  }),
                })}
                getSchema={() => writeV2(v2)}
              />
            </div>

            {framesExpanded && (
              <div
                data-testid="output-frames-schema"
                className="px-2 pb-2 pt-1.5 space-y-1.5"
                style={{ borderTop: "1px solid var(--border)" }}
              >
                {framesSchema.map((fs, fi) => (
                  <div
                    key={fi}
                    data-testid={`output-frames-schema-${fi}`}
                    className="rounded p-1.5"
                    style={{ border: "1px solid var(--border)", background: "var(--bg-input)" }}
                  >
                    <div
                      className="text-[11px] font-mono font-semibold truncate"
                      style={{ color: "var(--text-primary)" }}
                    >
                      {fs.label}
                    </div>
                    {fs.columns.length === 0 ? (
                      <div className="text-[10px] italic mt-0.5" style={{ color: "var(--text-muted)" }}>
                        No columns available for this frame.
                      </div>
                    ) : (
                      <div className="mt-0.5 space-y-0.5">
                        {fs.columns.map((c) => (
                          <div
                            key={c.name}
                            className="text-[10px] font-mono flex items-baseline gap-2"
                            style={{ color: "var(--text-muted)" }}
                          >
                            <span style={{ color: "var(--text-primary)" }}>{c.name}</span>
                            <span>{c.type}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Assembled-output preview — the whole response document from the
              CURRENT (unsaved) mapping via the dry-run route. Sits above the
              per-frame blocks; expand (or refresh) to (re)run. */}
          <JsonPreview
            testIdPrefix="output-preview"
            title="Output preview"
            rows={outputDocRows}
            totalRows={outputDocTotal}
            filename="output-preview"
            isOpen={outputPreviewOpen}
            onToggle={toggleOutputPreview}
            onRefresh={runOutputPreview}
            loading={outputLoading}
            error={outputError}
            emptyMessage="The assembled document is empty (no enabled rows mapped, or no source rows)."
          />

          <div className="space-y-2">
            {frames.map(({ edge, sourceNode, parentLabel, port, label, frameUnresolved, columns }, ei) => {
              const rows = rowsForPort(port)
              const isOpen = expanded[edge.id] ?? false
              const anyEnabled = rows.some((r) => r.entry.enabled)
              const sourceKind = frameSourceKind(edge, sourceNode)
              return (
                <FrameBlock
                  key={edge.id}
                  testIdPrefix={`output-frame-${ei}`}
                  label={label}
                  parentLabel={parentLabel}
                  frameUnresolved={frameUnresolved}
                  port={port}
                  columns={columns}
                  rows={rows}
                  isOpen={isOpen}
                  frameEnabled={anyEnabled}
                  rowStatus={rowStatus}
                  frameSchema={writeV2({ ...v2, outputMapping: rows.map((r) => r.entry) })}
                  loadFrameData={() => previewFrameData(edge, columns)}
                  frameDataCaveat={
                    sourceKind.multiFrame && !sourceKind.resolvable
                      ? "This frame's handle doesn't match any of the source's emitted frames, so the input preview falls back to the source's first frame and may not match this frame's rows."
                      : null
                  }
                  onToggleExpand={() => toggleExpanded(edge.id)}
                  onToggleFrameEnabled={(on) => {
                    // Per-frame enable toggles every row in the frame at once.
                    const next: OutputConfigV2 = {
                      ...v2,
                      outputMapping: v2.outputMapping.map((e) =>
                        e.source_port === port ? { ...e, enabled: on } : e,
                      ),
                    }
                    writeBack(next)
                  }}
                  onUpdateEntry={setEntry}
                  onMarkConfirmed={markConfirmed}
                  onAddRow={() => addRow(port)}
                  onRemoveRow={removeRow}
                  onClear={() => clearFrame(port)}
                  onAutoMap={() => autoMap(port, columns)}
                  onApplyPathSubstitution={(oldPrefix, newPrefix) =>
                    applyPathSubstitution(port, oldPrefix, newPrefix)
                  }
                  onPasteRows={(grid) => pasteRowsForPort(port, grid)}
                />
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}

// ─── FrameBlock ───────────────────────────────────────────────────

function FrameBlock({
  testIdPrefix,
  label,
  parentLabel,
  frameUnresolved,
  port,
  columns,
  rows,
  isOpen,
  frameEnabled,
  rowStatus,
  frameSchema,
  loadFrameData,
  frameDataCaveat,
  onToggleExpand,
  onToggleFrameEnabled,
  onUpdateEntry,
  onMarkConfirmed,
  onAddRow,
  onRemoveRow,
  onClear,
  onAutoMap,
  onApplyPathSubstitution,
  onPasteRows,
}: {
  testIdPrefix: string
  label: string
  parentLabel: string
  frameUnresolved: boolean
  port: string
  columns: string[]
  rows: { entry: OutputMappingEntryV2; index: number }[]
  isOpen: boolean
  frameEnabled: boolean
  rowStatus: RowStatusMap
  /** This frame's rows serialised as a standalone v2 schema — for Share/Save. */
  frameSchema: Record<string, unknown>
  /** Fetch this frame's INPUT rows (previews the upstream source node). */
  loadFrameData: () => Promise<{ rows: Record<string, unknown>[]; total: number }>
  /** A caveat to show on the input preview (e.g. multi-frame first-frame
   * collapse), or null when the preview is exact. */
  frameDataCaveat: string | null
  onToggleExpand: () => void
  onToggleFrameEnabled: (on: boolean) => void
  onUpdateEntry: (absIndex: number, patch: Partial<OutputMappingEntryV2>) => void
  onMarkConfirmed: (absIndex: number) => void
  onAddRow: () => void
  onRemoveRow: (absIndex: number) => void
  /** Remove ALL of this frame's mapping rows in one writeBack. */
  onClear: () => void
  onAutoMap: () => void
  /** Apply a prefix substitution across this frame's rows (pencil/apply). */
  onApplyPathSubstitution: (oldPrefix: string, newPrefix: string) => void
  /** Replace this frame's rows from a pasted tab-separated grid. */
  onPasteRows: (grid: string[][]) => void
}) {
  // Best-effort conflict detection, scoped to this frame (= source_port),
  // mirroring the backend's per-port rules: among ENABLED rows, two different
  // columns mapping to the same path, or two paths that are prefix-comparable.
  // Returns the set of ABSOLUTE indices that participate in a conflict.
  const conflicts = useMemo(() => detectConflicts(rows), [rows])

  // The frame's "header path" — shown in the header with a pencil. It defaults
  // to the rows' common root (a sensible starting prefix); literal storage means
  // there is no header/suffix split, so this is purely the prefix the user is
  // about to substitute.
  const headerPath = useMemo(
    () => commonRootPath(rows.map((r) => r.entry.output_path)),
    [rows],
  )
  // Inline path edit: a single boolean drives whether the header slot shows the
  // static path + pencil, or an in-place input + apply/cancel buttons. The draft
  // is initialised to `headerPath` each time edit mode is entered.
  const [editingPath, setEditingPath] = useState(false)
  const [newPath, setNewPath] = useState(headerPath)
  const pathInputRef = useRef<HTMLInputElement>(null)

  // On entering edit mode, focus the input and drop the cursor at the END (no
  // select-all) so typing extends the existing prefix rather than replacing it.
  useLayoutEffect(() => {
    if (!editingPath) return
    const el = pathInputRef.current
    if (!el) return
    el.focus()
    el.setSelectionRange(el.value.length, el.value.length)
  }, [editingPath])

  // Enter inline edit mode, seeding the draft with the current header path.
  const startEditingPath = useCallback(() => {
    setNewPath(headerPath)
    setEditingPath(true)
  }, [headerPath])

  // Apply the substitution (no-op + close when empty/unchanged), then exit.
  const applyEditingPath = useCallback(() => {
    const next = newPath.trim()
    if (next !== "" && next !== headerPath) {
      onApplyPathSubstitution(headerPath, next)
    }
    setEditingPath(false)
  }, [newPath, headerPath, onApplyPathSubstitution])

  // The per-frame column table as a grid for Copy/Save (mirrors the Paste-in
  // shape: column<TAB>output_path<TAB>enabled).
  const tableGrid = useMemo(
    () => ({
      headers: ["column", "path", "enabled"],
      rows: rows.map((r) => [
        r.entry.source_column,
        r.entry.output_path,
        String(r.entry.enabled),
      ]),
    }),
    [rows],
  )

  // ─── Per-frame input-data preview ───────────────────────────────
  //
  // The frame's INPUT rows (the upstream source node's preview), rendered as
  // JSON above the mapping table. Lazily loaded: expanding it (or refresh) runs
  // a previewNode against the source.
  const [dataPreviewOpen, setDataPreviewOpen] = useState(false)
  const [dataRows, setDataRows] = useState<Record<string, unknown>[] | null>(null)
  const [dataTotal, setDataTotal] = useState(0)
  const [dataLoading, setDataLoading] = useState(false)
  const [dataError, setDataError] = useState<string | null>(null)
  const dataReqSeq = useRef(0)

  const runDataPreview = useCallback(() => {
    const reqId = ++dataReqSeq.current
    setDataLoading(true)
    setDataError(null)
    loadFrameData()
      .then(({ rows: r, total }) => {
        if (dataReqSeq.current !== reqId) return
        setDataRows(r)
        setDataTotal(total)
        setDataLoading(false)
      })
      .catch((err: unknown) => {
        if (dataReqSeq.current !== reqId) return
        const message =
          err instanceof ApiError
            ? err.detail || err.message
            : err instanceof Error
              ? err.message
              : "Frame data preview failed"
        setDataRows(null)
        setDataError(message)
        setDataLoading(false)
      })
  }, [loadFrameData])

  const toggleDataPreview = useCallback(() => {
    setDataPreviewOpen((open) => {
      const next = !open
      if (next && dataRows === null && !dataLoading && dataError === null) {
        runDataPreview()
      }
      return next
    })
  }, [dataRows, dataLoading, dataError, runDataPreview])

  return (
    <div
      data-testid={testIdPrefix}
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}
    >
      <div className="flex items-center gap-2 px-2 py-2">
        <button
          data-testid={`${testIdPrefix}-toggle`}
          onClick={onToggleExpand}
          className="flex items-center gap-1 flex-1 min-w-0 text-left"
          title={isOpen ? "Collapse frame" : "Expand frame"}
        >
          {isOpen ? (
            <ChevronDown size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          ) : (
            <ChevronRight size={14} style={{ color: "var(--text-muted)" }} className="shrink-0" />
          )}
          <span
            className="overflow-hidden text-ellipsis whitespace-pre text-xs font-mono font-semibold"
            style={{ color: "var(--text-primary)", whiteSpace: "pre" }}
          >
            {frameUnresolved && (
              <span
                data-testid="output-frame-parent-label"
                className="shrink-0 text-xs font-semibold"
                style={{ color: "var(--text-primary)" }}
              >
                {parentLabel}
              </span>
            )}
            {label}
          </span>
          {frameUnresolved && (
            <span
              data-testid="output-frame-unresolved"
              role="img"
              aria-label="Unresolved frame"
              title="No emitted frame resolves for this connection"
              className="shrink-0"
              style={{ color: "var(--warning)" }}
            >
              <AlertTriangle size={11} />
            </span>
          )}
          <span className="text-[10px] shrink-0" style={{ color: "var(--text-muted)" }}>
            {rows.length} {rows.length === 1 ? "field" : "fields"}
          </span>
        </button>
        {/* The frame path, edited INLINE in the same header slot — no drawer,
            no vertical shift. Not editing: the path text (double-click to edit)
            + a pencil. Editing: a left-aligned input + tick/cross IN PLACE of
            the pencil; Enter applies, Escape/blur cancels. */}
        {editingPath ? (
          <div className="flex items-center gap-1 max-w-[40%] shrink-0">
            <input
              ref={pathInputRef}
              data-testid={`${testIdPrefix}-path-edit-input`}
              type="text"
              value={newPath}
              onChange={(e) => setNewPath(e.target.value)}
              onBlur={() => setEditingPath(false)}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyEditingPath()
                else if (e.key === "Escape") setEditingPath(false)
              }}
              placeholder={headerPath}
              className="flex-1 min-w-0 text-left text-[10px] px-1 py-0.5 rounded font-mono"
              style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            />
            <button
              data-testid={`${testIdPrefix}-path-edit-apply`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={applyEditingPath}
              title="Apply the new path prefix across this frame's rows"
              className="shrink-0 p-0.5 rounded"
              style={{ color: "var(--text-muted)" }}
            >
              <Check size={11} />
            </button>
            <button
              data-testid={`${testIdPrefix}-path-edit-cancel`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setEditingPath(false)}
              title="Cancel"
              className="shrink-0 p-0.5 rounded"
              style={{ color: "var(--text-muted)" }}
            >
              <X size={11} />
            </button>
          </div>
        ) : (
          <>
            <span
              data-testid={`${testIdPrefix}-header-path`}
              onDoubleClick={startEditingPath}
              className="text-[10px] font-mono text-right truncate max-w-[40%] shrink-0 cursor-text"
              style={{ color: "var(--text-muted)" }}
              title={`Frame path: ${headerPath}`}
            >
              {headerPath}
            </span>
            <button
              data-testid={`${testIdPrefix}-path-edit-toggle`}
              onClick={startEditingPath}
              title="Edit this frame's path prefix"
              className="shrink-0 p-0.5 rounded"
              style={{ color: "var(--text-muted)" }}
            >
              <Pencil size={11} />
            </button>
          </>
        )}
        <label
          className="flex items-center gap-1 text-[11px] shrink-0"
          title="Include this frame in the response"
          style={{ color: "var(--text-muted)" }}
        >
          <input
            data-testid={`${testIdPrefix}-enable`}
            type="checkbox"
            checked={frameEnabled}
            onChange={(e) => onToggleFrameEnabled(e.target.checked)}
          />
          enabled
        </label>
      </div>

      {isOpen && (
        <div className="px-2 pb-2 space-y-1.5" style={{ borderTop: "1px solid var(--border)" }}>
          {/* Shared table-actions strip for this frame's column-mapping table:
              Copy (TSV), Share (JSON), Save (JSON/CSV/TSV), and Paste-in. */}
          <div className="flex items-center justify-between gap-2 pt-1.5">
            <FrameTableActions
              testIdPrefix={`${testIdPrefix}-table`}
              filename={`output-${port || "frame"}`}
              getGrid={() => tableGrid}
              getSchema={() => frameSchema}
              onPaste={onPasteRows}
            />
            {/* Action strip, left→right: Add row (green), Infer (amber),
                Clear (red). Each is a compact tinted pill. */}
            <div className="flex items-center gap-2">
              <button
                data-testid={`${testIdPrefix}-add-row`}
                onClick={onAddRow}
                className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1"
                style={{
                  color: "var(--success)",
                  border: "1px solid var(--success-border)",
                  background: "var(--success-soft)",
                }}
                title="Add an empty mapping row"
              >
                <Plus size={11} />
                Add row
              </button>
              <button
                data-testid={`${testIdPrefix}-infer`}
                onClick={onAutoMap}
                disabled={columns.length === 0}
                className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1 disabled:opacity-40"
                style={{
                  color: "var(--warning-strong)",
                  border: "1px solid var(--warning-border)",
                  background: "var(--warning-soft-strong)",
                }}
                title="Infer one row per frame column"
              >
                <Wand2 size={11} />
                Infer
              </button>
              <button
                data-testid={`${testIdPrefix}-clear`}
                onClick={onClear}
                disabled={rows.length === 0}
                className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1 disabled:opacity-40"
                style={{
                  color: "var(--danger-text)",
                  border: "1px solid var(--danger-border-strong)",
                  background: "var(--danger-soft)",
                }}
                title="Remove all of this frame's mapping rows"
              >
                <X size={11} />
                Clear
              </button>
            </div>
          </div>

          {/* Per-frame INPUT-data preview — the upstream source's rows as JSON,
              below the action strip and above this frame's mapping table.
              Expand (or refresh) to (re)run. */}
          <JsonPreview
            testIdPrefix={`${testIdPrefix}-data-preview`}
            title="Input data"
            rows={dataRows ?? []}
            totalRows={dataTotal}
            filename={`output-${port || "frame"}-input`}
            isOpen={dataPreviewOpen}
            onToggle={toggleDataPreview}
            onRefresh={runDataPreview}
            loading={dataLoading}
            error={dataError}
            note={frameDataCaveat}
            emptyMessage="No input rows for this frame."
          />

          {rows.length === 0 && (
            <div className="text-[11px] italic" style={{ color: "var(--text-muted)" }}>
              No fields mapped from this frame yet. Click{" "}
              <span className="font-semibold">Infer</span> or{" "}
              <span className="font-semibold">Add row</span>.
            </div>
          )}

          {rows.map((r, rowIndex) => (
            <MappingRow
              key={r.index}
              testIdPrefix={`${testIdPrefix}-row-${rowIndex}`}
              entry={r.entry}
              columns={columns}
              status={rowStatus[r.index] ?? "Confirmed"}
              pathConflict={conflicts.has(r.index)}
              onColumn={(source_column) => {
                onUpdateEntry(r.index, { source_column })
                onMarkConfirmed(r.index)
              }}
              onPath={(output_path) => {
                onUpdateEntry(r.index, { output_path })
                onMarkConfirmed(r.index)
              }}
              onEnabled={(enabled) => onUpdateEntry(r.index, { enabled })}
              onRemove={() => onRemoveRow(r.index)}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Best-effort per-frame conflict detection over a frame's rows. Returns the
 * set of ABSOLUTE indices (`r.index`) that participate in a conflict — the
 * same coordinate the rows render and look up their status by. Mirrors the
 * backend's `validate_v2_output_mapping` per-port rules over ENABLED rows:
 *   - injectivity: two distinct columns → same path;
 *   - prefix-incomparability: two distinct paths where one's segment list is a
 *     prefix of the other's (comparing `(name, isArray)` pairs, per the backend
 *     `_Seg` equality — a scalar leaf and an array container at the same name
 *     are NOT comparable).
 * Only grammatically valid paths take part (invalid paths surface their own
 * error). The backend remains the authority.
 */
function detectConflicts(rows: { entry: OutputMappingEntryV2; index: number }[]): Set<number> {
  const conflicting = new Set<number>()
  const valid = rows.filter(
    ({ entry }) =>
      entry.enabled && entry.output_path && validateOutputPath(entry.output_path) === null,
  )
  for (let a = 0; a < valid.length; a++) {
    for (let b = a + 1; b < valid.length; b++) {
      const ea = valid[a].entry
      const eb = valid[b].entry
      const samePath = ea.output_path === eb.output_path
      const dup = samePath && ea.source_column !== eb.source_column
      const prefix = !samePath && prefixComparable(ea.output_path, eb.output_path)
      if (dup || prefix) {
        conflicting.add(valid[a].index)
        conflicting.add(valid[b].index)
      }
    }
  }
  return conflicting
}

/** One output-path segment, mirroring the backend `_Seg`: a JSON key plus
 * whether a `[:]` selector iterates its value as an array. */
interface PathSeg {
  name: string
  isArray: boolean
}

/** Segment list of a path (names of `.name` / `['name']` selectors, each
 * flagged `isArray` when followed by a `[:]` whole-array selector; the root and
 * the bare `[:]` markers are not segments). Used only for prefix comparison,
 * mirroring the backend `_parse_output_path` segment construction. */
function pathSegments(path: string): PathSeg[] {
  const segs: PathSeg[] = []
  let i = path.startsWith("$[:]") ? 4 : 1
  const dot = /^\.([A-Za-z_][A-Za-z0-9_]*)/
  const bracket = /^\[(['"])([^'"]+)\1\]/
  while (i < path.length) {
    const rest = path.slice(i)
    let name: string
    let m = dot.exec(rest)
    if (m) {
      name = m[1]
      i += m[0].length
    } else {
      m = bracket.exec(rest)
      if (m) {
        name = m[2]
        i += m[0].length
      } else {
        break
      }
    }
    const isArray = path.slice(i, i + 3) === "[:]"
    if (isArray) i += 3
    segs.push({ name, isArray })
  }
  return segs
}

/** True when one path's segment list is a prefix of the other's (or equal),
 * comparing `(name, isArray)` PAIRS — mirroring the backend `_prefix_comparable`
 * over `_Seg` tuples. A scalar leaf `$[:].obj` and an array container
 * `$[:].obj[:].x` differ at the `obj` segment's `isArray` flag, so they are NOT
 * prefix-comparable (the backend accepts them; the editor must not false-flag). */
function prefixComparable(a: string, b: string): boolean {
  const sa = pathSegments(a)
  const sb = pathSegments(b)
  const n = Math.min(sa.length, sb.length)
  for (let i = 0; i < n; i++) {
    if (sa[i].name !== sb[i].name || sa[i].isArray !== sb[i].isArray) return false
  }
  return true
}

// ─── MappingRow ───────────────────────────────────────────────────

function MappingRow({
  testIdPrefix,
  entry,
  columns,
  status,
  pathConflict,
  onColumn,
  onPath,
  onEnabled,
  onRemove,
}: {
  testIdPrefix: string
  entry: OutputMappingEntryV2
  columns: string[]
  status: OutputRowStatus
  pathConflict: boolean
  onColumn: (column: string) => void
  onPath: (path: string) => void
  onEnabled: (on: boolean) => void
  onRemove: () => void
}) {
  // The selected column may not be present in the available list (e.g. the
  // source schema changed) — keep it selectable so the value is preserved.
  const columnOptions = useMemo(() => {
    const opts = [...columns]
    if (entry.source_column && !opts.includes(entry.source_column)) {
      opts.unshift(entry.source_column)
    }
    return opts
  }, [columns, entry.source_column])

  return (
    <div data-testid={testIdPrefix} className="flex items-start gap-2">
      <input
        data-testid={`${testIdPrefix}-enabled`}
        type="checkbox"
        checked={entry.enabled}
        onChange={(e) => onEnabled(e.target.checked)}
        className="mt-1.5"
        title="Include this field in the response"
      />
      <div className="w-28 shrink-0">
        <select
          data-testid={`${testIdPrefix}-column`}
          value={entry.source_column}
          onChange={(e) => onColumn(e.target.value)}
          className="w-full text-[11px] px-1 py-0.5 rounded font-mono"
          style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
        >
          <option value="">— column —</option>
          {columnOptions.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </div>
      <CommittedTextInput
        dataTestId={`${testIdPrefix}-path`}
        value={entry.output_path}
        onCommit={onPath}
        validate={validatePathInput}
        containerClassName="flex-1 min-w-0"
        className="w-full text-[11px] px-1.5 py-0.5 rounded font-mono"
        style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-muted)" }}
        conflictNote={pathConflict ? "Conflicts with another field's path in this frame (best-effort)." : null}
      />
      {status === "Inferred" && (
        <span
          data-testid={`${testIdPrefix}-pill`}
          className="mt-1 text-[10px] font-semibold px-1.5 py-0.5 rounded shrink-0"
          style={{ background: "var(--warning-soft)", color: "var(--warning-strong)" }}
          title="Auto-mapped — edit the column or path to confirm"
        >
          Inferred
        </span>
      )}
      <button
        data-testid={`${testIdPrefix}-remove`}
        onClick={onRemove}
        title="Remove field"
        className="mt-1"
      >
        <X size={11} style={{ color: "var(--text-muted)" }} />
      </button>
    </div>
  )
}

// ─── path validation ──────────────────────────────────────────────
//
// Mirror the backend OUTPUT grammar + the §3 root gate (`_parse_output_path`):
// every output path must enter the array-outer document through the root array
// `$[:]`. `validateOutputPath` enforces that gate, so a non-array root — e.g.
// `$.foo` or `$.values[:].a` — is refused here; the OUTPUT assembler maps rows
// of a frame into the root array, which the `$[:]` root guarantees.

function validatePathInput(candidate: string): string | null {
  const trimmed = candidate.trim()
  if (!trimmed) return "An output path is required."
  return validateOutputPath(trimmed)
}

// ─── CommittedTextInput ───────────────────────────────────────────
//
// Mirrors the apiInput editor's committed-input pattern: a path buffers locally
// and commits on blur/Enter, refusing invalid candidates (keeping the draft +
// a visible error). This avoids per-keystroke config churn and never lets an
// invalid path silently reach the backend. The optional `conflictNote` is a
// non-blocking advisory (the path is grammatically fine but conflicts with a
// sibling — backend is the authority), shown alongside any hard error.

function CommittedTextInput({
  value,
  onCommit,
  validate,
  dataTestId,
  containerClassName,
  className,
  style,
  conflictNote,
}: {
  value: string
  onCommit: (next: string) => void
  validate: (candidate: string) => string | null
  dataTestId: string
  containerClassName: string
  className: string
  style: CSSProperties
  conflictNote?: string | null
}) {
  const [draft, setDraft] = useState<string | null>(null)
  const [lastValue, setLastValue] = useState(value)
  if (lastValue !== value) {
    setLastValue(value)
    setDraft(null)
  }
  const shown = draft ?? value
  const error = validate(shown)
  // Persistent §4 highlight: a VALID but non-canonical path (typed or inferred)
  // is flagged informationally — it commits and assembles identically, so this
  // never blocks. Only computed when the path is grammar-valid (error === null);
  // an invalid path surfaces its grammar error instead. OUTPUT paths are always
  // path inputs, so there is no label/column-name exemption here.
  const hint = error === null ? nonCanonicalHint(shown) : null
  const commit = () => {
    if (draft === null) return
    if (draft === value) {
      setDraft(null)
      return
    }
    if (validate(draft) !== null) return
    onCommit(draft)
    setDraft(null)
  }
  return (
    <div className={containerClassName}>
      <input
        data-testid={dataTestId}
        type="text"
        value={shown}
        aria-invalid={error !== null ? true : undefined}
        placeholder="$[:].field"
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit()
        }}
        className={className}
        style={
          error !== null
            ? { ...style, border: "1px solid var(--danger-border-strong)" }
            : hint !== null
              ? { ...style, border: "1px solid var(--accent-soft-strong)" }
              : style
        }
      />
      {error !== null && (
        <div
          data-testid={`${dataTestId}-error`}
          className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
        >
          {error}
        </div>
      )}
      {error === null && conflictNote && (
        <div
          data-testid={`${dataTestId}-conflict`}
          className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--warning-soft)", color: "var(--warning-strong)" }}
        >
          {conflictNote}
        </div>
      )}
      {hint !== null && (
        <div
          data-testid={`${dataTestId}-noncanonical`}
          className="mt-0.5 px-1.5 py-0.5 rounded text-[10px] leading-snug"
          style={{ background: "var(--accent-soft)", color: "var(--accent)" }}
        >
          {nonCanonicalNote(hint)}
        </div>
      )}
    </div>
  )
}
