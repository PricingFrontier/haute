import { useMemo, useState, useCallback, type CSSProperties } from "react"
import { ChevronRight, ChevronDown, Plus, X, Wand2 } from "lucide-react"
import type { OnUpdateConfig, SimpleNode, SimpleEdge } from "./_shared"
import { EditorLabel } from "../../components/form"
import { useGraph } from "../useGraph"
import { sanitizeName } from "../../utils/sanitizeName"
import {
  classifyConfig,
  emptyV2,
  migrateV1,
  writeV2,
  validateOutputPath,
  hasArraySelector,
  type OutputConfigV2,
  type OutputMappingEntryV2,
  type OutputRowStatus,
} from "./outputMappingSchema"

// ─── Frame identity + columns ─────────────────────────────────────
//
// One BLOCK per incoming edge = per source FRAME. The frame's identity is the
// edge's `sourceHandle` (the apiInput table label / per-port handle id);
// single-port sources have a null handle, in which case the frame resolves to
// the sanitised source-node label — exactly what the backend executor uses as
// the positional frame key (`edge.sourceHandle or sanitize(node-label)`, see
// `_execute_lazy.py` build_node_fns + `_graph_utils.py::_sanitize_func_name`).
// The user-facing name still shows the raw label.

/**
 * The persisted `source_port` for an edge — the backend's frame key:
 *   - its `sourceHandle` (multi-port apiInput / per-table handle), else
 *   - `sanitizeName(source node label)` (single-port source, null handle).
 * NEVER "" for a resolvable edge: two distinct single-port sources must persist
 * DISTINCT, non-empty ports so a genuine >=2-frame OUTPUT binds (the old `""`
 * fallback collapsed them and tripped `OutputMappingSchemaError`). Falls back
 * to the sanitised source-node id when the label is missing.
 */
function framePortId(edge: SimpleEdge, sourceNode: SimpleNode | undefined): string {
  if (edge.sourceHandle) return edge.sourceHandle
  const label = sourceNode?.data.label
  return sanitizeName(typeof label === "string" && label ? label : edge.source)
}

/** The user-facing frame name for an edge: the handle, else the source label. */
function frameLabel(edge: SimpleEdge, sourceNode: SimpleNode | undefined): string {
  if (edge.sourceHandle) return edge.sourceHandle
  return sourceNode?.data.label ?? edge.source
}

/**
 * Columns available for a frame.
 *
 * For an apiInput source with a v2 `tables` config, return the columns of the
 * table whose `label === edge.sourceHandle` (the multi-port case — each emitted
 * table is its own frame/handle). For a single-port source (null sourceHandle),
 * fall back to the source node's `_columns` (populated by preview/run).
 *
 * SHAPE NOTE: this helper is deliberately the only place that derives a frame's
 * column set. A future backend per-frame schema endpoint can replace the BODY
 * here without touching any caller — callers only ever see `string[]`.
 */
