import { useId, useState, type ReactNode } from "react"
import { ChevronRight } from "lucide-react"

import type { OnUpdateConfig } from "../editors"
import { configField } from "../../utils/configField"
import { toggleButtonStyle } from "./styles"
import { FailoverHelp } from "./FailoverHelp"
import {
  GLM_CV_DEFAULTS,
  GLM_CV_FOLDS_RANGE,
  GLM_MAX_ITER_RANGE,
  GLM_ROBUST_STANDARD_ERRORS,
  glmCrossValidates,
} from "./glmFamilies"
import {
  interactionEntryIssue,
  monotoneConstraintTerms,
  penalisedSmoothTerms,
  type InteractionSpec,
  type Terms,
} from "./glmTerms"
import { NumberField } from "./NumberField"

const REGULARIZATION_TYPES = [
  { value: "", label: "None" },
  { value: "ridge", label: "Ridge" },
  { value: "lasso", label: "Lasso" },
  { value: "elastic_net", label: "Elastic Net" },
] as const

const PENALTY_HELP =
  "Penalty-selection cross-validation chooses the penalty strength on held-out folds of the " +
  "training data. Fixed uses the alpha you enter."

const SOLVER_HELP =
  "Leave maximum iterations and tolerance blank to use RustyStats' defaults. " +
  "Robust (sandwich) standard errors keep coefficient estimates unchanged and " +
  "widen their standard errors when the variance assumption is doubtful."

const LABEL_STYLE = { color: "var(--text-secondary)" } as const
const INPUT_CLASS = "w-full mt-0.5 px-2.5 py-1.5 rounded-lg text-xs font-mono"
const INPUT_STYLE = { background: "var(--bg-input)", border: "1px solid var(--border)", color: "var(--text-primary)" } as const

export type GLMRegularizationConfigProps = {
  config: Record<string, unknown>
  onUpdate: OnUpdateConfig
}

function Conflict({ children }: { children: ReactNode }) {
  return <p role="alert" className="text-[11px]" style={{ color: "var(--danger)" }}>{children}</p>
}

function numberOrUndefined(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined
}

