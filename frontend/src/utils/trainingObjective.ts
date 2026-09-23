import { algorithmCapability } from "../panels/modelling/algorithmCapabilities"
import {
  interactionEntryIssue,
  monotoneConstraintTerms,
  penalisedSmoothTerms,
  type InteractionSpec,
  type Terms,
} from "../panels/modelling/glmTerms"
import { glmCrossValidates } from "../panels/modelling/glmFamilies"

/**
 * Frontend mirror of the backend's target/objective validation.
 *
 * The backend remains authoritative. This helper aggregates every currently
 * applicable issue for inline feedback, pane readiness and the Train summary.
 * Invalid configurations never submit a training request.
 */

export type TrainingConfigurationIssueCode =
  | "training-target"
  | "glm-family"
  | "glm-tweedie-variance-power"
  | "glm-negbin-theta"
  | "glm-terms"
  | "glm-elastic-net-l1-ratio"
  | "glm-cross-validation"
  | "glm-smooth-regularization"
  | "glm-robust-standard-errors"
  | "catboost-params"
  | "catboost-loss-function"
  | "catboost-tweedie-variance-power"
  | "monotone-loss"
  | "evaluation-config"
  | "final-refit"
  | "tuning-config"

export type TrainingConfigurationIssue = {
  code: TrainingConfigurationIssueCode
  message: string
}

/**
 * The reported metrics training will use: explicit `metrics`, else the
 * objective-implied defaults. Mirrors the backend's `effective_metrics`.
 */
export function effectiveMetrics(config: Record<string, unknown>): string[] {
  if (Array.isArray(config.metrics) && config.metrics.length > 0) {
    return config.metrics.filter((metric): metric is string => typeof metric === "string")
  }
  const glm = String(config.algorithm ?? "catboost").toLowerCase() === "glm"
  const objective = String((glm ? config.family : config.loss_function) ?? "").toLowerCase()
  if (
    config.task === "classification"
    || ["binomial", "quasibinomial", "logloss", "crossentropy"].includes(objective)
  ) {
    return ["auc", "logloss"]
  }
  if (["poisson", "quasipoisson", "negbinomial"].includes(objective)) return ["gini", "poisson_deviance"]
  if (objective === "tweedie") return ["gini", "tweedie_deviance"]
  if (objective === "gamma") return ["gini", "gamma_deviance"]
  return ["gini", "rmse"]
}

export function evaluationConfigurationIssues(rawEvaluation: unknown): TrainingConfigurationIssue[] {
  const issues: TrainingConfigurationIssue[] = []
  const evaluation = (
    rawEvaluation !== null
    && typeof rawEvaluation === "object"
    && !Array.isArray(rawEvaluation)
  )
    ? rawEvaluation as Record<string, unknown>
    : null
  const validation = (
    evaluation?.validation !== null
    && typeof evaluation?.validation === "object"
    && !Array.isArray(evaluation.validation)
  )
    ? evaluation.validation as Record<string, unknown>
    : null
  const strategy = evaluation?.strategy
  const method = validation?.method
  const configuredTest = evaluation?.test
  const hasConfiguredTest = configuredTest !== undefined && configuredTest !== null
  const test = (
    hasConfiguredTest
    && typeof configuredTest === "object"
    && !Array.isArray(configuredTest)
  )
    ? configuredTest as Record<string, unknown>
    : null
  const temporalValidationTimestamp = (
    typeof validation?.start === "string"
    && validation.start.length > 0
  )
    ? Date.parse(validation.start)
    : Number.NaN
  const temporalTestTimestamp = (
    typeof test?.start === "string"
    && test.start.length > 0
  )
    ? Date.parse(test.start)
    : Number.NaN
  const validTest = (
    !hasConfiguredTest
    || (
      test !== null
      && (
        strategy === "temporal"
          ? Number.isFinite(temporalTestTimestamp)
          : typeof test.size === "number"
            && Number.isFinite(test.size)
            && test.size > 0
            && test.size < 1
      )
    )
  )
  const validTemporalBoundaryOrder = (
    strategy !== "temporal"
    || method !== "single"
    || !hasConfiguredTest
    || (
      Number.isFinite(temporalValidationTimestamp)
      && Number.isFinite(temporalTestTimestamp)
      && temporalValidationTimestamp < temporalTestTimestamp
    )
  )
  const invalidFractionSum = strategy !== "temporal" && method === "single"
    && typeof validation?.size === "number" && typeof test?.size === "number"
    && validation.size + test.size >= 1
  const validEvaluation = (
    evaluation?.schema_version === 1
    && ["random", "group", "temporal"].includes(String(strategy))
    && validTest
    && !invalidFractionSum
    && validTemporalBoundaryOrder
    && (
      strategy === "temporal"
        ? typeof evaluation.date_column === "string"
          && evaluation.date_column.length > 0
        : Number.isInteger(evaluation.seed)
    )
    && (
      strategy !== "group"
      || (
        typeof evaluation.group_column === "string"
        && evaluation.group_column.length > 0
      )
    )
    && (
      method === "none"
      || (
        method === "single"
        && (
          strategy === "temporal"
            ? Number.isFinite(temporalValidationTimestamp)
            : typeof validation?.size === "number"
              && Number.isFinite(validation.size)
              && validation.size > 0
              && validation.size < 1
        )
      )
      || (
        method === "cross_validation"
        && Number.isInteger(validation?.fold_count)
        && Number(validation?.fold_count) >= 2
        && Number(validation?.fold_count) <= 10
        && (strategy !== "temporal" || validation?.window === "expanding")
      )
    )
  )
  if (!validEvaluation) {
    issues.push({
      code: "evaluation-config",
      message: invalidFractionSum
        ? "Validation and test must total below 100% so training retains some rows."
        : !validTemporalBoundaryOrder
          ? "Validation must start before the test set."
          : "Complete the split settings: split strategy, validation strategy, and required group/date fields.",
    })
  }

  return issues
}

