import { useMemo, useState } from "react"
import { Radio, Check, Plus, X } from "lucide-react"
import { FileBrowser, SchemaPreview } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import { useSchemaFetch } from "../../hooks/useSchemaFetch"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { CacheFetchButton } from "../../components/CacheFetchButton"
import {
  buildJsonCache,
  cancelJsonCache,
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
  type ColumnType,
} from "./apiInputSchema"

// Defect 2 — merge inferred tables into the user's existing tables by
// `path`. For a table whose path the user already has, we keep their
// curated emit/label/displayPath/row_id_column choices and only adopt
// the freshly inferred `columns` (the part that actually reflects a
// changed source JSON). Tables the user has that the inference no
// longer sees are dropped (the source no longer produces them); tables
// the inference adds that the user lacks are appended. This preserves
// deliberate user edits instead of clobbering the whole array.
function mergeInferredTables(
  existing: ApiInputTableV2[],
  inferred: ApiInputTableV2[],
): ApiInputTableV2[] {
  const byPath = new Map(existing.map((t) => [t.path, t]))
  return inferred.map((inf) => {
    const prev = byPath.get(inf.path)
    if (!prev) return inf
    return {
      ...inf,
      label: prev.label,
      emit: prev.emit,
      displayPath: prev.displayPath,
      row_id_column: prev.row_id_column,
    }
  })
}

