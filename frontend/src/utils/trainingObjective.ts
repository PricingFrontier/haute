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
  | "catboost-loss-function"
  | "catboost-tweedie-variance-power"

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
