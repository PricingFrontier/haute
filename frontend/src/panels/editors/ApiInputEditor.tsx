import { useMemo, useState } from "react"
import { Radio, Check, Plus, X, AlertTriangle } from "lucide-react"
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
  legacyToV2,
  writeV2,
  type ApiInputConfigV2,
  type ApiInputColumnV2,
  type ApiInputTableV2,
  type ColumnType,
} from "./apiInputSchema"

// ─── JsonCacheButton ──────────────────────────────────────────────
//
// Wraps the shared cache-button. The status check uses the v1 GET-by-path
// shape when no schema is on disk yet (i.e. no config_path supplied),
// and the POST-with-config_path shape otherwise (which also handles the
// v2 dispatch on the backend per commit 3).

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
}: {
  dataPath: string
  configPath?: string
}) {
  return (
    <CacheFetchButton<JsonCacheStatus>
      resourceKey={dataPath + "::" + (configPath ?? "")}
      getStatus={(_key) =>
        configPath
          ? getJsonCacheStatusForSchema({ path: dataPath, config_path: configPath })
          : getJsonCacheStatus(dataPath)
      }
      startFetch={(_key) =>
        buildJsonCache({ path: dataPath, config_path: configPath }).then(
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

  // Classify the config. v2 → render the schema editor. v1 → show the
  // migration banner with an explicit Migrate button. empty → render a
  // bare v2 surface the user can populate from scratch.
  const shape = useMemo(() => classifyConfig(config), [config])
  const v2: ApiInputConfigV2 =
    shape.kind === "v2"
      ? shape.v2
      : shape.kind === "v1"
      ? legacyToV2(shape.raw)
      : emptyV2(currentPath)

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
  const acceptMigration = () => {
    // Materialise the migrated v2 (or empty v2 if there's nothing to
    // migrate) onto disk. Subsequent saves now write v2.
    writeBack(v2)
  }
  const inferTables = async () => {
    if (!currentPath) return
    setInferring(true)
    setInferError(null)
    try {
      const result = await inferJsonCacheSchema({ path: currentPath })
      // Merge inferred tables into the current v2 by replacing the
      // whole `tables` array — the user can prune/edit afterwards.
      const inferred = result.tables as unknown as ApiInputTableV2[]
      writeBack({ ...v2, tables: inferred })
    } catch (e) {
      setInferError(e instanceof Error ? e.message : String(e))
    } finally {
      setInferring(false)
    }
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

        {/* Migration banner for v1 configs */}
        {shape.kind === "v1" && (
          <div
            data-testid="api-input-migration-banner"
            className="px-2.5 py-2 rounded-lg text-xs space-y-2"
            style={{
              background: "var(--warning-soft)",
              border: "1px solid var(--warning-border)",
              color: "var(--warning-strong)",
            }}
          >
            <div className="flex items-start gap-2">
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div>
                This API Input uses the legacy schema mapping. Click{" "}
                <span className="font-semibold">Migrate</span> to convert it to the new
                multi-frame format. Existing column types and renames are preserved.
              </div>
            </div>
            <button
              data-testid="api-input-migrate-btn"
              onClick={acceptMigration}
              className="px-2 py-1 rounded text-[11px] font-semibold"
              style={{
                background: "var(--warning)",
                color: "var(--bg)",
              }}
            >
              Migrate to v2
            </button>
          </div>
        )}

        {/* Tables editor (v2 surface) */}
        {(shape.kind === "v2" || shape.kind === "empty") && (
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
        )}

        {showCacheButton && (
          <JsonCacheButton dataPath={currentPath!} configPath={configPath} />
        )}
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
