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
  node_detail?: Record<string, unknown> | null
  row_lineage_type?: string | null
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
}
