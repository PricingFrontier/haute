import { useMemo, useState } from "react"
import { ChevronDown, ChevronRight, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { isNumericDtype } from "../../utils/polarsDtypes"
import useSettingsStore from "../../stores/useSettingsStore"
import {
  featureRemovalUpdate,
  finalSelectedFeatureNames,
  removedFinalFeatureNames,
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

const FEATURE_REMOVAL_CONFIRMATIONS: Record<ModellingAlgorithm, string> = {
  catboost:
    "Removing selected features also removes their monotonic constraints. Continue?",
  glm:
    "Removing selected features also removes their monotonic constraints and dependent GLM terms/interactions. Continue?",
}

export function CommonFeatureConfig({
  config,
  onUpdate,
  columns,
  algorithm,
}: Props) {
  const [filter, setFilter] = useState("")
  const monotonicOpen = useSettingsStore((state) =>
    state.isSectionOpen("modelling.monotonic"),
  )
  const toggleSection = useSettingsStore((state) => state.toggleSection)
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
  const monotonicCandidates = eligible.filter(
    (column) =>
      selectedNames.has(column.name) && isNumericDtype(column.dtype),
  )

  const requestExclusionUpdate = (nextExclude: string[]) => {
    const removed = removedFinalFeatureNames(
      config,
      eligible,
      nextExclude,
      algorithm,
    )
    if (removed.length > 0 && !confirm(FEATURE_REMOVAL_CONFIRMATIONS[algorithm])) return

    onUpdate(featureRemovalUpdate(config, eligible, nextExclude, algorithm))
  }

  return (
    <div className="space-y-4">
      <section aria-labelledby="model-features-heading">
        <h3
          id="model-features-heading"
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Features
        </h3>
        <input
          aria-label="Filter features"
          className="mt-1.5 w-full rounded-lg px-2.5 py-1.5 text-xs"
          style={{
            background: "var(--bg-input)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          }}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
          placeholder="Filter columns"
        />
        <div className="mt-1.5 flex gap-1">
          <button
            type="button"
            className="rounded px-2 py-1 text-[10px]"
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
            className="rounded px-2 py-1 text-[10px]"
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
        <div className="mt-2 space-y-1">
          {visible.map((column) => {
            const excluded = exclude.includes(column.name)
            return (
              <div
                className="flex items-center gap-2 text-[11px]"
                key={column.name}
              >
                <span
                  className="min-w-0 flex-1 truncate font-mono"
                  title={column.name}
                >
                  {column.name}
                </span>
                <span style={{ color: "var(--text-muted)" }}>
                  {column.dtype}
                </span>
                <button
                  type="button"
                  aria-pressed={!excluded}
                  onClick={() =>
                    requestExclusionUpdate(
                      excluded
                        ? exclude.filter((name) => name !== column.name)
                        : [...exclude, column.name],
                    )
                  }
                >
                  {excluded ? "Include" : "Exclude"}
                </button>
              </div>
            )
          })}
          {visible.length === 0 && (
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              No matching feature columns.
            </p>
          )}
          {staleExclusions.map((name) => (
            <div className="flex items-center gap-2 text-[11px]" key={name}>
              <span className="min-w-0 flex-1 truncate">
                {name} — not found
              </span>
              <button
                type="button"
                aria-label={`Remove ${name} exclusion`}
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

      <section>
        <button
          type="button"
          aria-expanded={monotonicOpen}
          className="flex items-center gap-1 text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
          onClick={() => toggleSection("modelling.monotonic")}
        >
          {monotonicOpen
            ? <ChevronDown size={12} aria-hidden="true" />
            : <ChevronRight size={12} aria-hidden="true" />}
          Monotonic Constraints
        </button>
        {monotonicOpen && (
          <div className="mt-1.5 space-y-1">
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
              Final selected numeric features only.
            </p>
            {monotonicCandidates.map((column) => {
              const value = monotone[column.name] ?? 0
              return (
                <div
                  className="flex items-center gap-2 text-[11px]"
                  key={column.name}
                >
                  <span className="min-w-0 flex-1 truncate font-mono">
                    {column.name}
                  </span>
                  {([-1, 0, 1] as const).map((direction) => (
                    <button
                      type="button"
                      key={direction}
                      aria-label={`${column.name}: ${
                        direction === -1
                          ? "decreasing"
                          : direction === 1
                            ? "increasing"
                            : "no constraint"
                      }`}
                      aria-pressed={value === direction}
                      onClick={() => {
                        const next = { ...monotone }
                        if (direction === 0) delete next[column.name]
                        else next[column.name] = direction
                        onUpdate(
                          "monotone_constraints",
                          Object.keys(next).length > 0 ? next : null,
                        )
                      }}
                    >
                      {direction === 1 ? "+1" : direction}
                    </button>
                  ))}
                </div>
              )
            })}
            {monotonicCandidates.length === 0 && (
              <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>
                No selected numeric features.
              </p>
            )}
          </div>
        )}
      </section>
    </div>
  )
}
