/**
 * Trace types. The trace's structure is the generated `TraceResponse`; the step's
 * `expression`, `calculation` and `node_detail` are open objects on the server, which
 * the UI gives the shapes below (`traceFromContract` checks the first two).
 */

import type {
  TraceColumnDerivationResponse,
  TraceColumnReadResponse,
  TraceColumnSourceResponse,
  TraceCorrelationDiagnosticResponse,
  TraceOmissionResponse,
  TraceResultResponse,
  TraceStepResponse,
} from "../generated/api-contracts.generated"

/** A node that computed a value a step read. */
export type TraceColumnSource = TraceColumnSourceResponse
/** A column a computed column read, and the nodes that computed the value read. */
export type TraceColumnRead = TraceColumnReadResponse
/** How a step computed one column the traced value depends on. */
export type TraceColumnDerivation = TraceColumnDerivationResponse
export type TraceCorrelationDiagnostic = TraceCorrelationDiagnosticResponse
export type TraceOmission = TraceOmissionResponse

export interface TraceInputSource {
  node_name: string
  expression_text?: string
  substituted_text?: string
  result_value?: unknown
  not_computable_reason?: string | null
  result_source?: string | null
  input_sources?: Record<string, TraceInputSource> | null
}

export interface TraceExpression {
  expression_text: string
  expression_type: string
  referenced_columns: string[]
}

export interface TraceExpressionChainLink {
  expression_text: string
  target_column: string
  substituted_text?: string
  result_value?: unknown
  not_computable_reason?: string | null
  result_source?: string | null
}

export interface TraceCalculation {
  substituted_text: string
  result_value: unknown
  not_computable_reason?: string | null
  result_source?: string | null
  input_values: Record<string, unknown>
  taken_branch?: string | null
  taken_branch_index?: number | null
  expression_chain?: TraceExpressionChainLink[] | null
  input_sources?: Record<string, TraceInputSource> | null
}

export type TraceStep = Omit<TraceStepResponse, "expression" | "calculation" | "node_detail"> & {
  expression: TraceExpression | null
  calculation: TraceCalculation | null
  node_detail: TraceNodeDetail | null
}

export interface RatingStepFactorDetail {
  column: string
  value: unknown
}

export interface RatingStepTableDetail {
  name?: string
  output_column?: string
  factors?: RatingStepFactorDetail[]
  lookup_keys?: Record<string, unknown>
  selected_value?: unknown
  post_code_output_value?: unknown
  rate_value?: unknown
  status?: "matched" | "default" | "no_match" | "unmatched_value"
  matched?: boolean
  default_used?: boolean
  default_value?: unknown
  matched_entry?: Record<string, unknown> | null
}

export interface RatingStepCombinedOutputDetail {
  column: string
  operation: string
  base_value: unknown
  input_values: Record<string, unknown>
  value: unknown
}

export interface RatingStepNodeDetail {
  detail_type: "rating_step"
  tables?: RatingStepTableDetail[]
  combined_outputs?: RatingStepCombinedOutputDetail[]
}

export interface BandingFactorDetail {
  input_column?: string
  output_column?: string
  banding_type?: string
  input_value?: unknown
  matched_band?: unknown
  rule_index?: number
  is_default?: boolean
  status?: "matched" | "default" | "no_match"
  lower_bound?: unknown
  upper_bound?: unknown
  lower_inclusive?: boolean | null
  upper_inclusive?: boolean | null
  conditions?: Array<{ operator: string; value: unknown }>
}

export interface BandingNodeDetail {
  detail_type: "banding"
  factors?: BandingFactorDetail[]
  input_column?: string
  output_column?: string
  input_value?: unknown
  matched_band?: unknown
  rule_index?: number
  is_default?: boolean
  status?: "matched" | "default" | "no_match"
  lower_bound?: unknown
  upper_bound?: unknown
  lower_inclusive?: boolean | null
  upper_inclusive?: boolean | null
  conditions?: Array<{ operator: string; value: unknown }>
}

export interface ModelScoreIdentityDetail {
  source_type?: string
  model_path?: string
  run_id?: string
  registered_model?: string
  version?: string
  task?: string
}

export interface ModelScoreContributionDetail {
  feature: string
  term?: string
  term_type?: string
  feature_value?: unknown
  shap_value: number
  abs_shap_value?: number
  contribution?: number
  contribution_value?: number
  abs_contribution?: number
  abs_contribution_value?: number
  rank?: number
  is_categorical?: boolean
}

