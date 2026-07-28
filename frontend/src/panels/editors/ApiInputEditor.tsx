import { useMemo, useState, type CSSProperties } from "react"
import { AlertTriangle, Radio, Check, HelpCircle, KeyRound, Plus, X } from "lucide-react"
import { SchemaPreview } from "./_shared"
import type { OnUpdateConfig, OnUpdateConfigResult } from "./_shared"
import { useSchemaFetch } from "../../hooks/useSchemaFetch"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import {
  apiInputHasEmittingTable,
  apiInputLabelIssue,
  apiInputLabelIssueMessage,
} from "../../utils/apiInputPorts"
import {
  CacheFetchButton,
  PARQUET_CACHE_LABELS,
} from "../../components/CacheFetchButton"
import { FrameTableActions } from "./FrameTableActions"
import PathPickerField from "./shared/PathPickerField"
import {
  buildJsonCache,
  getJsonCacheProgress,
  getJsonCacheStatus,
  getJsonCacheStatusForSchema,
  deleteJsonCache,
  inferJsonCacheSchema,
} from "../../api/client"
import {
  classifyConfig,
  emptyV2,
  readV2,
  writeV2,
  type ApiInputConfigV2,
  type ApiInputColumnV2,
  type ApiInputTableV2,
  type ColumnOrigin,
  type ColumnType,
} from "./apiInputSchema"
import { validateInputColumnPath, validateInputTablePath } from "./jsonpath"
import {
  buildAllKeyGroups,
  buildInheritGroups,
  buildInsertedColumns,
  buildPathInventory,
  ambiguousNames,
  dedupNameByPath,
  getCascadeDestinations,
  inheritedColumnName,
  orderFrameColumns,
  reconcileInferredTables,
  validateColumnPathAgainstFrame,
  type InheritGroup,
  type InventoryKey,
} from "./apiInputInherit"
import FramesTable, { type FramesTableRow } from "../../components/FramesTable"
import KeyPickerModal from "../../components/KeyPickerModal"
import Tooltip from "../../components/Tooltip"

// The re-infer merge is `reconcileInferredTables` (apiInputInherit.ts): the
// column-level reconciliation that supersedes the old whole-column-array
// adoption — confirmed and structurally-incomplete columns survive, stale
// non-confirmed ones go, fresh ones append (fresh side de-dup-suffixed), new
// frames arrive with the user's cascaded keys prepended.

// ─── JsonCacheButton ──────────────────────────────────────────────
//
// Wraps the shared cache-button. Sends the editor's in-memory v2 as
// `volatile_schema` on every cache POST so the build uses what the user
// is looking at, regardless of whether the on-disk config matches yet
// (working principle 4: volatile vs persistent at the schema plane
// mirrors PR13's data plane). When the editor has nothing to cache
// (no schema source, or no emit:true tables) the button is rendered
// `disabled` rather than firing a no-op POST.

type JsonCacheStatus = {
  cached: boolean
  path?: string
  data_path: string
  row_count: number
  column_count: number
  size_bytes: number
  cached_at: number
}

function JsonCacheButton({
  dataPath,
  configPath,
  volatileSchema,
  disabled,
  disabledReason,
}: {
  dataPath: string
  configPath?: string
  /** The editor's in-memory v2 (`writeV2(v2)` of the live state). When
   * defined, becomes `volatile_schema` on the cache POST so the backend
   * builds from the user's unsaved edits. */
  volatileSchema?: Record<string, unknown>
  disabled?: boolean
  disabledReason?: string
}) {
  // `volatileSchema` comes from the canonical `writeV2` writer, so its JSON
  // representation is a stable value identity. Array encoding also avoids the
  // delimiter collisions of a hand-built composite key. CacheFetchButton uses
  // this key only to reset/refetch status; the API callbacks below intentionally
  // continue to send the original path/schema payloads.
  const resourceKey = JSON.stringify([
    dataPath,
    configPath ?? null,
    volatileSchema ?? null,
  ])

  return (
    <CacheFetchButton<JsonCacheStatus>
      resourceKey={resourceKey}
      getStatus={(_key) =>
        configPath
          ? getJsonCacheStatusForSchema({
              path: dataPath,
              config_path: configPath,
              volatile_schema: volatileSchema,
            })
          : getJsonCacheStatus(dataPath)
      }
      startFetch={(_key) =>
        buildJsonCache({
          path: dataPath,
          config_path: configPath,
          volatile_schema: volatileSchema,
        }).then(
          (data) => ({ cached: true, ...data }) as JsonCacheStatus,
        )
      }
      getProgress={(_key) => getJsonCacheProgress(dataPath)}
      deleteCache={(_key) => deleteJsonCache(dataPath) as Promise<JsonCacheStatus>}
      timestampField="cached_at"
      labels={{
        ...PARQUET_CACHE_LABELS,
        notCachedHint: "Runs directly from JSON — cache as Parquet for faster repeat runs",
      }}
      disabled={disabled}
      disabledReason={disabledReason}
    />
  )
}

// ─── ApiInputEditor ───────────────────────────────────────────────

const COLUMN_TYPES: ColumnType[] = ["int", "float", "str", "bool", "date"]

