import ToggleButtonGroup from "../../components/ToggleButtonGroup"
import { DraftTextArea } from "../../components/form/CommittedTextField"
import { MODEL_COLORS } from "../../theme/colors"
import type { OnUpdateConfig } from "../editors/_shared"
import { NumberField } from "./NumberField"
import { MODELLING_INPUT_STYLE } from "./styles"
import { fitBudgetLabel } from "./trainingFitBudget"
import { parseHyperparameters, parseTuningSearchSpace } from "./hyperparameters"
import type { HyperparameterDrafts } from "./useHyperparameterDrafts"
import { apiErrorMessage } from "../../api/errors"

type Props = {
  algorithmLabel: string
  reservedKeys?: readonly string[]
  reservedKeysHelp?: string
  onUpdate: OnUpdateConfig
  /** The two JSON drafts, held by the caller so they outlive a pane switch. */
  drafts: HyperparameterDrafts
  tuning: Record<string, unknown> | null
  evaluation: Record<string, unknown>
  metrics: string[]
  onReviewSplit?: () => void
  /** The search space "Tune parameters" starts from; it must use the family's own keys. */
  starterSearchSpace?: Record<string, unknown>
  /** The family publishes its winning validation fit (t-boost): tuning needs no refit. */
  publishesValidationFit?: boolean
  /** The node's `refit_on_development` is not `false`, for the fit budget. */
  refitOnDevelopment: boolean
}

const CATBOOST_STARTER_SEARCH_SPACE: Record<string, unknown> = {
  depth: [4, 6, 8, 10],
  learning_rate: [0.01, 0.03, 0.05, 0.1, 0.2],
  l2_leaf_reg: [1, 3, 5, 10],
}

export function HyperparametersConfig({
  starterSearchSpace = CATBOOST_STARTER_SEARCH_SPACE,
  publishesValidationFit = false,
  refitOnDevelopment,
  algorithmLabel,
  reservedKeys = [],
  reservedKeysHelp = "",
  onUpdate,
  drafts,
  tuning,
  evaluation,
  metrics,
  onReviewSplit,
}: Props) {
  let fixedError: string | null = null
  let searchError: string | null = null
  if (!tuning) {
    try {
      parseHyperparameters(drafts.params.shown, reservedKeys, reservedKeysHelp)
    } catch (cause) {
      fixedError = apiErrorMessage(cause, "Invalid JSON")
    }
  } else {
    try {
      parseTuningSearchSpace(drafts.searchSpace.shown)
    } catch (cause) {
      searchError = apiErrorMessage(cause, "Invalid JSON")
    }
  }
  // The Train pane's run summary words the same budget.
  const fitBudget = fitBudgetLabel(evaluation, tuning, { publishesValidationFit, refitOnDevelopment })
  const updateTuning = (fields: Record<string, unknown>) => {
    if (tuning) onUpdate("tuning", { ...tuning, ...fields })
  }
  const setStrategy = (strategy: "fixed" | "tune") => {
    if ((strategy === "tune") === Boolean(tuning)) return
    if (strategy === "fixed") {
      onUpdate("tuning", null)
      return
    }
    const searchSpace = { ...starterSearchSpace }
    // Tuning hides the fixed-parameter editor: drop an unsaved draft rather
    // than keep text the user can no longer see or correct.
    drafts.params.discard()
    drafts.searchSpace.discard()
    onUpdate({
      ...(publishesValidationFit ? {} : { refit_on_development: true }),
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
        <div>
          <label
            className="block text-[13px]"
            style={{ color: "var(--text-secondary)" }}
          >
            Parameters JSON
            <DraftTextArea
              draft={drafts.params}
              aria-label={`${algorithmLabel} hyperparameters JSON`}
              aria-invalid={Boolean(fixedError)}
              spellCheck={false}
              rows={Math.min(24, Math.max(6, drafts.params.shown.split("\n").length + 1))}
              className={`${inputClass} leading-5`}
              style={{ ...MODELLING_INPUT_STYLE, resize: "vertical" }}
            />
          </label>
        </div>
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
            {fitBudget && <p className="mt-2 font-medium">{fitBudget}</p>}
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
            <DraftTextArea
              draft={drafts.searchSpace}
              aria-label={`${algorithmLabel} search space JSON`}
              aria-invalid={Boolean(searchError)}
              spellCheck={false}
              rows={Math.min(
                20,
                Math.max(6, drafts.searchSpace.shown.split("\n").length + 1),
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
