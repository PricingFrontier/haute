import { useEffect, useMemo, useRef } from "react"

import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { NODE_GROUP_COLORS } from "../../theme/colors"
import { safeParseInt } from "../../utils/configField"
import type { OnUpdateConfig } from "../editors"
import {
  formatHyperparameters,
  formatTuningSearchSpace,
  mergeReservedKeys,
  parseHyperparameters,
  parseTuningSearchSpace,
} from "./hyperparameters"

type Props = {
  algorithmLabel: string
  params: Record<string, unknown>
  defaultParams?: Record<string, unknown>
  reservedKeys?: readonly string[]
  reservedKeysHelp?: string
  onUpdate: OnUpdateConfig
  draft: string
  setDraft: (draft: string) => void
  tuning: Record<string, unknown> | null
  evaluation: Record<string, unknown>
  metrics: string[]
  searchSpaceDraft: string
  setSearchSpaceDraft: (draft: string) => void
}

function starterTuningSearchSpace(): Record<string, unknown> {
  return {
    depth: [4, 6, 8, 10],
    learning_rate: [0.01, 0.03, 0.05, 0.1, 0.2],
    l2_leaf_reg: [1, 3, 5, 10],
  }
}

export function HyperparametersConfig({
  algorithmLabel,
  params,
  defaultParams = {},
  reservedKeys = [],
  reservedKeysHelp = "",
  onUpdate,
  draft,
  setDraft,
  tuning,
  evaluation,
  metrics,
  searchSpaceDraft,
  setSearchSpaceDraft,
}: Props) {
  const stored = useMemo(
    () => formatHyperparameters(params, defaultParams, reservedKeys),
    [defaultParams, params, reservedKeys],
  )
  const previousStored = useRef(stored)

  useEffect(() => {
    if (stored === previousStored.current) return
    if (draft === previousStored.current) {
      setDraft(stored)
    }
    previousStored.current = stored
  }, [draft, setDraft, stored])

  const validation = (
    evaluation.validation !== null
    && typeof evaluation.validation === "object"
    && !Array.isArray(evaluation.validation)
  )
    ? evaluation.validation as Record<string, unknown>
    : {}
  const trialCount = typeof tuning?.trial_count === "number"
    ? tuning.trial_count
    : 20

  const updateFixedDraft = (nextDraft: string) => {
    setDraft(nextDraft)
    try {
      const projection = parseHyperparameters(
        nextDraft,
        reservedKeys,
        reservedKeysHelp,
      )
      const merged = mergeReservedKeys(params, projection, reservedKeys)
      onUpdate("params", merged)
    } catch {
      // Keep incomplete or invalid JSON local. The Train action validates this
      // same draft and surfaces the problem without submitting a request.
    }
  }

  const formatFixedDraft = () => {
    try {
      const projection = parseHyperparameters(
        draft,
        reservedKeys,
        reservedKeysHelp,
      )
      setDraft(formatHyperparameters(
        mergeReservedKeys(params, projection, reservedKeys),
        defaultParams,
        reservedKeys,
      ))
    } catch {
      // Preserve invalid text so the user can finish editing it.
    }
  }

  const updateTuning = (fields: Record<string, unknown>) => {
    if (!tuning) return
    onUpdate("tuning", { ...tuning, ...fields })
  }

  const setParameterStrategy = (strategy: "fixed" | "tune") => {
    const enabled = strategy === "tune"
    if (enabled === (tuning !== null)) return
    if (!enabled) {
      onUpdate("tuning", null)
      return
    }
    const searchSpace = starterTuningSearchSpace()
    const nextTuning = {
      schema_version: 1,
      trial_count: 20,
      seed: 42,
      metric: metrics[0] ?? "",
      search_space: searchSpace,
    }
    const hasTest = evaluation.test !== undefined
    const nextEvaluation = hasTest || validation.method === "none"
      ? evaluation
      : {
          ...evaluation,
          test: evaluation.strategy === "temporal"
            ? { start: "" }
            : { size: 0.2 },
        }
    setSearchSpaceDraft(formatTuningSearchSpace(searchSpace))
    onUpdate({ tuning: nextTuning, evaluation: nextEvaluation })
  }

  const updateSearchSpaceDraft = (nextDraft: string) => {
    setSearchSpaceDraft(nextDraft)
    try {
      updateTuning({ search_space: parseTuningSearchSpace(nextDraft) })
    } catch {
      // Keep incomplete or invalid JSON local. The Train action validates this
      // same draft and surfaces the problem without submitting a request.
    }
  }

  const formatSearchSpaceDraft = () => {
    try {
      setSearchSpaceDraft(
        formatTuningSearchSpace(parseTuningSearchSpace(searchSpaceDraft)),
      )
    } catch {
      // Preserve invalid text so the user can finish editing it.
    }
  }

  return (
    <section className="space-y-2.5">
      <div>
        <h3
          className="text-[11px] font-bold uppercase tracking-[0.08em]"
          style={{ color: "var(--text-muted)" }}
        >
          Hyperparameters
        </h3>
      </div>

      <div>
        <p
          id="parameter-strategy-label"
          className="text-[11px]"
          style={{ color: "var(--text-secondary)" }}
        >
          Parameter strategy
        </p>
        <div className="mt-1">
          <ToggleButtonGroup<"fixed" | "tune">
            value={tuning ? "tune" : "fixed"}
            onChange={setParameterStrategy}
            options={[
              { key: "fixed", label: "Fixed parameters" },
              { key: "tune", label: "Tune parameters" },
            ]}
            accentColor={NODE_GROUP_COLORS.model}
            ariaLabelledBy="parameter-strategy-label"
          />
        </div>
      </div>

      {!tuning ? (
        <label
          className="block text-[11px]"
          style={{ color: "var(--text-secondary)" }}
        >
          Parameters JSON
          <textarea
            aria-label={`${algorithmLabel} hyperparameters JSON`}
            value={draft}
            onChange={(event) => updateFixedDraft(event.target.value)}
            onBlur={formatFixedDraft}
            spellCheck={false}
            rows={Math.min(24, Math.max(10, draft.split("\n").length + 1))}
            className="mt-1.5 w-full rounded-lg px-2.5 py-2 font-mono text-xs leading-5"
            style={{
              background: "var(--bg-input)",
              border: "1px solid var(--border)",
              color: "var(--text-primary)",
              resize: "vertical",
            }}
          />
        </label>
      ) : (
        <>
          <div className="grid grid-cols-3 gap-2">
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Trial count
              <input
                aria-label="Tuning trial count"
                type="number"
                min={5}
                max={50}
                value={trialCount}
                onChange={(event) => updateTuning({
                  trial_count: Math.max(
                    5,
                    Math.min(50, safeParseInt(event.target.value, 20)),
                  ),
                })}
                className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              />
            </label>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Seed
              <input
                aria-label="Tuning seed"
                type="number"
                value={typeof tuning.seed === "number" ? tuning.seed : 42}
                onChange={(event) => updateTuning({
                  seed: safeParseInt(event.target.value, 42),
                })}
                className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              />
            </label>
            <label className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Selection metric
              <select
                aria-label="Tuning selection metric"
                value={typeof tuning.metric === "string" ? tuning.metric : ""}
                onChange={(event) => updateTuning({ metric: event.target.value })}
                className="mt-0.5 w-full rounded px-2 py-1 font-mono text-xs"
                style={{
                  background: "var(--bg-input)",
                  border: "1px solid var(--border)",
                  color: "var(--text-primary)",
                }}
              >
                {metrics.map((metric) => (
                  <option key={metric} value={metric}>{metric}</option>
                ))}
              </select>
            </label>
          </div>

          <label className="block text-[11px]" style={{ color: "var(--text-secondary)" }}>
            Search space JSON
            <textarea
              aria-label="CatBoost search space JSON"
              value={searchSpaceDraft}
              onChange={(event) => updateSearchSpaceDraft(event.target.value)}
              onBlur={formatSearchSpaceDraft}
              spellCheck={false}
              rows={Math.min(
                20,
                Math.max(6, searchSpaceDraft.split("\n").length + 1),
              )}
              className="mt-1.5 w-full rounded-lg px-2.5 py-2 font-mono text-xs leading-5"
              style={{
                background: "var(--bg-input)",
                border: "1px solid var(--border)",
                color: "var(--text-primary)",
                resize: "vertical",
              }}
            />
          </label>
        </>
      )}
    </section>
  )
}
