/**
 * Runtime parsers for values that cross the JSON / DOM boundary.
 *
 * These helpers fail loudly when backend payloads drift away from the
 * shapes the UI actually consumes, instead of letting the app explode
 * later with "cannot read property X of undefined".
 */

import type { Edge, Node } from "@xyflow/react"

import type {
  CacheStatusResponse,
  DatabricksCatalogsResponse,
  DatabricksSchemasResponse,
  DatabricksTablesResponse,
  DatabricksWarehousesResponse,
  DissolveSubmodelResponse,
  FetchProgressResponse,
  FetchTableResponse,
  FrontierAutoRangeResponse,
  FrontierPoint,
  FrontierResponse,
  FrontierSelectResponse,
  GitArchiveResponse,
  GitDeleteBranchResponse,
  GitCommitResponse,
  GitMilestoneEntry,
  GitMilestonesResponse,
  GitFileChange,
  GitLedgerSave,
  GitLedgerSavesResponse,
  GitManagedBranch,
  GitWorkingBranchesResponse,
  GitRestoreResponse,
  GitCreateWorkingBranchResponse,
  GitPrefs,
  GitSetIdentityResponse,
  GitSetWorkingBranchResponse,
  GitStatus,
  GitWorkingBranchResponse,
  JsonCacheBuildResponse,
  JsonCacheProgressResponse,
  JsonCacheStatusResponse,
  MlflowCheckResponse,
  MlflowLogResponse,
  OptimiserHistoryEntry,
  OptimiserEstimate,
  OptimiserSolveResponse,
  OptimiserSolveResult,
  OptimiserStatusResponse,
  PreviewNodeResponse,
  ApplyOptimiserResponse,
  SaveOptimiserResponse,
  SavePipelineResponse,
  SchemaResult,
  SubmodelCreateResponse,
  SubmodelGraphResponse,
  TraceResponse,
  TrainEstimate,
  TrainResponse,
  TrainStatusResponse,
  UtilityDeleteResponse,
  UtilityFile,
  UtilityListResponse,
  UtilityReadResponse,
  UtilityWriteResult,
} from "../api/types"
import type { ColumnInfo } from "./node"
import type {
  TraceInputSource,
  TraceResult,
  TraceSchemaDiff,
  TraceStep,
  WaterfallEntry,
  WaterfallError,
} from "./trace"

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

