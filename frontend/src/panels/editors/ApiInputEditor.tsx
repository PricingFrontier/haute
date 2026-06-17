import { useMemo, useState, type CSSProperties } from "react"
import { Radio, Check, Plus, X } from "lucide-react"
import { FileBrowser, SchemaPreview } from "./_shared"
import type { OnUpdateConfig } from "./_shared"
import { useSchemaFetch } from "../../hooks/useSchemaFetch"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import {
  apiInputLabelIssue,
  apiInputLabelIssueMessage,
} from "../../utils/apiInputPorts"
import { CacheFetchButton } from "../../components/CacheFetchButton"
import { FrameTableActions } from "./FrameTableActions"
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
  // Bundle 3d — PASTE-IN for a table's columns. The pasted grid is
  // tab-separated `name<TAB>path<TAB>type<TAB>selected` rows (the shape Copy
  // emits). A recognised header row is dropped; pasted columns REPLACE the
  // table's existing columns. Unknown types coerce to "str" (mirroring
  // readV2); blank-name/path rows are skipped (they'd be dropped on read
  // anyway). Pasted columns are author-confirmed (status "Confirmed").
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
      columns.push({ name, path, type, status: "Confirmed", selected, levels: null })
    }
    const next = {
      ...v2,
      tables: v2.tables.map((t, ti) => (ti === tableIdx ? { ...t, columns } : t)),
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
            ? "Toggle at least one table's emit so it produces a frame."
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
              {/* Positional keys, NOT `${table.path}-${ti}`: rows are only
                  ever appended/removed (never reordered), and a key derived
                  from the edited path remounted the row on every committed
                  path change — dropping focus mid-edit (CODE_REVIEW W1.5). */}
              {v2.tables.map((table, ti) => (
                <TableBlock
                  key={ti}
                  table={table}
                  testIdPrefix={`api-input-table-${ti}`}
                  validateLabel={validateTableLabel(ti)}
                  onUpdate={(patch) => updateTable(ti, patch)}
                  onRemove={() => removeTable(ti)}
                  onAddColumn={() => addColumn(ti)}
                  onUpdateColumn={(ci, patch) => updateColumn(ti, ci, patch)}
                  onRemoveColumn={(ci) => removeColumn(ti, ci)}
                  onPasteColumns={(grid) => pasteColumns(ti, grid)}
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
  validateLabel,
  onUpdate,
  onRemove,
  onAddColumn,
  onUpdateColumn,
  onRemoveColumn,
  onPasteColumns,
}: {
  table: ApiInputTableV2
  testIdPrefix: string
  validateLabel: (candidate: string) => string | null
  onUpdate: (patch: Partial<ApiInputTableV2>) => void
  onRemove: () => void
  onAddColumn: () => void
  onUpdateColumn: (colIdx: number, patch: Partial<ApiInputColumnV2>) => void
  onRemoveColumn: (colIdx: number) => void
  /** Replace this table's columns from a pasted tab-separated grid. */
  onPasteColumns: (grid: string[][]) => void
}) {
  return (
    <div
      data-testid={testIdPrefix}
      className="px-2 py-2 rounded-md space-y-1.5"
      style={{ border: "1px solid var(--border)", background: "var(--bg-soft)" }}
    >
      {/* Shared table-actions strip (pushed onto API inputs too): Copy the
          columns as TSV, Share/Save the table's schema as JSON, Save as
          CSV/TSV, and Paste columns in. */}
      <div className="flex justify-end">
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
          containerClassName="flex-1 min-w-0"
          className="w-full text-xs font-mono px-1.5 py-0.5 rounded"
          style={{
            background: "var(--bg)",
            border: "1px solid var(--border)",
            color: "var(--text)",
          }}
        />
        <CommittedTextInput
          dataTestId={`${testIdPrefix}-path`}
          value={table.path}
          onCommit={(path) => onUpdate({ path })}
          validate={requireNonBlank("A path is required — clearing it would delete this table from the schema.")}
          containerClassName="flex-1 min-w-0"
          className="w-full text-xs font-mono px-1.5 py-0.5 rounded"
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
            testIdPrefix={`${testIdPrefix}-col-${ci}`}
            validateName={(candidate) =>
              columnNameError(
                candidate,
                table.columns.filter((_, i) => i !== ci).map((c) => c.name),
              )
            }
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

/**
 * W1.9 — column-name validation, mirroring `validate_v2_schema`: blank
 * names are rejected, and names must be unique WITHIN their table
 * (`seen_col_names` resets per table — the same name in two different
 * tables is legal, each table is its own frame). Refusing at the commit
 * boundary also closes the readV2 silent-drop: a committed blank name
 * deleted the column row instantly.
 */
function columnNameError(candidate: string, otherNames: readonly string[]): string | null {
  if (!candidate.trim()) {
    return "A name is required — clearing it would delete this column from the schema."
  }
  if (otherNames.includes(candidate)) {
    return `Duplicate column name: "${candidate}" is already used in this table.`
  }
  return null
}

function ColumnRow({
  col,
  testIdPrefix,
  validateName,
  onUpdate,
  onRemove,
}: {
  col: ApiInputColumnV2
  testIdPrefix: string
  validateName: (candidate: string) => string | null
  onUpdate: (patch: Partial<ApiInputColumnV2>) => void
  onRemove: () => void
}) {
  return (
    <div data-testid={testIdPrefix} className="flex items-start gap-2 text-[11px]">
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
        style={{ background: "var(--bg)", border: "1px solid var(--border)" }}
      />
      <CommittedTextInput
        dataTestId={`${testIdPrefix}-path`}
        value={col.path}
        onCommit={(path) => onUpdate({ path })}
        validate={requireNonBlank("A path is required — clearing it would delete this column from the schema.")}
        containerClassName="flex-1 min-w-0"
        className="w-full px-1 py-0.5 rounded font-mono"
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
// arriving from disk or an infer-merge surface without any
// interaction.

/** Validator for fields where a blank value would destroy config. */
function requireNonBlank(message: string): (candidate: string) => string | null {
  return (candidate) => (candidate.trim() ? null : message)
}

function CommittedTextInput({
  value,
  onCommit,
  validate,
  dataTestId,
  containerClassName,
  className,
  style,
}: {
  /** The committed value from config — the source of truth when idle. */
  value: string
  /** Called once per commit boundary (blur / Enter) with the final value. */
  onCommit: (next: string) => void
  /** User-facing error for an invalid candidate; null = valid. Invalid
   * candidates are never committed. */
  validate: (candidate: string) => string | null
  dataTestId: string
  containerClassName: string
  className: string
  style: CSSProperties
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
  const error = validate(shown)
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
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") commit()
        }}
        className={className}
        style={error !== null ? { ...style, border: "1px solid var(--danger-border-strong)" } : style}
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