// ─── JsonCacheButton ──────────────────────────────────────────────
//
// Wraps the shared cache-button. Sends the editor's in-memory v2 as
// `volatile_schema` on every cache POST so the build uses what the user
// is looking at, regardless of whether the on-disk config matches yet
// (working principle 4: volatile vs persistent at the schema plane
// mirrors PR13's data plane). When the editor has nothing to cache
// (no schema source, or no emit:true tables) the button is rendered
// `disabled` rather than firing a no-op POST — T9/T10 in the v1-removal
// contract.

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
  return (
    <CacheFetchButton<JsonCacheStatus>
      resourceKey={dataPath + "::" + (configPath ?? "")}
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
      cancelFetch={(_key) => cancelJsonCache(dataPath)}
      timestampField="cached_at"
      labels={{
        fetchLabel: "Cache as Parquet",
        refreshLabel: "Refresh Cache",
        notCachedHint: "Not cached yet — click to flatten/shred and cache as Parquet",
        pendingLabel: "Processing...",
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
   * `rating/config/quote_input/quotes.json`). Passed through to the
   * cache button so the backend can dispatch v2 vs v1 by inspecting
   * the file's shape. The parent NodePanel computes this from the
   * node's id + nodeType. */
  configPath?: string
}) {
  const currentPath = configField<string | undefined>(config, "path", undefined)
  const { schema, loading: loadingSchema, fetchForPath } = useSchemaFetch(currentPath)
  const showCacheButton =
    !!currentPath && (currentPath.endsWith(".json") || currentPath.endsWith(".jsonl"))
  const [fileExpanded, setFileExpanded] = useState(false)
  const [inferring, setInferring] = useState(false)
  const [inferError, setInferError] = useState<string | null>(null)
  // Defect 2 — when a re-infer would overwrite tables the user has
  // already curated, we stage the normalised inferred tables here and
  // render a confirm/cancel gate instead of clobbering immediately.
  // First run (empty tables) skips the gate and applies in one click.
  const [pendingInferred, setPendingInferred] = useState<ApiInputTableV2[] | null>(null)

  // Classify the config. v2 → render the schema editor with its
  // tables. empty (including any pre-v2 config with stray legacy keys)
  // → render a bare v2 surface the user populates via Infer Tables /
  // Add Table. No migration banner — v1 is treated as if it doesn't
  // exist (working principle 1).
  const shape = useMemo(() => classifyConfig(config), [config])
  const v2: ApiInputConfigV2 =
    shape.kind === "v2" ? shape.v2 : emptyV2(currentPath)

  // Helpers to push state changes back through onUpdate. Each write
  // recomposes the full v2 record-shaped object so we never have to fan
  // out individual onUpdate calls per nested field.
  const writeBack = (next: ApiInputConfigV2) => {
    const raw = writeV2(next)
    // Use a single batched update so NodePanel only fires one
    // handleConfigUpdate (per the existing OnUpdateConfig contract).
    onUpdate(raw)
  }
  const updateTable = (i: number, patch: Partial<ApiInputTableV2>) => {
    const next = {
      ...v2,
      tables: v2.tables.map((t, idx) => (idx === i ? { ...t, ...patch } : t)),
    }
    writeBack(next)
  }
  const updateColumn = (
    tableIdx: number,
    colIdx: number,
    patch: Partial<ApiInputColumnV2>,
  ) => {
    const next = {
      ...v2,
      tables: v2.tables.map((t, ti) =>
        ti === tableIdx
          ? {
              ...t,
              columns: t.columns.map((c, ci) =>
                ci === colIdx ? { ...c, ...patch } : c,
              ),
            }
          : t,
      ),
    }
    writeBack(next)
  }
  const addColumn = (tableIdx: number) => {
    const table = v2.tables[tableIdx]
    const newColPath = `${table.path}.column_${table.columns.length + 1}`
    const newCol: ApiInputColumnV2 = {
      name: `column_${table.columns.length + 1}`,
      path: newColPath,
      type: "str",
      status: "Inferred",
      selected: true,
      levels: null,
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
  const addTable = () => {
    const newPath = v2.tables.length === 0 ? "$[*]" : `$[*].table_${v2.tables.length}[*]`
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
      // Route the raw /infer response through `readV2` so it's
      // sanitised exactly like every other read path (drops malformed
      // tables/columns, coerces unknown column types) instead of being
      // raw-cast into state.
      const inferred = readV2({ tables: result.tables as unknown[] }).tables
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
    // Merge-by-path: preserve the user's curated emit/label choices for
    // tables whose path is unchanged; adopt freshly inferred columns.
    writeBack({ ...v2, tables: mergeInferredTables(v2.tables, pendingInferred) })
    setPendingInferred(null)
  }
  const cancelInferred = () => setPendingInferred(null)

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

        {/* File picker — unchanged from v1 */}
        <div>
          <label
            className="text-[11px] font-bold uppercase tracking-[0.08em] mb-1.5 block"
            style={{ color: "var(--text-muted)" }}
          >
            Preview Data
            <span className="ml-1.5 normal-case tracking-normal font-normal">
              .json or .jsonl
            </span>
          </label>
          {currentPath && (
            <div
              className="px-2.5 py-2 rounded-lg flex items-center gap-2"
              style={{
                background: "var(--success-soft)",
                border: "1px solid var(--success-border)",
              }}
            >
              <Check
                size={14}
                style={{ color: "var(--success)" }}
                className="shrink-0"
              />
              <span
                className="text-xs font-mono truncate flex-1"
                style={{ color: "var(--success-hover)" }}
              >
                {currentPath}
              </span>
              <button
                data-testid="file-change-btn"
                onClick={() => setFileExpanded(!fileExpanded)}
                className="shrink-0 text-[11px] font-semibold px-2 py-0.5 rounded transition-colors"
                style={{ color: "var(--success-hover)" }}
              >
                {fileExpanded ? "close" : "change"}
              </button>
            </div>
          )}
          {(!currentPath || fileExpanded) && (
            <div className="mt-2">
              <FileBrowser
                currentPath={undefined}
                onSelect={(path) => {
                  onUpdate("path", path)
                  fetchForPath(path)
                  setFileExpanded(false)
                }}
                extensions=".json,.jsonl"
              />
            </div>
          )}
        </div>

        {/* Bundle 3b — cache button positioned ABOVE the Tables editor.
            Contextual rationale: the cache action operates on the data
            file selected just above; placing the affordance there
            groups it with the data source and leaves the schema editor
            (Tables) as the primary authoring surface below. */}
        {showCacheButton && (() => {
          // T9/T10: Cache button inactive when EITHER (a) no schema
          // source (no path AND no tables) OR (b) zero emit:true
          // tables. The CacheFetchButton renders disabled via
          // `disabledReason` tooltip + the existing
          // `disabled:opacity-40` class.
          const hasSchemaSource = v2.tables.length > 0
          const hasEmitTrue = v2.tables.some((t) => t.emit)
          const cacheDisabled = !hasSchemaSource || !hasEmitTrue
          const cacheReason = !hasSchemaSource
            ? "Add at least one table (Infer Tables / Add Table) before caching."
            : !hasEmitTrue
            ? "Toggle at least one table's emit so it produces a port."
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
              {v2.tables.map((table, ti) => (
                <TableBlock
                  key={`${table.path}-${ti}`}
                  table={table}
                  testIdPrefix={`api-input-table-${ti}`}
                  onUpdate={(patch) => updateTable(ti, patch)}
                  onRemove={() => removeTable(ti)}
                  onAddColumn={() => addColumn(ti)}
                  onUpdateColumn={(ci, patch) => updateColumn(ti, ci, patch)}
                  onRemoveColumn={(ci) => removeColumn(ti, ci)}
                />
              ))}
            </div>
          </div>
      </div>

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

      <SchemaPreview schema={schema} />
    </>
  )
}

// ─── TableBlock ───────────────────────────────────────────────────

function TableBlock({
  table,
  testIdPrefix,
  onUpdate,
  onRemove,
  onAddColumn,
  onUpdateColumn,
  onRemoveColumn,
}: {
  table: ApiInputTableV2
  testIdPrefix: string
  onUpdate: (patch: Partial<ApiInputTableV2>) => void
  onRemove: () => void
  onAddColumn: () => void
  onUpdateColumn: (colIdx: number, patch: Partial<ApiInputColumnV2>) => void
  onRemoveColumn: (colIdx: number) => void
}) {
  return (
    <div
      data-testid={testIdPrefix}
      className="px-2 py-2 rounded-md space-y-1.5"
      style={{ border: "1px solid var(--border)", background: "var(--bg-soft)" }}
    >
      <div className="flex items-center gap-2">
        <label
          className="flex items-center gap-1 text-[11px]"
          title="Emit this table as a data port"
        >
          <input
            data-testid={`${testIdPrefix}-emit`}
            type="checkbox"
            checked={table.emit}
            onChange={(e) => onUpdate({ emit: e.target.checked })}
          />
          emit
        </label>
        <input
          data-testid={`${testIdPrefix}-label`}
          type="text"
          value={table.label}
          onChange={(e) => onUpdate({ label: e.target.value })}
          className="flex-1 text-xs font-mono px-1.5 py-0.5 rounded"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border)",
            color: "var(--text)",
          }}
        />
        <input
          data-testid={`${testIdPrefix}-path`}
          type="text"
          value={table.path}
          onChange={(e) => onUpdate({ path: e.target.value })}
          className="flex-1 text-xs font-mono px-1.5 py-0.5 rounded"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border)",
            color: "var(--text-muted)",
          }}
        />
        <button
          data-testid={`${testIdPrefix}-remove`}
          onClick={onRemove}
          title="Remove table"
        >
          <X size={12} style={{ color: "var(--text-muted)" }} />
        </button>
      </div>
      <div className="pl-3 space-y-1">
        {table.columns.map((col, ci) => (
          <ColumnRow
            key={`${col.path}-${ci}`}
            col={col}
            testIdPrefix={`${testIdPrefix}-col-${ci}`}
            onUpdate={(patch) => onUpdateColumn(ci, patch)}
            onRemove={() => onRemoveColumn(ci)}
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

function ColumnRow({
  col,
  testIdPrefix,
  onUpdate,
  onRemove,
}: {
  col: ApiInputColumnV2
  testIdPrefix: string
  onUpdate: (patch: Partial<ApiInputColumnV2>) => void
  onRemove: () => void
}) {
  return (
    <div data-testid={testIdPrefix} className="flex items-center gap-2 text-[11px]">
      <input
        data-testid={`${testIdPrefix}-selected`}
        type="checkbox"
        checked={col.selected}
        onChange={(e) => onUpdate({ selected: e.target.checked })}
      />
      <input
        data-testid={`${testIdPrefix}-name`}
        type="text"
        value={col.name}
        onChange={(e) => onUpdate({ name: e.target.value })}
        className="w-32 px-1 py-0.5 rounded font-mono"
        style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
      />
      <input
        data-testid={`${testIdPrefix}-path`}
        type="text"
        value={col.path}
        onChange={(e) => onUpdate({ path: e.target.value })}
        className="flex-1 px-1 py-0.5 rounded font-mono"
        style={{
          background: "var(--bg)",
          border: "1px solid var(--border)",
          color: "var(--text-muted)",
        }}
      />
      <select
        data-testid={`${testIdPrefix}-type`}
        value={col.type}
        onChange={(e) => onUpdate({ type: e.target.value as ColumnType })}
        className="px-1 py-0.5 rounded"
        style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
      >
        {COLUMN_TYPES.map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <button data-testid={`${testIdPrefix}-remove`} onClick={onRemove}>
        <X size={10} style={{ color: "var(--text-muted)" }} />
      </button>
    </div>
  )
}