export function trainingConfigurationIssues(
  config: Record<string, unknown>,
): TrainingConfigurationIssue[] {
  const issues: TrainingConfigurationIssue[] = []
  const target = config.target
  if (typeof target !== "string" || target.trim() === "") {
    issues.push({
      code: "training-target",
      message: "Select a target column.",
    })
  }

  issues.push(...evaluationConfigurationIssues(config.evaluation))
  const evaluation = config.evaluation as Record<string, unknown> | undefined
  const validation = evaluation?.validation as Record<string, unknown> | undefined
  const method = validation?.method
  const refit = config.refit_on_development
  if ((refit !== undefined && typeof refit !== "boolean") || (
    refit === false && method !== "single"
  )) {
    issues.push({
      code: "final-refit",
      message: "Skipping the final refit requires holdout validation.",
    })
  }

  const tuning = (
    config.tuning !== null
    && typeof config.tuning === "object"
    && !Array.isArray(config.tuning)
  )
    ? config.tuning as Record<string, unknown>
    : null
  if (tuning) {
    if (refit === false && method === "single") {
      issues.push({
        code: "final-refit",
        message: "Parameter tuning requires a final refit.",
      })
    }
    const metrics = effectiveMetrics(config)
    const searchSpace = (
      tuning.search_space !== null
      && typeof tuning.search_space === "object"
      && !Array.isArray(tuning.search_space)
    )
      ? tuning.search_space as Record<string, unknown>
      : null
    const trialCount = Number(tuning.trial_count)
    const validationFitCount = method === "cross_validation"
      ? Number(validation?.fold_count)
      : method === "single"
        ? 1
        : 0
    const validTuning = (
      algorithmCapability(String(config.algorithm ?? ""))?.supports_tuning === true
      && tuning.schema_version === 1
      && Number.isInteger(tuning.trial_count)
      && trialCount >= 5
      && trialCount <= 50
      && Number.isInteger(tuning.seed)
      && typeof tuning.metric === "string"
      && metrics.includes(tuning.metric)
      && searchSpace !== null
      && Object.keys(searchSpace).length >= 1
      && Object.keys(searchSpace).length <= 32
      && validationFitCount > 0
      && trialCount * validationFitCount <= 200
    )
    if (!validTuning) {
      issues.push({
        code: "tuning-config",
        message:
          "Complete tuning: 5–50 trials, a configured selection metric, a " +
          "non-empty search space, and at most 200 trial-validation fits.",
      })
    }
  }

  const algorithm = String(config.algorithm ?? "catboost").toLowerCase()
  if (algorithm === "glm") {
    const family = config.family
    if (!family) {
      issues.push({
        code: "glm-family",
        message:
          "Choose a GLM distribution family (e.g. Poisson for claim counts, " +
          "Gamma for severity) - an unset family would silently train a " +
          "gaussian model.",
      })
    } else {
      const normalizedFamily = String(family).toLowerCase()
      const varPower = config.var_power
      if (
        normalizedFamily === "tweedie"
        && (varPower === undefined || varPower === null)
      ) {
        issues.push({
          code: "glm-tweedie-variance-power",
          message:
            "Set the Tweedie variance power (1=Poisson, 2=Gamma) - an unset " +
            "value would silently fit at power 1.5.",
        })
      }
      const theta = config.theta
      if (
        normalizedFamily === "negbinomial"
        && (theta === undefined || theta === null)
      ) {
        issues.push({
          code: "glm-negbin-theta",
          message:
            "Set the Negative Binomial dispersion (theta), or estimate it from " +
            "the data - RustyStats refuses to fit without it.",
        })
      }
    }

    const terms = config.terms
    const hasTerms = (
      terms !== null
      && typeof terms === "object"
      && !Array.isArray(terms)
      && Object.keys(terms).length > 0
    )
    if (!hasTerms) {
      issues.push({
        code: "glm-terms",
        message: "Add a term to at least one feature.",
      })
    }

    if (
      String(config.regularization ?? "").toLowerCase() === "elastic_net"
      && (config.l1_ratio === undefined || config.l1_ratio === null)
    ) {
      issues.push({
        code: "glm-elastic-net-l1-ratio",
        message: "Choose an L1 ratio.",
      })
    }

    if (glmCrossValidates(config)) {
      const missing = ([["cv_folds", "folds"], ["cv_selection", "selection rule"]] as const)
        .filter(([key]) => config[key] === undefined || config[key] === null || config[key] === "")
        .map(([, label]) => label)
      if (missing.length > 0) {
        issues.push({
          code: "glm-cross-validation",
          message:
            `Set the cross-validation ${missing.join(", ")} so the selected penalty is ` +
            "reproducible.",
        })
      }
    }

    const glmTerms: Terms = hasTerms ? terms as Terms : {}
    const glmInteractions = (Array.isArray(config.interactions) ? config.interactions : [])
      .filter((entry): entry is InteractionSpec => interactionEntryIssue(entry) === null)
    const smooth = penalisedSmoothTerms(glmTerms, glmInteractions)
    const regularized = typeof config.regularization === "string" && config.regularization !== ""
    if (regularized && smooth.length > 0) {
      issues.push({
        code: "glm-smooth-regularization",
        message:
          `Regularization cannot be combined with automatically smoothed splines (${smooth.join(", ")}): ` +
          "set Fixed df on those splines or turn regularization off.",
      })
    }
    const robust = config.robust_standard_errors
    if (typeof robust === "string" && robust !== "") {
      const monotone = monotoneConstraintTerms(glmTerms)
      const conflict = regularized
        ? "regularization"
        : monotone.length > 0
          ? `monotonicity constraints (${monotone.join(", ")})`
          : smooth.length > 0
            ? `automatically smoothed splines (${smooth.join(", ")})`
            : null
      if (conflict !== null) {
        issues.push({
          code: "glm-robust-standard-errors",
          message:
            `Robust standard errors cannot be combined with ${conflict}: RustyStats marks that ` +
            "inference as not valid. Turn robust standard errors off or remove the conflict.",
        })
      }
    }
    return issues
  }

  const lossFunction = config.loss_function
  if (!lossFunction) {
    issues.push({
      code: "catboost-loss-function",
      message:
        "Choose a training loss (e.g. Poisson for claim counts, RMSE for a " +
        "squared-error regression) - an unset loss would silently train " +
        "under the library default.",
    })
  } else if (
    String(lossFunction) === "Tweedie"
    && (typeof config.variance_power !== "number" || !Number.isFinite(config.variance_power)
      || config.variance_power <= 1 || config.variance_power >= 2)
  ) {
    issues.push({
      code: "catboost-tweedie-variance-power",
      message:
        "Set the Tweedie variance power greater than 1 and less than 2.",
    })
  }
  const capability = algorithmCapability(algorithm)
  if (
    lossFunction
    && capability?.monotone_unsupported_losses.includes(String(lossFunction))
    && hasMonotoneConstraints(config)
  ) {
    issues.push({
      code: "monotone-loss",
      message:
        `${capability.label} cannot apply monotonicity constraints with the ${String(lossFunction)} ` +
        "loss; remove them from the Features pane or choose another loss.",
    })
  }
  return issues
}

