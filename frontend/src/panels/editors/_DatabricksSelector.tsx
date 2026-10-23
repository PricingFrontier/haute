import { useState, useRef, useEffect, useCallback } from "react"
import { Check, ChevronDown, Loader2, RefreshCw } from "lucide-react"
import { getWarehouses, getCatalogs, getSchemas, getTables } from "../../api/client"
import { apiErrorMessage } from "../../api/errors"
import { CommittedTextField } from "../../components/form"

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

// ─── CatalogTablePicker ───────────────────────────────────────────

type CatalogItem = { name: string; comment: string }
type SchemaItem = { name: string; comment: string }
type TableItem = { name: string; full_name: string; table_type: string; comment: string }

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

  /* eslint-disable react-hooks/set-state-in-effect -- sync local state from prop */
  useEffect(() => {
    const p = table ? table.split(".") : []
    setCatalog(p[0] || "")
    setDbSchema(p[1] || "")
    setTableName(p[2] || "")
  }, [table])
  /* eslint-enable react-hooks/set-state-in-effect */

  const [catalogs, setCatalogs] = useState<CatalogItem[]>([])
  const [schemas, setSchemas] = useState<SchemaItem[]>([])
  const [tables, setTables] = useState<TableItem[]>([])

  const [loadingCatalogs, setLoadingCatalogs] = useState(false)
  const [loadingSchemas, setLoadingSchemas] = useState(false)
  const [loadingTables, setLoadingTables] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshCatalogs = () => {
    setLoadingCatalogs(true)
    setError(null)
    getCatalogs()
      .then((data) => { setCatalogs(data.catalogs || []); setLoadingCatalogs(false) })
      .catch((e: Error) => { setError(apiErrorMessage(e)); setLoadingCatalogs(false) })
  }

  const refreshSchemas = (cat: string) => {
    setLoadingSchemas(true)
    setError(null)
    getSchemas(cat)
      .then((data) => { setSchemas(data.schemas || []); setLoadingSchemas(false) })
      .catch((e: Error) => { setError(apiErrorMessage(e)); setLoadingSchemas(false) })
  }

  const refreshTables = (cat: string, sch: string) => {
    setLoadingTables(true)
    setError(null)
    getTables(cat, sch)
      .then((data) => { setTables(data.tables || []); setLoadingTables(false) })
      .catch((e: Error) => { setError(apiErrorMessage(e)); setLoadingTables(false) })
  }

  const selectStyle = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
  }

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Table</label>

      <div className="mt-1 space-y-1.5">
        {/* Catalog */}
        <select
          value={catalog}
          onFocus={refreshCatalogs}
          onChange={(e) => {
            const cat = e.target.value
            setCatalog(cat)
            setDbSchema("")
            setTableName("")
            setSchemas([])
            setTables([])
            onSelect("")
            if (cat) refreshSchemas(cat)
          }}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2"
          style={selectStyle}
        >
          <option value="">{loadingCatalogs ? "Loading..." : "Select catalog..."}</option>
          {catalog && catalogs.every((c) => c.name !== catalog) && (
            <option value={catalog}>{catalog}</option>
          )}
          {catalogs.map((c) => (
            <option key={c.name} value={c.name}>{c.name}{c.comment ? ` - ${c.comment}` : ""}</option>
          ))}
        </select>

        {/* Schema */}
        <select
          value={dbSchema}
          disabled={!catalog}
          onFocus={() => { if (catalog) refreshSchemas(catalog) }}
          onChange={(e) => {
            const sch = e.target.value
            setDbSchema(sch)
            setTableName("")
            setTables([])
            onSelect("")
            if (sch) refreshTables(catalog, sch)
          }}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2 disabled:opacity-40"
          style={selectStyle}
        >
          <option value="">{loadingSchemas ? "Loading..." : catalog ? "Select schema..." : "Select catalog first"}</option>
          {dbSchema && schemas.every((s) => s.name !== dbSchema) && (
            <option value={dbSchema}>{dbSchema}</option>
          )}
          {schemas.map((s) => (
            <option key={s.name} value={s.name}>{s.name}{s.comment ? ` - ${s.comment}` : ""}</option>
          ))}
        </select>

        {/* Table */}
        <select
          value={tableName}
          disabled={!dbSchema}
          onFocus={() => { if (catalog && dbSchema) refreshTables(catalog, dbSchema) }}
          onChange={(e) => {
            const tbl = e.target.value
            setTableName(tbl)
            if (tbl) {
              onSelect(`${catalog}.${dbSchema}.${tbl}`)
            } else {
              onSelect("")
            }
          }}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2 disabled:opacity-40"
          style={selectStyle}
        >
          <option value="">{loadingTables ? "Loading..." : dbSchema ? "Select table..." : "Select schema first"}</option>
          {tableName && tables.every((t) => t.name !== tableName) && (
            <option value={tableName}>{tableName}</option>
          )}
          {tables.map((t) => (
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
  const parts = table ? table.replace(/`/g, "").split(".") : []
  const [catalog, setCatalog] = useState(parts[0] || "")
  const [dbSchema, setDbSchema] = useState(parts[1] || "")
  const tableName = parts[2] || ""
  const [draftName, setDraftName] = useState(tableName)

  /* eslint-disable react-hooks/set-state-in-effect -- sync local state from prop */
  useEffect(() => {
    const p = table ? table.replace(/`/g, "").split(".") : []
    if (p[0]) setCatalog(p[0])
    if (p[1]) setDbSchema(p[1])
    setDraftName(p[2] || "")
  }, [table])
  /* eslint-enable react-hooks/set-state-in-effect */

  const [catalogs, setCatalogs] = useState<CatalogItem[]>([])
  const [schemas, setSchemas] = useState<SchemaItem[]>([])
  const [tables, setTables] = useState<TableItem[] | null>(null)
  const [loadingCatalogs, setLoadingCatalogs] = useState(false)
  const [loadingSchemas, setLoadingSchemas] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refreshCatalogs = () => {
    setLoadingCatalogs(true)
    setError(null)
    getCatalogs()
      .then((data) => { setCatalogs(data.catalogs || []); setLoadingCatalogs(false) })
      .catch((e: Error) => { setError(apiErrorMessage(e)); setLoadingCatalogs(false) })
  }

  const refreshSchemas = (cat: string) => {
    setLoadingSchemas(true)
    setError(null)
    getSchemas(cat)
      .then((data) => { setSchemas(data.schemas || []); setLoadingSchemas(false) })
      .catch((e: Error) => { setError(apiErrorMessage(e)); setLoadingSchemas(false) })
  }

  const refreshTables = useCallback((cat: string, sch: string) => {
    getTables(cat, sch)
      .then((data) => setTables(data.tables || []))
      .catch((e: Error) => setError(apiErrorMessage(e)))
  }, [])

  const tablesFor = useRef("")
  useEffect(() => {
    const key = catalog && dbSchema ? `${catalog}.${dbSchema}` : ""
    if (!key || tablesFor.current === key) return
    tablesFor.current = key
    refreshTables(catalog, dbSchema)
  }, [catalog, dbSchema, refreshTables])

  const nameValid = draftName === "" || TABLE_NAME_RE.test(draftName)
  const exists = tables?.some((t) => t.name.toLowerCase() === tableName.toLowerCase())
  const selectStyle = {
    background: 'var(--bg-input)',
    border: '1px solid var(--border)',
    color: 'var(--text-primary)',
  }

  const commitName = (value: string) => {
    const name = value.trim()
    setDraftName(name)
    if (!name) {
      onSelect("")
    } else if (TABLE_NAME_RE.test(name) && catalog && dbSchema) {
      onSelect(`${catalog}.${dbSchema}.${name}`)
    }
  }

  return (
    <div>
      <label className="text-[11px] font-bold uppercase tracking-[0.08em]" style={{ color: 'var(--text-muted)' }}>Table</label>

      <div className="mt-1 space-y-1.5">
        <select
          aria-label="Catalog"
          value={catalog}
          onFocus={refreshCatalogs}
          onChange={(e) => {
            const cat = e.target.value
            setCatalog(cat)
            setDbSchema("")
            setSchemas([])
            setTables(null)
            onSelect("")
            if (cat) refreshSchemas(cat)
          }}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2"
          style={selectStyle}
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
          value={dbSchema}
          disabled={!catalog}
          onFocus={() => { if (catalog) refreshSchemas(catalog) }}
          onChange={(e) => {
            const sch = e.target.value
            setDbSchema(sch)
            setTables(null)
            if (sch && draftName && TABLE_NAME_RE.test(draftName)) {
              onSelect(`${catalog}.${sch}.${draftName}`)
            } else {
              onSelect("")
            }
          }}
          className="w-full px-2.5 py-1.5 text-xs rounded-lg focus:outline-none focus:ring-2 disabled:opacity-40"
          style={selectStyle}
        >
          <option value="">{loadingSchemas ? "Loading..." : catalog ? "Select schema..." : "Select catalog first"}</option>
          {dbSchema && schemas.every((s) => s.name !== dbSchema) && (
            <option value={dbSchema}>{dbSchema}</option>
          )}
          {schemas.map((s) => (
            <option key={s.name} value={s.name}>{s.name}{s.comment ? ` - ${s.comment}` : ""}</option>
          ))}
        </select>

        <CommittedTextField
          type="text"
          aria-label="Table name"
          list="databricks-target-tables"
          placeholder={dbSchema ? "New or existing table name" : "Select schema first"}
          disabled={!dbSchema}
          value={draftName}
          onCommit={commitName}
          className="focus-ring w-full px-2.5 py-1.5 text-xs rounded-lg disabled:opacity-40"
          style={{ ...selectStyle, borderColor: nameValid ? 'var(--border)' : 'var(--danger)' }}
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