export interface PipelineResponse {
  nodes: Node[]
  edges: Edge[]
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  source_file?: string | null
  submodels?: Record<string, unknown> | null
  warning?: string | null
  sources?: string[]
  active_source?: string
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function typeName(value: unknown): string {
  if (value === null) return "null"
  if (Array.isArray(value)) return "array"
  return typeof value
}

function expectPlainObject(
  parser: string,
  value: unknown,
  field = "object",
): Record<string, unknown> {
  if (!isPlainObject(value)) {
    if (field === "object") {
      throw new Error(`${parser}: expected object, got ${value === null ? "null" : typeof value}`)
    }
    throw new Error(`${parser}: expected ${field} to be an object, got ${typeName(value)}`)
  }
  return value
}

function expectString(parser: string, value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`${parser}: expected ${field} to be a string, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

function expectNumber(parser: string, value: unknown, field: string): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new Error(`${parser}: expected ${field} to be a number, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

function expectBoolean(parser: string, value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${parser}: expected ${field} to be a boolean, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

function expectStringLiteral<T extends string>(
  parser: string,
  value: unknown,
  field: string,
  allowed: readonly T[],
): T {
  const parsed = expectString(parser, value, field)
  if (!allowed.includes(parsed as T)) {
    throw new Error(`${parser}: expected ${field} to be one of ${allowed.join(", ")}, got ${parsed}`)
  }
  return parsed as T
}

function optionalString(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = "",
): string {
  const value = obj[key]
  return value === undefined ? defaultValue : expectString(parser, value, `field \`${key}\``)
}

function optionalNumber(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = 0,
): number {
  const value = obj[key]
  return value === undefined ? defaultValue : expectNumber(parser, value, `field \`${key}\``)
}

function optionalBoolean(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = false,
): boolean {
  const value = obj[key]
  return value === undefined ? defaultValue : expectBoolean(parser, value, `field \`${key}\``)
}

function optionalNullableString(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: string | null = null,
): string | null {
  const value = obj[key]
  if (value === undefined) return defaultValue
  if (value === null || typeof value === "string") return value
  throw new Error(`${parser}: expected field \`${key}\` to be a string or null, got ${typeName(value)}`)
}

function optionalNullableNumber(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: number | null = null,
): number | null {
  const value = obj[key]
  if (value === undefined) return defaultValue
  if (value === null) return null
  return expectNumber(parser, value, `field \`${key}\``)
}

function expectArray(
  parser: string,
  value: unknown,
  field: string,
): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${parser}: expected ${field} to be an array, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

function parseArray<T>(
  parser: string,
  value: unknown,
  field: string,
  itemParser: (value: unknown, field: string) => T,
): T[] {
  return expectArray(parser, value, field).map((item, index) => itemParser(item, `${field}[${index}]`))
}

function optionalArray<T>(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  itemParser: (value: unknown, field: string) => T,
  defaultValue: T[] = [],
): T[] {
  const value = obj[key]
  return value === undefined ? defaultValue : parseArray(parser, value, `field \`${key}\``, itemParser)
}

function parseStringArray(parser: string, value: unknown, field: string): string[] {
  return parseArray(parser, value, field, (item, itemField) => expectString(parser, item, itemField))
}

function optionalStringArray(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: string[] = [],
): string[] {
  const value = obj[key]
  return value === undefined ? defaultValue : parseStringArray(parser, value, `field \`${key}\``)
}

function parsePlainObjectArray(
  parser: string,
  value: unknown,
  field: string,
): Record<string, unknown>[] {
  return parseArray(parser, value, field, (item, itemField) => expectPlainObject(parser, item, itemField))
}

function optionalPlainObjectArray(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: Record<string, unknown>[] = [],
): Record<string, unknown>[] {
  const value = obj[key]
  return value === undefined ? defaultValue : parsePlainObjectArray(parser, value, `field \`${key}\``)
}

function optionalFactorTables(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
): OptimiserSolveResult["factor_tables"] {
  const rawFactorTables = optionalNullableObject(parser, obj, key)
  if (rawFactorTables === null) return undefined

  const factorTables: NonNullable<OptimiserSolveResult["factor_tables"]> = {}
  for (const [factorName, rows] of Object.entries(rawFactorTables)) {
    factorTables[factorName] = parsePlainObjectArray(parser, rows, `field \`${key}.${factorName}\``)
  }
  return factorTables
}

function parseNumberRecord(
  parser: string,
  value: unknown,
  field: string,
): Record<string, number> {
  const obj = expectPlainObject(parser, value, field)
  const result: Record<string, number> = {}
  for (const [key, item] of Object.entries(obj)) {
    result[key] = expectNumber(parser, item, `${field}.${key}`)
  }
  return result
}

function optionalNumberRecord(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: Record<string, number> = {},
): Record<string, number> {
  const value = obj[key]
  return value === undefined ? defaultValue : parseNumberRecord(parser, value, `field \`${key}\``)
}

function parseStringRecord(
  parser: string,
  value: unknown,
  field: string,
): Record<string, string> {
  const obj = expectPlainObject(parser, value, field)
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(obj)) {
    result[key] = expectString(parser, item, `${field}.${key}`)
  }
  return result
}

function optionalStringRecord(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: Record<string, string> = {},
): Record<string, string> {
  const value = obj[key]
  return value === undefined ? defaultValue : parseStringRecord(parser, value, `field \`${key}\``)
}

function optionalNullableObject(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const value = obj[key]
  if (value === undefined || value === null) return null
  return expectPlainObject(parser, value, `field \`${key}\``)
}

function parseColumnInfo(value: unknown, field: string): ColumnInfo {
  const obj = expectPlainObject("parseColumnInfo", value, field)
  return {
    name: expectString("parseColumnInfo", obj.name, `${field}.name`),
    dtype: expectString("parseColumnInfo", obj.dtype, `${field}.dtype`),
  }
}

function parseNodeTiming(value: unknown, field: string): { node_id: string; label: string; timing_ms: number } {
  const obj = expectPlainObject("parsePreviewNodeResponse", value, field)
  return {
    node_id: expectString("parsePreviewNodeResponse", obj.node_id, `${field}.node_id`),
    label: expectString("parsePreviewNodeResponse", obj.label, `${field}.label`),
    timing_ms: expectNumber("parsePreviewNodeResponse", obj.timing_ms, `${field}.timing_ms`),
  }
}

function parseNodeMemory(value: unknown, field: string): { node_id: string; label: string; memory_bytes: number } {
  const obj = expectPlainObject("parsePreviewNodeResponse", value, field)
  return {
    node_id: expectString("parsePreviewNodeResponse", obj.node_id, `${field}.node_id`),
    label: expectString("parsePreviewNodeResponse", obj.label, `${field}.label`),
    memory_bytes: expectNumber("parsePreviewNodeResponse", obj.memory_bytes, `${field}.memory_bytes`),
  }
}

function parseSchemaWarning(value: unknown, field: string): { column: string; status: string } {
  const obj = expectPlainObject("parsePreviewNodeResponse", value, field)
  return {
    column: expectString("parsePreviewNodeResponse", obj.column, `${field}.column`),
    status: expectString("parsePreviewNodeResponse", obj.status, `${field}.status`),
  }
}

// ---------------------------------------------------------------------------
// Pipeline graph + preview contracts
// ---------------------------------------------------------------------------

export function isPipelineResponse(value: unknown): value is PipelineResponse {
  if (!isPlainObject(value)) return false
  if (!Array.isArray(value.nodes)) return false
  if (!Array.isArray(value.edges)) return false
  if (value.pipeline_name !== undefined && value.pipeline_name !== null && typeof value.pipeline_name !== "string") return false
  if (value.pipeline_description !== undefined && value.pipeline_description !== null && typeof value.pipeline_description !== "string") return false
  if (value.preamble !== undefined && value.preamble !== null && typeof value.preamble !== "string") return false
  if (value.source_file !== undefined && value.source_file !== null && typeof value.source_file !== "string") return false
  if (value.submodels !== undefined && value.submodels !== null && !isPlainObject(value.submodels)) return false
  if (value.warning !== undefined && value.warning !== null && typeof value.warning !== "string") return false
  if (value.active_source !== undefined && typeof value.active_source !== "string") return false
  if (value.sources !== undefined) {
    if (!Array.isArray(value.sources)) return false
    for (const item of value.sources) {
      if (typeof item !== "string") return false
    }
  }
  return true
}

export function parsePipelineResponse(value: unknown): PipelineResponse {
  const obj = expectPlainObject("parsePipelineResponse", value)
  const nodes = expectArray("parsePipelineResponse", obj.nodes, "field `nodes`")
  const edges = expectArray("parsePipelineResponse", obj.edges, "field `edges`")

  return {
    nodes: nodes as Node[],
    edges: edges as Edge[],
    pipeline_name: obj.pipeline_name === undefined ? undefined : optionalNullableString("parsePipelineResponse", obj, "pipeline_name"),
    pipeline_description: obj.pipeline_description === undefined ? undefined : optionalNullableString("parsePipelineResponse", obj, "pipeline_description"),
    preamble: obj.preamble === undefined ? undefined : optionalNullableString("parsePipelineResponse", obj, "preamble"),
    source_file: obj.source_file === undefined ? undefined : optionalNullableString("parsePipelineResponse", obj, "source_file"),
    submodels: obj.submodels === undefined ? undefined : obj.submodels === null ? null : expectPlainObject("parsePipelineResponse", obj.submodels, "field `submodels`"),
    warning: obj.warning === undefined ? undefined : optionalNullableString("parsePipelineResponse", obj, "warning"),
    sources: obj.sources === undefined ? undefined : parseStringArray("parsePipelineResponse", obj.sources, "field `sources`"),
    active_source: obj.active_source === undefined ? undefined : expectString("parsePipelineResponse", obj.active_source, "field `active_source`"),
  }
}

function parseNestedPipelineResponse(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
): PipelineResponse {
  try {
    return parsePipelineResponse(obj[key])
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`${parser}: invalid field \`${key}\`: ${message}`)
  }
}

export function parseSavePipelineResponse(value: unknown): SavePipelineResponse {
  const obj = expectPlainObject("parseSavePipelineResponse", value)
  return {
    status: optionalString("parseSavePipelineResponse", obj, "status", "saved"),
    file: expectString("parseSavePipelineResponse", obj.file, "field `file`"),
    pipeline_name: expectString("parseSavePipelineResponse", obj.pipeline_name, "field `pipeline_name`"),
    warnings: optionalStringArray("parseSavePipelineResponse", obj, "warnings"),
    git_sha: optionalNullableString("parseSavePipelineResponse", obj, "git_sha"),
  }
}

export function parseSubmodelCreateResponse(value: unknown): SubmodelCreateResponse {
  const obj = expectPlainObject("parseSubmodelCreateResponse", value)
  return {
    status: optionalString("parseSubmodelCreateResponse", obj, "status", "ok"),
    submodel_file: optionalString("parseSubmodelCreateResponse", obj, "submodel_file"),
    parent_file: optionalString("parseSubmodelCreateResponse", obj, "parent_file"),
    graph: parseNestedPipelineResponse("parseSubmodelCreateResponse", obj, "graph"),
  }
}

export function parseSubmodelGraphResponse(value: unknown): SubmodelGraphResponse {
  const obj = expectPlainObject("parseSubmodelGraphResponse", value)
  return {
    status: optionalString("parseSubmodelGraphResponse", obj, "status", "ok"),
    submodel_name: optionalString("parseSubmodelGraphResponse", obj, "submodel_name"),
    graph: parseNestedPipelineResponse("parseSubmodelGraphResponse", obj, "graph"),
  }
}

export function parseDissolveSubmodelResponse(value: unknown): DissolveSubmodelResponse {
  const obj = expectPlainObject("parseDissolveSubmodelResponse", value)
  return {
    status: optionalString("parseDissolveSubmodelResponse", obj, "status", "ok"),
    graph: parseNestedPipelineResponse("parseDissolveSubmodelResponse", obj, "graph"),
  }
}

export function parsePreviewNodeResponse(value: unknown): PreviewNodeResponse {
  const obj = expectPlainObject("parsePreviewNodeResponse", value)
  return {
    status: expectString("parsePreviewNodeResponse", obj.status, "field `status`"),
    node_id: expectString("parsePreviewNodeResponse", obj.node_id, "field `node_id`"),
    row_count: optionalNumber("parsePreviewNodeResponse", obj, "row_count"),
    column_count: optionalNumber("parsePreviewNodeResponse", obj, "column_count"),
    columns: optionalArray("parsePreviewNodeResponse", obj, "columns", parseColumnInfo),
    available_columns: optionalArray("parsePreviewNodeResponse", obj, "available_columns", parseColumnInfo),
    preview: optionalPlainObjectArray("parsePreviewNodeResponse", obj, "preview"),
    preview_columns: optionalStringArray("parsePreviewNodeResponse", obj, "preview_columns"),
    preview_row_count: optionalNumber("parsePreviewNodeResponse", obj, "preview_row_count"),
    preview_row_limit: optionalNullableNumber("parsePreviewNodeResponse", obj, "preview_row_limit"),
    preview_truncated: optionalBoolean("parsePreviewNodeResponse", obj, "preview_truncated"),
    error: optionalNullableString("parsePreviewNodeResponse", obj, "error"),
    error_line: optionalNullableNumber("parsePreviewNodeResponse", obj, "error_line"),
    timing_ms: optionalNumber("parsePreviewNodeResponse", obj, "timing_ms"),
    memory_bytes: optionalNumber("parsePreviewNodeResponse", obj, "memory_bytes"),
    timings: optionalArray("parsePreviewNodeResponse", obj, "timings", parseNodeTiming),
    memory: optionalArray("parsePreviewNodeResponse", obj, "memory", parseNodeMemory),
    schema_warnings: optionalArray("parsePreviewNodeResponse", obj, "schema_warnings", parseSchemaWarning),
    node_statuses: optionalStringRecord("parsePreviewNodeResponse", obj, "node_statuses"),
  }
}

// ---------------------------------------------------------------------------
// Trace contracts
// ---------------------------------------------------------------------------

function parseTraceSchemaDiff(value: unknown, field: string): TraceSchemaDiff {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    columns_added: obj.columns_added === undefined ? [] : parseStringArray("parseTraceResponse", obj.columns_added, `${field}.columns_added`),
    columns_removed: obj.columns_removed === undefined ? [] : parseStringArray("parseTraceResponse", obj.columns_removed, `${field}.columns_removed`),
    columns_modified: obj.columns_modified === undefined ? [] : parseStringArray("parseTraceResponse", obj.columns_modified, `${field}.columns_modified`),
    columns_passed: obj.columns_passed === undefined ? [] : parseStringArray("parseTraceResponse", obj.columns_passed, `${field}.columns_passed`),
  }
}

function parseTraceExpression(value: unknown, field: string): NonNullable<TraceStep["expression"]> {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    expression_text: expectString("parseTraceResponse", obj.expression_text, `${field}.expression_text`),
    expression_type: expectString("parseTraceResponse", obj.expression_type, `${field}.expression_type`),
    referenced_columns: obj.referenced_columns === undefined ? [] : parseStringArray("parseTraceResponse", obj.referenced_columns, `${field}.referenced_columns`),
  }
}

function parseTraceCalculation(value: unknown, field: string): NonNullable<TraceStep["calculation"]> {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    substituted_text: expectString("parseTraceResponse", obj.substituted_text, `${field}.substituted_text`),
    result_value: obj.result_value,
    input_values: optionalNullableObject("parseTraceResponse", { input_values: obj.input_values }, "input_values") ?? {},
    expression_chain: obj.expression_chain === undefined || obj.expression_chain === null
      ? null
      : parseExpressionChain(obj.expression_chain, `${field}.expression_chain`),
    input_sources: obj.input_sources === undefined || obj.input_sources === null
      ? null
      : parseTraceInputSources(obj.input_sources, `${field}.input_sources`),
  }
}

function parseExpressionChain(
  value: unknown,
  field: string,
): Array<{ expression_text: string; target_column: string; substituted_text?: string; result_value?: unknown }> {
  return parseArray("parseTraceResponse", value, field, (item, itemField) => {
    const obj = expectPlainObject("parseTraceResponse", item, itemField)
    return {
      expression_text: expectString("parseTraceResponse", obj.expression_text, `${itemField}.expression_text`),
      target_column: expectString("parseTraceResponse", obj.target_column, `${itemField}.target_column`),
      ...(obj.substituted_text === undefined ? {} : {
        substituted_text: expectString("parseTraceResponse", obj.substituted_text, `${itemField}.substituted_text`),
      }),
      ...(obj.result_value === undefined ? {} : { result_value: obj.result_value }),
    }
  })
}

function parseTraceInputSources(value: unknown, field: string): Record<string, TraceInputSource> {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  const result: Record<string, TraceInputSource> = {}
  for (const [column, source] of Object.entries(obj)) {
    result[column] = parseTraceInputSource(source, `${field}.${column}`)
  }
  return result
}

