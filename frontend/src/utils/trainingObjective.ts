/**
 * Frontend mirror of the backend's target/objective validation.
 *
 * The backend remains authoritative. This helper aggregates every currently
 * applicable issue so an invalid Train press can show one complete banner
 * without sending a request. Configuration panes and tabs never consume these
 * issues or reveal them proactively.
 */

export type TrainingConfigurationIssueCode =
  | "training-target"
  | "glm-family"
  | "glm-tweedie-variance-power"
  | "glm-negbin-theta"
  | "glm-factor-selection"
  | "glm-elastic-net-l1-ratio"
  | "catboost-params"
  | "catboost-loss-function"
  | "catboost-tweedie-variance-power"
  | "evaluation-config"
  | "tuning-config"

export type TrainingConfigurationIssue = {
  code: TrainingConfigurationIssueCode
  message: string
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

  const evaluation = (
    config.evaluation !== null
    && typeof config.evaluation === "object"
    && !Array.isArray(config.evaluation)
  )
    ? config.evaluation as Record<string, unknown>
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
  const validEvaluation = (
    evaluation?.schema_version === 1
    && ["random", "group", "temporal"].includes(String(strategy))
    && validTest
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
      message:
        "Complete the evaluation workflow: data structure, validation, and " +
        "any required group/date fields.",
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
    const metrics = Array.isArray(config.metrics) ? config.metrics : []
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
      String(config.algorithm ?? "").toLowerCase() === "catboost"
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
          "Gamma for severity) — an unset family would silently train a " +
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
            "Set the Tweedie variance power (1=Poisson, 2=Gamma) — an unset " +
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
            "the data — an unset value would silently fit at theta=1.0.",
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
    if (!hasTerms && !config.all_factors) {
      issues.push({
        code: "glm-factor-selection",
        message:
          "Add factors or tick 'All features' — an empty factor set would " +
          "silently auto-build a term for every column.",
      })
    }

    if (
      String(config.regularization ?? "").toLowerCase() === "elastic_net"
      && (config.l1_ratio === undefined || config.l1_ratio === null)
    ) {
      issues.push({
        code: "glm-elastic-net-l1-ratio",
        message:
          "Set the elastic-net L1 ratio (0 fits Ridge, 1 fits LASSO) — an " +
          "unset value would silently fit pure Ridge.",
      })
    }
    return issues
  }

  const lossFunction = config.loss_function
  if (!lossFunction) {
    issues.push({
      code: "catboost-loss-function",
      message:
        "Choose a training loss (e.g. Poisson for claim counts, RMSE for a " +
        "squared-error regression) — an unset loss would silently train " +
        "under the library default.",
    })
  } else if (
    String(lossFunction) === "Tweedie"
    && (config.variance_power === undefined || config.variance_power === null)
  ) {
    issues.push({
      code: "catboost-tweedie-variance-power",
      message:
        "Set the Tweedie variance power (1=Poisson, 2=Gamma) — an unset " +
        "value would silently train at power 1.5.",
    })
  }
  return issues
}