function frameColumns(edge: SimpleEdge, sourceNode: SimpleNode | undefined): string[] {
  if (!sourceNode) return []
  const data = sourceNode.data as Record<string, unknown>

  // apiInput multi-port: match the emitted table by its label === handle.
  if (edge.sourceHandle) {
    const cfg = data.config as Record<string, unknown> | undefined
    const tables = cfg && Array.isArray(cfg.tables) ? (cfg.tables as unknown[]) : null
    if (tables) {
      for (const t of tables) {
        if (!t || typeof t !== "object") continue
        const tt = t as Record<string, unknown>
        if (tt.label === edge.sourceHandle && Array.isArray(tt.columns)) {
          return (tt.columns as unknown[])
            .map((c) =>
              c && typeof c === "object" && typeof (c as Record<string, unknown>).name === "string"
                ? ((c as Record<string, unknown>).name as string)
                : null,
            )
            .filter((n): n is string => n !== null)
        }
      }
      // A handle that doesn't match any emitted table → no columns we can
      // surface from this source's config (best-effort; backend is authority).
      return []
    }
  }

  // Single-port source: cached columns from preview/run.
  const cols = data._columns as { name: string }[] | undefined
  if (Array.isArray(cols)) {
    return cols.map((c) => c.name).filter((n): n is string => typeof n === "string")
  }
  return []
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
  const { allNodes, edges } = useGraph()

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
        return {
          edge,
          sourceNode,
          port: framePortId(edge, sourceNode),
          label: frameLabel(edge, sourceNode),
          columns: frameColumns(edge, sourceNode),
        }
      }),
    [incomingEdges, nodeById],
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
        <div className="space-y-2">
          {frames.map(({ edge, port, label, columns }, ei) => {
            const rows = rowsForPort(port)
            const isOpen = expanded[edge.id] ?? false
            const anyEnabled = rows.some((r) => r.entry.enabled)
            return (
              <FrameBlock
                key={edge.id}
                testIdPrefix={`output-frame-${ei}`}
                label={label}
                columns={columns}
                rows={rows}
                isOpen={isOpen}
                frameEnabled={anyEnabled}
                rowStatus={rowStatus}
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
                onAutoMap={() => autoMap(port, columns)}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}

// ─── FrameBlock ───────────────────────────────────────────────────

function FrameBlock({
  testIdPrefix,
  label,
  columns,
  rows,
  isOpen,
  frameEnabled,
  rowStatus,
  onToggleExpand,
  onToggleFrameEnabled,
  onUpdateEntry,
  onMarkConfirmed,
  onAddRow,
  onRemoveRow,
  onAutoMap,
}: {
  testIdPrefix: string
  label: string
  columns: string[]
  rows: { entry: OutputMappingEntryV2; index: number }[]
  isOpen: boolean
  frameEnabled: boolean
  rowStatus: RowStatusMap
  onToggleExpand: () => void
  onToggleFrameEnabled: (on: boolean) => void
  onUpdateEntry: (absIndex: number, patch: Partial<OutputMappingEntryV2>) => void
  onMarkConfirmed: (absIndex: number) => void
  onAddRow: () => void
  onRemoveRow: (absIndex: number) => void
  onAutoMap: () => void
}) {
  // Best-effort conflict detection, scoped to this frame (= source_port),
  // mirroring the backend's per-port rules: among ENABLED rows, two different
  // columns mapping to the same path, or two paths that are prefix-comparable.
  // Returns the set of ABSOLUTE indices that participate in a conflict.
  const conflicts = useMemo(() => detectConflicts(rows), [rows])

  return (
    <div
      data-testid={testIdPrefix}
      className="rounded-md"
      style={{ border: "1px solid var(--border)", background: "var(--bg-soft)" }}
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
            className="text-xs font-mono font-semibold truncate"
            style={{ color: "var(--text-primary)" }}
          >
            {label}
          </span>
          <span className="text-[10px] shrink-0" style={{ color: "var(--text-muted)" }}>
            {rows.length} {rows.length === 1 ? "field" : "fields"}
          </span>
        </button>
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
          <div className="flex items-center justify-end gap-2 pt-1.5">
            <button
              data-testid={`${testIdPrefix}-auto-map`}
              onClick={onAutoMap}
              disabled={columns.length === 0}
              className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1 disabled:opacity-40"
              style={{ color: "var(--text-muted)" }}
              title="Add one Inferred row per frame column"
            >
              <Wand2 size={11} />
              Auto-map
            </button>
            <button
              data-testid={`${testIdPrefix}-add-row`}
              onClick={onAddRow}
              className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1"
              style={{ color: "var(--text-muted)" }}
            >
              <Plus size={11} />
              Add row
            </button>
          </div>

          {rows.length === 0 && (
            <div className="text-[11px] italic" style={{ color: "var(--text-muted)" }}>
              No fields mapped from this frame yet. Click{" "}
              <span className="font-semibold">Auto-map</span> or{" "}
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
// Mirror the backend grammar (`_parse_output_path`) AND require the `[:]`
// whole-array form (the Auto-map / canonical shape). A grammatically valid path
// with no array selector — e.g. `$.foo` — is refused: the OUTPUT assembler maps
// rows of a frame into an array of records, which requires a `[:]` selector.

function validatePathInput(candidate: string): string | null {
  const trimmed = candidate.trim()
  if (!trimmed) return "An output path is required."
  const grammar = validateOutputPath(trimmed)
  if (grammar !== null) return grammar
  if (!hasArraySelector(trimmed)) {
    return "Output path must use the whole-array form, e.g. $[:].field."
  }
  return null
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
    </div>
  )
}