function parseTraceInputSource(value: unknown, field: string): TraceInputSource {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    node_name: expectString("parseTraceResponse", obj.node_name, `${field}.node_name`),
    ...(obj.expression_text === undefined ? {} : {
      expression_text: expectString("parseTraceResponse", obj.expression_text, `${field}.expression_text`),
    }),
    ...(obj.substituted_text === undefined ? {} : {
      substituted_text: expectString("parseTraceResponse", obj.substituted_text, `${field}.substituted_text`),
    }),
    ...(obj.result_value === undefined ? {} : { result_value: obj.result_value }),
    input_sources: obj.input_sources === undefined || obj.input_sources === null
      ? null
      : parseTraceInputSources(obj.input_sources, `${field}.input_sources`),
  }
}

function parseTraceRenameInfo(value: unknown, field: string): NonNullable<TraceStep["rename_info"]> {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    original_name: expectString("parseTraceResponse", obj.original_name, `${field}.original_name`),
    chain: obj.chain === undefined ? [] : parseStringArray("parseTraceResponse", obj.chain, `${field}.chain`),
  }
}

function parseTraceStep(value: unknown, field: string): TraceStep {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  const expression = obj.expression === undefined || obj.expression === null ? null : parseTraceExpression(obj.expression, `${field}.expression`)
  const calculation = obj.calculation === undefined || obj.calculation === null ? null : parseTraceCalculation(obj.calculation, `${field}.calculation`)
  const node_detail = obj.node_detail === undefined || obj.node_detail === null ? null : expectPlainObject("parseTraceResponse", obj.node_detail, `${field}.node_detail`)
  const expression_chain = obj.expression_chain === undefined || obj.expression_chain === null ? null : parseExpressionChain(obj.expression_chain, `${field}.expression_chain`)
  const rename_info = obj.rename_info === undefined || obj.rename_info === null ? null : parseTraceRenameInfo(obj.rename_info, `${field}.rename_info`)

  return {
    node_id: expectString("parseTraceResponse", obj.node_id, `${field}.node_id`),
    node_name: expectString("parseTraceResponse", obj.node_name, `${field}.node_name`),
    node_type: expectString("parseTraceResponse", obj.node_type, `${field}.node_type`),
    schema_diff: parseTraceSchemaDiff(obj.schema_diff, `${field}.schema_diff`),
    input_values: obj.input_values === undefined ? {} : expectPlainObject("parseTraceResponse", obj.input_values, `${field}.input_values`),
    output_values: obj.output_values === undefined ? {} : expectPlainObject("parseTraceResponse", obj.output_values, `${field}.output_values`),
    column_relevant: obj.column_relevant === undefined ? true : expectBoolean("parseTraceResponse", obj.column_relevant, `${field}.column_relevant`),
    execution_ms: optionalNumber("parseTraceResponse", obj, "execution_ms"),
    expression,
    calculation,
    node_detail,
    row_lineage_type: optionalNullableString("parseTraceResponse", obj, "row_lineage_type"),
    taken_branch: optionalNullableString("parseTraceResponse", obj, "taken_branch"),
    taken_branch_index: optionalNullableNumber("parseTraceResponse", obj, "taken_branch_index"),
    null_explanation: optionalNullableString("parseTraceResponse", obj, "null_explanation"),
    expression_chain,
    rename_info,
  }
}

function parseWaterfallEntry(value: unknown, field: string): WaterfallEntry {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    label: expectString("parseTraceResponse", obj.label, `${field}.label`),
    operation: expectString("parseTraceResponse", obj.operation, `${field}.operation`),
    value: expectNumber("parseTraceResponse", obj.value, `${field}.value`),
    delta: expectNumber("parseTraceResponse", obj.delta, `${field}.delta`),
    cumulative: expectNumber("parseTraceResponse", obj.cumulative, `${field}.cumulative`),
  }
}

function parseWaterfallError(value: unknown, field: string): WaterfallError {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    error: expectString("parseTraceResponse", obj.error, `${field}.error`),
    error_type: expectString("parseTraceResponse", obj.error_type, `${field}.error_type`),
  }
}

function parseTraceResult(value: unknown, field: string): TraceResult {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  let waterfall: TraceResult["waterfall"] = null
  if (obj.waterfall !== undefined && obj.waterfall !== null) {
    waterfall = Array.isArray(obj.waterfall)
      ? parseArray("parseTraceResponse", obj.waterfall, `${field}.waterfall`, parseWaterfallEntry)
      : parseWaterfallError(obj.waterfall, `${field}.waterfall`)
  }

  return {
    target_node_id: expectString("parseTraceResponse", obj.target_node_id, `${field}.target_node_id`),
    row_index: expectNumber("parseTraceResponse", obj.row_index, `${field}.row_index`),
    column: optionalNullableString("parseTraceResponse", obj, "column"),
    output_value: obj.output_value,
    steps: obj.steps === undefined ? [] : parseArray("parseTraceResponse", obj.steps, `${field}.steps`, parseTraceStep),
    row_id_column: optionalNullableString("parseTraceResponse", obj, "row_id_column"),
    row_id_value: obj.row_id_value,
    total_nodes_in_pipeline: optionalNumber("parseTraceResponse", obj, "total_nodes_in_pipeline"),
    nodes_in_trace: optionalNumber("parseTraceResponse", obj, "nodes_in_trace"),
    execution_ms: optionalNumber("parseTraceResponse", obj, "execution_ms"),
    waterfall,
  }
}

export function parseTraceResponse(value: unknown): TraceResponse {
  const obj = expectPlainObject("parseTraceResponse", value)
  return {
    status: expectString("parseTraceResponse", obj.status, "field `status`"),
    trace: parseTraceResult(obj.trace, "field `trace`"),
    error: obj.error === undefined ? undefined : optionalString("parseTraceResponse", obj, "error"),
  }
}

// ---------------------------------------------------------------------------
// Schema + modelling contracts
// ---------------------------------------------------------------------------

export function parseSchemaResponse(value: unknown): SchemaResult {
  const obj = expectPlainObject("parseSchemaResponse", value)
  return {
    path: expectString("parseSchemaResponse", obj.path, "field `path`"),
    columns: obj.columns === undefined ? [] : parseArray("parseSchemaResponse", obj.columns, "field `columns`", parseColumnInfo),
    row_count: optionalNullableNumber("parseSchemaResponse", obj, "row_count"),
    row_count_estimated: optionalBoolean("parseSchemaResponse", obj, "row_count_estimated"),
    column_count: expectNumber("parseSchemaResponse", obj.column_count, "field `column_count`"),
    preview: optionalPlainObjectArray("parseSchemaResponse", obj, "preview"),
  }
}

function parseFeatureImportanceRow(value: unknown, field: string): NonNullable<TrainResponse["feature_importance"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    importance: expectNumber("parseTrainResponse", obj.importance, `${field}.importance`),
  }
}

function parseDoubleLiftRow(value: unknown, field: string): NonNullable<TrainResponse["double_lift"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    decile: expectNumber("parseTrainResponse", obj.decile, `${field}.decile`),
    actual: expectNumber("parseTrainResponse", obj.actual, `${field}.actual`),
    predicted: expectNumber("parseTrainResponse", obj.predicted, `${field}.predicted`),
    count: expectNumber("parseTrainResponse", obj.count, `${field}.count`),
  }
}

function parseShapSummaryRow(value: unknown, field: string): NonNullable<TrainResponse["shap_summary"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    mean_abs_shap: expectNumber("parseTrainResponse", obj.mean_abs_shap, `${field}.mean_abs_shap`),
  }
}

function parseAveBin(value: unknown, field: string): NonNullable<NonNullable<TrainResponse["ave_per_feature"]>[number]["bins"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    label: expectString("parseTrainResponse", obj.label, `${field}.label`),
    exposure: expectNumber("parseTrainResponse", obj.exposure, `${field}.exposure`),
    avg_actual: expectNumber("parseTrainResponse", obj.avg_actual, `${field}.avg_actual`),
    avg_predicted: expectNumber("parseTrainResponse", obj.avg_predicted, `${field}.avg_predicted`),
  }
}

function parseAvePerFeatureRow(value: unknown, field: string): NonNullable<TrainResponse["ave_per_feature"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    type: expectString("parseTrainResponse", obj.type, `${field}.type`),
    bins: obj.bins === undefined ? [] : parseArray("parseTrainResponse", obj.bins, `${field}.bins`, parseAveBin),
  }
}

function parseResidualHistogramRow(value: unknown, field: string): NonNullable<TrainResponse["residuals_histogram"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    bin_center: expectNumber("parseTrainResponse", obj.bin_center, `${field}.bin_center`),
    count: expectNumber("parseTrainResponse", obj.count, `${field}.count`),
    weighted_count: expectNumber("parseTrainResponse", obj.weighted_count, `${field}.weighted_count`),
  }
}

function parseActualVsPredictedRow(value: unknown, field: string): NonNullable<TrainResponse["actual_vs_predicted"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    actual: expectNumber("parseTrainResponse", obj.actual, `${field}.actual`),
    predicted: expectNumber("parseTrainResponse", obj.predicted, `${field}.predicted`),
    weight: expectNumber("parseTrainResponse", obj.weight, `${field}.weight`),
  }
}

function parseLorenzCurvePoint(value: unknown, field: string): NonNullable<TrainResponse["lorenz_curve"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    cum_weight_frac: expectNumber("parseTrainResponse", obj.cum_weight_frac, `${field}.cum_weight_frac`),
    cum_actual_frac: expectNumber("parseTrainResponse", obj.cum_actual_frac, `${field}.cum_actual_frac`),
  }
}

