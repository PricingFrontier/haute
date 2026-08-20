import { useMemo, useState } from "react"
import { Search, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { configField } from "../../utils/configField"
import { withAlpha } from "../../utils/color"
import { isNumericDtype } from "../../utils/polarsDtypes"
import {
  finalSelectedFeatureNames,
  roleColumns,
  type ModellingAlgorithm,
  type ModellingColumn,
} from "./featureSelection"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
  algorithm: ModellingAlgorithm
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

const INCLUDE_BUTTON_STYLE = {
  background: withAlpha(NODE_GROUP_COLORS.data, 0.1),
  border: `1px solid ${NODE_GROUP_COLORS.data}`,
  color: NODE_GROUP_COLORS.data,
} as const

const EXCLUDE_BUTTON_STYLE = {
  background: "var(--danger-soft)",
  border: "1px solid var(--danger)",
  color: "var(--danger)",
} as const

export function CommonFeatureConfig({
  config,
  onUpdate,
  columns,
  algorithm,
}: Props) {
  const [filter, setFilter] = useState("")
  const exclude = configField<string[]>(config, "exclude", [])
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
  const staleExclusions = exclude.filter(
    (name) => !columns.some((column) => column.name === name),
  )
  const visible = eligible.filter((column) =>
    column.name.toLowerCase().includes(filter.trim().toLowerCase()),
  )
  const selectedNames = finalSelectedFeatureNames(config, eligible, algorithm)
  const includedCount = eligible.filter(
    (column) => !exclude.includes(column.name),
  ).length

  const updateMonotonicity = (
    name: string,
    direction: (typeof MONOTONIC_DIRECTIONS)[number]["value"],
  ) => {
    const next = { ...monotone }
    if (direction === 0) delete next[name]
    else next[name] = direction
    onUpdate(
      "monotone_constraints",
      Object.keys(next).length > 0 ? next : null,
    )
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
    return algorithm === "glm"
      ? "Add this feature as a GLM factor to set monotonicity."
      : "Monotonicity is unavailable for this feature."
  }

  const requestExclusionUpdate = (nextExclude: string[]) => {
    onUpdate({ exclude: [...nextExclude] })
  }

  return (
    <section aria-labelledby="model-features-heading">
      <div className="flex items-end justify-between gap-3">
        <h3
          id="model-features-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Features
        </h3>
        <span
          className="text-[10px] tabular-nums"
          style={{ color: "var(--text-secondary)" }}
        >
          {includedCount} of {eligible.length} included
        </span>
      </div>

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

      <div className="mt-2 flex flex-wrap gap-1.5">
        <button
          type="button"
          aria-label="Include all features"
          className="rounded-lg px-2.5 py-1 text-[10px] font-medium transition-[filter] hover:brightness-125"
          style={INCLUDE_BUTTON_STYLE}
          onClick={() =>
            requestExclusionUpdate(
              exclude.filter((name) => !eligibleNames.has(name)),
            )
          }
        >
          Include all
        </button>
        <button
          type="button"
          aria-label="Exclude all features"
          className="rounded-lg px-2.5 py-1 text-[10px] font-medium transition-[filter] hover:brightness-125"
          style={EXCLUDE_BUTTON_STYLE}
          onClick={() =>
            requestExclusionUpdate([
              ...new Set([
                ...exclude,
                ...eligible.map((column) => column.name),
              ]),
            ])
          }
        >
          Exclude all
        </button>
      </div>

      <div className="mt-3 grid gap-1.5">
        {visible.map((column) => {
          const excluded = exclude.includes(column.name)
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
              className="flex min-w-0 items-center gap-1.5 rounded-lg px-2 py-1.5"
              key={column.name}
              style={{
                background: "var(--bg-elevated)",
                border: "1px solid var(--border)",
              }}
            >
              <span
                className="min-w-0 flex-1 truncate font-mono text-[11px] font-semibold"
                title={column.name}
                style={{ color: "var(--text-primary)" }}
              >
                {column.name}
              </span>
              <span
                className="max-w-20 shrink-0 truncate rounded-full px-1.5 py-0.5 font-mono text-[9px]"
                title={column.dtype}
                style={{
                  background: "var(--chrome-hover)",
                  color: "var(--text-secondary)",
                }}
              >
                {column.dtype}
              </span>
              <button
                type="button"
                aria-label={`${column.name} is ${
                  excluded
                    ? "excluded; click to include"
                    : "included; click to exclude"
                }`}
                aria-pressed={!excluded}
                className="shrink-0 rounded-lg px-2.5 py-1 text-[10px] font-medium transition-[filter] hover:brightness-125"
                style={excluded ? EXCLUDE_BUTTON_STYLE : INCLUDE_BUTTON_STYLE}
                title={
                  excluded
                    ? "Excluded — click to include"
                    : "Included — click to exclude"
                }
                onClick={() =>
                  requestExclusionUpdate(
                    excluded
                      ? exclude.filter((name) => name !== column.name)
                      : [...exclude, column.name],
                  )
                }
              >
                {excluded ? "Exclude" : "Include"}
              </button>

              <fieldset
                className="m-0 shrink-0 border-0 p-0 disabled:opacity-40 disabled:grayscale"
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
            No matching feature columns.
          </p>
        )}

        {staleExclusions.map((name) => (
          <div
            className="flex items-center gap-2 rounded-lg px-2.5 py-2 text-[11px]"
            key={name}
            style={{
              background: "var(--danger-soft-subtle)",
              border: "1px solid var(--danger-border)",
              color: "var(--danger-text-soft)",
            }}
          >
            <span className="min-w-0 flex-1 truncate">
              {name} — not found
            </span>
            <button
              type="button"
              aria-label={`Remove ${name} exclusion`}
              className="rounded p-1 hover:bg-[var(--danger-soft)]"
              onClick={() =>
                onUpdate(
                  "exclude",
                  exclude.filter((entry) => entry !== name),
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
