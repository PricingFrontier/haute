/**
 * Frontend mirror of the backend `training_objective_issue`
 * (src/haute/modelling/_train_config.py). Returns the first incomplete part
 * of the training objective, or null when the objective is fully specified.
 *
 * An unset objective parameter must gate the Train button, never fall through
 * to a library/literal failover (CatBoost RMSE, GLM gaussian, Tweedie power
 * 1.5, elastic-net collapsing to Ridge at l1_ratio=0, auto-terms over every
 * column). `field` is a short noun phrase for the button hint; `message` is
 * the full explanation shown below the disabled button. This must stay in
 * step with the backend so a config that passes the UI also passes the route.
 */

export type ObjectiveIssue = { field: string; message: string }

function firstSet(...values: unknown[]): unknown {
  for (const value of values) {
    if (value !== null && value !== undefined) return value
  }
  return undefined
}

export function trainingObjectiveIssue(
  config: Record<string, unknown>,
): ObjectiveIssue | null {
  const params = (config.params as Record<string, unknown> | undefined) ?? {}
  const algorithm = String(config.algorithm ?? "catboost").toLowerCase()

  if (algorithm === "glm") {
    const family = params.family ?? config.family
    if (!family) {
      return {
        field: "distribution family",
        message:
          "Choose a GLM distribution family (e.g. Poisson for claim counts, " +
          "Gamma for severity) — an unset family would silently train a " +
          "gaussian model.",
      }
    }
    const varPower = firstSet(params.var_power, config.var_power, config.variance_power)
    if (String(family).toLowerCase() === "tweedie" && varPower === undefined) {
      return {
        field: "Tweedie variance power",
        message:
          "Set the Tweedie variance power (1=Poisson, 2=Gamma) — an unset " +
          "value would silently fit at power 1.5.",
      }
    }
    const terms = firstSet(params.terms, config.terms) as
      | Record<string, unknown>
      | undefined
    const allFactors = firstSet(params.all_factors, config.all_factors)
    const hasTerms = !!terms && Object.keys(terms).length > 0
    if (!hasTerms && !allFactors) {
      return {
        field: "factor selection",
        message:
          "Add factors or tick 'All features' — an empty factor set would " +
          "silently auto-build a term for every column.",
      }
    }
    const regularization = firstSet(params.regularization, config.regularization)
    const l1Ratio = firstSet(params.l1_ratio, config.l1_ratio)
    if (String(regularization ?? "").toLowerCase() === "elastic_net" && l1Ratio === undefined) {
      return {
        field: "elastic-net L1 ratio",
        message:
          "Set the elastic-net L1 ratio (0 fits Ridge, 1 fits LASSO) — an " +
          "unset value would silently fit pure Ridge.",
      }
    }
    return null
  }

  const lossFunction = config.loss_function
  if (!lossFunction) {
    return {
      field: "loss function",
      message:
        "Choose a training loss (e.g. Poisson for claim counts, RMSE for a " +
        "squared-error regression) — an unset loss would silently train " +
        "under the library default.",
    }
  }
  const variancePower = firstSet(config.variance_power, config.var_power)
  if (String(lossFunction) === "Tweedie" && variancePower === undefined) {
    return {
      field: "Tweedie variance power",
      message:
        "Set the Tweedie variance power (1=Poisson, 2=Gamma) — an unset " +
        "value would silently train at power 1.5.",
    }
  }
  return null
}