function parsePdpGridPoint(value: unknown, field: string): NonNullable<NonNullable<TrainResponse["pdp_data"]>[number]["grid"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const rawValue = obj.value
  if (typeof rawValue !== "string" && typeof rawValue !== "number") {
    throw new Error(`parseTrainResponse: expected ${field}.value to be a string or number, got ${rawValue === undefined ? "missing" : typeName(rawValue)}`)
  }
  return {
    value: rawValue,
    avg_prediction: expectNumber("parseTrainResponse", obj.avg_prediction, `${field}.avg_prediction`),
  }
}

function parsePdpFeatureRow(value: unknown, field: string): NonNullable<TrainResponse["pdp_data"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    type: expectString("parseTrainResponse", obj.type, `${field}.type`),
    grid: obj.grid === undefined ? [] : parseArray("parseTrainResponse", obj.grid, `${field}.grid`, parsePdpGridPoint),
  }
}

function parseGlmCoefficientRow(value: unknown, field: string): NonNullable<TrainResponse["glm_coefficients"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    coefficient: expectNumber("parseTrainResponse", obj.coefficient, `${field}.coefficient`),
    std_error: expectNumber("parseTrainResponse", obj.std_error, `${field}.std_error`),
    z_value: expectNumber("parseTrainResponse", obj.z_value, `${field}.z_value`),
    p_value: expectNumber("parseTrainResponse", obj.p_value, `${field}.p_value`),
    significance: expectString("parseTrainResponse", obj.significance, `${field}.significance`),
  }
}

function parseGlmRelativityRow(value: unknown, field: string): NonNullable<TrainResponse["glm_relativities"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    relativity: expectNumber("parseTrainResponse", obj.relativity, `${field}.relativity`),
    ci_lower: obj.ci_lower === undefined ? undefined : expectNumber("parseTrainResponse", obj.ci_lower, `${field}.ci_lower`),
    ci_upper: obj.ci_upper === undefined ? undefined : expectNumber("parseTrainResponse", obj.ci_upper, `${field}.ci_upper`),
  }
}

function parseTrainDiagnosticsError(value: unknown, field: string): NonNullable<TrainResponse["diagnostics_errors"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    diagnostic: expectString("parseTrainResponse", obj.diagnostic, `${field}.diagnostic`),
    error: expectString("parseTrainResponse", obj.error, `${field}.error`),
    error_type: expectString("parseTrainResponse", obj.error_type, `${field}.error_type`),
  }
}

function parseLossHistoryEntry(value: unknown, field: string): NonNullable<TrainResponse["loss_history"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const iteration = expectNumber("parseTrainResponse", obj.iteration, `${field}.iteration`)
  const result: NonNullable<TrainResponse["loss_history"]>[number] = { iteration }
  for (const [key, item] of Object.entries(obj)) {
    if (key === "iteration") continue
    result[key] = expectNumber("parseTrainResponse", item, `${field}.${key}`)
  }
  return result
}

export function parseTrainResponse(value: unknown): TrainResponse {
  const obj = expectPlainObject("parseTrainResponse", value)
  const rawRegularization = optionalNullableObject("parseTrainResponse", obj, "glm_regularization_path")

  return {
    status: expectStringLiteral("parseTrainResponse", obj.status, "field `status`", ["started", "completed", "error"]),
    job_id: optionalNullableString("parseTrainResponse", obj, "job_id"),
    metrics: optionalNumberRecord("parseTrainResponse", obj, "metrics"),
    feature_importance: optionalArray("parseTrainResponse", obj, "feature_importance", parseFeatureImportanceRow),
    model_path: optionalString("parseTrainResponse", obj, "model_path"),
    train_rows: optionalNumber("parseTrainResponse", obj, "train_rows"),
    test_rows: optionalNumber("parseTrainResponse", obj, "test_rows"),
    holdout_rows: optionalNumber("parseTrainResponse", obj, "holdout_rows"),
    holdout_metrics: optionalNumberRecord("parseTrainResponse", obj, "holdout_metrics"),
    diagnostics_set: optionalString("parseTrainResponse", obj, "diagnostics_set", "validation"),
    features: optionalStringArray("parseTrainResponse", obj, "features"),
    cat_features: optionalStringArray("parseTrainResponse", obj, "cat_features"),
    error: optionalNullableString("parseTrainResponse", obj, "error"),
    best_iteration: optionalNullableNumber("parseTrainResponse", obj, "best_iteration"),
    loss_history: optionalArray("parseTrainResponse", obj, "loss_history", parseLossHistoryEntry),
    double_lift: optionalArray("parseTrainResponse", obj, "double_lift", parseDoubleLiftRow),
    shap_summary: optionalArray("parseTrainResponse", obj, "shap_summary", parseShapSummaryRow),
    feature_importance_loss: optionalArray("parseTrainResponse", obj, "feature_importance_loss", parseFeatureImportanceRow),
    ave_per_feature: optionalArray("parseTrainResponse", obj, "ave_per_feature", parseAvePerFeatureRow),
    residuals_histogram: optionalArray("parseTrainResponse", obj, "residuals_histogram", parseResidualHistogramRow),
    residuals_stats: optionalNumberRecord("parseTrainResponse", obj, "residuals_stats"),
    actual_vs_predicted: optionalArray("parseTrainResponse", obj, "actual_vs_predicted", parseActualVsPredictedRow),
    lorenz_curve: optionalArray("parseTrainResponse", obj, "lorenz_curve", parseLorenzCurvePoint),
    lorenz_curve_perfect: optionalArray("parseTrainResponse", obj, "lorenz_curve_perfect", parseLorenzCurvePoint),
    pdp_data: optionalArray("parseTrainResponse", obj, "pdp_data", parsePdpFeatureRow),
    warning: optionalNullableString("parseTrainResponse", obj, "warning"),
    total_source_rows: optionalNullableNumber("parseTrainResponse", obj, "total_source_rows"),
    glm_coefficients: optionalArray("parseTrainResponse", obj, "glm_coefficients", parseGlmCoefficientRow),
    glm_relativities: optionalArray("parseTrainResponse", obj, "glm_relativities", parseGlmRelativityRow),
    glm_fit_statistics: optionalNumberRecord("parseTrainResponse", obj, "glm_fit_statistics"),
    glm_regularization_path: rawRegularization === null
      ? null
      : {
          selected_alpha: rawRegularization.selected_alpha === undefined ? undefined : expectNumber("parseTrainResponse", rawRegularization.selected_alpha, "field `glm_regularization_path.selected_alpha`"),
          n_nonzero: rawRegularization.n_nonzero === undefined ? undefined : expectNumber("parseTrainResponse", rawRegularization.n_nonzero, "field `glm_regularization_path.n_nonzero`"),
        },
    diagnostics_errors: optionalArray("parseTrainResponse", obj, "diagnostics_errors", parseTrainDiagnosticsError),
  }
}

export function parseTrainStatusResponse(value: unknown): TrainStatusResponse {
  const obj = expectPlainObject("parseTrainStatusResponse", value)
  return {
    status: expectStringLiteral("parseTrainStatusResponse", obj.status, "field `status`", ["running", "completed", "error"]),
    progress: optionalNumber("parseTrainStatusResponse", obj, "progress"),
    message: optionalString("parseTrainStatusResponse", obj, "message"),
    iteration: optionalNumber("parseTrainStatusResponse", obj, "iteration"),
    total_iterations: optionalNumber("parseTrainStatusResponse", obj, "total_iterations"),
    train_loss: optionalNumberRecord("parseTrainStatusResponse", obj, "train_loss"),
    elapsed_seconds: optionalNumber("parseTrainStatusResponse", obj, "elapsed_seconds"),
    result: obj.result === undefined || obj.result === null ? null : parseTrainResponse(obj.result),
    warning: optionalNullableString("parseTrainStatusResponse", obj, "warning"),
  }
}

export function parseMlflowCheckResponse(value: unknown): MlflowCheckResponse {
  const obj = expectPlainObject("parseMlflowCheckResponse", value)
  const mlflowInstalled = expectBoolean("parseMlflowCheckResponse", obj.mlflow_installed, "field `mlflow_installed`")
  const mlflowImportable = optionalBoolean("parseMlflowCheckResponse", obj, "mlflow_importable", mlflowInstalled)
  const trackingConfiguredValue = obj.tracking_configured ?? obj.tracking_available
  return {
    mlflow_installed: mlflowInstalled,
    mlflow_importable: mlflowImportable,
    tracking_configured: trackingConfiguredValue === undefined
      ? mlflowInstalled && mlflowImportable
      : expectBoolean("parseMlflowCheckResponse", trackingConfiguredValue, "field `tracking_configured`"),
    backend: optionalString("parseMlflowCheckResponse", obj, "backend"),
    databricks_host: optionalString("parseMlflowCheckResponse", obj, "databricks_host"),
    detail: optionalString("parseMlflowCheckResponse", obj, "detail"),
  }
}

export function parseTrainEstimateResponse(value: unknown): TrainEstimate {
  const obj = expectPlainObject("parseTrainEstimateResponse", value)
  return {
    total_rows: optionalNullableNumber("parseTrainEstimateResponse", obj, "total_rows"),
    safe_row_limit: optionalNullableNumber("parseTrainEstimateResponse", obj, "safe_row_limit"),
    estimated_mb: optionalNumber("parseTrainEstimateResponse", obj, "estimated_mb"),
    training_mb: optionalNumber("parseTrainEstimateResponse", obj, "training_mb"),
    available_mb: optionalNumber("parseTrainEstimateResponse", obj, "available_mb"),
    bytes_per_row: optionalNumber("parseTrainEstimateResponse", obj, "bytes_per_row"),
    was_downsampled: optionalBoolean("parseTrainEstimateResponse", obj, "was_downsampled"),
    warning: optionalNullableString("parseTrainEstimateResponse", obj, "warning"),
    gpu_vram_estimated_mb: optionalNullableNumber("parseTrainEstimateResponse", obj, "gpu_vram_estimated_mb"),
    gpu_vram_available_mb: optionalNullableNumber("parseTrainEstimateResponse", obj, "gpu_vram_available_mb"),
    gpu_warning: optionalNullableString("parseTrainEstimateResponse", obj, "gpu_warning"),
  }
}