export interface ModelScoreExplanationDetail {
  /** What the price rests on that the ladder does not show (a row priced as a full policy term). */
  notes?: string[]
  type?: "catboost_shap" | "rustystats_glm_contributions" | "xgboost_contributions" | "lightgbm_contributions" | "ebm_terms" | "tboost_tables" | string
  method?: "catboost_shap" | "rustystats_glm_contributions" | "xgboost_contributions" | "lightgbm_contributions" | "ebm_terms" | "tboost_tables" | string
  status?: "ok" | "error" | string
  output_space?: "prediction" | "raw_formula_val" | "linear_predictor" | string
  prediction_space?: string
  base_value?: number
  sum_contributions?: number
  contribution_sum?: number
  prediction_from_shap?: number
  prediction_from_contributions?: number
  model_output_value?: number
  model_prediction_value?: number
  prediction_value?: number | null
  output_difference?: number | null
  family?: string
  link?: string
  link_function?: string
  feature_count?: number
  feature_values?: Record<string, unknown>
  contributions?: ModelScoreContributionDetail[]
  truncated?: boolean
  omitted_count?: number
  error?: string
  error_type?: string
}

export interface ModelScoreNodeDetail {
  detail_type: "model_score"
  prediction_value?: unknown
  prediction_column?: string
  feature_columns?: string[]
  feature_values?: Record<string, unknown>
  feature_metadata_unavailable?: string
  model_identity?: ModelScoreIdentityDetail
  explanation?: ModelScoreExplanationDetail
}

export interface OptimiserApplyOnlineCandidateDetail {
  scenario_index: number
  scenario_value: unknown
  objective: number
  decision_score: number
  selected: boolean
  is_baseline: boolean
  constraints?: Record<string, unknown>
  linearised_constraints?: Record<string, unknown>
  lambda_terms?: Record<string, unknown>
}

export interface OptimiserApplyRatebookFactorDetail {
  name: string
  input_value: unknown
  factor?: string
  factor_value: number
  running_total: number
  status: string
  default_used?: boolean
}

export interface OptimiserApplyOnlineNodeDetail {
  detail_type: "optimiser_apply"
  mode: "online"
  status?: "ok"
  output_column: string
  output_value: unknown
  quote_id_column?: string
  quote_id_value?: unknown
  scenario_index_column?: string
  scenario_value_column?: string
  objective_column?: string
  constraints?: Record<string, unknown>
  lambdas?: Record<string, unknown>
  candidates: OptimiserApplyOnlineCandidateDetail[]
  selected?: OptimiserApplyOnlineCandidateDetail | null
  baseline?: OptimiserApplyOnlineCandidateDetail | null
}

/** The combined-factor collar step: the product of factors clipped to the scored grid range. */
export interface OptimiserApplyRatebookCollarDetail {
  min: number
  max: number
  /** The product of the factors, before the collar. */
  before: number
  /** The deployed value, after the collar. */
  after: number
  /** True when the product lay outside the collar and was clipped. */
  applied: boolean
}

export interface OptimiserApplyRatebookNodeDetail {
  detail_type: "optimiser_apply"
  mode: "ratebook"
  status?: "ok"
  output_column: string
  output_value: unknown
  base_value: number
  factors: OptimiserApplyRatebookFactorDetail[]
  collar: OptimiserApplyRatebookCollarDetail
  final_value: unknown
  message?: string
}

export interface OptimiserApplyErrorNodeDetail {
  detail_type: "optimiser_apply"
  mode: string
  status: "error"
  error: string
  error_type?: string
}

export type OptimiserApplyNodeDetail =
  | OptimiserApplyOnlineNodeDetail
  | OptimiserApplyRatebookNodeDetail
  | OptimiserApplyErrorNodeDetail

export interface ScenarioExpanderNodeDetail {
  detail_type: "scenario_expander"
  scenario_value?: unknown
  scenario_column?: string
  scenario_index?: unknown
  parameters?: {
    min_value?: unknown
    max_value?: unknown
    steps?: unknown
  }
  error?: string
  error_type?: string
}

export interface LiveSwitchNodeDetail {
  detail_type: "live_switch"
  active_branch?: string
  active_scenario?: string
  pruned_branches?: string[]
  error?: string
  error_type?: string
}

export interface GenericTraceNodeDetail {
  detail_type?: string
  [key: string]: unknown
}

export type TraceNodeDetail = (
  | RatingStepNodeDetail
  | BandingNodeDetail
  | ModelScoreNodeDetail
  | OptimiserApplyNodeDetail
  | ScenarioExpanderNodeDetail
  | LiveSwitchNodeDetail
  | GenericTraceNodeDetail
) & Record<string, unknown>

export type TraceResult = Omit<TraceResultResponse, "steps"> & { steps: TraceStep[] }
