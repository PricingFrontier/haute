import { useMemo, useState } from "react"
import { Search, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { isNumericDtype } from "../../utils/polarsDtypes"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { withAlpha } from "../../utils/color"
import { getDtypeColor } from "../../utils/dtypeColors"
import {
  finalSelectedFeatureNames,
  roleColumnReasons,
  roleColumns,
  type ModellingColumn,
} from "./featureSelection"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

const MONOTONIC_DIRECTIONS = [
  {
    value: -1,
    label: "decreasing",
    glyph: "↓",
    color: "var(--danger)",
    activeBackground: "var(--danger-soft)",
  },
  {
    value: 0,
    label: "no constraint",
    glyph: "−",
    color: "var(--warning-strong)",
    activeBackground: "var(--warning-soft)",
  },
  {
    value: 1,
    label: "increasing",
    glyph: "↑",
    color: NODE_GROUP_COLORS.data,
    activeBackground: withAlpha(NODE_GROUP_COLORS.data, 0.1),
  },
] as const

export function CommonFeatureConfig({ config, onUpdate, columns }: Props) {
  const [filter, setFilter] = useState("")
  const [membership, setMembership] = useState<"all" | "included" | "excluded">(
    "all",
  )
  // Features are opt-in: a column is a feature only once it is ticked.
  const featureColumns = configField<string[]>(config, "feature_columns", [])
  const monotone = configField<Record<string, number>>(
    config,
    "monotone_constraints",
    {},
  )

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

  const updateMonotonicity = (
    name: string,
    direction: (typeof MONOTONIC_DIRECTIONS)[number]["value"],
  ) => {
    const next = { ...monotone }
    if (direction === 0) delete next[name]
    else next[name] = direction
    onUpdate("monotone_constraints", Object.keys(next).length > 0 ? next : null)
  }

  const monotonicityUnavailableReason = (
    column: ModellingColumn,
    excluded: boolean,
  ) => {
    if (!isNumericDtype(column.dtype)) {
      return "Monotonicity is only available for numeric features."
    }
    if (excluded) {
      return "Include this feature to set monotonicity."
    }
    return "Monotonicity is unavailable for this feature."
  }

  // Upstream column order, then any stored name upstream no longer has.
  const writeSelection = (next: ReadonlySet<string>) => {
    onUpdate({
      feature_columns: [
        ...columns.map((column) => column.name).filter((name) => next.has(name)),
        ...featureColumns.filter((name) => next.has(name) && !upstreamNames.has(name)),
      ],
    })
  }
  const roleText = [...roleColumnReasons(config).entries()]
    .map(([name, role]) => `${name} (${role})`)
    .join(", ")

  return (
    <section aria-labelledby="model-features-heading">
      <div className="flex items-end justify-between gap-3">
        <h3
          id="model-features-heading"
          className="text-[14px] font-semibold"
          style={{ color: "var(--text-muted)" }}
        >
          Features
        </h3>
        <span
          className="text-xs tabular-nums"
          style={{ color: "var(--text-secondary)" }}
        >
          {includedCount} included · {eligible.length - includedCount} excluded
        </span>
      </div>

      {roleText && (
        <p className="mt-1 text-[12px]" style={{ color: "var(--text-muted)" }}>
          Excluded from predictors: {roleText}.
        </p>
      )}

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
            aria-label="Search features"
            className="w-full rounded-lg py-2 pl-8 pr-2.5 text-xs outline-none focus:ring-1 focus:ring-[var(--model-accent-border)]"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
            }}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Search features"
          />
        </div>

        <div
          className="flex flex-wrap gap-1.5"
          role="group"
          aria-label="Feature filter"
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
            aria-label="Include all features"
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
            aria-label="Exclude all features"
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
        <span aria-hidden="true" /> <span>Feature</span>
        <span>Type</span>
        <span>Monotonicity</span>
      </div>
      <div
        className="mt-1 divide-y divide-[var(--border)]"
        style={{ borderColor: "var(--border)" }}
      >
        {visible.map((column) => {
          const excluded = !selectedNames.has(column.name)
          const canSetMonotonicity =
            selectedNames.has(column.name) && isNumericDtype(column.dtype)
          const monotonicity = monotone[column.name] ?? 0
          const unavailableReason = monotonicityUnavailableReason(
            column,
            excluded,
          )

          return (
            <div
              role="group"
              aria-label={`${column.name} feature`}
              className="grid min-w-0 grid-cols-[1rem_minmax(0,1fr)_4.5rem_6rem] items-center gap-2 py-2"
              key={column.name}
              style={{
                background: "transparent",
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
              <fieldset
                className="m-0 min-w-0 border-0 p-0 disabled:opacity-40 disabled:grayscale"
                disabled={!canSetMonotonicity}
                title={canSetMonotonicity ? undefined : unavailableReason}
              >
                <legend className="sr-only">Monotonicity</legend>
                <div className="flex items-center gap-1">
                  {MONOTONIC_DIRECTIONS.map((direction) => {
                    const active = monotonicity === direction.value
                    return (
                      <button
                        type="button"
                        key={direction.value}
                        aria-label={`${column.name}: ${direction.label}`}
                        aria-pressed={active}
                        className="flex h-6 w-6 items-center justify-center rounded-lg text-xs font-semibold transition-[filter] hover:brightness-125 disabled:cursor-not-allowed"
                        style={{
                          background: active
                            ? direction.activeBackground
                            : "var(--bg-input)",
                          border: `1px solid ${
                            active ? direction.color : "var(--border)"
                          }`,
                          color: direction.color,
                        }}
                        title={
                          canSetMonotonicity
                            ? `${column.name}: ${direction.label}`
                            : unavailableReason
                        }
                        onClick={() =>
                          updateMonotonicity(column.name, direction.value)
                        }
                      >
                        <span aria-hidden="true">{direction.glyph}</span>
                      </button>
                    )
                  })}
                </div>
              </fieldset>
            </div>
          )
        })}

        {visible.length === 0 && (
          <p
            className="rounded-lg border border-dashed px-3 py-5 text-center text-[10px]"
            style={{ color: "var(--text-muted)", borderColor: "var(--border)" }}
          >
            {columns.length === 0
              ? `The upstream columns are not known yet${featureColumns.length > 0 ? `; ${featureColumns.length} saved ${featureColumns.length === 1 ? "feature" : "features"} will apply` : ""}.`
              : "No matching feature columns."}
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
              aria-label={`Remove ${name} feature`}
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