export function parseMlflowLogResponse(value: unknown): MlflowLogResponse {
  const obj = expectPlainObject("parseMlflowLogResponse", value)
  return {
    status: expectStringLiteral("parseMlflowLogResponse", obj.status, "field `status`", ["ok", "error"]),
    backend: optionalString("parseMlflowLogResponse", obj, "backend"),
    experiment_name: optionalString("parseMlflowLogResponse", obj, "experiment_name"),
    run_id: optionalNullableString("parseMlflowLogResponse", obj, "run_id"),
    run_url: optionalNullableString("parseMlflowLogResponse", obj, "run_url"),
    tracking_uri: optionalString("parseMlflowLogResponse", obj, "tracking_uri"),
    error: optionalNullableString("parseMlflowLogResponse", obj, "error"),
  }
}

// ---------------------------------------------------------------------------
// Optimiser contracts
// ---------------------------------------------------------------------------

function parseOptimiserHistoryEntry(value: unknown, field: string): OptimiserHistoryEntry {
  const obj = expectPlainObject("parseOptimiserStatusResponse", value, field)
  return {
    iteration: expectNumber("parseOptimiserStatusResponse", obj.iteration, `${field}.iteration`),
    total_objective: expectNumber("parseOptimiserStatusResponse", obj.total_objective, `${field}.total_objective`),
    max_lambda_change: expectNumber("parseOptimiserStatusResponse", obj.max_lambda_change, `${field}.max_lambda_change`),
    all_constraints_satisfied: obj.all_constraints_satisfied === undefined ? undefined : expectBoolean("parseOptimiserStatusResponse", obj.all_constraints_satisfied, `${field}.all_constraints_satisfied`),
    lambdas: obj.lambdas === undefined ? undefined : parseNumberRecord("parseOptimiserStatusResponse", obj.lambdas, `${field}.lambdas`),
    total_constraints: obj.total_constraints === undefined ? undefined : parseNumberRecord("parseOptimiserStatusResponse", obj.total_constraints, `${field}.total_constraints`),
  }
}

export function parseSolveOptimiserResponse(value: unknown): OptimiserSolveResponse {
  const obj = expectPlainObject("parseSolveOptimiserResponse", value)
  return {
    status: expectStringLiteral("parseSolveOptimiserResponse", obj.status, "field `status`", ["started", "error"]),
    job_id: optionalNullableString("parseSolveOptimiserResponse", obj, "job_id"),
    error: optionalNullableString("parseSolveOptimiserResponse", obj, "error"),
  }
}

export function parseOptimiserEstimateResponse(value: unknown): OptimiserEstimate {
  const obj = expectPlainObject("parseOptimiserEstimateResponse", value)
  return {
    total_rows: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "total_rows"),
    quote_count: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "quote_count"),
    scenarios_per_quote_min: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "scenarios_per_quote_min"),
    scenarios_per_quote_max: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "scenarios_per_quote_max"),
    scenarios_per_quote_mean: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "scenarios_per_quote_mean"),
    expanded_row_count: optionalNullableNumber("parseOptimiserEstimateResponse", obj, "expanded_row_count"),
  }
}

export function parseFrontierResponse(value: unknown, field = "object"): FrontierResponse {
  const obj = expectPlainObject("parseOptimiserStatusResponse", value, field)
  return {
    status: expectString("parseOptimiserStatusResponse", obj.status, `${field}.status`),
    points: optionalArray("parseOptimiserStatusResponse", obj, "points", parseFrontierPoint),
    n_points: optionalNumber("parseOptimiserStatusResponse", obj, "n_points"),
    points_returned: optionalNumber("parseOptimiserStatusResponse", obj, "points_returned"),
    constraint_names: optionalStringArray("parseOptimiserStatusResponse", obj, "constraint_names"),
    points_limit: optionalNullableNumber("parseOptimiserStatusResponse", obj, "points_limit"),
    points_truncated: optionalBoolean("parseOptimiserStatusResponse", obj, "points_truncated"),
  }
}

function parseFrontierPoint(value: unknown, field: string): FrontierPoint {
  const obj = expectPlainObject("parseOptimiserStatusResponse", value, field)
  return {
    ...obj,
    index: obj.index === undefined ? undefined : expectNumber("parseOptimiserStatusResponse", obj.index, `${field}.index`),
    total_objective: obj.total_objective === undefined
      ? undefined
      : expectNumber("parseOptimiserStatusResponse", obj.total_objective, `${field}.total_objective`),
    constraints: obj.constraints === undefined
      ? undefined
      : parseNumberRecord("parseOptimiserStatusResponse", obj.constraints, `${field}.constraints`),
    lambdas: obj.lambdas === undefined
      ? undefined
      : parseNumberRecord("parseOptimiserStatusResponse", obj.lambdas, `${field}.lambdas`),
  }
}

export function parseFrontierAutoRangeResponse(value: unknown): FrontierAutoRangeResponse {
  const obj = expectPlainObject("parseFrontierAutoRangeResponse", value)
  const rawRanges = expectPlainObject("parseFrontierAutoRangeResponse", obj.ranges, "field `ranges`")
  const ranges: FrontierAutoRangeResponse["ranges"] = {}
  for (const [key, item] of Object.entries(rawRanges)) {
    const range = expectPlainObject(
      "parseFrontierAutoRangeResponse",
      item,
      `field \`ranges.${key}\``,
    )
    ranges[key] = {
      min: expectNumber("parseFrontierAutoRangeResponse", range.min, `field \`ranges.${key}.min\``),
      max: expectNumber("parseFrontierAutoRangeResponse", range.max, `field \`ranges.${key}.max\``),
    }
  }
  return {
    status: expectString("parseFrontierAutoRangeResponse", obj.status, "field `status`"),
    ranges,
    method: expectString("parseFrontierAutoRangeResponse", obj.method, "field `method`"),
    warning: optionalNullableString("parseFrontierAutoRangeResponse", obj, "warning"),
  }
}

function parseOptimiserSolveResult(value: unknown, field: string): OptimiserSolveResult {
  const obj = expectPlainObject("parseOptimiserStatusResponse", value, field)
  const stats = optionalNullableObject("parseOptimiserStatusResponse", obj, "scenario_value_stats")
  const histogram = optionalNullableObject("parseOptimiserStatusResponse", obj, "scenario_value_histogram")
  const factorTables = optionalFactorTables("parseOptimiserStatusResponse", obj, "factor_tables")

  return {
    mode: obj.mode === undefined ? undefined : optionalNullableString("parseOptimiserStatusResponse", obj, "mode"),
    total_objective: expectNumber("parseOptimiserStatusResponse", obj.total_objective, `${field}.total_objective`),
    baseline_objective: expectNumber("parseOptimiserStatusResponse", obj.baseline_objective, `${field}.baseline_objective`),
    constraints: obj.constraints === undefined ? {} : parseNumberRecord("parseOptimiserStatusResponse", obj.constraints, `${field}.constraints`),
    baseline_constraints: obj.baseline_constraints === undefined ? {} : parseNumberRecord("parseOptimiserStatusResponse", obj.baseline_constraints, `${field}.baseline_constraints`),
    lambdas: obj.lambdas === undefined ? {} : parseNumberRecord("parseOptimiserStatusResponse", obj.lambdas, `${field}.lambdas`),
    converged: expectBoolean("parseOptimiserStatusResponse", obj.converged, `${field}.converged`),
    iterations: obj.iterations === undefined ? undefined : optionalNullableNumber("parseOptimiserStatusResponse", obj, "iterations"),
    n_quotes: obj.n_quotes === undefined ? undefined : optionalNullableNumber("parseOptimiserStatusResponse", obj, "n_quotes"),
    n_steps: obj.n_steps === undefined ? undefined : optionalNullableNumber("parseOptimiserStatusResponse", obj, "n_steps"),
    cd_iterations: obj.cd_iterations === undefined ? undefined : optionalNullableNumber("parseOptimiserStatusResponse", obj, "cd_iterations"),
    factor_tables: factorTables,
    history: obj.history === undefined || obj.history === null ? null : parseArray("parseOptimiserStatusResponse", obj.history, `${field}.history`, parseOptimiserHistoryEntry),
    warning: obj.warning === undefined ? undefined : optionalNullableString("parseOptimiserStatusResponse", obj, "warning"),
    frontier_error: obj.frontier_error === undefined ? undefined : optionalNullableString("parseOptimiserStatusResponse", obj, "frontier_error"),
    scenario_value_stats: stats === null
      ? undefined
      : {
          mean: expectNumber("parseOptimiserStatusResponse", stats.mean, "field `scenario_value_stats.mean`"),
          std: expectNumber("parseOptimiserStatusResponse", stats.std, "field `scenario_value_stats.std`"),
          min: expectNumber("parseOptimiserStatusResponse", stats.min, "field `scenario_value_stats.min`"),
          max: expectNumber("parseOptimiserStatusResponse", stats.max, "field `scenario_value_stats.max`"),
          p5: expectNumber("parseOptimiserStatusResponse", stats.p5, "field `scenario_value_stats.p5`"),
          p25: expectNumber("parseOptimiserStatusResponse", stats.p25, "field `scenario_value_stats.p25`"),
          p50: expectNumber("parseOptimiserStatusResponse", stats.p50, "field `scenario_value_stats.p50`"),
          p75: expectNumber("parseOptimiserStatusResponse", stats.p75, "field `scenario_value_stats.p75`"),
          p95: expectNumber("parseOptimiserStatusResponse", stats.p95, "field `scenario_value_stats.p95`"),
          pct_increase: expectNumber("parseOptimiserStatusResponse", stats.pct_increase, "field `scenario_value_stats.pct_increase`"),
          pct_decrease: expectNumber("parseOptimiserStatusResponse", stats.pct_decrease, "field `scenario_value_stats.pct_decrease`"),
        },
    scenario_value_histogram: histogram === null
      ? undefined
      : {
          counts: parseArray("parseOptimiserStatusResponse", histogram.counts, "field `scenario_value_histogram.counts`", (item, itemField) => expectNumber("parseOptimiserStatusResponse", item, itemField)),
          edges: parseArray("parseOptimiserStatusResponse", histogram.edges, "field `scenario_value_histogram.edges`", (item, itemField) => expectNumber("parseOptimiserStatusResponse", item, itemField)),
        },
    clamp_rate: obj.clamp_rate === undefined ? undefined : obj.clamp_rate === null ? null : expectNumber("parseOptimiserStatusResponse", obj.clamp_rate, `${field}.clamp_rate`),
    frontier: obj.frontier === undefined || obj.frontier === null ? null : parseFrontierResponse(obj.frontier, `${field}.frontier`),
  }
}

