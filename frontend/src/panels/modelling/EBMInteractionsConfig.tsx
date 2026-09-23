/**
 * EBM pairwise-interaction control.
 *
 * Main effects are the features the Features list includes; this control sets
 * which pairwise interaction terms the EBM may add, written to the
 * ``interactions`` parameter: a count lets EBM pick its strongest pairs, a
 * list fixes the pairs. The backend validates both forms and refuses a pair
 * involving a monotone-constrained feature.
 */
import { useMemo } from "react"
import { Plus, X } from "lucide-react"
import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import {
  finalSelectedFeatureNames,
  roleColumns,
  type ModellingColumn,
} from "./featureSelection"

type Props = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
  columns: ModellingColumn[]
}

type Pair = [string, string]

const DEFAULT_INTERACTION_COUNT = 10

function interactionPairs(value: unknown): Pair[] | null {
  if (!Array.isArray(value)) return null
  return value.map((pair) =>
    Array.isArray(pair) ? [String(pair[0] ?? ""), String(pair[1] ?? "")] : ["", ""],
  )
}

export function EBMInteractionsConfig({ config, onUpdate, columns }: Props) {
  const params = configField<Record<string, unknown>>(config, "params", {})
  const interactions = params.interactions ?? 0
  const pairs = interactionPairs(interactions)
  const mode = pairs === null ? "automatic" : "pairs"
  const count = typeof interactions === "number" ? interactions : 0
  const monotone = configField<Record<string, number>>(config, "monotone_constraints", {})

  const features = useMemo(() => {
    const roles = roleColumns(config)
    const eligible = columns.filter((column) => !roles.has(column.name))
    return [...finalSelectedFeatureNames(config, eligible)]
  }, [columns, config])

  const write = (next: unknown) => onUpdate("params", { ...params, interactions: next })
  const setPair = (index: number, position: 0 | 1, name: string) => {
    const next = (pairs ?? []).map((pair) => [...pair] as Pair)
    next[index][position] = name
    write(next)
  }
  const inputClass = "rounded-md px-2 py-1 text-[13px]"
  const inputStyle = {
    background: "var(--bg-input)",
    border: "1px solid var(--border)",
    color: "var(--text-primary)",
  }

  return (
    <section aria-labelledby="ebm-interactions-heading" className="space-y-3">
      <div>
        <h3
          id="ebm-interactions-heading"
          className="text-[14px] font-semibold"
          style={{ color: "var(--text-muted)" }}
        >
          Pairwise interactions
        </h3>
        <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
          Every included feature is a main effect. Interactions add two-feature terms, each
          kept as one term in results and explanations. A monotone-constrained feature cannot
          take part in an interaction.
        </p>
      </div>
      <div role="radiogroup" aria-label="Interaction selection" className="flex gap-4 text-[13px]">
        <label className="flex items-center gap-1.5">
          <input
            type="radio"
            name="ebm-interaction-mode"
            checked={mode === "automatic"}
            onChange={() => write(DEFAULT_INTERACTION_COUNT)}
          />
          Let EBM choose
        </label>
        <label className="flex items-center gap-1.5">
          <input
            type="radio"
            name="ebm-interaction-mode"
            checked={mode === "pairs"}
            onChange={() => write([])}
          />
          Choose pairs
        </label>
      </div>
      {mode === "automatic" ? (
        <label className="flex items-center gap-2 text-[13px]" style={{ color: "var(--text-secondary)" }}>
          Up to
          <input
            type="number"
            min={0}
            step={1}
            aria-label="Maximum interaction count"
            className={`${inputClass} w-20`}
            style={inputStyle}
            value={count}
            onChange={(event) => {
              const next = Number.parseInt(event.target.value, 10)
              write(Number.isFinite(next) && next >= 0 ? next : 0)
            }}
          />
          {count === 1 ? "interaction" : "interactions"}, the strongest pairs EBM finds on the training rows.
        </label>
      ) : (
        <div className="space-y-2">
          {(pairs ?? []).length === 0 && (
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              No pairs yet: the model has main effects only.
            </p>
          )}
          {(pairs ?? []).map((pair, index) => (
            <div
              key={index}
              role="group"
              aria-label={`Interaction ${index + 1}`}
              className="flex items-center gap-2"
            >
              {([0, 1] as const).map((position) => (
                <select
                  key={position}
                  aria-label={`Interaction ${index + 1} feature ${position + 1}`}
                  className={`${inputClass} min-w-0 flex-1`}
                  style={inputStyle}
                  value={pair[position]}
                  onChange={(event) => setPair(index, position, event.target.value)}
                >
                  <option value="">Choose a feature</option>
                  {features.map((name) => (
                    <option key={name} value={name} disabled={Boolean(monotone[name])}>
                      {name}{monotone[name] ? " (monotone)" : ""}
                    </option>
                  ))}
                  {pair[position] && !features.includes(pair[position]) && (
                    <option value={pair[position]}>{pair[position]} (not a feature)</option>
                  )}
                </select>
              ))}
              <button
                type="button"
                aria-label={`Remove interaction ${index + 1}`}
                className="rounded p-1"
                style={{ color: "var(--text-muted)" }}
                onClick={() => write((pairs ?? []).filter((_, i) => i !== index))}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          <button
            type="button"
            className="flex items-center gap-1 text-[13px]"
            style={{ color: "var(--accent)" }}
            onClick={() => write([...(pairs ?? []), ["", ""]])}
          >
            <Plus size={14} /> Add interaction
          </button>
        </div>
      )}
    </section>
  )
}
