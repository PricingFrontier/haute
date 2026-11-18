import { useState, useRef, useEffect, useCallback } from "react"
import { Check, ChevronDown, Loader2, RefreshCw } from "lucide-react"
import { getWarehouses, getCatalogs, getSchemas, getTables } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import { CommittedTextField } from "../../components/form"
import { useCommittedDraft } from "../../components/form/useCommittedDraft"

// ─── WarehousePicker ──────────────────────────────────────────────
//
// The compute the Databricks read runs on. Choosing one of the workspace's
// SQL warehouses (serverless included) is the default; "Enter HTTP path
// manually" covers all-purpose clusters, which the warehouse list cannot
// offer. The list loads as soon as the picker shows, so a saved path
// resolves to its warehouse name and a workspace with a single warehouse
// needs no choice at all.

type Warehouse = {
  id: string
  name: string
  http_path: string
  state: string
  size: string
  serverless?: boolean
}

const WAREHOUSE_PATH_PREFIX = "/sql/1.0/warehouses/"

function isManualPath(httpPath: string): boolean {
  return httpPath !== "" && !httpPath.startsWith(WAREHOUSE_PATH_PREFIX)
}

function stateColor(state: string): string {
  if (state === "RUNNING") return "var(--success)"
  if (state === "STOPPED" || state === "DELETED") return "var(--danger)"
  return "var(--warning-strong)"
}

function WarehouseSummary({ warehouse }: { warehouse: Warehouse }) {
  return (
    <>
      <span
        className="w-1.5 h-1.5 rounded-full flex-shrink-0"
        style={{ background: stateColor(warehouse.state) }}
        title={warehouse.state}
      />
      <span className="font-medium truncate">{warehouse.name}</span>
      {warehouse.serverless && (
        <span
          className="text-[9px] font-semibold uppercase tracking-[0.06em] px-1 rounded flex-shrink-0"
          style={{ background: 'var(--accent-soft)', color: 'var(--text-accent)' }}
        >
          Serverless
        </span>
      )}
    </>
  )
}