export function parseApplyOptimiserResponse(value: unknown): ApplyOptimiserResponse {
  const obj = expectPlainObject("parseApplyOptimiserResponse", value)
  return {
    status: expectString("parseApplyOptimiserResponse", obj.status, "field `status`"),
    total_objective: optionalNumber("parseApplyOptimiserResponse", obj, "total_objective"),
    constraints: optionalNumberRecord("parseApplyOptimiserResponse", obj, "constraints"),
    from_artifact: optionalBoolean("parseApplyOptimiserResponse", obj, "from_artifact"),
    preview: optionalPlainObjectArray("parseApplyOptimiserResponse", obj, "preview"),
    row_count: optionalNumber("parseApplyOptimiserResponse", obj, "row_count"),
    preview_row_count: optionalNumber("parseApplyOptimiserResponse", obj, "preview_row_count"),
    preview_row_limit: optionalNullableNumber("parseApplyOptimiserResponse", obj, "preview_row_limit"),
    preview_truncated: optionalBoolean("parseApplyOptimiserResponse", obj, "preview_truncated"),
    error: optionalNullableString("parseApplyOptimiserResponse", obj, "error"),
  }
}

export function parseFrontierSelectResponse(value: unknown): FrontierSelectResponse {
  const obj = expectPlainObject("parseFrontierSelectResponse", value)
  const stats = optionalNullableObject("parseFrontierSelectResponse", obj, "scenario_value_stats")
  const histogram = optionalNullableObject("parseFrontierSelectResponse", obj, "scenario_value_histogram")
  return {
    status: expectString("parseFrontierSelectResponse", obj.status, "field `status`"),
    point_index: obj.point_index === undefined ? undefined : optionalNullableNumber("parseFrontierSelectResponse", obj, "point_index"),
    total_objective: optionalNumber("parseFrontierSelectResponse", obj, "total_objective"),
    constraints: optionalNumberRecord("parseFrontierSelectResponse", obj, "constraints"),
    baseline_objective: optionalNumber("parseFrontierSelectResponse", obj, "baseline_objective"),
    baseline_constraints: optionalNumberRecord("parseFrontierSelectResponse", obj, "baseline_constraints"),
    lambdas: optionalNumberRecord("parseFrontierSelectResponse", obj, "lambdas"),
    converged: optionalBoolean("parseFrontierSelectResponse", obj, "converged", true),
    iterations: obj.iterations === undefined ? undefined : optionalNullableNumber("parseFrontierSelectResponse", obj, "iterations"),
    cd_iterations: obj.cd_iterations === undefined ? undefined : optionalNullableNumber("parseFrontierSelectResponse", obj, "cd_iterations"),
    factor_tables: optionalFactorTables("parseFrontierSelectResponse", obj, "factor_tables"),
    history: obj.history === undefined || obj.history === null ? null : parseArray("parseFrontierSelectResponse", obj.history, "field `history`", parseOptimiserHistoryEntry),
    warning: obj.warning === undefined ? undefined : optionalNullableString("parseFrontierSelectResponse", obj, "warning"),
    scenario_value_stats: stats === null
      ? undefined
      : {
          mean: expectNumber("parseFrontierSelectResponse", stats.mean, "field `scenario_value_stats.mean`"),
          std: expectNumber("parseFrontierSelectResponse", stats.std, "field `scenario_value_stats.std`"),
          min: expectNumber("parseFrontierSelectResponse", stats.min, "field `scenario_value_stats.min`"),
          max: expectNumber("parseFrontierSelectResponse", stats.max, "field `scenario_value_stats.max`"),
          p5: expectNumber("parseFrontierSelectResponse", stats.p5, "field `scenario_value_stats.p5`"),
          p25: expectNumber("parseFrontierSelectResponse", stats.p25, "field `scenario_value_stats.p25`"),
          p50: expectNumber("parseFrontierSelectResponse", stats.p50, "field `scenario_value_stats.p50`"),
          p75: expectNumber("parseFrontierSelectResponse", stats.p75, "field `scenario_value_stats.p75`"),
          p95: expectNumber("parseFrontierSelectResponse", stats.p95, "field `scenario_value_stats.p95`"),
          pct_increase: expectNumber("parseFrontierSelectResponse", stats.pct_increase, "field `scenario_value_stats.pct_increase`"),
          pct_decrease: expectNumber("parseFrontierSelectResponse", stats.pct_decrease, "field `scenario_value_stats.pct_decrease`"),
        },
    scenario_value_histogram: histogram === null
      ? undefined
      : {
          counts: parseArray("parseFrontierSelectResponse", histogram.counts, "field `scenario_value_histogram.counts`", (item, itemField) => expectNumber("parseFrontierSelectResponse", item, itemField)),
          edges: parseArray("parseFrontierSelectResponse", histogram.edges, "field `scenario_value_histogram.edges`", (item, itemField) => expectNumber("parseFrontierSelectResponse", item, itemField)),
        },
    clamp_rate: obj.clamp_rate === undefined ? undefined : obj.clamp_rate === null ? null : expectNumber("parseFrontierSelectResponse", obj.clamp_rate, "field `clamp_rate`"),
    error: optionalNullableString("parseFrontierSelectResponse", obj, "error"),
  }
}

export function parseSaveOptimiserResponse(value: unknown): SaveOptimiserResponse {
  const obj = expectPlainObject("parseSaveOptimiserResponse", value)
  return {
    status: expectString("parseSaveOptimiserResponse", obj.status, "field `status`"),
    path: optionalNullableString("parseSaveOptimiserResponse", obj, "path"),
    message: optionalString("parseSaveOptimiserResponse", obj, "message"),
  }
}

export function parseOptimiserStatusResponse(value: unknown): OptimiserStatusResponse {
  const obj = expectPlainObject("parseOptimiserStatusResponse", value)
  return {
    status: expectStringLiteral("parseOptimiserStatusResponse", obj.status, "field `status`", ["running", "completed", "error"]),
    progress: optionalNumber("parseOptimiserStatusResponse", obj, "progress"),
    message: optionalString("parseOptimiserStatusResponse", obj, "message"),
    elapsed_seconds: optionalNumber("parseOptimiserStatusResponse", obj, "elapsed_seconds"),
    result: obj.result === undefined || obj.result === null ? null : parseOptimiserSolveResult(obj.result, "field `result`"),
    frontier: obj.frontier === undefined || obj.frontier === null ? null : parseFrontierResponse(obj.frontier, "field `frontier`"),
  }
}

// ---------------------------------------------------------------------------
// Databricks / cache / git contracts
// ---------------------------------------------------------------------------

function parseWarehouse(value: unknown, field: string): DatabricksWarehousesResponse["warehouses"][number] {
  const obj = expectPlainObject("parseDatabricksWarehousesResponse", value, field)
  return {
    id: expectString("parseDatabricksWarehousesResponse", obj.id, `${field}.id`),
    name: expectString("parseDatabricksWarehousesResponse", obj.name, `${field}.name`),
    http_path: expectString("parseDatabricksWarehousesResponse", obj.http_path, `${field}.http_path`),
    state: expectString("parseDatabricksWarehousesResponse", obj.state, `${field}.state`),
    size: optionalString("parseDatabricksWarehousesResponse", obj, "size"),
  }
}

function parseCatalog(value: unknown, field: string): DatabricksCatalogsResponse["catalogs"][number] {
  const obj = expectPlainObject("parseDatabricksCatalogsResponse", value, field)
  return {
    name: expectString("parseDatabricksCatalogsResponse", obj.name, `${field}.name`),
    comment: optionalString("parseDatabricksCatalogsResponse", obj, "comment"),
  }
}

function parseSchemaItem(value: unknown, field: string): DatabricksSchemasResponse["schemas"][number] {
  const obj = expectPlainObject("parseDatabricksSchemasResponse", value, field)
  return {
    name: expectString("parseDatabricksSchemasResponse", obj.name, `${field}.name`),
    comment: optionalString("parseDatabricksSchemasResponse", obj, "comment"),
  }
}

