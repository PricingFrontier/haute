/** Trace types — mirrors backend TraceResult shape. */

export interface TraceSchemaDiff {
  columns_added: string[]
  columns_removed: string[]
  columns_modified: string[]
  columns_passed: string[]
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

export interface TraceNodeDetail {
  detail_type?: string
  [key: string]: unknown
}

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