export function WarehousePicker({
  httpPath,
  onSelect,
  purpose = "read",
}: {
  httpPath: string
  onSelect: (httpPath: string) => void
  /** What the warehouse runs, for the required-field hint. */
  purpose?: "read" | "write"
}) {
  const [startedManual] = useState(() => isManualPath(httpPath))
  const [manual, setManual] = useState(startedManual)
  const [warehouses, setWarehouses] = useState<Warehouse[] | null>(null)
  const [loading, setLoading] = useState(!startedManual)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState(false)
  const requested = useRef(false)
  const latest = useRef({ httpPath, onSelect })
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    latest.current = { httpPath, onSelect }
  })

  const fetchWarehouses = useCallback(() => {
    requested.current = true
    getWarehouses()
      .then((data) => {
        const list = data.warehouses || []
        setWarehouses(list)
        setLoading(false)
        const { httpPath: current, onSelect: select } = latest.current
        if (!current && list.length === 1) select(list[0].http_path)
      })
      .catch((e: Error) => {
        setError(apiErrorMessage(e))
        setLoading(false)
      })
  }, [])

  const reload = () => {
    setLoading(true)
    setError(null)
    fetchWarehouses()
  }

  useEffect(() => {
    if (!startedManual && !requested.current) fetchWarehouses()
  }, [startedManual, fetchWarehouses])

  useEffect(() => {
    if (!open) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", closeOnOutsideClick)
    return () => document.removeEventListener("mousedown", closeOnOutsideClick)
  }, [open])

  const selected = warehouses?.find((wh) => wh.http_path === httpPath)
  const missing = httpPath !== "" && warehouses !== null && !selected
  const required = !httpPath && !loading
  const fieldStyle = {
    background: 'var(--bg-input)',
    border: `1px solid ${required ? 'var(--warning-border-emphasis)' : 'var(--border)'}`,
    color: 'var(--text-primary)',
  }

  const chooseFromList = () => {
    setManual(false)
    if (!requested.current) reload()
  }

  return (
    <div
      ref={rootRef}
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          event.stopPropagation()
          setOpen(false)
        }
      }}
    >
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>SQL Warehouse</label>
        {!manual && (
          <button
            type="button"
            onClick={reload}
            disabled={loading}
            className="p-0.5 rounded transition-colors disabled:opacity-40"
            style={{ color: 'var(--text-muted)' }}
            title="Refresh warehouses"
            aria-label="Refresh warehouses"
          >
            <RefreshCw size={11} className={loading ? "animate-spin" : undefined} />
          </button>
        )}
      </div>

      {manual ? (
        <div className="mt-1 space-y-1">
          <CommittedTextField
            type="text"
            aria-label="HTTP path"
            placeholder="Paste an HTTP path"
            value={httpPath}
            onCommit={(value) => onSelect(value.trim())}
            className="focus-ring w-full px-2.5 py-1.5 text-xs font-mono rounded-lg"
            style={fieldStyle}
          />
          <p className="text-[10px]" style={{ color: 'var(--text-muted)' }}>
            From a warehouse's Connection details tab, or an all-purpose cluster's JDBC/ODBC tab.
          </p>
          <button
            type="button"
            onClick={chooseFromList}
            className="text-[10px] font-medium underline-offset-2 hover:underline"
            style={{ color: 'var(--text-accent)' }}
          >
            Choose from warehouse list
          </button>
        </div>
      ) : (
        <>
          <button
            type="button"
            aria-haspopup="listbox"
            aria-expanded={open}
            onClick={() => setOpen((current) => !current)}
            className="focus-ring mt-1 w-full flex items-center gap-2 px-2.5 py-1.5 text-xs rounded-lg text-left"
            style={fieldStyle}
          >
            {selected ? (
              <WarehouseSummary warehouse={selected} />
            ) : httpPath ? (
              <span className="font-mono truncate">{httpPath}</span>
            ) : (
              <span style={{ color: 'var(--text-muted)' }}>
                {loading ? "Loading warehouses..." : "Select SQL warehouse..."}
              </span>
            )}
            <ChevronDown size={12} className="ml-auto flex-shrink-0" style={{ color: 'var(--text-muted)' }} />
          </button>

          {required && !open && (
            <p className="mt-1 text-[10px]" style={{ color: 'var(--warning)' }}>
              Required. Haute runs the {purpose} on this warehouse.
            </p>
          )}
          {missing && (
            <p className="mt-1 text-[10px]" style={{ color: 'var(--warning)' }}>
              This warehouse is not in the workspace's list. It may have been deleted, or your token cannot see it.
            </p>
          )}

          {open && (
            <div className="mt-1.5 rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)', background: 'var(--bg-input)' }}>
              {loading ? (
                <div className="flex items-center gap-1.5 px-2.5 py-2 text-[10px]" style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                  <Loader2 size={11} className="animate-spin" /> Loading warehouses...
                </div>
              ) : warehouses && warehouses.length > 0 ? (
                <div role="listbox" aria-label="SQL warehouses">
                  {warehouses.map((wh) => (
                    <button
                      key={wh.id}
                      type="button"
                      role="option"
                      aria-selected={httpPath === wh.http_path}
                      onClick={() => {
                        onSelect(wh.http_path)
                        setOpen(false)
                      }}
                      className="w-full flex items-center gap-2 px-2.5 py-2 text-xs transition-colors hover:brightness-110"
                      style={{
                        background: httpPath === wh.http_path ? 'var(--accent-soft)' : 'transparent',
                        borderBottom: '1px solid var(--border)',
                        color: 'var(--text-primary)',
                      }}
                    >
                      <WarehouseSummary warehouse={wh} />
                      {wh.size && (
                        <span className="text-[10px] ml-auto flex-shrink-0" style={{ color: 'var(--text-muted)' }}>{wh.size}</span>
                      )}
                      {httpPath === wh.http_path && <Check size={12} style={{ color: 'var(--accent)' }} className="flex-shrink-0" />}
                    </button>
                  ))}
                </div>
              ) : warehouses ? (
                <div className="px-2.5 py-2 text-[10px]" style={{ borderBottom: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                  No SQL Warehouses found in this workspace
                </div>
              ) : null}
              <button
                type="button"
                onClick={() => {
                  setManual(true)
                  setOpen(false)
                }}
                className="w-full px-2.5 py-2 text-[11px] text-left transition-colors hover:brightness-110"
                style={{ color: 'var(--text-secondary)' }}
              >
                Enter HTTP path manually...
              </button>
            </div>
          )}
        </>
      )}

      {error && !manual && (
        <div className="mt-1.5 text-[10px] px-2 py-1 rounded" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
    </div>
  )
}

// ─── Catalog and schema ───────────────────────────────────────────
//
// Both table pickers start from a Unity Catalog catalog and schema. Each list
// loads when its select is focused (and the schemas when a catalog is chosen);
// a saved name the list lacks still shows as an option.

type CatalogItem = { name: string; comment: string }
type SchemaItem = { name: string; comment: string }
type TableItem = { name: string; full_name: string; table_type: string; comment: string }

const SELECT_STYLE = {
  background: 'var(--bg-input)',
  border: '1px solid var(--border)',
  color: 'var(--text-primary)',
}
const SELECT_CLASS = "w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2 disabled:opacity-40"

function CatalogSchemaSelects({
  catalog,
  schema,
  onCatalogChange,
  onSchemaChange,
  onError,
}: {
  catalog: string
  schema: string
  onCatalogChange: (catalog: string) => void
  onSchemaChange: (schema: string) => void
  /** A list's failure, or null when a list is asked for again. */
  onError: (message: string | null) => void
}) {
  const [catalogs, setCatalogs] = useState<CatalogItem[]>([])
  // The schemas of one catalog, so another catalog's never show.
  const [schemas, setSchemas] = useState<{ catalog: string; items: SchemaItem[] } | null>(null)
  const [loadingCatalogs, setLoadingCatalogs] = useState(false)
  const [loadingSchemas, setLoadingSchemas] = useState(false)

  const refreshCatalogs = () => {
    setLoadingCatalogs(true)
    onError(null)
    getCatalogs()
      .then((data) => { setCatalogs(data.catalogs || []); setLoadingCatalogs(false) })
      .catch((e: unknown) => { onError(apiErrorMessage(e)); setLoadingCatalogs(false) })
  }
  const refreshSchemas = (cat: string) => {
    setLoadingSchemas(true)
    onError(null)
    getSchemas(cat)
      .then((data) => { setSchemas({ catalog: cat, items: data.schemas || [] }); setLoadingSchemas(false) })
      .catch((e: unknown) => { onError(apiErrorMessage(e)); setLoadingSchemas(false) })
  }
  const schemaItems = schemas?.catalog === catalog ? schemas.items : []

  return (
    <>
      <select
        aria-label="Catalog"
        value={catalog}
        onFocus={refreshCatalogs}
        onChange={(e) => {
          const cat = e.target.value
          onCatalogChange(cat)
          if (cat) refreshSchemas(cat)
        }}
        className={SELECT_CLASS}
        style={SELECT_STYLE}
      >
        <option value="">{loadingCatalogs ? "Loading..." : "Select catalog..."}</option>
        {catalog && catalogs.every((c) => c.name !== catalog) && (
          <option value={catalog}>{catalog}</option>
        )}
        {catalogs.map((c) => (
          <option key={c.name} value={c.name}>{c.name}{c.comment ? ` - ${c.comment}` : ""}</option>
        ))}
      </select>

      <select
        aria-label="Schema"
        value={schema}
        disabled={!catalog}
        onFocus={() => { if (catalog) refreshSchemas(catalog) }}
        onChange={(e) => onSchemaChange(e.target.value)}
        className={SELECT_CLASS}
        style={SELECT_STYLE}
      >
        <option value="">{loadingSchemas ? "Loading..." : catalog ? "Select schema..." : "Select catalog first"}</option>
        {schema && schemaItems.every((s) => s.name !== schema) && (
          <option value={schema}>{schema}</option>
        )}
        {schemaItems.map((s) => (
          <option key={s.name} value={s.name}>{s.name}{s.comment ? ` - ${s.comment}` : ""}</option>
        ))}
      </select>
    </>
  )
}

/** A table's three parts, as the pickers show them. */
type TableParts = { catalog: string; schema: string; table: string }

function tableParts(table: string, quoted = false): TableParts {
  const p = table ? (quoted ? table.replace(/`/g, "") : table).split(".") : []
  return { catalog: p[0] || "", schema: p[1] || "", table: p[2] || "" }
}

const sameParts = (a: TableParts, b: TableParts) => a.catalog === b.catalog && a.schema === b.schema && a.table === b.table

/**
 * A picker's parts as the shared draft: a catalog or schema chosen before its table is
 * written as `""`, which the config cannot hold, so the picker's own choice keeps the
 * draft while an Undo, a Redo or another editor's change of the table lets it go.
 */
function useTableDraft(table: string, quoted: boolean, fullName: (parts: TableParts) => string, onSelect: (fullName: string) => void) {
  return useCommittedDraft(
    tableParts(table, quoted),
    (next) => {
      const full = fullName(next)
      onSelect(full)
      return { ok: true, stays: tableParts(full, quoted) }
    },
    { equals: sameParts },
  )
}

/**
 * The tables of the schema on show: `load(catalog, schema)` asks for a schema's
 * tables, and only the answer to the latest ask, for the schema on show, is listed.
 */
function useSchemaTables(catalog: string, schema: string, onError: (message: string) => void) {
  const key = catalog && schema ? `${catalog}.${schema}` : ""
  const [listed, setListed] = useState<{ key: string; items: TableItem[] } | null>(null)
  const [requested, setRequested] = useState("")
  const latest = useRef({ requested: "", onError })
  useEffect(() => {
    latest.current.onError = onError
  })
  const load = useCallback((cat: string, sch: string) => {
    const asked = `${cat}.${sch}`
    latest.current.requested = asked
    setRequested(asked)
    getTables(cat, sch)
      .then((data) => {
        if (latest.current.requested === asked) setListed({ key: asked, items: data.tables || [] })
      })
      .catch((e: unknown) => {
        if (latest.current.requested === asked) latest.current.onError(apiErrorMessage(e))
      })
  }, [])
  const tables = listed?.key === key ? listed.items : null
  return { tables, loading: key !== "" && requested === key && tables === null, load }
}

// ─── CatalogTablePicker ───────────────────────────────────────────

export function CatalogTablePicker({
  table,
  onSelect,
}: {
  table: string
  onSelect: (fullName: string) => void
}) {
  // Clearing the table on a new catalog or schema is written as `""`: the catalog and
  // schema just chosen stay on screen.
  const draft = useTableDraft(table, false, (p) => (p.catalog && p.schema && p.table ? `${p.catalog}.${p.schema}.${p.table}` : ""), onSelect)
  const { catalog, schema: dbSchema, table: tableName } = draft.shown
  const [error, setError] = useState<string | null>(null)

  const { tables, loading: loadingTables, load: loadTables } = useSchemaTables(catalog, dbSchema, setError)
  const refreshTables = (cat: string, sch: string) => {
    setError(null)
    loadTables(cat, sch)
  }
  const tableItems = tables ?? []

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Table</label>

      <div className="mt-1 space-y-1.5">
        <CatalogSchemaSelects
          catalog={catalog}
          schema={dbSchema}
          onCatalogChange={(cat) => draft.commit({ catalog: cat, schema: "", table: "" })}
          onSchemaChange={(sch) => {
            draft.commit({ catalog, schema: sch, table: "" })
            if (sch) refreshTables(catalog, sch)
          }}
          onError={setError}
        />

        <select
          aria-label="Table"
          value={tableName}
          disabled={!dbSchema}
          onFocus={() => { if (catalog && dbSchema) refreshTables(catalog, dbSchema) }}
          onChange={(e) => draft.commit({ catalog, schema: dbSchema, table: e.target.value })}
          className={SELECT_CLASS}
          style={SELECT_STYLE}
        >
          <option value="">{loadingTables ? "Loading..." : dbSchema ? "Select table..." : "Select schema first"}</option>
          {tableName && tableItems.every((t) => t.name !== tableName) && (
            <option value={tableName}>{tableName}</option>
          )}
          {tableItems.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}{t.table_type ? ` (${t.table_type})` : ""}{t.comment ? ` - ${t.comment}` : ""}
            </option>
          ))}
        </select>
      </div>

      {table && (
        <div className="mt-1.5 text-[10px] font-mono px-1" style={{ color: 'var(--text-muted)' }}>
          {table}
        </div>
      )}

      {error && (
        <div className="mt-1.5 text-[10px] px-2 py-1 rounded" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
    </div>
  )
}

// ─── TargetTablePicker ────────────────────────────────────────────
//
// The table a Databricks Data Output writes. The catalog and schema are
// chosen from Unity Catalog; the table is a name, which may be one of the
// schema's tables or a new one the write creates.

const TABLE_NAME_RE = /^[\w-]+$/

export function TargetTablePicker({
  table,
  onSelect,
}: {
  table: string
  onSelect: (fullName: string) => void
}) {
  const tableName = tableParts(table, true).table
  // An invalid name clears the destination: the previous table must not stay the one
  // Write replaces while the field shows a different name. The picker's own choice keeps
  // the invalid name, and its error, on screen; an outside change of the table lets it go.
  const draft = useTableDraft(
    table,
    true,
    (p) => (p.table && TABLE_NAME_RE.test(p.table) && p.catalog && p.schema ? `${p.catalog}.${p.schema}.${p.table}` : ""),
    onSelect,
  )
  const { catalog, schema: dbSchema, table: draftName } = draft.shown
  const [error, setError] = useState<string | null>(null)

  // The schema's tables, for suggestions and to say whether the name exists.
  const { tables, load: loadTables } = useSchemaTables(catalog, dbSchema, setError)
  useEffect(() => {
    if (catalog && dbSchema) loadTables(catalog, dbSchema)
  }, [catalog, dbSchema, loadTables])

  const nameValid = draftName === "" || TABLE_NAME_RE.test(draftName)
  const exists = tables?.some((t) => t.name.toLowerCase() === tableName.toLowerCase())

  const commitName = (value: string) => {
    draft.commit({ catalog, schema: dbSchema, table: value.trim() })
  }

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Table</label>

      <div className="mt-1 space-y-1.5">
        <CatalogSchemaSelects
          catalog={catalog}
          schema={dbSchema}
          onCatalogChange={(cat) => draft.commit({ catalog: cat, schema: "", table: draftName })}
          onSchemaChange={(sch) => draft.commit({ catalog, schema: sch, table: draftName })}
          onError={setError}
        />

        <CommittedTextField
          type="text"
          aria-label="Table name"
          list="databricks-target-tables"
          placeholder={dbSchema ? "New or existing table name" : "Select schema first"}
          disabled={!dbSchema}
          value={draftName}
          onCommit={commitName}
          className="focus-ring w-full px-2.5 py-1.5 text-xs rounded-lg disabled:opacity-40"
          style={{ ...SELECT_STYLE, borderColor: nameValid ? 'var(--border)' : 'var(--danger)' }}
        />
        <datalist id="databricks-target-tables">
          {(tables ?? []).map((t) => (
            <option key={t.name} value={t.name} />
          ))}
        </datalist>
      </div>

      {!nameValid ? (
        <p role="alert" className="mt-1.5 text-[10px]" style={{ color: 'var(--danger)' }}>
          Use only letters, digits, underscores and hyphens.
        </p>
      ) : table && tables !== null ? (
        <p className="mt-1.5 text-[10px] px-1" style={{ color: 'var(--text-muted)' }}>
          <span className="font-mono">{table}</span>
          {exists ? " - existing table" : " - new table, created on first write"}
        </p>
      ) : null}

      {error && (
        <div className="mt-1.5 text-[10px] px-2 py-1 rounded" style={{ background: 'var(--danger-soft)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
    </div>
  )
}
