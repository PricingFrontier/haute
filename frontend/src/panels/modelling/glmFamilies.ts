/**
 * GLM families, links, and solver settings RustyStats 0.9.0 supports.
 *
 * `GLM_FAMILY_LINKS` must equal the backend `GLM_FAMILY_LINKS` in
 * `src/haute/modelling/_train_config.py`; a contract test pins them. The first
 * link of each family is its canonical link.
 */
export const GLM_FAMILY_LINKS = {
  gaussian: ["identity", "log"],
  poisson: ["log", "identity"],
  quasipoisson: ["log", "identity"],
  binomial: ["logit", "log", "identity"],
  quasibinomial: ["logit", "log", "identity"],
  gamma: ["log", "identity"],
  tweedie: ["log", "identity"],
  negbinomial: ["log", "identity"],
} as const satisfies Record<string, readonly string[]>

export type GlmFamily = keyof typeof GLM_FAMILY_LINKS

export function isGlmFamily(value: unknown): value is GlmFamily {
  return typeof value === "string" && Object.prototype.hasOwnProperty.call(GLM_FAMILY_LINKS, value)
}

export const GLM_REGULARIZATIONS = ["ridge", "lasso", "elastic_net"] as const
export const GLM_CV_SELECTIONS = ["min", "1se"] as const
export const GLM_ROBUST_STANDARD_ERRORS = ["HC0", "HC1", "HC2", "HC3"] as const
export const GLM_CV_FOLDS_RANGE = [2, 20] as const
export const GLM_MAX_ITER_RANGE = [1, 10_000] as const
/** Written when a regularisation type is chosen and the setting is absent. */
export const GLM_CV_DEFAULTS = { cv_folds: 5, cv_selection: "min", cv_seed: 42 } as const

/** Whether a regularised GLM selects its penalty by cross-validation. */
export function glmCrossValidates(config: Record<string, unknown>): boolean {
  const regularization = config.regularization
  if (typeof regularization !== "string" || regularization === "") return false
  return config.alpha === undefined || config.alpha === null || config.alpha === 0
}
