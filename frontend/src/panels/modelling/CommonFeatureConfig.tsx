import { useEffect, useMemo, useRef, useState } from "react"
import { Search, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors/_shared"
import { configField } from "../../utils/configField"
import { FeatureMonotonicity } from "./FeatureMonotonicity"
import { getDtypeColor } from "../../utils/dtypeColors"
import {
  finalSelectedFeatureNames,
  orderedFeatureSelection,
  roleColumns,
  type ModellingColumn,
} from "./featureSelection"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
  /** What a row is called: a feature, or a factor for an interactive build (the Modelling view). */
  noun?: "feature" | "factor"
  /** The factor chosen in the Modelling view: its row is marked and scrolled into view. */
  chosen?: string | null
}

export function CommonFeatureConfig({
  config,
  onUpdate,
  columns,
  noun = "feature",
  chosen = null,
}: Props) {
  const plural = `${noun}s`
  const Noun = `${noun[0].toUpperCase()}${noun.slice(1)}`
  const [filter, setFilter] = useState("")
  const [membership, setMembership] = useState<"all" | "included" | "excluded">(
    "all",
  )
  // Features are opt-in: a column is a feature only once it is ticked.
  const featureColumns = configField<string[]>(config, "feature_columns", [])
  const eligible = useMemo(() => {
    const roles = roleColumns(config)
    return columns.filter((column) => !roles.has(column.name))
  }, [columns, config])
  const eligibleNames = useMemo(
    () => new Set(eligible.map((column) => column.name)),
    [eligible],
  )
  const upstreamNames = useMemo(
    () => new Set(columns.map((column) => column.name)),
    [columns],
  )
  const selectedNames = finalSelectedFeatureNames(config, eligible)
  // Missing features are only flagged once the upstream columns have arrived.
  const staleFeatures = columns.length === 0 ? [] : featureColumns.filter(
    (name) => !upstreamNames.has(name),
  )
  const visible = eligible.filter(
    (column) =>
      column.name.toLowerCase().includes(filter.trim().toLowerCase()) &&
      (membership === "all" ||
        (membership === "included") === selectedNames.has(column.name)),
  )
  const includedCount = selectedNames.size
  // The chosen factor's row, scrolled into view each time the choice changes; a row the
  // search or the filter hides has nothing to scroll to.
  const chosenRow = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    chosenRow.current?.scrollIntoView({ block: "nearest" })
  }, [chosen])

  // Upstream column order, then any stored name upstream no longer has.
  const writeSelection = (next: ReadonlySet<string>) => {
    onUpdate({
      feature_columns: orderedFeatureSelection(columns, featureColumns, next),
    })
  }
  return (
    <section aria-labelledby="model-features-heading">
      <div className="flex flex-wrap items-end justify-between gap-x-3 gap-y-1">
        <h3
          id="model-features-heading"
          className="text-[14px] font-semibold"
          style={{ color: "var(--text-muted)" }}
        >
          {Noun}s
        </h3>
        <span
          className="text-xs tabular-nums"
          style={{ color: "var(--text-secondary)" }}
        >
          {includedCount} included · {eligible.length - includedCount} excluded
        </span>
      </div>

      <div
        className="sticky top-0 z-10 mt-2 space-y-2 py-1"
        style={{ background: "var(--bg-panel)" }}
      >
        <div className="relative mt-2">
          <Search
            aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2"
            size={13}
            style={{ color: "var(--text-muted)" }}
          />
          <input
            aria-label={`Search ${plural}`}
            className="w-full rounded-lg py-2 pl-8 pr-2.5 text-xs outline-none focus:ring-1 focus:ring-[var(--model-accent-border)]"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder={`Search ${plural}`}
          />
        </div>

        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label={`${Noun} filter`}
        >
          {(["all", "included", "excluded"] as const).map((state) => (
            <button
              key={state}
              type="button"
              aria-pressed={membership === state}
              onClick={() => setMembership(state)}
              className="rounded px-2 py-1 text-[12px]"
              style={{
                background:
                  membership === state
                    ? "var(--model-accent-soft)"
                    : "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
              }}
            >
              {state[0].toUpperCase() + state.slice(1)} (
              {state === "all"
                ? eligible.length
                : state === "included"
                  ? includedCount
                  : eligible.length - includedCount}
              )
            </button>
          ))}
        </div>
        <div className="flex flex-wrap gap-1.5">
          <button
            type="button"
            aria-label={`Include all ${plural}`}
            className="rounded px-2 py-1 text-[12px] font-medium transition-[filter] hover:brightness-125"
            style={{
              background: "var(--model-accent-soft)",
              border: "1px solid var(--model-accent-border)",
              color: "var(--model-accent)",
            }}
            onClick={() =>
              writeSelection(new Set([...featureColumns, ...eligibleNames]))
            }
          >
            Include all
          </button>
          <button
            type="button"
            aria-label={`Exclude all ${plural}`}
            className="rounded px-2 py-1 text-[12px] font-medium transition-[filter] hover:brightness-125"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-secondary)",
            }}
            onClick={() =>
              writeSelection(
                new Set(featureColumns.filter((name) => !eligibleNames.has(name))),
              )
            }
          >
            Exclude all
          </button>
        </div>
      </div>

      <div
        className="mt-4 grid grid-cols-[1rem_minmax(0,1fr)_4.5rem_6rem] gap-2 px-0 text-[11px]"
        style={{ color: "var(--text-muted)" }}
      >
        <span aria-hidden="true" /> <span>{Noun}</span>
        <span>Type</span>
        <span>Monotonicity</span>
      </div>
      <div
        className="mt-1 divide-y divide-[var(--border)]"
        style={{ borderColor: "var(--border)" }}
      >
        {visible.map((column) => {
          const excluded = !selectedNames.has(column.name)
          return (
            <div
              role="group"
              aria-label={`${column.name} ${noun}`}
              aria-current={column.name === chosen ? "true" : undefined}
              ref={column.name === chosen ? chosenRow : undefined}
              className="grid min-w-0 grid-cols-[1rem_minmax(0,1fr)_4.5rem_6rem] items-center gap-2 py-2"
              key={column.name}
              style={{
                background: column.name === chosen ? "var(--model-accent-soft)" : "transparent",
              }}
            >
              <input
                type="checkbox"
                aria-label={`Include ${column.name}`}
                checked={!excluded}
                className="accent-purple-500"
                onChange={() => {
                  const next = new Set(featureColumns)
                  if (excluded) next.add(column.name)
                  else next.delete(column.name)
                  writeSelection(next)
                }}
              />
              <span
                className="min-w-0 truncate font-mono text-[13px] font-semibold"
                title={column.name}
                style={{ color: "var(--text-primary)" }}
              >
                {column.name}
              </span>
              <span
                className={`max-w-full justify-self-start truncate rounded-full px-1.5 py-0.5 font-mono text-[11px] ${getDtypeColor(column.dtype)}`}
                title={column.dtype}
                style={{ background: "var(--chrome-hover)" }}
              >
                {column.dtype}
              </span>
              <FeatureMonotonicity config={config} column={column} included={!excluded} onUpdate={onUpdate} noun={noun} />
            </div>
          )
        })}

        {visible.length === 0 && (
          <p
            className="rounded-lg border border-dashed px-3 py-5 text-center text-[10px]"
            style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}
          >
            {columns.length === 0
              ? `The upstream columns are not known yet${featureColumns.length > 0 ? `; ${featureColumns.length} saved ${featureColumns.length === 1 ? noun : plural} will apply` : ""}.`
              : `No matching ${noun} columns.`}
          </p>
        )}

        {staleFeatures.map((name) => (
          <div
            className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px]"
            key={name}
            style={{
              background: "var(--danger-soft-subtle)",
              border: "1px solid var(--danger-border)",
              color: "var(--danger-text-soft)",
            }}
          >
            <span className="min-w-0 flex-1 truncate">{name} - not found</span>
            <button
              type="button"
              aria-label={`Remove ${name} ${noun}`}
              className="rounded p-1 hover:bg-[var(--danger-soft)]"
              onClick={() =>
                onUpdate(
                  "feature_columns",
                  featureColumns.filter((entry) => entry !== name),
                )
              }
            >
              <X size={12} aria-hidden="true" />
            </button>
          </div>
        ))}
      </div>
    </section>
  )
}