function hasMonotoneConstraints(config: Record<string, unknown>): boolean {
  const constraints = config.monotone_constraints
  if (constraints === null || typeof constraints !== "object" || Array.isArray(constraints)) {
    return false
  }
  // Mirrors the backend's _excluded_feature_names: explicit feature_columns win
  // over a stale exclusion, so a constraint on such a feature stays active.
  const explicit = new Set(
    Array.isArray(config.feature_columns) ? config.feature_columns.map(String) : [],
  )
  const excluded = new Set(
    (Array.isArray(config.exclude) ? config.exclude.map(String) : [])
      .filter((name) => !explicit.has(name)),
  )
  return Object.keys(constraints).some((name) => !excluded.has(name))
}

/** Destination of a readiness issue, shared by tabs and the Train summary. */
export function trainingIssuePane(issue: TrainingConfigurationIssue): "target" | "features" | "params" | "split" {
  switch (issue.code) {
    case "evaluation-config": return "split"
    case "final-refit": return "split"
    case "catboost-params":
    case "tuning-config":
    case "glm-elastic-net-l1-ratio":
    case "glm-cross-validation":
    case "glm-smooth-regularization":
    case "glm-robust-standard-errors": return "params"
    case "glm-terms":
    case "monotone-loss": return "features"
    default: return "target"
  }
}