function parseTableItem(value: unknown, field: string): DatabricksTablesResponse["tables"][number] {
  const obj = expectPlainObject("parseDatabricksTablesResponse", value, field)
  return {
    name: expectString("parseDatabricksTablesResponse", obj.name, `${field}.name`),
    full_name: expectString("parseDatabricksTablesResponse", obj.full_name, `${field}.full_name`),
    table_type: optionalString("parseDatabricksTablesResponse", obj, "table_type"),
    comment: optionalString("parseDatabricksTablesResponse", obj, "comment"),
  }
}

export function parseDatabricksWarehousesResponse(value: unknown): DatabricksWarehousesResponse {
  const obj = expectPlainObject("parseDatabricksWarehousesResponse", value)
  return {
    warehouses: optionalArray("parseDatabricksWarehousesResponse", obj, "warehouses", parseWarehouse),
  }
}

export function parseDatabricksCatalogsResponse(value: unknown): DatabricksCatalogsResponse {
  const obj = expectPlainObject("parseDatabricksCatalogsResponse", value)
  return {
    catalogs: optionalArray("parseDatabricksCatalogsResponse", obj, "catalogs", parseCatalog),
  }
}

export function parseDatabricksSchemasResponse(value: unknown): DatabricksSchemasResponse {
  const obj = expectPlainObject("parseDatabricksSchemasResponse", value)
  return {
    schemas: optionalArray("parseDatabricksSchemasResponse", obj, "schemas", parseSchemaItem),
  }
}

export function parseDatabricksTablesResponse(value: unknown): DatabricksTablesResponse {
  const obj = expectPlainObject("parseDatabricksTablesResponse", value)
  return {
    tables: optionalArray("parseDatabricksTablesResponse", obj, "tables", parseTableItem),
  }
}

export function parseFetchTableResponse(value: unknown): FetchTableResponse {
  const obj = expectPlainObject("parseFetchTableResponse", value)
  return {
    path: expectString("parseFetchTableResponse", obj.path, "field `path`"),
    table: expectString("parseFetchTableResponse", obj.table, "field `table`"),
    row_count: expectNumber("parseFetchTableResponse", obj.row_count, "field `row_count`"),
    column_count: expectNumber("parseFetchTableResponse", obj.column_count, "field `column_count`"),
    columns: parseStringRecord("parseFetchTableResponse", obj.columns, "field `columns`"),
    size_bytes: expectNumber("parseFetchTableResponse", obj.size_bytes, "field `size_bytes`"),
    fetched_at: expectNumber("parseFetchTableResponse", obj.fetched_at, "field `fetched_at`"),
    fetch_seconds: expectNumber("parseFetchTableResponse", obj.fetch_seconds, "field `fetch_seconds`"),
  }
}

export function parseCacheStatusResponse(value: unknown): CacheStatusResponse {
  const obj = expectPlainObject("parseCacheStatusResponse", value)
  return {
    cached: expectBoolean("parseCacheStatusResponse", obj.cached, "field `cached`"),
    path: obj.path === undefined ? undefined : optionalNullableString("parseCacheStatusResponse", obj, "path") ?? undefined,
    table: optionalString("parseCacheStatusResponse", obj, "table"),
    row_count: optionalNumber("parseCacheStatusResponse", obj, "row_count"),
    column_count: optionalNumber("parseCacheStatusResponse", obj, "column_count"),
    size_bytes: optionalNumber("parseCacheStatusResponse", obj, "size_bytes"),
    fetched_at: optionalNumber("parseCacheStatusResponse", obj, "fetched_at"),
    columns: optionalStringRecord("parseCacheStatusResponse", obj, "columns"),
  }
}

export function parseFetchProgressResponse(value: unknown): FetchProgressResponse {
  const obj = expectPlainObject("parseFetchProgressResponse", value)
  return {
    active: expectBoolean("parseFetchProgressResponse", obj.active, "field `active`"),
    rows: obj.rows === undefined ? undefined : expectNumber("parseFetchProgressResponse", obj.rows, "field `rows`"),
    elapsed: obj.elapsed === undefined ? undefined : expectNumber("parseFetchProgressResponse", obj.elapsed, "field `elapsed`"),
    batches: obj.batches === undefined ? undefined : expectNumber("parseFetchProgressResponse", obj.batches, "field `batches`"),
  }
}

export function parseJsonCacheBuildResponse(value: unknown): JsonCacheBuildResponse {
  const obj = expectPlainObject("parseJsonCacheBuildResponse", value)
  return {
    path: expectString("parseJsonCacheBuildResponse", obj.path, "field `path`"),
    data_path: expectString("parseJsonCacheBuildResponse", obj.data_path, "field `data_path`"),
    row_count: expectNumber("parseJsonCacheBuildResponse", obj.row_count, "field `row_count`"),
    column_count: expectNumber("parseJsonCacheBuildResponse", obj.column_count, "field `column_count`"),
    columns: parseStringRecord("parseJsonCacheBuildResponse", obj.columns, "field `columns`"),
    size_bytes: expectNumber("parseJsonCacheBuildResponse", obj.size_bytes, "field `size_bytes`"),
    cached_at: expectNumber("parseJsonCacheBuildResponse", obj.cached_at, "field `cached_at`"),
    cache_seconds: expectNumber("parseJsonCacheBuildResponse", obj.cache_seconds, "field `cache_seconds`"),
  }
}

export function parseJsonCacheProgressResponse(value: unknown): JsonCacheProgressResponse {
  const obj = expectPlainObject("parseJsonCacheProgressResponse", value)
  return {
    active: expectBoolean("parseJsonCacheProgressResponse", obj.active, "field `active`"),
    rows: obj.rows === undefined ? undefined : expectNumber("parseJsonCacheProgressResponse", obj.rows, "field `rows`"),
    elapsed: obj.elapsed === undefined ? undefined : expectNumber("parseJsonCacheProgressResponse", obj.elapsed, "field `elapsed`"),
    phase: obj.phase === undefined ? undefined : expectString("parseJsonCacheProgressResponse", obj.phase, "field `phase`"),
  }
}

export function parseJsonCacheStatusResponse(value: unknown): JsonCacheStatusResponse {
  const obj = expectPlainObject("parseJsonCacheStatusResponse", value)
  return {
    cached: expectBoolean("parseJsonCacheStatusResponse", obj.cached, "field `cached`"),
    path: obj.path === undefined ? undefined : optionalNullableString("parseJsonCacheStatusResponse", obj, "path") ?? undefined,
    data_path: optionalString("parseJsonCacheStatusResponse", obj, "data_path"),
    row_count: optionalNumber("parseJsonCacheStatusResponse", obj, "row_count"),
    column_count: optionalNumber("parseJsonCacheStatusResponse", obj, "column_count"),
    size_bytes: optionalNumber("parseJsonCacheStatusResponse", obj, "size_bytes"),
    cached_at: optionalNumber("parseJsonCacheStatusResponse", obj, "cached_at"),
    columns: optionalStringRecord("parseJsonCacheStatusResponse", obj, "columns"),
  }
}

function parseUtilityFile(value: unknown, field: string): UtilityFile {
  const obj = expectPlainObject("parseUtilityListResponse", value, field)
  return {
    name: expectString("parseUtilityListResponse", obj.name, `${field}.name`),
    module: expectString("parseUtilityListResponse", obj.module, `${field}.module`),
  }
}

export function parseUtilityListResponse(value: unknown): UtilityListResponse {
  const obj = expectPlainObject("parseUtilityListResponse", value)
  return {
    files: optionalArray("parseUtilityListResponse", obj, "files", parseUtilityFile),
  }
}

export function parseUtilityReadResponse(value: unknown): UtilityReadResponse {
  const obj = expectPlainObject("parseUtilityReadResponse", value)
  return {
    name: expectString("parseUtilityReadResponse", obj.name, "field `name`"),
    module: expectString("parseUtilityReadResponse", obj.module, "field `module`"),
    content: expectString("parseUtilityReadResponse", obj.content, "field `content`"),
  }
}

export function parseUtilityWriteResponse(value: unknown): UtilityWriteResult {
  const obj = expectPlainObject("parseUtilityWriteResponse", value)
  return {
    status: optionalString("parseUtilityWriteResponse", obj, "status", "ok"),
    name: optionalString("parseUtilityWriteResponse", obj, "name"),
    module: optionalString("parseUtilityWriteResponse", obj, "module"),
    import_line: optionalString("parseUtilityWriteResponse", obj, "import_line"),
    error: optionalNullableString("parseUtilityWriteResponse", obj, "error"),
    error_line: optionalNullableNumber("parseUtilityWriteResponse", obj, "error_line"),
  }
}

export function parseUtilityDeleteResponse(value: unknown): UtilityDeleteResponse {
  const obj = expectPlainObject("parseUtilityDeleteResponse", value)
  return {
    status: optionalString("parseUtilityDeleteResponse", obj, "status", "ok"),
    module: expectString("parseUtilityDeleteResponse", obj.module, "field `module`"),
  }
}

export function parseGitStatusResponse(value: unknown): GitStatus {
  const obj = expectPlainObject("parseGitStatusResponse", value)
  return {
    branch: expectString("parseGitStatusResponse", obj.branch, "field `branch`"),
    is_main: expectBoolean("parseGitStatusResponse", obj.is_main, "field `is_main`"),
    is_read_only: expectBoolean("parseGitStatusResponse", obj.is_read_only, "field `is_read_only`"),
    changed_files: optionalStringArray("parseGitStatusResponse", obj, "changed_files"),
    main_ahead: optionalBoolean("parseGitStatusResponse", obj, "main_ahead"),
    main_ahead_by: optionalNumber("parseGitStatusResponse", obj, "main_ahead_by"),
    main_last_updated: optionalNullableString("parseGitStatusResponse", obj, "main_last_updated"),
  }
}

const WORKING_BRANCH_STATES = ["ready", "unset", "invalid", "divergent"] as const

