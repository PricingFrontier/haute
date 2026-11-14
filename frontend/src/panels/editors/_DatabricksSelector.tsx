import { useState, useRef, useEffect, useCallback } from "react"
import { Check, ChevronDown, Loader2, RefreshCw } from "lucide-react"
import { getWarehouses, getCatalogs, getSchemas, getTables } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import { CommittedTextField } from "../../components/form"
import { Select } from "../../haute-ui"

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
            field="large"
            className="w-full font-mono"
            style={required ? { borderColor: 'var(--warning-border-emphasis)' } : undefined}
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
      <Select fieldSize="large"
        aria-label="Catalog"
        value={catalog}
        onFocus={refreshCatalogs}
        onChange={(e) => {
          const cat = e.target.value
          onCatalogChange(cat)
          if (cat) refreshSchemas(cat)
        }}
        className="w-full"
      >
        <option value="">{loadingCatalogs ? "Loading..." : "Select catalog..."}</option>
        {catalog && catalogs.every((c) => c.name !== catalog) && (
          <option value={catalog}>{catalog}</option>
        )}
        {catalogs.map((c) => (
          <option key={c.name} value={c.name}>{c.name}{c.comment ? ` - ${c.comment}` : ""}</option>
        ))}
      </Select>

      <Select fieldSize="large"
        aria-label="Schema"
        value={schema}
        disabled={!catalog}
        onFocus={() => { if (catalog) refreshSchemas(catalog) }}
        onChange={(e) => onSchemaChange(e.target.value)}
        className="w-full"
      >
        <option value="">{loadingSchemas ? "Loading..." : catalog ? "Select schema..." : "Select catalog first"}</option>
        {schema && schemaItems.every((s) => s.name !== schema) && (
          <option value={schema}>{schema}</option>
        )}
        {schemaItems.map((s) => (
          <option key={s.name} value={s.name}>{s.name}{s.comment ? ` - ${s.comment}` : ""}</option>
        ))}
      </Select>
    </>
  )
}

/**
 * A picker's own choice of table, told apart from a change made elsewhere (undo,
 * redo, another editor): `choose` records what it sends, and `isEcho` says
 * whether the `table` that came back is that choice, which the picker's local
 * state already holds.
 */
function useOwnChoice(onSelect: (fullName: string) => void) {
  const chosen = useRef<string | null>(null)
  const choose = (fullName: string) => {
    chosen.current = fullName
    onSelect(fullName)
  }
  const isEcho = (table: string) => {
    const echoed = table === chosen.current
    chosen.current = null
    return echoed
  }
  return { choose, isEcho }
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
  const parts = table ? table.split(".") : []
  const [catalog, setCatalog] = useState(parts[0] || "")
  const [dbSchema, setDbSchema] = useState(parts[1] || "")
  const [tableName, setTableName] = useState(parts[2] || "")
  const [error, setError] = useState<string | null>(null)
  const { choose, isEcho } = useOwnChoice(onSelect)

  /* eslint-disable react-hooks/set-state-in-effect -- sync local state from prop */
  useEffect(() => {
    // Clearing the table on a new catalog or schema comes back as `""`: the
    // catalog and schema just chosen stay.
    if (isEcho(table)) return
    const p = table ? table.split(".") : []
    setCatalog(p[0] || "")
    setDbSchema(p[1] || "")
    setTableName(p[2] || "")
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isEcho reads a ref
  }, [table])
  /* eslint-enable react-hooks/set-state-in-effect */

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
          onCatalogChange={(cat) => {
            setCatalog(cat)
            setDbSchema("")
            setTableName("")
            choose("")
          }}
          onSchemaChange={(sch) => {
            setDbSchema(sch)
            setTableName("")
            choose("")
            if (sch) refreshTables(catalog, sch)
          }}
          onError={setError}
        />

        <Select fieldSize="large"
          aria-label="Table"
          value={tableName}
          disabled={!dbSchema}
          onFocus={() => { if (catalog && dbSchema) refreshTables(catalog, dbSchema) }}
          onChange={(e) => {
            const tbl = e.target.value
            setTableName(tbl)
            choose(tbl ? `${catalog}.${dbSchema}.${tbl}` : "")
          }}
          className="w-full"
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
        </Select>
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
  const parts = table ? table.replace(/`/g, "").split(".") : []
  const [catalog, setCatalog] = useState(parts[0] || "")
  const [dbSchema, setDbSchema] = useState(parts[1] || "")
  const tableName = parts[2] || ""
  const [draftName, setDraftName] = useState(tableName)
  const [error, setError] = useState<string | null>(null)
  // When the change of `table` is this picker's choice coming back, the local
  // state already holds it; re-syncing would wipe an invalid draft name (whose
  // commit cleared the destination) along with its error.
  const { choose, isEcho } = useOwnChoice(onSelect)

  /* eslint-disable react-hooks/set-state-in-effect -- sync local state from prop */
  useEffect(() => {
    if (isEcho(table)) return
    const p = table ? table.replace(/`/g, "").split(".") : []
    if (p[0]) setCatalog(p[0])
    if (p[1]) setDbSchema(p[1])
    setDraftName(p[2] || "")
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isEcho reads a ref
  }, [table])
  /* eslint-enable react-hooks/set-state-in-effect */

  // The schema's tables, for suggestions and to say whether the name exists.
  const { tables, load: loadTables } = useSchemaTables(catalog, dbSchema, setError)
  useEffect(() => {
    if (catalog && dbSchema) loadTables(catalog, dbSchema)
  }, [catalog, dbSchema, loadTables])

  const nameValid = draftName === "" || TABLE_NAME_RE.test(draftName)
  const exists = tables?.some((t) => t.name.toLowerCase() === tableName.toLowerCase())

  const commitName = (value: string) => {
    const name = value.trim()
    setDraftName(name)
    // An invalid name clears the destination: the previous table must not stay
    // the one Write replaces while the field shows a different name.
    if (name && TABLE_NAME_RE.test(name) && catalog && dbSchema) {
      choose(`${catalog}.${dbSchema}.${name}`)
    } else {
      choose("")
    }
  }

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Table</label>

      <div className="mt-1 space-y-1.5">
        <CatalogSchemaSelects
          catalog={catalog}
          schema={dbSchema}
          onCatalogChange={(cat) => {
            setCatalog(cat)
            setDbSchema("")
            choose("")
          }}
          onSchemaChange={(sch) => {
            setDbSchema(sch)
            choose(sch && draftName && TABLE_NAME_RE.test(draftName) ? `${catalog}.${sch}.${draftName}` : "")
          }}
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
          aria-invalid={nameValid ? undefined : true}
          field="large"
          className="w-full"
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