export default function ApiInputEditor({
  config,
  onUpdate,
  accentColor,
  configPath,
}: {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  accentColor: string
  /** Pipeline-relative path to the on-disk schema mapping file (e.g.
   * `rating/config/quote_input/quotes.json`). */
  configPath?: string
}) {
  const currentPath = configField<string | undefined>(config, "path", undefined)
  const { schema, loading: loadingSchema, error: schemaError, fetchForPath } = useSchemaFetch(currentPath)
  const showCacheButton =
    !!currentPath && /\.(?:json|jsonl|ndjson|xml)$/i.test(currentPath)
  const [inferring, setInferring] = useState(false)
  const [inferError, setInferError] = useState<string | null>(null)
  // Defect 2 — when a re-infer would overwrite tables the user has
  // already curated, we stage the normalised inferred tables here and
  // render a confirm/cancel gate instead of clobbering immediately.
  // First run (empty tables) skips the gate and applies in one click.
  const [pendingInferred, setPendingInferred] = useState<ApiInputTableV2[] | null>(null)
  // The most-recent-inference snapshot: a single snapshot overwritten on every
  // infer (never accumulated), so a key deleted from a frame — or whose whole
  // frame was deleted — can still be offered by the inventory.
  const [lastInfer, setLastInfer] = useState<ApiInputTableV2[] | null>(null)
  // Salted naming (full dotted leaf → `customer_id`) vs bare leaf (`id`) for
  // keys arriving without an inventory name. Salted is the default.
  const [saltNames, setSaltNames] = useState(true)
  const [labelCommitErrors, setLabelCommitErrors] = useState<Record<number, string>>({})
  // Which key picker is open: cascade works over the whole inventory from the
  // frames table; inherit / add-keys (inherit-attributes) target one frame.
  const [picker, setPicker] = useState<
    | { mode: "cascade" }
    | { mode: "inherit" | "attributes"; tableIdx: number }
    | null
  >(null)

  // Classify the config. v2 renders its tables; a fresh empty config
  // renders a bare surface populated via Infer Tables / Add Table.
  const shape = useMemo(() => classifyConfig(config), [config])
  const v2: ApiInputConfigV2 =
    shape.kind === "v2" ? shape.v2 : emptyV2(currentPath)

  // The path inventory: every key across the current frames plus the
  // most-recent inference snapshot (current frames win on name/type).
  const inventory = useMemo(
    () => buildPathInventory(v2.tables, lastInfer),
    [v2.tables, lastInfer],
  )
  // Names carried by MORE THAN ONE distinct path across the frames (ruled
  // 2026-07-14): a name should mean one field everywhere — same-path reuse is
  // the point of name transport, but different paths sharing a name get a
  // warning shade on their name inputs.
  const ambiguous = useMemo(() => ambiguousNames(v2.tables), [v2.tables])
  // JSON-appearance ranks for the keys-section ordering (ruled 2026-07-09):
  // the inventory's insertion order is the working proxy for data-model order.
  const jsonOrder = useMemo(() => {
    const m = new Map<string, number>()
    let i = 0
    for (const path of inventory.keys()) m.set(path, i++)
    return m
  }, [inventory])

  // Helpers to push state changes back through onUpdate. Each write
  // recomposes the full v2 record-shaped object so we never have to fan
  // out individual onUpdate calls per nested field.
  const writeBack = (next: ApiInputConfigV2): OnUpdateConfigResult => {
    const raw = writeV2(next)
    // Use a single batched update so NodePanel only fires one
    // handleConfigUpdate (per the existing OnUpdateConfig contract).
    const commitResult = onUpdate(raw)
    if (commitResult.ok) setLabelCommitErrors({})
    return commitResult
  }
  const updateTable = (
    i: number,
    patch: Partial<ApiInputTableV2>,
  ): OnUpdateConfigResult => {
    const next = {
      ...v2,
      tables: v2.tables.map((t, idx) => (idx === i ? { ...t, ...patch } : t)),
    }
    const result = writeBack(next)
    if (Object.hasOwn(patch, "label") && !result.ok) {
      setLabelCommitErrors((previous) => ({ ...previous, [i]: result.error }))
    }
    return result
  }
  // W1.4 — label validation, mirroring the backend's save-time rules
  // (`validate_v2_schema`): blank labels, duplicates, and sanitised-form
  // collisions are hard-rejected there, and the label doubles as the
  // React Flow handle id / runtime frame name. An invalid label must be
  // refused in the editor with a visible reason — committing it would
  // create a frame identity the backend can never emit.
  const validateTableLabel = (i: number) => (candidate: string) =>
    apiInputLabelIssueMessage(
      apiInputLabelIssue(
        candidate,
        v2.tables.filter((_, idx) => idx !== i).map((t) => t.label),
      ),
    )
  const updateColumn = (
    tableIdx: number,
    colIdx: number,
    patch: Partial<ApiInputColumnV2>,
  ): OnUpdateConfigResult => {
    // Editing a column's name, path, or type IS confirming it — the user has
    // looked at the row and made it theirs.
    const confirming =
      "name" in patch || "path" in patch || "type" in patch
        ? { status: "Confirmed" as const }
        : null
    // Committing a path onto a still-blank name fills the name in from the
    // inventory's used name for that path, falling back to the (salted) leaf —
    // so a blank hand-added row inherits its name instead of needing a second
    // edit (ruled 2026-07-09).
    const current = v2.tables[tableIdx]?.columns[colIdx]
    if (
      patch.path !== undefined &&
      patch.name === undefined &&
      current !== undefined &&
      current.name === ""
    ) {
      try {
        const derived = dedupNameByPath(
          inventory.get(patch.path)?.name ?? inheritedColumnName(patch.path, saltNames),
          patch.path,
          new Set(
            v2.tables[tableIdx].columns.filter((_, i) => i !== colIdx).map((c) => c.name),
          ),
        )
        patch = { ...patch, name: derived }
      } catch {
        /* unparseable path: leave the name blank; the validator flags it */
      }
    }
    // The row-id nomination references a column BY NAME; a rename must carry
    // the nomination along or it points at a name that no longer exists.
    const oldName = current?.name
    const next = {
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? {
              ...t,
              row_id_column:
                patch.name !== undefined && t.row_id_column === oldName
                  ? patch.name
                  : t.row_id_column,
              columns: t.columns.map((c, ci) =>
                ci === colIdx ? { ...c, ...patch, ...confirming } : c,
              ),
            }
          : t,
      ),
    }
    return writeBack(next)
  }
  const addColumn = (tableIdx: number) => {
    // A hand-added column arrives BLANK (ruled 2026-07-09): no placeholder
    // column_N name or synthetic path. The render-gate flags the incomplete row
    // (and reconcile's blank carve-out protects it); committing a valid path
    // auto-derives a still-blank name, and edit-confirms makes it Confirmed.
    const newCol: ApiInputColumnV2 = {
      name: "",
      path: "",
      type: "str",
      status: "Inferred",
      selected: true,
      levels: null,
      origin: "manual",
    }
    const next: ApiInputConfigV2 = {
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? {
              ...t,
              columns: [...t.columns, newCol],
            }
          : t,
      ),
    }
    writeBack(next)
  }
  const removeColumn = (tableIdx: number, colIdx: number) => {
    const next = {
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? { ...t, columns: t.columns.filter((_, ci) => ci !== colIdx) }
          : t,
      ),
    }
    writeBack(next)
  }
  // Bundle 3d — PASTE-IN for a table's columns. The pasted grid is
  // tab-separated `name<TAB>path<TAB>type<TAB>selected` rows (the shape Copy
  // emits). A recognised header row is dropped; pasted columns REPLACE the
  // table's existing columns. Unknown types coerce to "str" (mirroring
  // readV2); blank-name/path rows are skipped — fresh paste input drops
  // the incomplete (like the infer path); the render-gate KEEP applies
  // only to already-persisted entries. Pasted columns are
  // author-confirmed (status "Confirmed").
  const pasteColumns = (tableIdx: number, grid: string[][]) => {
    const body =
      grid.length > 0 &&
      grid[0][0]?.trim().toLowerCase() === "name" &&
      grid[0][1]?.trim().toLowerCase() === "path"
        ? grid.slice(1)
        : grid
    const columns: ApiInputColumnV2[] = []
    for (const cells of body) {
      const name = (cells[0] ?? "").trim()
      const path = (cells[1] ?? "").trim()
      if (!name || !path) continue
      const rawType = (cells[2] ?? "").trim().toLowerCase()
      const type = (["int", "float", "str", "bool", "date"] as const).includes(
        rawType as ColumnType,
      )
        ? (rawType as ColumnType)
        : "str"
      const selectedCell = (cells[3] ?? "").trim().toLowerCase()
      const selected =
        selectedCell === "" ? true : selectedCell !== "false" && selectedCell !== "0" && selectedCell !== "no"
      // Pasted columns were deliberately supplied by the user — manual origin
      // (ruled 2026-07-09), arriving confirmed like a hand-entered field.
      columns.push({
        name,
        path,
        type,
        status: "Confirmed",
        selected,
        levels: null,
        origin: "manual",
      })
    }
    const next = {
      ...v2,
      tables: v2.tables.map((t, ti) => (ti === tableIdx ? { ...t, columns } : t)),
    }
    writeBack(next)
  }
  const addTable = () => {
    const newPath = v2.tables.length === 0 ? "$[:]" : `$[:].table_${v2.tables.length}[:]`
    const newLabel = newPath
    const next = {
      ...v2,
      tables: [
        ...v2.tables,
        {
          path: newPath,
          label: newLabel,
          displayPath: null,
          emit: v2.tables.length === 0,
          columns: [],
        },
      ],
    }
    writeBack(next)
  }
  const removeTable = (i: number) => {
    const next = { ...v2, tables: v2.tables.filter((_, idx) => idx !== i) }
    writeBack(next)
  }
  const inferTables = async () => {
    if (!currentPath) return
    setInferring(true)
    setInferError(null)
    try {
      const result = await inferJsonCacheSchema({ path: currentPath })
      // Route the raw /infer response through `readV2` so unknown column
      // types are coerced instead of being raw-cast into state. Infer is
      // fresh backend output that was never user-persisted, so it opts
      // into dropping structurally-incomplete tables/columns via
      // `{ dropIncomplete: true }` — unlike the disk/render read paths,
      // which now KEEP blanks so the editor surfaces them for repair
      // (render-gate invariant) instead of silently deleting them.
      const inferred = readV2(
        { tables: result.tables as unknown[] },
        { dropIncomplete: true },
      ).tables
      // Refresh the single most-recent-inference snapshot on EVERY infer,
      // whether or not the replace gate is later confirmed.
      setLastInfer(inferred)
      if (v2.tables.length === 0) {
        // First run — nothing to clobber, apply in one click.
        writeBack({ ...v2, tables: inferred })
      } else {
        // The user already has tables: stage the inferred set behind a
        // confirm gate so deliberate edits are never silently lost.
        setPendingInferred(inferred)
      }
    } catch (e) {
      setInferError(e instanceof Error ? e.message : String(e))
    } finally {
      setInferring(false)
    }
  }
  const confirmInferred = () => {
    if (!pendingInferred) return
    writeBack({
      ...v2,
      tables: reconcileInferredTables(v2.tables, pendingInferred, inventory, saltNames),
    })
    setPendingInferred(null)
  }
  const cancelInferred = () => setPendingInferred(null)

  // Using a path as a key CONFIRMS every column already carrying it, on any
  // frame — the source field of a cascade/inherit included (ruled 2026-07-09):
  // deliberately keying on a field witnesses its correctness just as receiving
  // it does, and an unconfirmed source would be eaten by the next re-infer
  // while its descendants persist deeper in the data model. The same act also
  // MARKS the carriers as keys (`key: true`, ruled 2026-07-09) so the schema
  // tracks which fields have been used as keys.
  const confirmColumnsByPath = (
    tables: ApiInputTableV2[],
    usedPaths: ReadonlySet<string>,
  ): ApiInputTableV2[] =>
    tables.map((t) => ({
      ...t,
      columns: t.columns.map((c) =>
        usedPaths.has(c.path) && (c.status !== "Confirmed" || c.key !== true)
          ? { ...c, status: "Confirmed" as const, key: true }
          : c,
      ),
    }))

  // One shared write step every add/cascade/inherit goes through: unique
  // salted name (inventory name transported first), path verbatim, type from
  // the inventory, right origin, arrives Confirmed, inserted at the TOP of the
  // frame's column list (shallowest level first, then add order).
  const addKeyColumnsToFrame = (
    tableIdx: number,
    paths: readonly string[],
    origin: ColumnOrigin = "inherited",
  ) => {
    const table = v2.tables[tableIdx]
    if (!table) return
    const pathSet = new Set(paths)
    const present = new Set(table.columns.map((c) => c.path))
    const fresh = paths.filter((p) => !present.has(p))
    // Even when everything selected is already present, the selection is still
    // a deliberate use of those keys — confirm the carriers; skip the write
    // only when there is truly nothing to insert AND nothing to confirm.
    const anyToConfirm = v2.tables.some((t) =>
      t.columns.some((c) => pathSet.has(c.path) && (c.status !== "Confirmed" || c.key !== true)),
    )
    if (fresh.length === 0 && !anyToConfirm) return
    const withInserts =
      fresh.length === 0
        ? v2.tables
        : v2.tables.map((t, ti) =>
            ti === tableIdx
              ? {
                  ...t,
                  columns: [
                    ...t.columns,
                    ...buildInsertedColumns(
                      fresh,
                      inventory,
                      new Set(t.columns.map((c) => c.name)),
                      origin,
                      saltNames,
                    ),
                  ],
                }
              : t,
          )
    writeBack({ ...v2, tables: orderAllFrames(confirmColumnsByPath(withInserts, pathSet)) })
  }

  // Cascade: push each selected key into every deeper frame on its branch.
  // Adding is idempotent for a given key set — already-present paths are
  // skipped per destination — and one writeBack covers all frames.
  const applyCascade = (paths: readonly string[]) => {
    const additions = new Map<number, string[]>()
    for (const p of paths) {
      for (const di of getCascadeDestinations(p, v2.tables)) {
        const table = v2.tables[di]
        if (table.columns.some((c) => c.path === p)) continue
        const list = additions.get(di)
        if (list) list.push(p)
        else additions.set(di, [p])
      }
    }
    const pathSet = new Set(paths)
    const anyToConfirm = v2.tables.some((t) =>
      t.columns.some((c) => pathSet.has(c.path) && (c.status !== "Confirmed" || c.key !== true)),
    )
    if (additions.size === 0 && !anyToConfirm) return
    const withInserts = v2.tables.map((t, ti) => {
      const list = additions.get(ti)
      if (!list) return t
      const inserted = buildInsertedColumns(
        list,
        inventory,
        new Set(t.columns.map((c) => c.name)),
        "inherited",
        saltNames,
      )
      return { ...t, columns: [...t.columns, ...inserted] }
    })
    // Cascading a key confirms its carriers everywhere — the SOURCE field
    // included, so the next re-infer can't remove the shallow original while
    // its broadcast copies persist deeper in the data model.
    writeBack({ ...v2, tables: orderAllFrames(confirmColumnsByPath(withInserts, pathSet)) })
  }

  // Re-apply the keys-section ordering on every frame — key acts can mark
  // carriers on frames other than the one being edited.
  const orderAllFrames = (tables: ApiInputTableV2[]): ApiInputTableV2[] =>
    tables.map((t) => ({ ...t, columns: orderFrameColumns(t.columns, t.path, jsonOrder) }))

  // Toggle a row's key membership (ruled 2026-07-09): ON marks it a key,
  // confirms it, and moves it into the keys section at the top; OFF returns it
  // to the non-keys in data-model order (confirmation is not revoked — the
  // field was still witnessed).
  const toggleKeyColumn = (tableIdx: number, colIdx: number) => {
    const table = v2.tables[tableIdx]
    const col = table?.columns[colIdx]
    if (!col) return
    let columns: ApiInputColumnV2[]
    if (col.key === true) {
      columns = table.columns.map((c, ci) => (ci === colIdx ? { ...c, key: false } : c))
    } else {
      // Append the newly keyed row so full-depth keys read in toggle (add)
      // order after the stable sort — its old position was non-key order.
      columns = table.columns.filter((_, ci) => ci !== colIdx)
      columns.push({ ...col, key: true, status: "Confirmed" as const })
    }
    writeBack({
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? { ...t, columns: orderFrameColumns(columns, t.path, jsonOrder) }
          : t,
      ),
    })
  }

  // Hand-entered field (inherit-attributes): name transported from the
  // inventory (salted-leaf fallback), supplied type, manual origin, arrives
  // confirmed. A path ALREADY on the
  // frame is never duplicated (ruled 2026-07-09): the existing column is
  // promoted to the top of the schema keeping its internal field-name, type,
  // and origin pill, and is confirmed — same witnessing logic as a cascade.
  const addManualColumn = (tableIdx: number, path: string, type: ColumnType | null) => {
    const table = v2.tables[tableIdx]
    if (!table) return
    const existingIdx = table.columns.findIndex((c) => c.path === path)
    if (existingIdx >= 0) {
      const cols = [...table.columns]
      const [existing] = cols.splice(existingIdx, 1)
      // Append: a promoted field is the newest key, so it lands last among the
      // full-depth keys (add order); the section sort does the rest.
      cols.push({ ...existing, status: "Confirmed" as const, key: true })
      writeBack({
        ...v2,
        tables: v2.tables.map((t, ti) =>
          ti === tableIdx
            ? { ...t, columns: orderFrameColumns(cols, t.path, jsonOrder) }
            : t,
        ),
      })
      return
    }
    if (type === null) return // a NEW entry is not complete without a type
    // Used-name transport (ruled 2026-07-09): a hand-entered path takes the
    // inventory's name for that path first — the same field reads identically
    // across frames — falling back to the salted leaf for a path the editor
    // has never seen.
    const name = dedupNameByPath(
      inventory.get(path)?.name ?? inheritedColumnName(path, saltNames),
      path,
      new Set(table.columns.map((c) => c.name)),
    )
    const newCol: ApiInputColumnV2 = {
      name,
      path,
      type,
      status: "Confirmed",
      selected: true,
      levels: null,
      origin: "manual",
      key: true,
    }
    writeBack({
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? { ...t, columns: orderFrameColumns([...t.columns, newCol], t.path, jsonOrder) }
          : t,
      ),
    })
  }

  const confirmAllColumns = (tableIdx: number) => {
    writeBack({
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? {
              ...t,
              columns: t.columns.map((c) =>
                c.status === "Confirmed" ? c : { ...c, status: "Confirmed" as const },
              ),
            }
          : t,
      ),
    })
  }

  // One fact row per frame for the frames table. An invalid-path frame keeps
  // its row (render-gate: a persisted entry must surface) with the failure
  // named; invalid columns are counted so the row never disagrees with what
  // opening the frame shows.
  const frameRows: FramesTableRow[] = v2.tables.map((t) => {
    const pathError = validateTablePath(t.path)
    return {
      label: t.label,
      path: t.path,
      emit: t.emit,
      columnCount: t.columns.length,
      invalidColumnCount: t.columns.filter((c) => columnInvalidInFrame(c, t.path)).length,
      pathError,
      canInherit: pathError === null && buildInheritGroups(t.path, inventory).length > 0,
    }
  })

  // Keys the cascade picker shows checked+disabled: nowhere left to push —
  // either no deeper frame exists on the key's branch, or every one already
  // carries the key (adding is idempotent, so re-selecting would be a no-op).
  const fullyCascaded = (): Set<string> => {
    const done = new Set<string>()
    for (const key of inventory.keys()) {
      const dests = getCascadeDestinations(key, v2.tables)
      if (
        dests.length === 0 ||
        dests.every((di) => v2.tables[di].columns.some((c) => c.path === key))
      ) {
        done.add(key)
      }
    }
    return done
  }

  return (
    <>
      <div className="px-4 py-3 space-y-3" data-testid="api-input-editor">
        <div
          className="flex items-center gap-2 px-2.5 py-2 rounded-lg text-xs font-medium"
          style={{
            background: withAlpha(accentColor, 0.1),
            border: `1px solid ${withAlpha(accentColor, 0.3)}`,
            color: accentColor,
          }}
        >
          <Radio size={14} />
          <span>This node receives live API requests at deploy time</span>
        </div>

        <PathPickerField
          label="Preview Data"
          sublabel=".json, .jsonl, .ndjson, or .xml"
          value={currentPath ?? ""}
          extensions=".json,.jsonl,.ndjson,.xml"
          onSelect={(path) => {
            onUpdate("path", path)
            fetchForPath(path)
          }}
        />

        {/* Bundle 3b — cache button positioned ABOVE the Tables editor.
            Contextual rationale: the cache action operates on the data
            file selected just above; placing the affordance there
            groups it with the data source and leaves the schema editor
            (Tables) as the primary authoring surface below. */}
        {showCacheButton && (() => {
          // Cache eligibility shares the frontend mirror of backend
          // `table_is_emitting`: emit=true AND at least one selected column.
          const hasSchemaSource = v2.tables.length > 0
          const hasEmitTrue = v2.tables.some((t) => t.emit)
          const hasEmittingTable = apiInputHasEmittingTable({ tables: v2.tables })
          const cacheDisabled = !hasSchemaSource || !hasEmittingTable
          const cacheReason = !hasSchemaSource
            ? "Add at least one table (Infer Tables / Add Table) before caching."
            : !hasEmitTrue
            ? "Toggle at least one table's emit so it produces a frame."
            : !hasEmittingTable
            ? "Select at least one column in an emitted table before caching."
            : undefined
          return (
            <JsonCacheButton
              dataPath={currentPath!}
              configPath={configPath}
              volatileSchema={writeV2(v2)}
              disabled={cacheDisabled}
              disabledReason={cacheReason}
            />
          )
        })()}

        {/* Frames table — the surface cascade and inherit-attributes operate
            from. Hidden while there are no frames yet. Using an entry point
            while the replace-tables confirmation gate is open DISMISSES the
            gate (ruled 2026-07-14): a key act is an implicit "keep my tables",
            so the pending replace is cancelled rather than the act refused. */}
        {v2.tables.length > 0 && (
          <FramesTable
            rows={frameRows}
            accentColor={accentColor}
            onCascade={() => {
              setPendingInferred(null)
              setPicker({ mode: "cascade" })
            }}
            onInherit={(i) => {
              setPendingInferred(null)
              setPicker({ mode: "inherit", tableIdx: i })
            }}
            onAddKeys={(i) => {
              setPendingInferred(null)
              setPicker({ mode: "attributes", tableIdx: i })
            }}
          />
        )}

        {/* Tables editor (v2 surface — the only surface) */}
        <div data-testid="api-input-tables">
            <div className="flex items-center justify-between mb-1.5">
              <label
                className="text-[11px] font-bold uppercase tracking-[0.08em] block"
                style={{ color: "var(--text-muted)" }}
              >
                Tables
              </label>
              <div className="flex items-center gap-2">
                <label
                  className="flex items-center gap-1 text-[10px]"
                  style={{ color: "var(--text-muted)" }}
                >
                  <input
                    data-testid="api-input-salt-toggle"
                    type="checkbox"
                    checked={saltNames}
                    onChange={(e) => setSaltNames(e.target.checked)}
                  />
                  salt names
                  <Tooltip label="Key naming: the dotted part of the path inside its record collapses to underscores — $[:].customer.id becomes customer_id — so sibling leaves like customer.id and order.id stay distinct. Any remaining name collision gets a numeric suffix (_2). Untick to name by the bare leaf (id) instead, relying on the suffix alone.">
                    <HelpCircle
                      size={11}
                      data-testid="api-input-salt-help"
                      style={{ color: "var(--text-muted)" }}
                    />
                  </Tooltip>
                </label>
                {currentPath && (
                  <button
                    data-testid="api-input-infer-btn"
                    onClick={inferTables}
                    disabled={inferring}
                    className="text-[11px] font-semibold px-2 py-0.5 rounded"
                    style={{ color: accentColor, border: `1px solid ${accentColor}` }}
                  >
                    {inferring ? "Inferring..." : "Infer Tables"}
                  </button>
                )}
                <button
                  data-testid="api-input-add-table-btn"
                  onClick={addTable}
                  className="text-[11px] font-semibold px-2 py-0.5 rounded flex items-center gap-1"
                  style={{ color: "var(--text-muted)" }}
                >
                  <Plus size={11} />
                  Add Table
                </button>
              </div>
            </div>
            {inferError && (
              <div
                className="px-2 py-1 rounded text-[11px] mb-1.5"
                style={{
                  background: "var(--danger-soft)",
                  color: "var(--danger-text)",
                }}
              >
                {inferError}
              </div>
            )}
            {pendingInferred && (
              <div
                data-testid="api-input-infer-confirm-banner"
                className="px-2.5 py-2 rounded-md mb-1.5 flex flex-col gap-1.5"
                style={{
                  background: "var(--warning-soft)",
                  border: "1px solid var(--warning-border)",
                }}
              >
                <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-muted)" }}>
                  Re-inferring will replace your current tables. Tables with an
                  unchanged path keep your emit/label edits and pick up the newly
                  inferred columns; the rest are replaced.
                </p>
                <div className="flex items-center gap-2">
                  <button
                    data-testid="api-input-infer-confirm"
                    onClick={confirmInferred}
                    className="text-[11px] font-semibold px-2 py-0.5 rounded"
                    style={{ background: "var(--warning-strong)", color: "var(--text-on-accent)" }}
                  >
                    Replace tables
                  </button>
                  <button
                    data-testid="api-input-infer-cancel"
                    onClick={cancelInferred}
                    className="text-[11px] font-semibold px-2 py-0.5 rounded"
                    style={{ color: "var(--text-muted)" }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
            {v2.tables.length === 0 && (
              <div
                className="text-xs italic"
                style={{ color: "var(--text-muted)" }}
              >
                No tables yet. Click <span className="font-semibold">Infer Tables</span>{" "}
                to auto-populate from the data file, or{" "}
                <span className="font-semibold">Add Table</span> to start from scratch.
              </div>
            )}
            <div className="space-y-2">
              {/* Positional keys, NOT `${table.path}-${ti}`: rows are only
                  ever appended/removed (never reordered), and a key derived
                  from the edited path remounted the row on every committed
                  path change — dropping focus mid-edit (CODE_REVIEW W1.5). */}
              {v2.tables.map((table, ti) => (
                <TableBlock
                  key={ti}
                  table={table}
                  testIdPrefix={`api-input-table-${ti}`}
                  ambiguousNames={ambiguous}
                  validateLabel={validateTableLabel(ti)}
                  commitError={labelCommitErrors[ti] ?? null}
                  onUpdate={(patch) => updateTable(ti, patch)}
                  onRemove={() => removeTable(ti)}
                  onAddColumn={() => addColumn(ti)}
                  onUpdateColumn={(ci, patch) => updateColumn(ti, ci, patch)}
                  onRemoveColumn={(ci) => removeColumn(ti, ci)}
                  onPasteColumns={(grid) => pasteColumns(ti, grid)}
                  onConfirmAll={() => confirmAllColumns(ti)}
                  onToggleKeyColumn={(ci) => toggleKeyColumn(ti, ci)}
                />
              ))}
            </div>
          </div>
      </div>

      {/* Raw source-file schema (top-level columns — e.g. `Struct(...)` /
          `List(...)` for nested fields). This is the un-shredded root, which
          only makes sense as a bootstrap source peek for a fresh node.
          Once the config is v2 (has tables[]), the per-frame tables editor
          ABOVE is the schema view; the raw root schema is redundant and
          misleading for a multi-frame source (it shows opaque Struct types for
          the very fields that get shredded into their own frames), so suppress
          it for v2. */}
      {shape.kind !== "v2" && (
        <>
          {loadingSchema && (
            <div
              className="px-4 py-3"
              style={{ borderTop: "1px solid var(--border)" }}
            >
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                Loading schema...
              </span>
            </div>
          )}

          {schemaError && (
            <div
              className="px-4 py-2 text-xs space-y-1"
              style={{ borderTop: "1px solid var(--border)" }}
            >
              <p role="alert" style={{ color: "var(--danger-text)" }}>
                Could not fetch schema: {schemaError}
              </p>
              {currentPath && (
                <button
                  type="button"
                  onClick={() => fetchForPath(currentPath)}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium"
                  style={{ background: "var(--bg-elevated)", color: "var(--text-primary)" }}
                >
                  Retry schema
                </button>
              )}
            </div>
          )}

          <SchemaPreview schema={schema} />
        </>
      )}

      {/* The one picker dialog in its three modes. Cascade offers the whole
          inventory grouped by level; inherit offers a frame's shallower-level
          keys; add-keys (inherit-attributes) offers the shallower keys first
          (the cascade-compatible ones) then the frame's own level, plus the
          enter-a-field-by-hand section. */}
      {picker?.mode === "cascade" && (
        <KeyPickerModal
          title="Cascade keys"
          targetLabel="each key is pushed into every deeper frame on its branch"
          accentColor={accentColor}
          groups={buildAllKeyGroups(inventory)}
          existingPaths={fullyCascaded()}
          confirmLabel={(n) => `Cascade ${n}`}
          onConfirm={(paths) => {
            applyCascade(paths)
            setPicker(null)
          }}
          onClose={() => setPicker(null)}
        />
      )}
      {picker !== null && picker.mode !== "cascade" && (() => {
        const t = v2.tables[picker.tableIdx]
        if (!t) return null
        const isAttributes = picker.mode === "attributes"
        return (
          <KeyPickerModal
            title={isAttributes ? "Add keys" : "Inherit keys"}
            targetLabel={`${t.label || "(unnamed)"} — ${t.path}`}
            accentColor={accentColor}
            groups={
              isAttributes
                ? attributesGroups(t, inventory)
                : buildInheritGroups(t.path, inventory)
            }
            existingPaths={new Set(t.columns.map((c) => c.path))}
            existingKeyPaths={
              new Set(t.columns.filter((c) => c.key === true).map((c) => c.path))
            }
            onConfirm={(paths) => {
              addKeyColumnsToFrame(picker.tableIdx, paths, "inherited")
              setPicker(null)
            }}
            onClose={() => setPicker(null)}
            manualEntry={
              isAttributes
                ? {
                    validatePath: (p) =>
                      validateColumnPath(p) ?? validateColumnPathAgainstFrame(p, t.path),
                    onAdd: (path, type) => addManualColumn(picker.tableIdx, path, type),
                  }
                : undefined
            }
          />
        )
      })()}
    </>
  )
}

// ─── TableBlock ───────────────────────────────────────────────────

function TableBlock({
  table,
  testIdPrefix,
  ambiguousNames,
  validateLabel,
  commitError,
  onUpdate,
  onRemove,
  onAddColumn,
  onUpdateColumn,
  onRemoveColumn,
  onPasteColumns,
  onConfirmAll,
  onToggleKeyColumn,
}: {
  table: ApiInputTableV2
  testIdPrefix: string
  /** Names carried by more than one distinct path across ALL frames → the
   * distinct paths carrying each (the warning-shade input for ColumnRow). */
  ambiguousNames: ReadonlyMap<string, string[]>
  validateLabel: (candidate: string) => string | null
  commitError: string | null
  onUpdate: (patch: Partial<ApiInputTableV2>) => OnUpdateConfigResult
  onRemove: () => void
  onAddColumn: () => void
  onUpdateColumn: (colIdx: number, patch: Partial<ApiInputColumnV2>) => OnUpdateConfigResult
  onRemoveColumn: (colIdx: number) => void
  /** Replace this table's columns from a pasted tab-separated grid. */
  onPasteColumns: (grid: string[][]) => void
  /** Confirm every not-yet-confirmed column on this frame. */
  onConfirmAll: () => void
  /** Toggle a column's key membership (moves it in/out of the keys section). */
  onToggleKeyColumn: (colIdx: number) => void
}) {
  return (
    <div
      data-testid={testIdPrefix}
      className="px-2 py-2 rounded-md space-y-1.5"
      style={{ border: "1px solid var(--border)", background: "var(--bg-elevated)" }}
    >
      {/* Shared table-actions strip (pushed onto API inputs too): Copy the
          columns as TSV, Share/Save the table's schema as JSON, Save as
          CSV/TSV, and Paste columns in. */}
      <div className="flex items-center justify-end gap-2">
        {/* Confirm-all appears only while the frame has not-yet-confirmed
            columns, and disappears with the last of them. */}
        {table.columns.some((c) => c.status !== "Confirmed") && (
          <button
            type="button"
            data-testid={`${testIdPrefix}-confirm-all`}
            onClick={onConfirmAll}
            title="Confirm every not-yet-confirmed column (confirmed columns survive a re-infer)"
            className="text-[10px] font-semibold px-1.5 py-0.5 rounded flex items-center gap-1"
            style={{ color: "var(--success)", border: "1px solid var(--success-border)" }}
          >
            <Check size={9} />
            Confirm all
          </button>
        )}
        <FrameTableActions
          testIdPrefix={`${testIdPrefix}-table`}
          filename={`api-input-${table.label || "table"}`}
          getGrid={() => ({
            headers: ["name", "path", "type", "selected"],
            rows: table.columns.map((c) => [c.name, c.path, c.type, String(c.selected)]),
          })}
          getSchema={() => ({
            path: table.path,
            label: table.label,
            displayPath: table.displayPath ?? null,
            emit: table.emit,
            row_id_column: table.row_id_column ?? null,
            columns: table.columns.map((c) => ({
              name: c.name,
              path: c.path,
              type: c.type,
              status: c.status,
              selected: c.selected,
              levels: c.levels ?? null,
            })),
          })}
          onPaste={onPasteColumns}
        />
      </div>
      <div className="flex items-start gap-2">
        <label
          className="flex items-center gap-1 text-[11px] pt-1"
          title="Emit this table as a data frame"
        >
          <input
            data-testid={`${testIdPrefix}-emit`}
            type="checkbox"
            checked={table.emit}
            onChange={(e) => onUpdate({ emit: e.target.checked })}
          />
          emit
        </label>
        {/* W1.3/W1.4 — the label IS the frame's handle id (raw, end to
            end through codegen → save → parse). It commits atomically
            on blur/Enter, and blank/duplicate/collision candidates are
            refused with visible validation instead of ever reaching
            config (where a per-keystroke commit used to destroy the
            edges bound to a connected frame). */}
        <CommittedTextInput
          dataTestId={`${testIdPrefix}-label`}
          value={table.label}
          onCommit={(label) => onUpdate({ label })}
          validate={validateLabel}
          commitError={commitError}
          containerClassName="flex-1 min-w-0"
          className="w-full text-xs font-mono px-1.5 py-0.5 rounded"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
        />
        <CommittedTextInput
          dataTestId={`${testIdPrefix}-path`}
          value={table.path}
          onCommit={(path) => onUpdate({ path })}
          validate={validateTablePath}
          containerClassName="flex-1 min-w-0"
          className="w-full text-xs font-mono px-1.5 py-0.5 rounded"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        />
        <button
          data-testid={`${testIdPrefix}-remove`}
          onClick={onRemove}
          title="Remove table"
          className="pt-1"
        >
          <X size={12} style={{ color: "var(--text-muted)" }} />
        </button>
      </div>
      <div className="pl-3 space-y-1">
        {/* Positional keys for the same reason as the table rows above:
            `${col.path}-${ci}` remounted the row (and lost focus) on every
            committed path edit (CODE_REVIEW W1.5). */}
        {table.columns.map((col, ci) => (
          <ColumnRow
            key={ci}
            col={col}
            framePath={table.path}
            ambiguousNames={ambiguousNames}
            testIdPrefix={`${testIdPrefix}-col-${ci}`}
            validateName={(candidate) =>
              columnNameError(
                candidate,
                table.columns.filter((_, i) => i !== ci).map((c) => c.name),
              )
            }
            onUpdate={(patch) => onUpdateColumn(ci, patch)}
            onRemove={() => onRemoveColumn(ci)}
            onToggleKey={() => onToggleKeyColumn(ci)}
          />
        ))}
        <button
          data-testid={`${testIdPrefix}-add-col`}
          onClick={onAddColumn}
          className="text-[11px] font-semibold px-1.5 py-0.5 rounded flex items-center gap-1"
          style={{ color: "var(--text-muted)" }}
        >
          <Plus size={10} />
          Add Column
        </button>
      </div>
    </div>
  )
}

// ─── ColumnRow ────────────────────────────────────────────────────

/**
 * W1.9 — column-name validation, mirroring `validate_v2_schema`: blank
 * names are rejected, and names must be unique WITHIN their table
 * (`seen_col_names` resets per table — the same name in two different
 * tables is legal, each table is its own frame). This refuses an
 * INTERACTIVE blank commit; complementarily, `readV2` no longer drops a
 * blank-name column arriving from disk (it surfaces here with this same
 * error), so the column can never silently vanish from either direction.
 */
function columnNameError(candidate: string, otherNames: readonly string[]): string | null {
  if (!candidate.trim()) {
    return "A name is required — this column is invalid and can't be saved without one."
  }
  if (otherNames.includes(candidate)) {
    return `Duplicate column name: "${candidate}" is already used in this table.`
  }
  return null
}

/** The per-column origin chip: which of inferred / inherited / manual the
 * column is, with a check glyph once confirmed — a confirmed column still
 * reads as what it originally was. */
function OriginChip({
  origin,
  confirmed,
  testId,
}: {
  origin: ColumnOrigin
  confirmed: boolean
  testId: string
}) {
  const palette: Record<ColumnOrigin, { color: string; background: string }> = {
    inferred: { color: "var(--text-muted)", background: "var(--bg-input)" },
    inherited: { color: "var(--accent)", background: "var(--accent-soft)" },
    manual: { color: "var(--success)", background: "var(--success-soft)" },
  }
  return (
    <span
      data-testid={testId}
      title={`${origin}${confirmed ? ", confirmed" : ""}`}
      className="shrink-0 inline-flex items-center gap-0.5 px-1 py-0.5 rounded text-[9px] font-semibold uppercase tracking-wide"
      style={palette[origin]}
    >
      {confirmed && <Check size={8} />}
      {origin}
    </span>
  )
}

function ColumnRow({
  col,
  framePath,
  ambiguousNames,
  testIdPrefix,
  validateName,
  onUpdate,
  onRemove,
  onToggleKey,
}: {
  col: ApiInputColumnV2
  framePath: string
  ambiguousNames: ReadonlyMap<string, string[]>
  testIdPrefix: string
  validateName: (candidate: string) => string | null
  onUpdate: (patch: Partial<ApiInputColumnV2>) => OnUpdateConfigResult
  onRemove: () => void
  onToggleKey: () => void
}) {
  // Cross-frame name collision (ruled 2026-07-14): this name is also carried
  // by a DIFFERENT path somewhere. Warning shade, not an error — the engine
  // only rejects duplicates within one frame — but a name should mean one
  // field everywhere, so surface it for repair.
  const collidingPaths = (ambiguousNames.get(col.name) ?? []).filter((p) => p !== col.path)
  // Against-frame check, recomputed on every render: editing the FRAME's path
  // re-checks its columns against the new path so none are left stranded.
  // Only meaningful when both paths individually pass the grammar (each has
  // its own inline error otherwise).
  const frameError =
    col.path.trim() &&
    validateColumnPath(col.path) === null &&
    validateTablePath(framePath) === null
      ? validateColumnPathAgainstFrame(col.path, framePath)
      : null
  return (
    <div data-testid={testIdPrefix} className="space-y-0.5">
    <div className="flex items-start gap-2 text-[11px]">
      <input
        data-testid={`${testIdPrefix}-selected`}
        type="checkbox"
        checked={col.selected}
        onChange={(e) => onUpdate({ selected: e.target.checked })}
      />
      <CommittedTextInput
        dataTestId={`${testIdPrefix}-name`}
        value={col.name}
        onCommit={(name) => onUpdate({ name })}
        validate={validateName}
        containerClassName="w-32 shrink-0"
        className="w-full px-1 py-0.5 rounded font-mono"
        style={
          collidingPaths.length > 0
            ? {
                background: "var(--danger-soft)",
                border: "1px solid var(--danger-border-strong)",
              }
            : { background: "var(--bg-input)", border: "1px solid var(--border)" }
        }
      />
      {collidingPaths.length > 0 && (
        <Tooltip
          label={`"${col.name}" is also the name of a different field: ${collidingPaths.join(", ")}. A name should mean one field everywhere — rename one of them.`}
        >
          <span data-testid={`${testIdPrefix}-name-collision`} className="shrink-0 mt-0.5">
            <AlertTriangle size={10} style={{ color: "var(--danger-text)" }} />
          </span>
        </Tooltip>
      )}
      <CommittedTextInput
        dataTestId={`${testIdPrefix}-path`}
        value={col.path}
        onCommit={(path) => onUpdate({ path })}
        validate={validateColumnPath}
        containerClassName="flex-1 min-w-0"
        className="w-full px-1 py-0.5 rounded font-mono"
        style={{
          background: "var(--bg-input)",
          border: "1px solid var(--border)",
          color: "var(--text-muted)",
        }}
      />
      <select
        data-testid={`${testIdPrefix}-type`}
        value={col.type}
        onChange={(e) => onUpdate({ type: e.target.value as ColumnType })}
        className="px-1 py-0.5 rounded"
        style={{ background: "var(--bg-input)", border: "1px solid var(--border)" }}
      >
        {COLUMN_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <Tooltip
        label={
          col.key === true
            ? "A key — click to remove it from the keys section (stays confirmed)"
            : "Make this field a key: confirms it and moves it into the keys at the top"
        }
      >
        <button
          type="button"
          data-testid={`${testIdPrefix}-key`}
          aria-pressed={col.key === true}
          onClick={onToggleKey}
          className="shrink-0 mt-0.5"
        >
          <KeyRound
            size={10}
            style={{
              color: col.key === true ? "var(--accent)" : "var(--text-muted)",
              opacity: col.key === true ? 1 : 0.45,
            }}
          />
        </button>
      </Tooltip>
      <OriginChip
        origin={col.origin ?? "inferred"}
        confirmed={col.status === "Confirmed"}
        testId={`${testIdPrefix}-origin`}
      />
      {col.status !== "Confirmed" && (
        <button
          type="button"
          data-testid={`${testIdPrefix}-confirm`}
          onClick={() => onUpdate({ status: "Confirmed" })}
          title="Confirm this column (a confirmed column survives a re-infer)"
        >
          <Check size={10} style={{ color: "var(--success)" }} />
        </button>
      )}
      <button data-testid={`${testIdPrefix}-remove`} onClick={onRemove}>
        <X size={10} style={{ color: "var(--text-muted)" }} />
      </button>
    </div>
    {frameError !== null && (
      <div
        data-testid={`${testIdPrefix}-frame-error`}
        className="px-1.5 py-0.5 rounded text-[10px] leading-snug"
        style={{ background: "var(--danger-soft)", color: "var(--danger-text)" }}
      >
        {frameError}
      </div>
    )}
    </div>
  )
}

// ─── CommittedTextInput ───────────────────────────────────────────
//
// CODE_REVIEW W1.5 (paths) + W1.3/W1.4 (labels) — schema-identity text
// fields buffer locally and commit on blur or Enter instead of writing
// to config per keystroke. The old per-keystroke scheme had coupled
// defects: (1) row keys derived from the path remounted the row on
// each committed keystroke and the input lost focus; (2) every
// half-typed value reached the config, churning structuralVersion
// downstream; (3) for LABELS — which double as React Flow handle ids /
// backend frame names — each keystroke was a live frame-identity change
// that destroyed the edges bound to a connected frame; (4) a
// transiently blank path/label silently destroyed config via readV2.
//
// `validate` closes (4) for deliberate edits too: an invalid candidate
// (blank path; blank/duplicate/sanitised-colliding label; blank or
// per-table-duplicate column name — W1.9) is REFUSED at the commit
// boundary — the draft and a visible error stay in place so the user
// can fix or revert, and nothing destructive ever reaches config. When
// idle, the committed value itself is validated, so invalid states
// arriving from disk or an infer-merge surface without any interaction
// — load-bearing now that `readV2` KEEPS blank-path/blank-name entries
// (default read path) instead of silently dropping them: the kept entry
// renders here and this validation is what makes it visible/repairable.

// ─── INPUT path grammar validation ────────────────────────────────
//
// Previously the table/column path inputs only `requireNonBlank` — the INPUT
// grammar was backend-only, surfaced as a save-time 422 (PATH_GRAMMAR.md).
// These wrap the shared grammar core (`jsonpath.ts`, the mirror of
// `_jsonpath.py`): a TABLE path must end at an array `[:]` or be the root array;
// a COLUMN path must name a leaf (the `$value` reserved leaf is allowed). Blank
// keeps its dedicated blank-guard message — an interactive clear is refused at
// the commit boundary, and a blank arriving from disk (which readV2 now KEEPS)
// is idle-flagged by this same validator — then the grammar decides everything
// else, so an invalid path is caught in-editor, not as a 422 on save.

/** INPUT table-path validator: blank-guard + the shared table-path grammar. */
function validateTablePath(candidate: string): string | null {
  const trimmed = candidate.trim()
  if (!trimmed) {
    return "A path is required — this table is invalid and can't be saved without one."
  }
  return validateInputTablePath(trimmed)
}

/** INPUT column-path validator: blank-guard + the shared column-path grammar. */
function validateColumnPath(candidate: string): string | null {
  if (!candidate.trim()) {
    return "A path is required — this column is invalid and can't be saved without one."
  }
  return validateInputColumnPath(candidate.trim())
}

/** A column the frames table should count as invalid: structurally incomplete
 * (blank name/path), failing the path grammar, or — when the frame's own path
 * is sound — pointing deeper than or sideways from its frame. */
function columnInvalidInFrame(col: ApiInputColumnV2, framePath: string): boolean {
  if (!col.name.trim() || !col.path.trim()) return true
  if (validateColumnPath(col.path) !== null) return true
  if (validateTablePath(framePath) !== null) return false
  return validateColumnPathAgainstFrame(col.path, framePath) !== null
}

/** Candidate groups for the add-keys (inherit-attributes) mode: the frame's
 * shallower-level keys first — exactly the cascade-compatible ones, so they
 * lead the list — then the keys at the frame's own level. */
function attributesGroups(
  table: ApiInputTableV2,
  inventory: ReadonlyMap<string, InventoryKey>,
): InheritGroup[] {
  const shallower = buildInheritGroups(table.path, inventory)
  const grouped = new Set(
    shallower.flatMap((g) => g.candidates.map((c) => c.path)),
  )
  const sameLevel: InventoryKey[] = []
  for (const key of inventory.values()) {
    if (grouped.has(key.path)) continue
    if (validateColumnPathAgainstFrame(key.path, table.path) !== null) continue
    sameLevel.push(key)
  }
  if (sameLevel.length === 0) return shallower
  return [
    ...shallower,
    { ancestorPath: table.path, ancestorLabel: "this level", candidates: sameLevel },
  ]
}

function CommittedTextInput({
  value,
  onCommit,
  validate,
  commitError = null,
  dataTestId,
  containerClassName,
  className,
  style,
}: {
  /** The committed value from config — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur / Enter) with the final value. */
  onCommit: (next: string) => OnUpdateConfigResult
  /** User-facing error for an invalid candidate; null = valid. Invalid
   * candidates are never committed. */
  validate: (candidate: string) => string | null
  dataTestId: string
  containerClassName: string
  className: string
  style: CSSProperties
  /** Graph-level rejection from the commit owner, distinct from local validation. */
  commitError?: string | null
}) {
  // Raw edit buffer; null = not editing, render the committed value.
  const [draft, setDraft] = useState<string | null>(null)
  // External committed-value changes win over a stale draft (React's
  // adjust-state-on-render pattern). This matters because rows use
  // positional keys: after removing the row above, this instance is
  // adopted by the row that slides up, and the dead row's half-typed
  // draft must never be shown for — or committed into — the survivor.
  // Same for a confirmed re-infer replacing the tables wholesale.
  const [lastValue, setLastValue] = useState(value)
  if (lastValue !== value) {
    setLastValue(value)
    setDraft(null)
  }
  const shown = draft ?? value
  const validationError = validate(shown)
  const error = validationError ?? commitError
  const commit = () => {
    if (draft === null) return
    // Skip no-op commits: a draft equal to the committed value would
    // only churn config/structuralVersion without changing anything.
    if (draft === value) {
      setDraft(null)
      return
    }
    // Refuse invalid commits — keep the draft and the visible error so
    // the user sees exactly what was rejected and why. Failing loud at
    // the editor beats a backend 422 at save or a KeyError at run.
    if (validate(draft) !== null) return
    const result = onCommit(draft)
    if (result.ok) setDraft(null)
  }
  return (
    <div className={containerClassName}>
      <input
        data-testid={dataTestId}
        type="text"
        value={shown}
        aria-invalid={error !== null ? true : undefined}
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
    </div>
  )
}
