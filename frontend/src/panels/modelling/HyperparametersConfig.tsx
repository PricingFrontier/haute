import { useEffect, useMemo, useRef } from "react"
import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { MODEL_COLORS } from "../../theme/colors"
import type { OnUpdateConfig } from "../editors"
import { NumberField } from "./NumberField"
import { MODELLING_INPUT_STYLE } from "./styles"
import { trainingFitBudget } from "./trainingFitBudget"
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
  onReviewSplit?: () => void
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
  onReviewSplit,
}: Props) {
  const stored = useMemo(
    () => formatHyperparameters(params, reservedKeys),
    [params, reservedKeys],
  )
  const previousStored = useRef(stored)
  useEffect(() => {
    if (stored === previousStored.current) return
    if (draft === previousStored.current) setDraft(stored)
    previousStored.current = stored
  }, [draft, setDraft, stored])

  let projection: Record<string, unknown> | null = null
  let fixedError: string | null = null
  let searchError: string | null = null
  try {
    projection = parseHyperparameters(draft, reservedKeys, reservedKeysHelp)
  } catch (cause) {
    fixedError = cause instanceof Error ? cause.message : "Invalid JSON"
  }
  if (tuning) {
    try {
      parseTuningSearchSpace(searchSpaceDraft)
    } catch (cause) {
      searchError = cause instanceof Error ? cause.message : "Invalid JSON"
    }
  }
  const budget = trainingFitBudget(evaluation, tuning)
  const updateFixedDraft = (nextDraft: string) => {
    setDraft(nextDraft)
    try {
      onUpdate(
        "params",
        mergeReservedKeys(
          params,
          parseHyperparameters(nextDraft, reservedKeys, reservedKeysHelp),
          reservedKeys,
        ),
      )
    } catch {
      /* An invalid draft remains editable and is shown below. */
    }
  }
  const updateTuning = (fields: Record<string, unknown>) => {
    if (tuning) onUpdate("tuning", { ...tuning, ...fields })
  }
  const setStrategy = (strategy: "fixed" | "tune") => {
    if ((strategy === "tune") === Boolean(tuning)) return
    if (strategy === "fixed") {
      onUpdate("tuning", null)
      return
    }
    const searchSpace = starterTuningSearchSpace()
    setSearchSpaceDraft(formatTuningSearchSpace(searchSpace))
    onUpdate({
      refit_on_development: true,
      tuning: {
        schema_version: 1,
        trial_count: 20,
        seed: 42,
        metric: metrics[0] ?? "",
        search_space: searchSpace,
      },
    })
  }
  const inputClass = "mt-1 w-full rounded-lg px-3 py-2 font-mono text-[13px]"
  return (
    <section className="space-y-5">
      <div>
        <p
          id="parameter-strategy-label"
          className="mb-2 text-[13px]"
          style={{ color: "var(--text-secondary)" }}
        >
          Parameter strategy
        </p>
        <ToggleButtonGroup<"fixed" | "tune">
          value={tuning ? "tune" : "fixed"}
          onChange={setStrategy}
          options={[
            { key: "fixed", label: "Fixed parameters" },
            { key: "tune", label: "Tune parameters" },
          ]}
          accentColor={MODEL_COLORS.accent}
          ariaLabelledBy="parameter-strategy-label"
        />
      </div>
      {!tuning ? (
        <label
          className="block text-[13px]"
          style={{ color: "var(--text-secondary)" }}
        >
          Parameters JSON
          <textarea
            aria-label={`${algorithmLabel} hyperparameters JSON`}
            aria-invalid={Boolean(fixedError)}
            value={draft}
            onChange={(event) => updateFixedDraft(event.target.value)}
            onBlur={() => {
              if (projection) setDraft(formatHyperparameters(projection))
            }}
            spellCheck={false}
            rows={Math.min(24, Math.max(6, draft.split("\n").length + 1))}
            className={`${inputClass} leading-5`}
            style={{ ...MODELLING_INPUT_STYLE, resize: "vertical" }}
          />
        </label>
      ) : (
        <>
          <div
            className="rounded-lg border p-3 text-xs leading-5"
            style={{
              borderColor: "var(--border)",
              background: "var(--bg-input)",
            }}
          >
            <p>
              {evaluation.test == null
                ? "No test set is reserved. Reserve one in Split for an independent evaluation."
                : "The test set stays held out during hyperparameter tuning."}
            </p>
            {onReviewSplit && (
              <button
                type="button"
                onClick={onReviewSplit}
                className="mt-1 font-medium"
                style={{ color: MODEL_COLORS.accent }}
              >
                Review split →
              </button>
            )}
            {budget && (
              <p className="mt-2 font-medium">
                {budget.total} total fits: {budget.trials} trials ×{" "}
                {budget.folds} validation fits + 1 final fit.
              </p>
            )}
          </div>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-[13px]">
              Trial count
              <NumberField
                label="Tuning trial count"
                value={
                  typeof tuning.trial_count === "number"
                    ? tuning.trial_count
                    : undefined
                }
                min={5}
                max={50}
                integer
                required
                step={1}
                className={inputClass}
                onCommit={(value) => updateTuning({ trial_count: value })}
              />
            </label>
            <label className="text-[13px]">
              Seed
              <NumberField
                label="Tuning seed"
                value={
                  typeof tuning.seed === "number" ? tuning.seed : undefined
                }
                min={Number.MIN_SAFE_INTEGER}
                max={Number.MAX_SAFE_INTEGER}
                integer
                required
                step={1}
                className={inputClass}
                onCommit={(value) => updateTuning({ seed: value })}
              />
            </label>
            <label className="col-span-2 text-[13px]">
              Selection metric
              <select
                aria-label="Tuning selection metric"
                value={typeof tuning.metric === "string" ? tuning.metric : ""}
                onChange={(event) =>
                  updateTuning({ metric: event.target.value })
                }
                className={inputClass}
                style={MODELLING_INPUT_STYLE}
              >
                <option value="">Choose a metric</option>
                {metrics.map((metric) => (
                  <option key={metric} value={metric}>
                    {metric}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="block text-[13px]">
            Search space JSON
            <textarea
              aria-label="CatBoost search space JSON"
              aria-invalid={Boolean(searchError)}
              value={searchSpaceDraft}
              onChange={(event) => {
                setSearchSpaceDraft(event.target.value)
                try {
                  updateTuning({
                    search_space: parseTuningSearchSpace(event.target.value),
                  })
                } catch {
                  /* Preserve invalid drafts; the error is shown below. */
                }
              }}
              onBlur={() => {
                if (!searchError)
                  setSearchSpaceDraft(
                    formatTuningSearchSpace(
                      parseTuningSearchSpace(searchSpaceDraft),
                    ),
                  )
              }}
              spellCheck={false}
              rows={Math.min(
                20,
                Math.max(6, searchSpaceDraft.split("\n").length + 1),
              )}
              className={`${inputClass} leading-5`}
              style={{ ...MODELLING_INPUT_STYLE, resize: "vertical" }}
            />
          </label>
        </>
      )}
      {(fixedError || searchError) && (
        <p
          role="alert"
          className="text-xs leading-5"
          style={{ color: "var(--danger)" }}
        >
          {fixedError
            ? `Parameters JSON: ${fixedError}`
            : `Search space JSON: ${searchError}`}
        </p>
      )}
    </section>
  )
}