export function parseGitWorkingBranchResponse(value: unknown): GitWorkingBranchResponse {
  const obj = expectPlainObject("parseGitWorkingBranchResponse", value)
  return {
    working_branch: optionalNullableString("parseGitWorkingBranchResponse", obj, "working_branch"),
    state: expectStringLiteral(
      "parseGitWorkingBranchResponse",
      obj.state,
      "field `state`",
      WORKING_BRANCH_STATES,
    ),
    errors: optionalStringArray("parseGitWorkingBranchResponse", obj, "errors"),
    current_branch: expectString(
      "parseGitWorkingBranchResponse",
      obj.current_branch,
      "field `current_branch`",
    ),
    last_save_sha: optionalNullableString("parseGitWorkingBranchResponse", obj, "last_save_sha"),
    eligible_branches: optionalStringArray(
      "parseGitWorkingBranchResponse",
      obj,
      "eligible_branches",
    ),
    identity_set: optionalBoolean("parseGitWorkingBranchResponse", obj, "identity_set"),
    user_name: optionalNullableString("parseGitWorkingBranchResponse", obj, "user_name"),
    user_email: optionalNullableString("parseGitWorkingBranchResponse", obj, "user_email"),
  }
}

export function parseGitSetWorkingBranchResponse(value: unknown): GitSetWorkingBranchResponse {
  const obj = expectPlainObject("parseGitSetWorkingBranchResponse", value)
  return {
    working_branch: expectString(
      "parseGitSetWorkingBranchResponse",
      obj.working_branch,
      "field `working_branch`",
    ),
    state: expectStringLiteral(
      "parseGitSetWorkingBranchResponse",
      obj.state,
      "field `state`",
      WORKING_BRANCH_STATES,
    ),
    last_save_sha: optionalNullableString(
      "parseGitSetWorkingBranchResponse",
      obj,
      "last_save_sha",
    ),
  }
}

export function parseGitSetIdentityResponse(value: unknown): GitSetIdentityResponse {
  const obj = expectPlainObject("parseGitSetIdentityResponse", value)
  return {
    user_name: expectString("parseGitSetIdentityResponse", obj.user_name, "field `user_name`"),
    user_email: expectString("parseGitSetIdentityResponse", obj.user_email, "field `user_email`"),
    scope: expectStringLiteral("parseGitSetIdentityResponse", obj.scope, "field `scope`", [
      "local",
      "global",
    ] as const),
  }
}

export function parseGitCommitResponse(value: unknown): GitCommitResponse {
  const obj = expectPlainObject("parseGitCommitResponse", value)
  return {
    sha: expectString("parseGitCommitResponse", obj.sha, "field `sha`"),
    short_sha: expectString("parseGitCommitResponse", obj.short_sha, "field `short_sha`"),
    working_branch: expectString(
      "parseGitCommitResponse",
      obj.working_branch,
      "field `working_branch`",
    ),
    version_label: optionalNullableString("parseGitCommitResponse", obj, "version_label"),
  }
}

function parseGitMilestoneEntry(value: unknown, field: string): GitMilestoneEntry {
  const obj = expectPlainObject("parseGitMilestonesResponse", value, field)
  return {
    sha: expectString("parseGitMilestonesResponse", obj.sha, `${field}.sha`),
    short_sha: expectString("parseGitMilestonesResponse", obj.short_sha, `${field}.short_sha`),
    message: expectString("parseGitMilestonesResponse", obj.message, `${field}.message`),
    timestamp: expectString("parseGitMilestonesResponse", obj.timestamp, `${field}.timestamp`),
    version_label: optionalNullableString("parseGitMilestonesResponse", obj, "version_label"),
  }
}

export function parseGitMilestonesResponse(value: unknown): GitMilestonesResponse {
  const obj = expectPlainObject("parseGitMilestonesResponse", value)
  return {
    working_branch: optionalNullableString("parseGitMilestonesResponse", obj, "working_branch"),
    entries: optionalArray("parseGitMilestonesResponse", obj, "entries", parseGitMilestoneEntry),
  }
}

function parseGitFileChange(value: unknown, field: string): GitFileChange {
  const obj = expectPlainObject("parseGitLedgerSavesResponse", value, field)
  return {
    status: expectString("parseGitLedgerSavesResponse", obj.status, `${field}.status`),
    path: expectString("parseGitLedgerSavesResponse", obj.path, `${field}.path`),
    old_path: optionalNullableString("parseGitLedgerSavesResponse", obj, "old_path"),
  }
}

function parseGitLedgerSave(value: unknown, field: string): GitLedgerSave {
  const obj = expectPlainObject("parseGitLedgerSavesResponse", value, field)
  return {
    sha: expectString("parseGitLedgerSavesResponse", obj.sha, `${field}.sha`),
    short_sha: expectString("parseGitLedgerSavesResponse", obj.short_sha, `${field}.short_sha`),
    message: expectString("parseGitLedgerSavesResponse", obj.message, `${field}.message`),
    timestamp: expectString("parseGitLedgerSavesResponse", obj.timestamp, `${field}.timestamp`),
    files: optionalArray("parseGitLedgerSavesResponse", obj, "files", parseGitFileChange),
  }
}

export function parseGitLedgerSavesResponse(value: unknown): GitLedgerSavesResponse {
  const obj = expectPlainObject("parseGitLedgerSavesResponse", value)
  return {
    saves: optionalArray("parseGitLedgerSavesResponse", obj, "saves", parseGitLedgerSave),
  }
}

function parseGitManagedBranch(value: unknown, field: string): GitManagedBranch {
  const obj = expectPlainObject("parseGitWorkingBranchesResponse", value, field)
  return {
    name: expectString("parseGitWorkingBranchesResponse", obj.name, `${field}.name`),
    is_current: expectBoolean("parseGitWorkingBranchesResponse", obj.is_current, `${field}.is_current`),
    is_archived: expectBoolean("parseGitWorkingBranchesResponse", obj.is_archived, `${field}.is_archived`),
    has_unmerged_saves: expectBoolean(
      "parseGitWorkingBranchesResponse", obj.has_unmerged_saves, `${field}.has_unmerged_saves`,
    ),
    has_uncommitted_changes: optionalBoolean(
      "parseGitWorkingBranchesResponse", obj, "has_uncommitted_changes", false,
    ),
    forked_from: optionalNullableString(
      "parseGitWorkingBranchesResponse", obj, "forked_from",
    ),
  }
}

export function parseGitWorkingBranchesResponse(value: unknown): GitWorkingBranchesResponse {
  const obj = expectPlainObject("parseGitWorkingBranchesResponse", value)
  return {
    current: optionalNullableString("parseGitWorkingBranchesResponse", obj, "current"),
    branches: optionalArray(
      "parseGitWorkingBranchesResponse", obj, "branches", parseGitManagedBranch,
    ),
  }
}

export function parseGitRestoreResponse(value: unknown): GitRestoreResponse {
  const obj = expectPlainObject("parseGitRestoreResponse", value)
  return {
    restored_as: expectString("parseGitRestoreResponse", obj.restored_as, "restored_as"),
  }
}

export function parseGitCreateWorkingBranchResponse(
  value: unknown,
): GitCreateWorkingBranchResponse {
  const obj = expectPlainObject("parseGitCreateWorkingBranchResponse", value)
  return {
    working_branch: expectString(
      "parseGitCreateWorkingBranchResponse", obj.working_branch, "working_branch",
    ),
    moved: expectBoolean("parseGitCreateWorkingBranchResponse", obj.moved, "moved"),
    switched: expectBoolean(
      "parseGitCreateWorkingBranchResponse", obj.switched, "switched",
    ),
    last_save_sha: optionalNullableString(
      "parseGitCreateWorkingBranchResponse", obj, "last_save_sha",
    ),
  }
}

export function parseGitPrefs(value: unknown): GitPrefs {
  const obj = expectPlainObject("parseGitPrefs", value)
  return {
    skip_switch_confirm: optionalBoolean(
      "parseGitPrefs", obj, "skip_switch_confirm", false,
    ),
  }
}

export function parseGitArchiveResponse(value: unknown): GitArchiveResponse {
  const obj = expectPlainObject("parseGitArchiveResponse", value)
  return {
    archived_as: expectString("parseGitArchiveResponse", obj.archived_as, "field `archived_as`"),
  }
}

export function parseGitDeleteBranchResponse(value: unknown): GitDeleteBranchResponse {
  const obj = expectPlainObject("parseGitDeleteBranchResponse", value)
  return {
    status: optionalString("parseGitDeleteBranchResponse", obj, "status", "ok"),
    branch: expectString("parseGitDeleteBranchResponse", obj.branch, "field `branch`"),
  }
}

// ---------------------------------------------------------------------------
// React Flow node validation
// ---------------------------------------------------------------------------

export function validateReactFlowNode(value: unknown): Node {
  const obj = expectPlainObject("validateReactFlowNode", value)
  const id = expectString("validateReactFlowNode", obj.id, "field `id`")
  if (id === "") {
    throw new Error(
      "validateReactFlowNode: field `id` must not be an empty string (ReactFlow silently drops edges that reference empty ids)",
    )
  }
  const position = expectPlainObject("validateReactFlowNode", obj.position, "field `position`")
  expectNumber("validateReactFlowNode", position.x, "field `position.x`")
  expectNumber("validateReactFlowNode", position.y, "field `position.y`")
  if (obj.data === undefined) {
    throw new Error("validateReactFlowNode: expected field `data` to be a plain object, got missing")
  }
  if (!isPlainObject(obj.data)) {
    throw new Error(`validateReactFlowNode: expected field \`data\` to be a plain object, got ${typeName(obj.data)}`)
  }
  return value as Node
}
