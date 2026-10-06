/**
 * How the optimiser's Lagrange multipliers are named wherever they are shown.
 *
 * λ is the multiplier the solver put on a constraint's term when it chose each
 * quote's scenario. It is shown as its own quantity and never read as a claim
 * about how tight the constraint is: in this discrete solve a positive λ can sit
 * beside positive slack, so whether a constraint is met is judged only from its
 * bound and achieved total.
 */
export const LAMBDA_LABEL = "λ (multiplier)"

export const LAMBDA_HELP =
  "The solver's Lagrange multiplier on this constraint's term: the weight it gave the " +
  "constraint when choosing each quote's scenario. It is not a measure of whether the " +
  "constraint is met; read Status and Slack for that."
