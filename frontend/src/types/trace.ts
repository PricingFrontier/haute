/** Trace types — mirrors backend TraceResult shape. */

export interface TraceSchemaDiff {
  columns_added: string[]
  columns_removed: string[]
  columns_modified: string[]
  columns_passed: string[]
}

export interface TraceInputSource {
  node_name: string
  expression_text?: string
  substituted_text?: string
  result_value?: unknown
  input_sources?: Record<string, TraceInputSource> | null
}

export interface TraceStep {
  node_id: string
  node_name: string
  node_type: string
  schema_diff: TraceSchemaDiff
  input_values: Record<string, unknown>
  output_values: Record<string, unknown>
  column_relevant: boolean
  execution_ms: number
  expression?: {
    expression_text: string
    expression_type: string
    referenced_columns: string[]
  } | null
  calculation?: {
    substituted_text: string
    result_value: unknown
    input_values: Record<string, unknown>
    expression_chain?: Array<{
      expression_text: string
      target_column: string
      substituted_text?: string
      result_value?: unknown
    }> | null
    input_sources?: Record<string, TraceInputSource> | null
  } | null
  node_detail?: TraceNodeDetail | null
  row_lineage_type?: string | null
  taken_branch?: string | null
  taken_branch_index?: number | null
  null_explanation?: string | null
  expression_chain?: Array<{expression_text: string; target_column: string}> | null
  rename_info?: {original_name: string; chain: string[]} | null
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
  combined?: {
    column?: string
    operation?: string
    value?: unknown
    input_values?: unknown[]
  }
  matched_key?: Record<string, unknown>
  lookup_keys?: Record<string, unknown>
  matched_row?: unknown
  default_used?: boolean
  rate_value?: unknown
  matched?: boolean
}

export interface BandingFactorDetail {
  column?: string
  input_column?: string
  output_column?: string
  banding_type?: string
  input_value?: unknown
  selected_band?: unknown
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
  column?: string
  input_column?: string
  output_column?: string
  input_value?: unknown
  selected_band?: unknown
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
  run_id?: string
  registered_model?: string
  version?: string
  task?: string
}

export interface ModelScoreContributionDetail {
  feature: string
  feature_value?: unknown
  shap_value: number
  abs_shap_value?: number
  rank?: number
  is_categorical?: boolean
}

export interface ModelScoreExplanationDetail {
  type?: "catboost_shap" | string
  method?: "catboost_shap" | string
  status?: "ok" | "error" | string
  output_space?: "prediction" | "raw_formula_val" | string
  prediction_space?: string
  base_value?: number
  sum_contributions?: number
  contribution_sum?: number
  prediction_from_shap?: number
  model_output_value?: number
  prediction_value?: number | null
  output_difference?: number | null
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
  model_identity?: ModelScoreIdentityDetail
  explanation?: ModelScoreExplanationDetail
}

export interface GenericTraceNodeDetail {
  detail_type?: string
  [key: string]: unknown
}

export type TraceNodeDetail = (
  | RatingStepNodeDetail
  | BandingNodeDetail
  | ModelScoreNodeDetail
  | GenericTraceNodeDetail
) & Record<string, unknown>

export interface WaterfallEntry {
  label: string
  operation: string
  value: number
  delta: number
  cumulative: number
}

export interface TraceResult {
  target_node_id: string
  row_index: number
  column: string | null
  output_value: unknown
  steps: TraceStep[]
  row_id_column: string | null
  row_id_value: unknown
  total_nodes_in_pipeline: number
  nodes_in_trace: number
  execution_ms: number
  waterfall?: WaterfallEntry[] | WaterfallError | null
}

export interface WaterfallError {
  error: string
  error_type: string
}
