import { useId, useMemo, useState } from "react"
import { Search, SlidersHorizontal } from "lucide-react"
import type { OnUpdateConfig } from "../panels/editors/_shared"
import { FeatureMonotonicity } from "../panels/modelling/FeatureMonotonicity"
import { finalSelectedFeatureNames, orderedFeatureSelection, roleColumns, type ModellingColumn } from "../panels/modelling/featureSelection"
import { configField } from "../utils/configField"
import { IconButton } from "../workbench/schema/IconButton"
import "./disclosures.css"

/** One list for charting a factor and editing how the chosen model fits it. */
export default function FactorList({ config, columns, selected, onSelect, onUpdate, readOnly }: {
  config: Record<string, unknown>
  columns: readonly ModellingColumn[]
  selected: string | null
  onSelect: (factor: string) => void
  onUpdate: OnUpdateConfig
  readOnly: boolean
}) {
  const prefix = useId()
  const factors = useMemo(() => {
    const roles = roleColumns(config)
    return columns.filter((column) => !roles.has(column.name))
  }, [columns, config])
  const saved = configField<string[]>(config, "feature_columns", [])
  const upstream = new Set(columns.map((column) => column.name))
  const missing = saved.filter((name) => !upstream.has(name))
  const included = finalSelectedFeatureNames(config, factors)
  const [search, setSearch] = useState("")
  const [expanded, setExpanded] = useState<string | null>(null)
  // Mount on first use, then retain the controls so closing and reversal can animate.
  const [mountedFactors, setMountedFactors] = useState<ReadonlySet<string>>(() => new Set())
  const query = search.trim().toLowerCase()
  const rows: { name: string; column?: ModellingColumn }[] = [
    ...factors.map((column) => ({ name: column.name, column })),
    ...missing.map((name) => ({ name })),
  ]
  const shown = rows.filter(({ name }) => name.toLowerCase().includes(query))
  const writeSelection = (next: ReadonlySet<string>) => {
    if (!readOnly) onUpdate({ feature_columns: orderedFeatureSelection(columns, saved, next) })
  }
  if (rows.length === 0) return <p className="px-1 text-[12px]" style={{ color: "var(--text-muted)" }}>No factors</p>

  return (
    <>
      <div className="shrink-0 space-y-2 pb-2">
        <div className="relative">
          <Search aria-hidden="true" className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2" size={12} style={{ color: "var(--text-muted)" }} />
          <input
            type="search"
            aria-label="Search factors"
            className="w-full rounded-md py-1.5 pl-6 pr-2 text-[12px] outline-none focus:ring-1 focus:ring-[var(--model-accent-border)]"
            style={{ background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" }}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-1 text-[10px]" style={{ color: "var(--text-muted)" }}>
          <button type="button" className="toolbar-btn focus-ring rounded-md py-1 disabled:opacity-40" disabled={readOnly} aria-label="Include all factors" onClick={() => writeSelection(new Set([...saved, ...factors.map(({ name }) => name)]))}>Include all</button>
          <button type="button" className="toolbar-btn focus-ring rounded-md py-1 disabled:opacity-40" disabled={readOnly} aria-label="Exclude all factors" onClick={() => {
            const eligible = new Set(factors.map(({ name }) => name))
            writeSelection(new Set(saved.filter((name) => !eligible.has(name))))
          }}>Exclude all</button>
        </div>
      </div>
      {shown.length === 0 && <p className="px-1 text-[12px]" style={{ color: "var(--text-muted)" }}>No matching factors</p>}
      <ul aria-label="Factor names" className="relative min-h-0 flex-1 space-y-0.5 overflow-y-auto">
        {shown.map(({ name, column }) => {
          const pressed = name === selected
          const open = name === expanded
          const controlsId = `${prefix}-${name}-fitting`
          return (
            <li key={name} className="modelling-disclosure-row rounded-lg" data-selected={pressed}>
              <div className="flex min-w-0 items-center">
                <button
                  type="button"
                  aria-pressed={pressed}
                  title={name}
                  className="modelling-disclosure-name focus-ring flex min-w-0 flex-1 items-center rounded-lg px-2 py-1.5 text-left text-[12px] leading-4"
                  style={{ color: pressed ? "var(--model-accent)" : included.has(name) ? "var(--text-primary)" : "var(--text-muted)" }}
                  onClick={() => {
                    onSelect(name)
                    setExpanded(null)
                  }}
                >
                  <span className={open ? "min-w-0 break-all" : "min-w-0 truncate"}>{name}</span>
                </button>
                <IconButton
                  icon={SlidersHorizontal}
                  aria-label={`${name} settings`}
                  title="Fitting options"
                  aria-expanded={open}
                  aria-controls={controlsId}
                  className="mr-1"
                  style={{ color: open ? "var(--text-primary)" : "var(--text-muted)" }}
                  onClick={() => {
                    setExpanded(open ? null : name)
                    if (!mountedFactors.has(name)) {
                      setMountedFactors((previous) => new Set([...previous, name]))
                    }
                  }}
                />
                <input
                  type="checkbox"
                  aria-label={`Include ${name}`}
                  title={`Include ${name}`}
                  checked={column === undefined ? saved.includes(name) : included.has(name)}
                  disabled={readOnly}
                  className="mr-2 h-3 w-3 shrink-0"
                  style={{ accentColor: "var(--model-accent)" }}
                  onChange={(event) => {
                    const next = new Set(saved)
                    if (event.target.checked) next.add(name)
                    else next.delete(name)
                    writeSelection(next)
                  }}
                />
              </div>
              <div
                id={controlsId}
                className="modelling-disclosure-body"
                data-expanded={open}
                aria-hidden={!open}
                inert={!open ? true : undefined}
              >
                <div className="min-h-0 overflow-hidden">
                  {mountedFactors.has(name) && (
                    <div role="group" aria-label={`${name} fitting options`} className="relative mx-2 space-y-2 border-t pb-2.5 pt-2 text-[11px]" style={{ borderColor: "var(--model-accent-border)" }}>
                      {column === undefined ? (
                        <div className="flex items-center justify-between gap-1" style={{ color: "var(--danger-text-soft)" }}>
                          <span>Not found</span>
                          <button type="button" disabled={readOnly} aria-label={`Remove ${name} factor`} className="focus-ring disabled:opacity-40" onClick={() => writeSelection(new Set(saved.filter((entry) => entry !== name)))}>Remove</button>
                        </div>
                      ) : (
                        <div className="flex flex-wrap items-center justify-between gap-1">
                          <span style={{ color: "var(--text-muted)" }}>Direction</span>
                          <FeatureMonotonicity config={config} column={column} included={included.has(name)} onUpdate={onUpdate} noun="factor" disabled={readOnly} />
                        </div>
                      )}
                  </div>
                  )}
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </>
  )
}