export function GLMRegularizationConfig({ config, onUpdate }: GLMRegularizationConfigProps) {
  const [solverOpen, setSolverOpen] = useState(false)
  const solverId = useId()
  const regularization = configField(config, "regularization", "")
  const l1RatioSet = config.l1_ratio !== undefined && config.l1_ratio !== null
  const l1Ratio = configField(config, "l1_ratio", 0.5)
  const isActive = !!regularization
  const crossValidated = glmCrossValidates(config)
  const robust = typeof config.robust_standard_errors === "string" ? config.robust_standard_errors : ""

  const terms = (config.terms !== null && typeof config.terms === "object" && !Array.isArray(config.terms)
    ? config.terms
    : {}) as Terms
  const interactions = (Array.isArray(config.interactions) ? config.interactions : [])
    .filter((entry): entry is InteractionSpec => interactionEntryIssue(entry) === null)
  const smooth = penalisedSmoothTerms(terms, interactions)
  const monotone = monotoneConstraintTerms(terms)
  const robustConflict = !robust
    ? null
    : isActive
      ? "regularization"
      : monotone.length > 0
        ? `monotonicity constraints (${monotone.join(", ")})`
        : smooth.length > 0
          ? `automatically smoothed splines (${smooth.join(", ")})`
          : null

  const chooseType = (value: string) => {
    if (value === "") {
      onUpdate("regularization", null)
      return
    }
    // Store reproducible cross-validation defaults when absent; preserve existing values.
    const missing = Object.fromEntries(
      Object.entries(GLM_CV_DEFAULTS).filter(([key]) => config[key] === undefined || config[key] === null),
    )
    const l1RatioDefault = value === "elastic_net" && !l1RatioSet ? { l1_ratio: 0.5 } : {}
    onUpdate({ regularization: value, ...missing, ...l1RatioDefault })
  }

  return (
    <div>
      <h3 className="text-sm font-semibold" style={{ color: "var(--text-muted)" }}>
        Regularization
      </h3>

      <div className="mt-1.5 space-y-2">
        <div className="flex flex-wrap gap-1.5">
          {REGULARIZATION_TYPES.map(r => (
            <button
              key={r.value}
              type="button"
              aria-pressed={regularization === r.value}
              onClick={() => chooseType(r.value)}
              className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
              style={toggleButtonStyle(regularization === r.value)}
            >
              {r.label}
            </button>
          ))}
        </div>

        {isActive && smooth.length > 0 && (
          <Conflict>
            Regularization cannot be combined with automatically smoothed splines ({smooth.join(", ")}): set
            Fixed df on those splines or turn regularization off.
          </Conflict>
        )}

        {isActive && (
          <>
            <div>
              <span className="flex items-center gap-1 text-xs" style={LABEL_STYLE}>
                Penalty
                <FailoverHelp label={PENALTY_HELP} />
              </span>
              <div role="group" aria-label="Penalty mode" className="mt-1 flex gap-1.5">
                <button
                  type="button"
                  aria-pressed={crossValidated}
                  onClick={() => onUpdate("alpha", null)}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(crossValidated)}
                >
                  Cross-validated
                </button>
                <button
                  type="button"
                  aria-pressed={!crossValidated}
                  onClick={() => {
                    if (crossValidated) onUpdate("alpha", 1)
                  }}
                  className="px-2.5 py-1 rounded-md text-xs font-mono transition-colors"
                  style={toggleButtonStyle(!crossValidated)}
                >
                  Fixed
                </button>
              </div>
            </div>

            {crossValidated ? (
              <div className="grid grid-cols-[repeat(auto-fit,minmax(6.5rem,1fr))] gap-2">
                <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                  Folds
                  <NumberField
                    label="Cross-validation folds"
                    value={numberOrUndefined(config.cv_folds)}
                    min={GLM_CV_FOLDS_RANGE[0]}
                    max={GLM_CV_FOLDS_RANGE[1]}
                    step={1}
                    integer
                    required
                    className={INPUT_CLASS}
                    style={INPUT_STYLE}
                    onCommit={(value) => onUpdate("cv_folds", value)}
                  />
                </label>
                <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                  Selection rule
                  <select
                    aria-label="Cross-validation selection rule"
                    value={typeof config.cv_selection === "string" ? config.cv_selection : ""}
                    onChange={(event) => onUpdate("cv_selection", event.target.value)}
                    className={INPUT_CLASS}
                    style={INPUT_STYLE}
                  >
                    {typeof config.cv_selection !== "string" && <option value="" disabled>Choose…</option>}
                    <option value="min">Minimum deviance</option>
                    <option value="1se">One standard error</option>
                  </select>
                </label>
              </div>
            ) : (
              <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                Alpha
                <NumberField
                  label="Regularization alpha"
                  value={numberOrUndefined(config.alpha)}
                  min={0}
                  exclusiveMin
                  step="any"
                  integer={false}
                  required
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                  onCommit={(value) => onUpdate("alpha", value)}
                />
              </label>
            )}

            {regularization === "elastic_net" && (
              <div>
                <label className="text-[11px]" style={{ color: "var(--text-muted)" }} htmlFor={`${solverId}-l1-ratio`}>L1 ratio</label>
                <input
                  id={`${solverId}-l1-ratio`}
                  type="range" min={0} max={1} step={0.05}
                  value={l1Ratio}
                  onChange={(e) => onUpdate("l1_ratio", parseFloat(e.target.value))}
                  className="w-full mt-1.5"
                />
                <div className="text-[11px] font-mono text-right" style={{ color: "var(--text-muted)" }}>
                  {l1RatioSet ? l1Ratio.toFixed(2) : "Not set"}
                </div>
              </div>
            )}
          </>
        )}

        <div>
          <button
            type="button"
            aria-expanded={solverOpen}
            aria-controls={solverId}
            className="focus-ring flex items-center gap-0.5 rounded text-sm font-semibold"
            style={{ color: "var(--text-muted)" }}
            onClick={() => setSolverOpen(!solverOpen)}
          >
            <ChevronRight size={12} className={solverOpen ? "rotate-90" : ""} aria-hidden="true" />
            Solver
          </button>
          {robustConflict && (
            <Conflict>
              Robust standard errors cannot be combined with {robustConflict}: RustyStats marks that inference as
              not valid. Turn robust standard errors off or remove the conflict.
            </Conflict>
          )}
          <div id={solverId} hidden={!solverOpen} className="mt-1.5 space-y-2">
            <p className="text-[10px]" style={{ color: "var(--text-muted)" }}>{SOLVER_HELP}</p>
            <div className="grid grid-cols-[repeat(auto-fit,minmax(7rem,1fr))] gap-2">
              <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                Maximum iterations
                <NumberField
                  label="Maximum iterations"
                  value={numberOrUndefined(config.max_iter)}
                  min={GLM_MAX_ITER_RANGE[0]}
                  max={GLM_MAX_ITER_RANGE[1]}
                  step={1}
                  integer
                  placeholder="Default"
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                  onCommit={(value) => onUpdate("max_iter", value ?? null)}
                />
              </label>
              <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                Tolerance
                <NumberField
                  label="Convergence tolerance"
                  value={numberOrUndefined(config.tol)}
                  min={0}
                  max={1}
                  exclusiveMin
                  exclusiveMax
                  step="any"
                  integer={false}
                  placeholder="Default"
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                  onCommit={(value) => onUpdate("tol", value ?? null)}
                />
              </label>
              <label className="flex flex-col text-xs" style={LABEL_STYLE}>
                Robust standard errors
                <select
                  aria-label="Robust standard errors"
                  value={robust}
                  onChange={(event) => onUpdate("robust_standard_errors", event.target.value || null)}
                  className={INPUT_CLASS}
                  style={INPUT_STYLE}
                >
                  <option value="">Off</option>
                  {GLM_ROBUST_STANDARD_ERRORS.map((type) => <option key={type} value={type}>{type}</option>)}
                </select>
              </label>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
