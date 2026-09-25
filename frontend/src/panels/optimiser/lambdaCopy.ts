/**
 * How the optimiser's Lagrange multipliers are named wherever they are shown.
 *
 * The solver keeps each λ non-negative and prices a constraint by it, so λ is
 * the constraint's shadow price: the objective gained per unit the bound is
 * relaxed. 0 means the constraint is not binding at the solution.
 */
export const LAMBDA_LABEL = "λ (shadow price)"

export const LAMBDA_HELP =
  "The objective gained per unit the constraint's bound is relaxed, in objective units " +
  "per constraint unit. 0 means the constraint is not binding at this solution."
