/**
 * Runtime parsers for values that cross the JSON / DOM boundary.
 *
 * These helpers fail loudly when backend payloads drift away from the
 * shapes the UI actually consumes, instead of letting the app explode
 * later with "cannot read property X of undefined".
 */

import type { Node } from "@xyflow/react"

import {
  EXECUTION_STRATEGY_SCHEMA_VERSION,
  validateExecutionStrategyDiagnostic,
} from "../generated/api-contracts.execution-strategy-diagnostic.validators.mjs"
import type {
  OptimiserFrontierAutoRangeStatusResponse as GeneratedOptimiserFrontierAutoRangeStatusResponse,
  OptimiserFrontierResponse as GeneratedOptimiserFrontierResponse,
  OptimiserFrontierStatusResponse as GeneratedOptimiserFrontierStatusResponse,
  OptimiserStatusResponse as GeneratedOptimiserStatusResponse,
} from "../generated/api-contracts.generated"
import type {
  ExplorePivotMembersResponse as GeneratedExplorePivotMembersResponse,
  ExplorePivotPath as GeneratedExplorePivotPath,
  ExplorePivotResult as GeneratedExplorePivotResult,
  ExplorePivotRunResponse as GeneratedExplorePivotRunResponse,
  ExplorePivotStatusResponse as GeneratedExplorePivotStatusResponse,
  NodeDataStatusResponse as GeneratedNodeDataStatusResponse,
  WriteOutputResponse as GeneratedWriteOutputResponse,
} from "../generated/api-contracts.generated"
import type {
  NodeInstanceCreateResponse,
  DissolveSubmodelResponse,
  ExecutionAdmission,
  ExecutionCacheProof,
  ExecutionMemoryPressureEvent,
  InputPreparationRecord,
  ExecutionWarning,
  SharedSnapshotCapture,
  SharedSnapshotCaptureSkip,
  SharedSnapshotSeed,
  ExecutionMetrics,
  ExecutionColumnWidth,
  ExecutionColumnWidths,
  ExecutionStreamabilityEvidence,
  ExecutionStrategyDiagnostic,
  ExecutionStrategyBoundary,
  ExecutionStrategyBoundedCollection,
  ExecutionStrategyProjectionCause,
  ExecutionStrategyProvenance,
  ExecutionStrategyReason,
  ExecutionStageMetrics,
  ExplorePivotMemberKey,
  ExplorePivotMembersResponse,
  ExplorePivotPath,
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
  FrontierAutoRangeStatusResponse,
  FrontierStatusResponse,
  FrontierResponse,
  NodeDataStatusResponse,
  OptimiserStatusResponse,
  PortPreview,
  PreviewInputsResponse,
  PreviewNodeResponse,
  SchemaWarning,
  PreviewProgressResponse,
  PreviewSeedPlanEntry,
  SavePipelineResponse,
  SchemaResult,
  WriteOutputResponse,
  SubmodelCreateResponse,
  SubmodelGraphResponse,
  TraceResponse,
} from "../api/types"
import {
  PIPELINE_NODE_TYPES,
  type BackendNodeStatus,
  type ColumnInfo,
  type PipelineEdge,
} from "./node"
import type {
  TraceColumnDerivation,
  TraceColumnRead,
  TraceColumnSource,
  TraceCorrelationDiagnostic,
  TraceInputSource,
  TraceOmission,
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
  edges: PipelineEdge[]
  pipeline_name?: string | null
  pipeline_description?: string | null
  preamble?: string | null
  source_file?: string | null
  submodels?: Record<string, unknown> | null
  warning?: string | null
  sources?: string[]
  active_source?: string
  preserved_blocks: string[]
  source_revision: string | null
}
export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

export function typeName(value: unknown): string {
  if (value === null) return "null"
  if (Array.isArray(value)) return "array"
  return typeof value
}

export function expectPlainObject(
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

export function expectString(parser: string, value: unknown, field: string): string {
  if (typeof value !== "string") {
    throw new Error(`${parser}: expected ${field} to be a string, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

export function expectNonBlankString(parser: string, value: unknown, field: string): string {
  const parsed = expectString(parser, value, field)
  if (parsed.trim() === "") {
    throw new Error(`${parser}: expected ${field} to be non-blank`)
  }
  return parsed
}

function expectNullableNonBlankString(
  parser: string,
  value: unknown,
  field: string,
): string | null {
  if (value === null) return null
  return expectNonBlankString(parser, value, field)
}

export function expectNumber(parser: string, value: unknown, field: string): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new Error(`${parser}: expected ${field} to be a number, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

export function expectSchemaVersionOne(parser: string, value: unknown, field: string): 1 {
  if (value !== 1) {
    throw new Error(`${parser}: expected ${field} to be 1, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return 1
}

export function expectExactKeys(
  parser: string,
  obj: Record<string, unknown>,
  field: string,
  keys: readonly string[],
): void {
  const actual = Object.keys(obj).sort()
  const expected = [...keys].sort()
  if (actual.length !== expected.length || actual.some((key, index) => key !== expected[index])) {
    throw new Error(`${parser}: ${field} has unexpected or missing fields`)
  }
}

export function expectBoolean(parser: string, value: unknown, field: string): boolean {
  if (typeof value !== "boolean") {
    throw new Error(`${parser}: expected ${field} to be a boolean, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

export function expectStringLiteral<T extends string>(
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

export function optionalString(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = "",
): string {
  const value = obj[key]
  return value === undefined ? defaultValue : expectString(parser, value, `field \`${key}\``)
}

export function optionalNumber(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = 0,
): number {
  const value = obj[key]
  return value === undefined ? defaultValue : expectNumber(parser, value, `field \`${key}\``)
}

export function optionalBoolean(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue = false,
): boolean {
  const value = obj[key]
  return value === undefined ? defaultValue : expectBoolean(parser, value, `field \`${key}\``)
}

export function optionalNullableString(
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

export function optionalNullableNumber(
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

export function expectNullableString(
  parser: string,
  value: unknown,
  field: string,
): string | null {
  if (value === null || typeof value === "string") return value
  throw new Error(`${parser}: expected ${field} to be a string or null, got ${value === undefined ? "missing" : typeName(value)}`)
}

export function expectNullableNumber(
  parser: string,
  value: unknown,
  field: string,
): number | null {
  if (value === null) return null
  return expectNumber(parser, value, field)
}

export function expectArray(
  parser: string,
  value: unknown,
  field: string,
): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${parser}: expected ${field} to be an array, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
}

export function parseArray<T>(
  parser: string,
  value: unknown,
  field: string,
  itemParser: (value: unknown, field: string) => T,
): T[] {
  return expectArray(parser, value, field).map((item, index) => itemParser(item, `${field}[${index}]`))
}

export function optionalArray<T>(
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

export function optionalNumberRecord(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: Record<string, number> = {},
): Record<string, number> {
  const value = obj[key]
  return value === undefined ? defaultValue : parseNumberRecord(parser, value, `field \`${key}\``)
}

const NODE_STATUS_VALUES = ["ok", "error"] as const

/** Parse a `Record<string, BackendNodeStatus>`, validating every value against the
 *  closed status set so a drifting backend fails loud here rather than at an
 *  unchecked `as` cast downstream (usePipelineAPI). */
function optionalNodeStatusRecord(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  defaultValue: Record<string, BackendNodeStatus> = {},
): Record<string, BackendNodeStatus> {
  const value = obj[key]
  if (value === undefined) return defaultValue
  const inner = expectPlainObject(parser, value, `field \`${key}\``)
  const result: Record<string, BackendNodeStatus> = {}
  for (const [nodeId, status] of Object.entries(inner)) {
    result[nodeId] = expectStringLiteral(parser, status, `${key}.${nodeId}`, NODE_STATUS_VALUES)
  }
  return result
}

function parseArrayRecord<T>(
  parser: string,
  value: unknown,
  field: string,
  itemParser: (value: unknown, field: string) => T,
): Record<string, T[]> {
  const obj = expectPlainObject(parser, value, field)
  const result: Record<string, T[]> = {}
  for (const [recordKey, item] of Object.entries(obj)) {
    result[recordKey] = parseArray(parser, item, `${field}.${recordKey}`, itemParser)
  }
  return result
}

function optionalArrayRecord<T>(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  itemParser: (value: unknown, field: string) => T,
  defaultValue: Record<string, T[]> = {},
): Record<string, T[]> {
  const value = obj[key]
  return value === undefined
    ? defaultValue
    : parseArrayRecord(parser, value, `field \`${key}\``, itemParser)
}

/** Parse a doubly-nested record `Record<string, Record<string, T[]>>` —
 * used by `node_frame_columns` (node_id → frame label → columns). */
function optionalNestedArrayRecord<T>(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
  itemParser: (value: unknown, field: string) => T,
  defaultValue: Record<string, Record<string, T[]>> = {},
): Record<string, Record<string, T[]>> {
  const value = obj[key]
  if (value === undefined) return defaultValue
  const outer = expectPlainObject(parser, value, `field \`${key}\``)
  const result: Record<string, Record<string, T[]>> = {}
  for (const [recordKey, inner] of Object.entries(outer)) {
    result[recordKey] = parseArrayRecord(parser, inner, `field \`${key}\`.${recordKey}`, itemParser)
  }
  return result
}

export function optionalNullableObject(
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

function parseSchemaWarning(value: unknown, field: string): SchemaWarning {
  const parser = "parsePreviewNodeResponse"
  const obj = expectPlainObject(parser, value, field)
  const status = expectString(parser, obj.status, `${field}.status`)
  if (status === "unavailable") {
    if (obj.column != null) throw new Error(`${parser}: ${field}.column must be absent for an unavailable schema`)
    return {
      status,
      column: optionalNullableString(parser, obj, "column"),
      input: expectNonBlankString(parser, obj.input, `${field}.input`),
      detail: expectNonBlankString(parser, obj.detail, `${field}.detail`),
    }
  }
  return {
    column: expectString(parser, obj.column, `${field}.column`),
    status,
    input: optionalNullableString(parser, obj, "input"),
    detail: optionalNullableString(parser, obj, "detail"),
  }
}

function parseExecutionStageMetrics(
  parser: string,
  value: unknown,
  field: string,
): ExecutionStageMetrics {
  const obj = expectPlainObject(parser, value, field)
  return {
    schema_version: optionalNumber(parser, obj, "schema_version", 1),
    name: optionalString(parser, obj, "name"),
    operation: optionalString(parser, obj, "operation"),
    profile: optionalString(parser, obj, "profile"),
    elapsed_ms: optionalNumber(parser, obj, "elapsed_ms"),
    node_id: optionalNullableString(parser, obj, "node_id"),
    job_id: optionalNullableString(parser, obj, "job_id"),
    rss_start_bytes: optionalNullableNumber(parser, obj, "rss_start_bytes"),
    rss_end_bytes: optionalNullableNumber(parser, obj, "rss_end_bytes"),
    rss_delta_bytes: optionalNullableNumber(parser, obj, "rss_delta_bytes"),
    rss_peak_bytes: optionalNullableNumber(parser, obj, "rss_peak_bytes"),
    rows_in: optionalNullableNumber(parser, obj, "rows_in"),
    rows_out: optionalNullableNumber(parser, obj, "rows_out"),
    bytes_read: optionalNullableNumber(parser, obj, "bytes_read"),
    bytes_written: optionalNullableNumber(parser, obj, "bytes_written"),
    columns_scanned: optionalNullableNumber(parser, obj, "columns_scanned"),
    n_collects: optionalNumber(parser, obj, "n_collects"),
    n_checkpoints: optionalNumber(parser, obj, "n_checkpoints"),
  }
}

function parseExecutionAdmission(
  parser: string,
  value: unknown,
  field: string,
): ExecutionAdmission {
  const obj = expectPlainObject(parser, value, field)
  return {
    admitted: optionalBoolean(parser, obj, "admitted", true),
    operation: optionalString(parser, obj, "operation"),
    profile: optionalString(parser, obj, "profile"),
    memory_limit_bytes: optionalNumber(parser, obj, "memory_limit_bytes"),
    rss_at_admission_bytes: optionalNullableNumber(parser, obj, "rss_at_admission_bytes"),
    rss_limit_bytes: optionalNullableNumber(parser, obj, "rss_limit_bytes"),
    process_rss_limit_bytes: optionalNullableNumber(parser, obj, "process_rss_limit_bytes"),
    headroom_bytes: optionalNullableNumber(parser, obj, "headroom_bytes"),
    config_key: optionalString(parser, obj, "config_key"),
    budget_policy: optionalString(parser, obj, "budget_policy", "fixed_default"),
    available_ram_bytes: optionalNullableNumber(parser, obj, "available_ram_bytes"),
    os_reserve_bytes: optionalNullableNumber(parser, obj, "os_reserve_bytes"),
    reason: optionalString(parser, obj, "reason"),
  }
}

function parseExecutionMemoryPressureEvent(
  parser: string,
  value: unknown,
  field: string,
): ExecutionMemoryPressureEvent {
  const obj = expectPlainObject(parser, value, field)
  return {
    schema_version: optionalNumber(parser, obj, "schema_version", 1),
    event: obj.event === undefined
      ? "memory_pressure"
      : expectStringLiteral(parser, obj.event, `${field}.event`, ["memory_pressure"]),
    operation: optionalString(parser, obj, "operation"),
    profile: optionalString(parser, obj, "profile"),
    job_id: optionalNullableString(parser, obj, "job_id"),
    node_id: optionalNullableString(parser, obj, "node_id"),
    stage: optionalNullableString(parser, obj, "stage"),
    label: optionalNullableString(parser, obj, "label"),
    threshold_ratio: optionalNumber(parser, obj, "threshold_ratio"),
    threshold_percent: optionalNumber(parser, obj, "threshold_percent"),
    rss_bytes: optionalNumber(parser, obj, "rss_bytes"),
    rss_limit_bytes: optionalNumber(parser, obj, "rss_limit_bytes"),
    headroom_bytes: optionalNumber(parser, obj, "headroom_bytes"),
    headroom_used_bytes: optionalNumber(parser, obj, "headroom_used_bytes"),
    rss_peak_bytes: optionalNumber(parser, obj, "rss_peak_bytes"),
    memory_limit_bytes: optionalNullableNumber(parser, obj, "memory_limit_bytes"),
    memory_baseline_bytes: optionalNullableNumber(parser, obj, "memory_baseline_bytes"),
    baseline_rss_bytes: optionalNullableNumber(parser, obj, "baseline_rss_bytes"),
    budget_policy: optionalNullableString(parser, obj, "budget_policy"),
    config_key: optionalNullableString(parser, obj, "config_key"),
    available_ram_bytes: optionalNullableNumber(parser, obj, "available_ram_bytes"),
    os_reserve_bytes: optionalNullableNumber(parser, obj, "os_reserve_bytes"),
    pressure_ratio: optionalNumber(parser, obj, "pressure_ratio"),
  }
}

function parseInputPreparationRecord(
  parser: string,
  value: unknown,
  field: string,
): InputPreparationRecord {
  const obj = expectPlainObject(parser, value, field)
  return {
    node_id: optionalString(parser, obj, "node_id"),
    identity_digest: optionalString(parser, obj, "identity_digest"),
    action: expectStringLiteral(parser, obj.action, `${field}.action`, ["reused", "built", "refreshed"]),
    build_class: optionalString(parser, obj, "build_class"),
    execution: expectStringLiteral(parser, obj.execution, `${field}.execution`, ["in_process", "worker"]),
    memory_limit_bytes: optionalNullableNumber(parser, obj, "memory_limit_bytes"),
    elapsed_seconds: optionalNumber(parser, obj, "elapsed_seconds"),
    row_count: optionalNullableNumber(parser, obj, "row_count"),
    size_bytes: optionalNullableNumber(parser, obj, "size_bytes"),
    generation_id: optionalNullableString(parser, obj, "generation_id"),
    warning_code: optionalNullableString(parser, obj, "warning_code"),
  }
}

function parseSnapshotColumns(parser: string, value: unknown, field: string): "all" | string[] {
  return value === "all" ? "all" : parseStringArray(parser, value, field)
}

function parseSharedSnapshotSeed(
  parser: string,
  value: unknown,
  field: string,
): SharedSnapshotSeed {
  const obj = expectPlainObject(parser, value, field)
  return {
    node_id: expectString(parser, obj.node_id, `${field}.node_id`),
    identity_digest: expectString(parser, obj.identity_digest, `${field}.identity_digest`),
    generation_id: expectString(parser, obj.generation_id, `${field}.generation_id`),
    columns: parseSnapshotColumns(parser, obj.columns, `${field}.columns`),
  }
}

function parseSharedSnapshotCapture(
  parser: string,
  value: unknown,
  field: string,
): SharedSnapshotCapture {
  const obj = expectPlainObject(parser, value, field)
  return {
    node_id: expectString(parser, obj.node_id, `${field}.node_id`),
    identity_digest: expectString(parser, obj.identity_digest, `${field}.identity_digest`),
    kind: expectStringLiteral(parser, obj.kind, `${field}.kind`, [
      "structural",
      "materialising",
      "model_score",
      "consumed",
    ]),
    outcome: expectStringLiteral(parser, obj.outcome, `${field}.outcome`, [
      "published",
      "superseded",
    ]),
    generation_id: optionalNullableString(parser, obj, "generation_id"),
    columns: parseSnapshotColumns(parser, obj.columns, `${field}.columns`),
    ...(obj.write_strategy === undefined
      ? {}
      : {
          write_strategy: expectNullableStringLiteral(
            parser,
            obj.write_strategy,
            `${field}.write_strategy`,
            ["chunked_join", "sliced", "input_sliced", "native", "prewritten"] as const,
          ),
        }),
    ...optionalNullablePositiveCount(parser, obj, "write_parts", field),
    ...optionalNullablePositiveCount(parser, obj, "write_chunk_rows", field),
    ...optionalNullableCount(parser, obj, "write_staged_inputs", field),
    ...optionalNullablePositiveCount(parser, obj, "write_input_slices", field),
    ...(obj.write_native_reason === undefined
      ? {}
      : {
          write_native_reason: expectNullableString(
            parser,
            obj.write_native_reason,
            `${field}.write_native_reason`,
          ),
        }),
    ...(obj.write_blocking_operator === undefined
      ? {}
      : {
          write_blocking_operator: expectNullableString(
            parser,
            obj.write_blocking_operator,
            `${field}.write_blocking_operator`,
          ),
        }),
  }
}

function parseSharedSnapshotCaptureSkip(
  parser: string,
  value: unknown,
  field: string,
): SharedSnapshotCaptureSkip {
  const obj = expectPlainObject(parser, value, field)
  return {
    node_id: expectString(parser, obj.node_id, `${field}.node_id`),
    reason: expectStringLiteral(parser, obj.reason, `${field}.reason`, [
      "cheap_segment",
      "slice_transparent_feeder",
    ]),
  }
}

function optionalNullableCount<K extends string>(
  parser: string,
  obj: Record<string, unknown>,
  key: K,
  field: string,
  { positive = false }: { positive?: boolean } = {},
): Partial<Record<K, number | null>> {
  const value = obj[key]
  if (value === undefined) return {}
  if (value === null) return { [key]: null } as Partial<Record<K, number | null>>
  const expectCount = positive ? expectPositiveInteger : expectNonNegativeInteger
  return {
    [key]: expectCount(parser, value, `${field}.${key}`),
  } as Partial<Record<K, number | null>>
}

function optionalNullablePositiveCount<K extends string>(
  parser: string,
  obj: Record<string, unknown>,
  key: K,
  field: string,
): Partial<Record<K, number | null>> {
  return optionalNullableCount(parser, obj, key, field, { positive: true })
}

function parseExecutionWarning(parser: string, value: unknown, field: string): ExecutionWarning {
  const obj = expectPlainObject(parser, value, field)
  return {
    code: expectString(parser, obj.code, `${field}.code`),
    node_id: optionalNullableString(parser, obj, "node_id"),
    reason: optionalNullableString(parser, obj, "reason"),
  }
}

function parseExecutionMetrics(
  parser: string,
  value: unknown,
  field: string,
): ExecutionMetrics {
  const obj = expectPlainObject(parser, value, field)
  const admission = optionalNullableObject(parser, obj, "admission")
  const estimatedBytes = parseOptionalNullableNonNegativeNumber(obj, "estimated_bytes", field)
  const rawEstimatedBytes = parseOptionalNullableNonNegativeNumber(obj, "raw_estimated_bytes", field)
  const calibrationFactor = parseOptionalNullableNonNegativeNumber(obj, "estimate_calibration_factor_basis_points", field)
  const admissionBasis = obj.estimate_admission_basis === undefined || obj.estimate_admission_basis === null
    ? null
    : expectStringLiteral(parser, obj.estimate_admission_basis, `${field}.estimate_admission_basis`, ESTIMATE_ADMISSION_BASES)
  validateCalibratedEstimateEvidence(field, estimatedBytes, rawEstimatedBytes, calibrationFactor, admissionBasis)
  return {
    schema_version: optionalNumber(parser, obj, "schema_version", 1),
    operation: optionalString(parser, obj, "operation"),
    profile: optionalString(parser, obj, "profile"),
    job_id: optionalNullableString(parser, obj, "job_id"),
    status: optionalNullableString(parser, obj, "status"),
    terminal_reason: optionalNullableString(parser, obj, "terminal_reason"),
    stage_count: optionalNumber(parser, obj, "stage_count"),
    retained_stage_count: optionalNumber(parser, obj, "retained_stage_count"),
    truncated_stage_count: optionalNumber(parser, obj, "truncated_stage_count"),
    stages_truncated: optionalBoolean(parser, obj, "stages_truncated"),
    total_elapsed_ms: optionalNumber(parser, obj, "total_elapsed_ms"),
    node_elapsed_ms: optionalNumberRecord(parser, obj, "node_elapsed_ms"),
    stage_elapsed_ms: optionalNumberRecord(parser, obj, "stage_elapsed_ms"),
    rss_start_bytes: optionalNullableNumber(parser, obj, "rss_start_bytes"),
    rss_end_bytes: optionalNullableNumber(parser, obj, "rss_end_bytes"),
    rss_delta_bytes: optionalNullableNumber(parser, obj, "rss_delta_bytes"),
    rss_peak_bytes: optionalNullableNumber(parser, obj, "rss_peak_bytes"),
    max_rss_bytes: optionalNullableNumber(parser, obj, "max_rss_bytes"),
    n_collects: optionalNumber(parser, obj, "n_collects"),
    n_checkpoints: optionalNumber(parser, obj, "n_checkpoints"),
    memory_pressure_event_count: optionalNumber(parser, obj, "memory_pressure_event_count"),
    retained_memory_pressure_event_count: optionalNumber(parser, obj, "retained_memory_pressure_event_count"),
    truncated_memory_pressure_event_count: optionalNumber(parser, obj, "truncated_memory_pressure_event_count"),
    memory_pressure_events_truncated: optionalBoolean(parser, obj, "memory_pressure_events_truncated"),
    memory_limit_bytes: optionalNullableNumber(parser, obj, "memory_limit_bytes"),
    memory_baseline_bytes: optionalNullableNumber(parser, obj, "memory_baseline_bytes"),
    rss_limit_bytes: optionalNullableNumber(parser, obj, "rss_limit_bytes"),
    streamability: obj.streamability === undefined
      ? null
      : expectNullableStringLiteral(parser, obj.streamability, `${field}.streamability`, ["streaming", "materialising"]),
    streamability_evidence: obj.streamability_evidence === undefined
      ? { state: "unavailable", total_count: null, items: [] }
      : parseStreamabilityEvidence(obj.streamability_evidence, `${field}.streamability_evidence`),
    column_widths: obj.column_widths === undefined
      ? { state: "available", total_count: 0, items: [] }
      : parseColumnWidths(obj.column_widths, `${field}.column_widths`),
    requested_column_width_total: parseOptionalNullableNonNegativeNumber(obj, "requested_column_width_total", field),
    physically_scanned_column_width_total: parseOptionalNullableNonNegativeNumber(obj, "physically_scanned_column_width_total", field),
    cache_proof: parseExecutionCacheProof(obj.cache_proof, `${field}.cache_proof`),
    bytes_read: parseOptionalNullableNonNegativeNumber(obj, "bytes_read", field),
    bytes_written: parseOptionalNullableNonNegativeNumber(obj, "bytes_written", field),
    estimated_bytes: estimatedBytes,
    raw_estimated_bytes: rawEstimatedBytes,
    estimate_calibration_factor_basis_points: calibrationFactor,
    estimate_admission_basis: admissionBasis,
    observed_peak_rss_bytes: parseOptionalNullableNonNegativeNumber(obj, "observed_peak_rss_bytes", field),
    observed_peak_rss_growth_bytes: parseOptionalNullableNonNegativeNumber(obj, "observed_peak_rss_growth_bytes", field),
    checkpoint_count: parseOptionalNonNegativeNumber(obj, "checkpoint_count", field),
    chunk_count: parseOptionalNonNegativeNumber(obj, "chunk_count", field),
    admission: admission === null
      ? null
      : parseExecutionAdmission(parser, admission, `${field}.admission`),
    execution_strategy: parseExecutionStrategyDiagnostic(obj.execution_strategy),
    stages: optionalArray(parser, obj, "stages", (item, itemField) =>
      parseExecutionStageMetrics(parser, item, itemField),
    ),
    memory_pressure_events: optionalArray(parser, obj, "memory_pressure_events", (item, itemField) =>
      parseExecutionMemoryPressureEvent(parser, item, itemField),
    ),
    input_preparation: optionalArray(parser, obj, "input_preparation", (item, itemField) =>
      parseInputPreparationRecord(parser, item, itemField),
    ),
    shared_snapshot_seeds: optionalArray(parser, obj, "shared_snapshot_seeds", (item, itemField) =>
      parseSharedSnapshotSeed(parser, item, itemField),
    ),
    shared_snapshot_captures: optionalArray(parser, obj, "shared_snapshot_captures", (item, itemField) =>
      parseSharedSnapshotCapture(parser, item, itemField),
    ),
    shared_snapshot_capture_skips: optionalArray(
      parser,
      obj,
      "shared_snapshot_capture_skips",
      (item, itemField) => parseSharedSnapshotCaptureSkip(parser, item, itemField),
    ),
    warnings: optionalArray(parser, obj, "warnings", (item, itemField) =>
      parseExecutionWarning(parser, item, itemField),
    ),
    ...(obj.training_write_strategy === undefined
      ? {}
      : {
          training_write_strategy: expectNullableString(
            parser,
            obj.training_write_strategy,
            `${field}.training_write_strategy`,
          ),
        }),
    ...optionalNullablePositiveCount(parser, obj, "training_write_input_slices", field),
    ...(obj.training_write_native_reason === undefined
      ? {}
      : {
          training_write_native_reason: expectNullableString(
            parser,
            obj.training_write_native_reason,
            `${field}.training_write_native_reason`,
          ),
        }),
    ...(obj.training_write_blocking_operator === undefined
      ? {}
      : {
          training_write_blocking_operator: expectNullableString(
            parser,
            obj.training_write_blocking_operator,
            `${field}.training_write_blocking_operator`,
          ),
        }),
    ...(obj.data_output_write_strategy === undefined
      ? {}
      : {
          data_output_write_strategy: expectNullableString(
            parser,
            obj.data_output_write_strategy,
            `${field}.data_output_write_strategy`,
          ),
        }),
    ...optionalNullablePositiveCount(parser, obj, "data_output_write_input_slices", field),
    ...(obj.data_output_write_native_reason === undefined
      ? {}
      : {
          data_output_write_native_reason: expectNullableString(
            parser,
            obj.data_output_write_native_reason,
            `${field}.data_output_write_native_reason`,
          ),
        }),
  }
}

function expectNullableStringLiteral<T extends string>(
  parser: string,
  value: unknown,
  field: string,
  allowed: readonly T[],
): T | null {
  if (value === null) return null
  return expectStringLiteral(parser, value, field, allowed)
}

function expectNonNegativeMetricNumber(value: unknown, field: string): number {
  return expectInteger(value, field, true)
}

function parseOptionalNullableNonNegativeNumber(
  obj: Record<string, unknown>,
  key: string,
  field: string,
): number | null {
  const value = obj[key]
  if (value === undefined || value === null) return null
  return expectNonNegativeMetricNumber(value, `${field}.${key}`)
}

function parseOptionalNonNegativeNumber(
  obj: Record<string, unknown>,
  key: string,
  field: string,
): number {
  const value = obj[key]
  return value === undefined ? 0 : expectNonNegativeMetricNumber(value, `${field}.${key}`)
}

function parseStreamabilityEvidence(value: unknown, field: string): ExecutionStreamabilityEvidence {
  const obj = expectPlainObject("execution metrics", value, field)
  const state = expectStringLiteral("execution metrics", obj.state, `${field}.state`, ["available", "unavailable", "truncated"])
  const items = expectArray("execution metrics", obj.items, `${field}.items`).map((item, index) =>
    expectString("execution metrics", item, `${field}.items[${index}]`),
  )
  if (items.length > 32) throw new Error(`execution metrics: ${field} exceeds its 32-item cap`)
  const totalCount = obj.total_count === null ? null : expectInteger(obj.total_count, `${field}.total_count`, true)
  if (state === "unavailable") {
    if (totalCount !== null || items.length !== 0) throw new Error(`execution metrics: unavailable ${field} is inconsistent`)
  } else if (totalCount === null || (state === "available" && totalCount !== items.length) || (state === "truncated" && totalCount <= items.length)) {
    throw new Error(`execution metrics: ${field} count is inconsistent`)
  }
  for (let index = 1; index < items.length; index += 1) {
    if (compareUnicode(items[index - 1], items[index]) >= 0) throw new Error(`execution metrics: ${field} must be sorted and unique`)
  }
  return { state, total_count: totalCount, items }
}

function parseColumnWidths(value: unknown, field: string): ExecutionColumnWidths {
  const obj = expectPlainObject("execution metrics", value, field)
  const state = expectStringLiteral("execution metrics", obj.state, `${field}.state`, ["available", "truncated"])
  const items = expectArray("execution metrics", obj.items, `${field}.items`).map((item, index): ExecutionColumnWidth => {
    const itemObj = expectPlainObject("execution metrics", item, `${field}.items[${index}]`)
    const nullableWidth = (key: string): number | null => {
      const itemValue = itemObj[key]
      return itemValue === null ? null : expectNonNegativeMetricNumber(itemValue, `${field}.items[${index}].${key}`)
    }
    return {
      node_id: expectString("execution metrics", itemObj.node_id, `${field}.items[${index}].node_id`),
      input_width: nullableWidth("input_width"),
      output_width: nullableWidth("output_width"),
      requested_width: nullableWidth("requested_width"),
      physically_scanned_width: nullableWidth("physically_scanned_width"),
    }
  })
  if (items.length > 128) throw new Error(`execution metrics: ${field} exceeds its 128-item cap`)
  const totalCount = expectInteger(obj.total_count, `${field}.total_count`, true)
  if ((state === "available" && totalCount !== items.length) || (state === "truncated" && totalCount <= items.length)) {
    throw new Error(`execution metrics: ${field} count is inconsistent`)
  }
  for (let index = 1; index < items.length; index += 1) {
    if (compareUnicode(items[index - 1].node_id, items[index].node_id) >= 0) throw new Error(`execution metrics: ${field} must be sorted and unique`)
  }
  return { state, total_count: totalCount, items }
}

function parseExecutionCacheProof(value: unknown, field: string): ExecutionCacheProof {
  const obj = expectPlainObject("execution metrics", value, field)
  const reasons = expectPlainObject("execution metrics", obj.miss_reason_counts, `${field}.miss_reason_counts`)
  const missReasonCounts = {
    metadata_source_mismatch: expectNonNegativeMetricNumber(reasons.metadata_source_mismatch, `${field}.miss_reason_counts.metadata_source_mismatch`),
    artifact_integrity_schema_failure: expectNonNegativeMetricNumber(reasons.artifact_integrity_schema_failure, `${field}.miss_reason_counts.artifact_integrity_schema_failure`),
    unreadable_artifact: expectNonNegativeMetricNumber(reasons.unreadable_artifact, `${field}.miss_reason_counts.unreadable_artifact`),
    proof_unavailable: expectNonNegativeMetricNumber(reasons.proof_unavailable, `${field}.miss_reason_counts.proof_unavailable`),
  }
  const misses = expectNonNegativeMetricNumber(obj.misses, `${field}.misses`)
  if (misses !== Object.values(missReasonCounts).reduce((total, count) => total + count, 0)) {
    throw new Error(`execution metrics: ${field}.misses does not equal its closed reason-count total`)
  }
  return {
    hits: expectNonNegativeMetricNumber(obj.hits, `${field}.hits`),
    misses,
    direct_fallbacks: expectNonNegativeMetricNumber(obj.direct_fallbacks, `${field}.direct_fallbacks`),
    miss_reason_counts: missReasonCounts,
  }
}

const STRATEGY_STATUS = {
  projected: "projected",
  "schema-all-except": "projected",
  "full-width-admitted-eager": "admitted_eager",
  "unprojected-streaming-boundary": "boundary",
  "materialisation-boundary": "boundary",
  "full-width-conservative": "warned",
  unsupported: "rejected",
  "not-planned": "not_planned",
} as const

const DETAIL_STATES = ["available", "unavailable", "truncated"] as const
const ESTIMATE_ADMISSION_BASES = ["provided", "projected_columns", "complete_width_fallback"] as const

function validateCalibratedEstimateEvidence(
  field: string,
  estimatedBytes: number | null | undefined,
  rawEstimatedBytes: number | null | undefined,
  factorBasisPoints: number | null | undefined,
  admissionBasis: string | null | undefined,
): void {
  const evidence = [rawEstimatedBytes, factorBasisPoints, admissionBasis]
  if (!evidence.some((value) => value !== null && value !== undefined)) return
  if (
    estimatedBytes === null || estimatedBytes === undefined ||
    rawEstimatedBytes === null || rawEstimatedBytes === undefined ||
    factorBasisPoints === null || factorBasisPoints === undefined ||
    admissionBasis === null || admissionBasis === undefined
  ) {
    throw new Error(`${field}: calibrated estimate evidence must be complete`)
  }
  if (factorBasisPoints < 10_000) {
    throw new Error(`${field}: estimate calibration factor cannot reduce an estimate`)
  }
  if (factorBasisPoints > 80_000) {
    throw new Error(`${field}: estimate calibration factor exceeds the supported cap`)
  }
  const expected = (
    BigInt(rawEstimatedBytes) * BigInt(factorBasisPoints) + 9_999n
  ) / 10_000n
  if (BigInt(estimatedBytes) !== expected) {
    throw new Error(`${field}: calibrated estimate bytes do not match raw bytes and factor`)
  }
}

export function expectInteger(value: unknown, field: string, nonNegative = false): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || (nonNegative && value < 0)) {
    throw new Error(`execution strategy: expected ${field} to be a ${nonNegative ? "non-negative " : ""}integer`)
  }
  return value
}

function compareUnicode(left: string, right: string): number {
  const leftPoints = Array.from(left)
  const rightPoints = Array.from(right)
  for (let index = 0; index < Math.min(leftPoints.length, rightPoints.length); index += 1) {
    const difference = leftPoints[index].codePointAt(0)! - rightPoints[index].codePointAt(0)!
    if (difference !== 0) return difference
  }
  return leftPoints.length - rightPoints.length
}

function compareTuple(left: (number | string)[], right: (number | string)[]): number {
  for (let index = 0; index < left.length; index += 1) {
    const a = left[index]
    const b = right[index]
    const difference = typeof a === "number" && typeof b === "number" ? a - b : compareUnicode(String(a), String(b))
    if (difference !== 0) return difference
  }
  return 0
}

function executionStrategyValidationError(): Error {
  const issue = validateExecutionStrategyDiagnostic.errors?.[0]
  if (issue === undefined) {
    return new Error(
      "execution strategy: generated validator rejected the payload without an error",
    )
  }
  const missing = issue.params.missingProperty
  const path = `${issue.instancePath}${
    issue.keyword === "required" && typeof missing === "string"
      ? `/${missing}`
      : ""
  }` || "/"
  return new Error(
    `execution strategy: invalid contract at ${path}: ${issue.keyword}`,
  )
}

function parseDiagnosticCollection<TInput, TOutput>(
  collection: {
    state: "available" | "unavailable" | "truncated"
    total_count: number | null
    items: TInput[]
  },
  name: string,
  projectItem: (item: TInput) => TOutput,
  sortKey: (item: TOutput) => (number | string)[],
): ExecutionStrategyBoundedCollection<TOutput> {
  const { state, total_count: totalCount } = collection
  const items = collection.items.map(projectItem)
  if (state === "unavailable") {
    if (totalCount !== null || items.length !== 0) throw new Error(`execution strategy: unavailable ${name} is inconsistent`)
  } else if (totalCount === null || (state === "available" && totalCount !== items.length) || (state === "truncated" && totalCount <= items.length)) {
    throw new Error(`execution strategy: ${name} count is inconsistent`)
  }
  for (let index = 1; index < items.length; index += 1) {
    if (compareTuple(sortKey(items[index - 1]), sortKey(items[index])) > 0) {
      throw new Error(`execution strategy: ${name} are not in canonical order`)
    }
  }
  return { state, total_count: totalCount, items }
}

function projectionCause(cause: ExecutionStrategyProjectionCause): ExecutionStrategyProjectionCause {
  return {
    node_id: cause.node_id,
    operator: cause.operator,
    kind: cause.kind,
    reason_code: cause.reason_code,
    message: cause.message,
    total_count: cause.total_count,
    ...(cause.parent_node_id === undefined ? {} : { parent_node_id: cause.parent_node_id }),
    ...(cause.operation === undefined ? {} : { operation: cause.operation }),
  }
}

/** Parses the additive V1 strategy diagnostic. Unknown versions are unavailable. */
export function parseExecutionStrategyDiagnostic(value: unknown): ExecutionStrategyDiagnostic | null {
  if (value === undefined || value === null) return null
  const obj = expectPlainObject("execution strategy", value)
  if (expectInteger(obj.schema_version, "schema_version") !== EXECUTION_STRATEGY_SCHEMA_VERSION) return null
  if (!validateExecutionStrategyDiagnostic(obj)) {
    throw executionStrategyValidationError()
  }

  const { strategy, status } = obj
  if (status !== STRATEGY_STATUS[strategy]) throw new Error("execution strategy: status does not match strategy")
  const boundaries = parseDiagnosticCollection(
    obj.boundaries,
    "boundaries",
    (item): ExecutionStrategyBoundary => ({
      topological_rank: item.topological_rank,
      node_id: item.node_id,
      operator: item.operator,
      boundary_kind: item.boundary_kind,
    }),
    (item) => [item.topological_rank, item.node_id, item.operator, item.boundary_kind],
  )
  const reasons = parseDiagnosticCollection(
    obj.reasons,
    "reasons",
    (item): ExecutionStrategyReason => ({
      reason_code: item.reason_code,
      topological_rank: item.topological_rank ?? null,
      node_id: item.node_id ?? null,
      operator: item.operator ?? null,
      ...(item.message === undefined ? {} : { message: item.message }),
      ...(item.parent_node_id === undefined ? {} : { parent_node_id: item.parent_node_id }),
    }),
    (item) => [item.topological_rank ?? Infinity, item.node_id ?? "", item.reason_code, item.operator ?? ""],
  )
  const provenance = parseDiagnosticCollection(
    obj.provenance,
    "provenance",
    (item): ExecutionStrategyProvenance => ({
      column: item.column,
      origin_kind: item.origin_kind,
      ...(item.source_node_id === undefined ? {} : { source_node_id: item.source_node_id }),
      ...(item.source_column === undefined ? {} : { source_column: item.source_column }),
    }),
    (item) => [item.column, item.origin_kind, item.source_node_id ?? "", item.source_column ?? ""],
  )
  const detailState = obj.detail_state
  const expectedDetailState = [boundaries.state, reasons.state, provenance.state].reduce((worst, state) => DETAIL_STATES.indexOf(state) > DETAIL_STATES.indexOf(worst) ? state : worst)
  if (detailState !== expectedDetailState) throw new Error("execution strategy: detail_state is inconsistent")
  validateCalibratedEstimateEvidence(
    "execution strategy",
    obj.estimated_peak_bytes,
    obj.raw_estimated_peak_bytes,
    obj.estimate_calibration_factor_basis_points,
    obj.estimate_admission_basis,
  )
  return {
    schema_version: EXECUTION_STRATEGY_SCHEMA_VERSION,
    status,
    strategy,
    profile: obj.profile,
    boundedness: obj.boundedness,
    reason_code: obj.reason_code,
    detail_state: detailState,
    boundaries,
    reasons,
    provenance,
    ...(obj.blocking_node_id === undefined ? {} : { blocking_node_id: obj.blocking_node_id }),
    ...(obj.blocking_operator === undefined ? {} : { blocking_operator: obj.blocking_operator }),
    ...(obj.remediation === undefined ? {} : { remediation: obj.remediation }),
    ...(obj.estimated_peak_bytes === undefined ? {} : { estimated_peak_bytes: obj.estimated_peak_bytes }),
    ...(obj.raw_estimated_peak_bytes === undefined ? {} : { raw_estimated_peak_bytes: obj.raw_estimated_peak_bytes }),
    ...(obj.estimate_calibration_factor_basis_points === undefined ? {} : { estimate_calibration_factor_basis_points: obj.estimate_calibration_factor_basis_points }),
    ...(obj.estimate_admission_basis === undefined ? {} : { estimate_admission_basis: obj.estimate_admission_basis }),
    ...(obj.headroom_bytes === undefined ? {} : { headroom_bytes: obj.headroom_bytes }),
    ...(obj.assumptions === undefined ? {} : { assumptions: [...obj.assumptions] }),
    ...(obj.projection_cause === undefined
      ? {}
      : { projection_cause: obj.projection_cause === null ? null : projectionCause(obj.projection_cause) }),
  }
}

export function optionalExecutionMetrics(
  parser: string,
  obj: Record<string, unknown>,
  key = "execution_metrics",
): ExecutionMetrics | null {
  const value = obj[key]
  if (value === undefined || value === null) return null
  return parseExecutionMetrics(parser, value, `field \`${key}\``)
}

// ---------------------------------------------------------------------------
// Pipeline graph + preview contracts
// ---------------------------------------------------------------------------

const PIPELINE_NODE_TYPE_VALUES = Object.values(PIPELINE_NODE_TYPES)

function isKnownPipelineNode(value: unknown): boolean {
  if (!isPlainObject(value)) return false
  if (
    value.type !== undefined &&
    (
      typeof value.type !== "string" ||
      !PIPELINE_NODE_TYPE_VALUES.some((nodeType) => nodeType === value.type)
    )
  ) return false
  if (value.data !== undefined) {
    const data = value.data
    if (!isPlainObject(data)) return false
    if (
      data.nodeType !== undefined &&
      (
        typeof data.nodeType !== "string" ||
        !PIPELINE_NODE_TYPE_VALUES.some((nodeType) => nodeType === data.nodeType)
      )
    ) return false
  }
  return true
}

function parsePipelineNode(value: unknown, index: number): Node {
  const parser = "parsePipelineResponse"
  const field = `nodes[${index}]`
  const node = expectPlainObject(parser, value, `field \`${field}\``)
  if (node.type !== undefined) {
    expectStringLiteral(
      parser,
      node.type,
      `field \`${field}.type\``,
      PIPELINE_NODE_TYPE_VALUES,
    )
  }
  if (node.data !== undefined) {
    const data = expectPlainObject(parser, node.data, `field \`${field}.data\``)
    if (data.nodeType !== undefined) {
      expectStringLiteral(
        parser,
        data.nodeType,
        `field \`${field}.data.nodeType\``,
        PIPELINE_NODE_TYPE_VALUES,
      )
    }
  }
  return node as unknown as Node
}

export function isPipelineResponse(value: unknown): value is PipelineResponse {
  if (!isPlainObject(value)) return false
  if (!Array.isArray(value.nodes)) return false
  if (!value.nodes.every(isKnownPipelineNode)) return false
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
  if (!Array.isArray(value.preserved_blocks) || !value.preserved_blocks.every((item) => typeof item === "string")) return false
  if (
    value.source_revision !== null
    && (typeof value.source_revision !== "string" || value.source_revision.trim() === "")
  ) return false
  return true
}

export function parsePipelineResponse(value: unknown): PipelineResponse {
  // Integrity metadata is required for all graph documents.
  const obj = expectPlainObject("parsePipelineResponse", value)
  const nodes = expectArray("parsePipelineResponse", obj.nodes, "field `nodes`")
    .map(parsePipelineNode)
  const edges = expectArray("parsePipelineResponse", obj.edges, "field `edges`")

  return {
    preserved_blocks: parseStringArray("parsePipelineResponse", obj.preserved_blocks, "preserved_blocks"),
    source_revision: expectNullableNonBlankString("parsePipelineResponse", obj.source_revision, "source_revision"),
    nodes,
    edges: edges as PipelineEdge[],
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
    throw new Error(`${parser}: invalid field \`${key}\`: ${message}`, { cause: error })
  }
}

export function parseSavePipelineResponse(value: unknown): SavePipelineResponse {
  const obj = expectPlainObject("parseSavePipelineResponse", value)
  return {
    source_revision: expectNonBlankString("parseSavePipelineResponse", obj.source_revision, "source_revision"),
    document_fingerprint: expectNonBlankString("parseSavePipelineResponse", obj.document_fingerprint, "document_fingerprint"),
    status: optionalString("parseSavePipelineResponse", obj, "status", "saved"),
    file: expectString("parseSavePipelineResponse", obj.file, "field `file`"),
    pipeline_name: expectString("parseSavePipelineResponse", obj.pipeline_name, "field `pipeline_name`"),
    warnings: optionalStringArray("parseSavePipelineResponse", obj, "warnings"),
    git_sha: optionalNullableString("parseSavePipelineResponse", obj, "git_sha"),
    identity_required: optionalBoolean("parseSavePipelineResponse", obj, "identity_required"),
    uncaptured: optionalStringArray("parseSavePipelineResponse", obj, "uncaptured"),
  }
}

export function parseSubmodelCreateResponse(value: unknown): SubmodelCreateResponse {
  const obj = expectPlainObject("parseSubmodelCreateResponse", value)
  return {
    status: optionalString("parseSubmodelCreateResponse", obj, "status", "ok"),
    submodel_file: expectNonBlankString("parseSubmodelCreateResponse", obj.submodel_file, "submodel_file"),
    parent_file: expectNonBlankString("parseSubmodelCreateResponse", obj.parent_file, "parent_file"),
    graph: parseNestedPipelineResponse("parseSubmodelCreateResponse", obj, "graph"),
    source_revision: expectNonBlankString("parseSubmodelCreateResponse", obj.source_revision, "source_revision"),
  }
}

export function parseNodeInstanceCreateResponse(value: unknown): NodeInstanceCreateResponse {
  const parser = "parseNodeInstanceCreateResponse"
  const obj = expectPlainObject(parser, value)
  return {
    status: expectNonBlankString(parser, obj.status, "status"),
    graph: parseNestedPipelineResponse(parser, obj, "graph"),
    owner_name: expectNonBlankString(parser, obj.owner_name, "owner_name"),
    instance_name: expectNonBlankString(parser, obj.instance_name, "instance_name"),
    definition_id: expectNonBlankString(parser, obj.definition_id, "definition_id"),
    source_revision: expectNonBlankString(parser, obj.source_revision, "source_revision"),
  }
}

export function parseSubmodelGraphResponse(value: unknown): SubmodelGraphResponse {
  const obj = expectPlainObject("parseSubmodelGraphResponse", value)
  return {
    status: optionalString("parseSubmodelGraphResponse", obj, "status", "ok"),
    submodel_name: expectNonBlankString(
      "parseSubmodelGraphResponse",
      obj.submodel_name,
      "submodel_name",
    ),
    graph: parseNestedPipelineResponse("parseSubmodelGraphResponse", obj, "graph"),
    submodel_file: expectNonBlankString("parseSubmodelGraphResponse", obj.submodel_file, "submodel_file"),
    definition_id: expectNonBlankString(
      "parseSubmodelGraphResponse",
      obj.definition_id,
      "definition_id",
    ),
  }
}

export function parseDissolveSubmodelResponse(value: unknown): DissolveSubmodelResponse {
  const obj = expectPlainObject("parseDissolveSubmodelResponse", value)
  for (const removedField of [
    "submodel_file_deleted",
    "retained_submodel_file",
  ] as const) {
    if (Object.hasOwn(obj, removedField)) {
      throw new Error(
        `parseDissolveSubmodelResponse: field \`${removedField}\` is no longer supported`,
      )
    }
  }
  return {
    status: optionalString("parseDissolveSubmodelResponse", obj, "status", "ok"),
    graph: parseNestedPipelineResponse("parseDissolveSubmodelResponse", obj, "graph"),
    source_revision: expectNonBlankString("parseDissolveSubmodelResponse", obj.source_revision, "source_revision"),
    instance_id: expectNonBlankString(
      "parseDissolveSubmodelResponse",
      obj.instance_id,
      "instance_id",
    ),
    definition_id: expectNonBlankString(
      "parseDissolveSubmodelResponse",
      obj.definition_id,
      "definition_id",
    ),
  }
}

/** Parse `port_previews`: each frame's preview, by its label. */
function optionalPortPreviews(parser: string, obj: Record<string, unknown>, key: string): Record<string, PortPreview> {
  const value = obj[key]
  if (value === undefined) return {}
  const record = expectPlainObject(parser, value, `field \`${key}\``)
  return Object.fromEntries(
    Object.entries(record).map(([label, port]) => {
      const item = expectPlainObject(parser, port, `field \`${key}.${label}\``)
      return [
        label,
        {
          row_count: optionalNumber(parser, item, "row_count"),
          columns: optionalArray(parser, item, "columns", parseColumnInfo),
          preview: optionalPlainObjectArray(parser, item, "preview"),
          preview_truncated: optionalBoolean(parser, item, "preview_truncated"),
        },
      ]
    }),
  )
}

export function parsePreviewNodeResponse(value: unknown): PreviewNodeResponse {
  const obj = expectPlainObject("parsePreviewNodeResponse", value)
  return {
    status: expectStringLiteral(
      "parsePreviewNodeResponse",
      obj.status,
      "field `status`",
      NODE_STATUS_VALUES,
    ),
    node_id: expectString("parsePreviewNodeResponse", obj.node_id, "field `node_id`"),
    row_count: optionalNumber("parsePreviewNodeResponse", obj, "row_count"),
    column_count: optionalNumber("parsePreviewNodeResponse", obj, "column_count"),
    columns: optionalArray("parsePreviewNodeResponse", obj, "columns", parseColumnInfo),
    available_columns: optionalArray("parsePreviewNodeResponse", obj, "available_columns", parseColumnInfo),
    frame_columns: optionalArrayRecord(
      "parsePreviewNodeResponse",
      obj,
      "frame_columns",
      parseColumnInfo,
    ),
    preview: optionalPlainObjectArray("parsePreviewNodeResponse", obj, "preview"),
    preview_columns: optionalStringArray("parsePreviewNodeResponse", obj, "preview_columns"),
    preview_row_count: optionalNumber("parsePreviewNodeResponse", obj, "preview_row_count"),
    preview_row_limit: optionalNullableNumber("parsePreviewNodeResponse", obj, "preview_row_limit"),
    preview_truncated: optionalBoolean("parsePreviewNodeResponse", obj, "preview_truncated"),
    port_previews: optionalPortPreviews("parsePreviewNodeResponse", obj, "port_previews"),
    error: optionalNullableString("parsePreviewNodeResponse", obj, "error"),
    error_line: optionalNullableNumber("parsePreviewNodeResponse", obj, "error_line"),
    timing_ms: optionalNumber("parsePreviewNodeResponse", obj, "timing_ms"),
    memory_bytes: optionalNumber("parsePreviewNodeResponse", obj, "memory_bytes"),
    timings: optionalArray("parsePreviewNodeResponse", obj, "timings", parseNodeTiming),
    memory: optionalArray("parsePreviewNodeResponse", obj, "memory", parseNodeMemory),
    schema_warnings: optionalArray("parsePreviewNodeResponse", obj, "schema_warnings", parseSchemaWarning),
    node_statuses: optionalNodeStatusRecord("parsePreviewNodeResponse", obj, "node_statuses"),
    node_columns: optionalArrayRecord(
      "parsePreviewNodeResponse",
      obj,
      "node_columns",
      parseColumnInfo,
    ),
    node_available_columns: optionalArrayRecord(
      "parsePreviewNodeResponse",
      obj,
      "node_available_columns",
      parseColumnInfo,
    ),
    node_schema_warnings: optionalArrayRecord(
      "parsePreviewNodeResponse",
      obj,
      "node_schema_warnings",
      parseSchemaWarning,
    ),
    node_frame_columns: optionalNestedArrayRecord(
      "parsePreviewNodeResponse",
      obj,
      "node_frame_columns",
      parseColumnInfo,
    ),
    execution_metrics: optionalExecutionMetrics("parsePreviewNodeResponse", obj, "execution_metrics"),
    seed_plan: optionalArray("parsePreviewNodeResponse", obj, "seed_plan", parsePreviewSeedPlanEntry),
  }
}

const PREVIEW_SEED_KINDS = ["seeded", "captured"] as const

export function parsePreviewSeedPlanEntry(value: unknown, field: string): PreviewSeedPlanEntry {
  const parser = "parsePreviewNodeResponse"
  const obj = expectPlainObject(parser, value, field)
  if (obj.port_label !== null && obj.port_label !== undefined) {
    throw new Error(`${parser}: expected ${field}.port_label to be null`)
  }
  return {
    node_id: expectNonBlankString(parser, obj.node_id, `${field}.node_id`),
    port_label: null,
    node_label: expectString(parser, obj.node_label, `${field}.node_label`),
    identity_digest: expectNonBlankString(parser, obj.identity_digest, `${field}.identity_digest`),
    generation_id: expectNonBlankString(parser, obj.generation_id, `${field}.generation_id`),
    columns:
      obj.columns === null ? null : parseStringArray(parser, obj.columns, `${field}.columns`),
    created_at: expectNonBlankString(parser, obj.created_at, `${field}.created_at`),
    kind: expectStringLiteral(parser, obj.kind, `${field}.kind`, PREVIEW_SEED_KINDS),
  }
}

export function parsePreviewInputsResponse(value: unknown): PreviewInputsResponse {
  const obj = expectPlainObject("parsePreviewInputsResponse", value)
  return {
    input_node_ids: parseStringArray(
      "parsePreviewInputsResponse",
      obj.input_node_ids,
      "field `input_node_ids`",
    ),
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
    not_computable_reason: optionalNullableString("parseTraceResponse", obj, "not_computable_reason"),
    result_source: optionalNullableString("parseTraceResponse", obj, "result_source"),
    input_values: optionalNullableObject("parseTraceResponse", { input_values: obj.input_values }, "input_values") ?? {},
    taken_branch: optionalNullableString("parseTraceResponse", obj, "taken_branch"),
    taken_branch_index: optionalNullableNumber("parseTraceResponse", obj, "taken_branch_index"),
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
): NonNullable<NonNullable<TraceStep["calculation"]>["expression_chain"]> {
  return parseArray("parseTraceResponse", value, field, (item, itemField) => {
    const obj = expectPlainObject("parseTraceResponse", item, itemField)
    return {
      expression_text: expectString("parseTraceResponse", obj.expression_text, `${itemField}.expression_text`),
      target_column: expectString("parseTraceResponse", obj.target_column, `${itemField}.target_column`),
      ...(obj.substituted_text === undefined ? {} : {
        substituted_text: expectString("parseTraceResponse", obj.substituted_text, `${itemField}.substituted_text`),
      }),
      ...(obj.result_value === undefined ? {} : { result_value: obj.result_value }),
      not_computable_reason: optionalNullableString("parseTraceResponse", obj, "not_computable_reason"),
      result_source: optionalNullableString("parseTraceResponse", obj, "result_source"),
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
    not_computable_reason: optionalNullableString("parseTraceResponse", obj, "not_computable_reason"),
    result_source: optionalNullableString("parseTraceResponse", obj, "result_source"),
    input_sources: obj.input_sources === undefined || obj.input_sources === null
      ? null
      : parseTraceInputSources(obj.input_sources, `${field}.input_sources`),
  }
}

function parseTraceColumnSource(value: unknown, field: string): TraceColumnSource {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    node_id: expectString("parseTraceResponse", obj.node_id, `${field}.node_id`),
    column: expectString("parseTraceResponse", obj.column, `${field}.column`),
    before_code: expectBoolean("parseTraceResponse", obj.before_code, `${field}.before_code`),
  }
}

function parseTraceColumnRead(value: unknown, field: string): TraceColumnRead {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    column: expectString("parseTraceResponse", obj.column, `${field}.column`),
    sources: parseArray("parseTraceResponse", obj.sources, `${field}.sources`, parseTraceColumnSource),
  }
}

function parseTraceColumnDerivation(value: unknown, field: string): TraceColumnDerivation {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  if (!("result_value" in obj)) {
    throw new Error(`parseTraceResponse: expected ${field}.result_value, got missing`)
  }
  return {
    column: expectString("parseTraceResponse", obj.column, `${field}.column`),
    expression_text: expectNullableString("parseTraceResponse", obj.expression_text, `${field}.expression_text`),
    substituted_text: expectNullableString("parseTraceResponse", obj.substituted_text, `${field}.substituted_text`),
    result_value: obj.result_value,
    not_computable_reason: expectNullableString("parseTraceResponse", obj.not_computable_reason, `${field}.not_computable_reason`),
    result_source: expectNullableString("parseTraceResponse", obj.result_source, `${field}.result_source`),
    reads: obj.reads === null
      ? null
      : parseArray("parseTraceResponse", obj.reads, `${field}.reads`, parseTraceColumnRead),
    error: expectNullableString("parseTraceResponse", obj.error, `${field}.error`),
    error_type: expectNullableString("parseTraceResponse", obj.error_type, `${field}.error_type`),
  }
}

function parseTraceStep(value: unknown, field: string): TraceStep {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  const expression = obj.expression === undefined || obj.expression === null ? null : parseTraceExpression(obj.expression, `${field}.expression`)
  const calculation = obj.calculation === undefined || obj.calculation === null ? null : parseTraceCalculation(obj.calculation, `${field}.calculation`)
  const node_detail = obj.node_detail === undefined || obj.node_detail === null ? null : expectPlainObject("parseTraceResponse", obj.node_detail, `${field}.node_detail`)

  return {
    node_id: expectString("parseTraceResponse", obj.node_id, `${field}.node_id`),
    node_name: expectString("parseTraceResponse", obj.node_name, `${field}.node_name`),
    node_type: expectString("parseTraceResponse", obj.node_type, `${field}.node_type`),
    schema_diff: parseTraceSchemaDiff(obj.schema_diff, `${field}.schema_diff`),
    input_values: obj.input_values === undefined ? {} : expectPlainObject("parseTraceResponse", obj.input_values, `${field}.input_values`),
    output_values: obj.output_values === undefined ? {} : expectPlainObject("parseTraceResponse", obj.output_values, `${field}.output_values`),
    topological_rank: expectNonNegativeTraceInteger(obj.topological_rank, `${field}.topological_rank`),
    column_relevant: obj.column_relevant === undefined ? true : expectBoolean("parseTraceResponse", obj.column_relevant, `${field}.column_relevant`),
    contributed_columns: parseStringArray("parseTraceResponse", obj.contributed_columns, `${field}.contributed_columns`),
    derivations: parseArray("parseTraceResponse", obj.derivations, `${field}.derivations`, parseTraceColumnDerivation),
    expression,
    calculation,
    node_detail,
    row_lineage_type: optionalNullableString("parseTraceResponse", obj, "row_lineage_type"),
    snapshot_generation_id: optionalNullableString("parseTraceResponse", obj, "snapshot_generation_id"),
    identical_row_count: parseIdenticalRowCount(obj.identical_row_count, `${field}.identical_row_count`),
  }
}

function parseIdenticalRowCount(value: unknown, field: string): number | null {
  if (value === undefined || value === null) return null
  const parsed = expectNonNegativeTraceInteger(value, field)
  if (parsed < 2) {
    throw new Error(`parseTraceResponse: expected ${field} to be at least 2`)
  }
  return parsed
}

function parseWaterfallEntry(value: unknown, field: string): WaterfallEntry {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    label: expectString("parseTraceResponse", obj.label, `${field}.label`),
    operation: expectStringLiteral("parseTraceResponse", obj.operation, `${field}.operation`, [
      "base",
      "multiply",
      "add",
    ]),
    value: expectNumber("parseTraceResponse", obj.value, `${field}.value`),
    delta: expectNumber("parseTraceResponse", obj.delta, `${field}.delta`),
    cumulative: expectNumber("parseTraceResponse", obj.cumulative, `${field}.cumulative`),
    default_used: expectBoolean("parseTraceResponse", obj.default_used, `${field}.default_used`),
  }
}

function parseWaterfallError(value: unknown, field: string): WaterfallError {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    error: expectString("parseTraceResponse", obj.error, `${field}.error`),
    error_type: expectString("parseTraceResponse", obj.error_type, `${field}.error_type`),
  }
}

function parseTraceCorrelationDiagnostic(value: unknown, field: string): TraceCorrelationDiagnostic {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    ...obj,
    code: expectString("parseTraceResponse", obj.code, `${field}.code`),
    severity: expectString("parseTraceResponse", obj.severity, `${field}.severity`),
    reason: expectString("parseTraceResponse", obj.reason, `${field}.reason`),
    message: expectString("parseTraceResponse", obj.message, `${field}.message`),
    node_id: optionalNullableString("parseTraceResponse", obj, "node_id"),
    child_node_id: optionalNullableString("parseTraceResponse", obj, "child_node_id"),
    match_strategy: optionalNullableString("parseTraceResponse", obj, "match_strategy"),
    match_columns: obj.match_columns === undefined ? [] : parseStringArray("parseTraceResponse", obj.match_columns, `${field}.match_columns`),
    ignored_columns: obj.ignored_columns === undefined ? [] : parseStringArray("parseTraceResponse", obj.ignored_columns, `${field}.ignored_columns`),
    matched_row_count: optionalNullableNumber("parseTraceResponse", obj, "matched_row_count"),
    matched_row_indices: obj.matched_row_indices === undefined
      ? []
      : parseArray("parseTraceResponse", obj.matched_row_indices, `${field}.matched_row_indices`, (item, itemField) =>
        expectNumber("parseTraceResponse", item, itemField)),
    seed_node_ids: obj.seed_node_ids === undefined
      ? []
      : parseStringArray("parseTraceResponse", obj.seed_node_ids, `${field}.seed_node_ids`),
  }
}

function expectNonNegativeTraceInteger(value: unknown, field: string): number {
  const parsed = expectNumber("parseTraceResponse", value, field)
  if (!Number.isInteger(parsed) || parsed < 0) {
    throw new Error(`parseTraceResponse: expected ${field} to be a non-negative integer`)
  }
  return parsed
}

function parseTraceOmission(value: unknown, field: string): TraceOmission {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  const reason = expectString("parseTraceResponse", obj.reason, `${field}.reason`)
  if (reason.length === 0) {
    throw new Error(`parseTraceResponse: expected ${field}.reason to be non-empty`)
  }
  return {
    node_id: expectString("parseTraceResponse", obj.node_id, `${field}.node_id`),
    node_name: expectString("parseTraceResponse", obj.node_name, `${field}.node_name`),
    node_type: expectString("parseTraceResponse", obj.node_type, `${field}.node_type`),
    topological_rank: expectNonNegativeTraceInteger(obj.topological_rank, `${field}.topological_rank`),
    reason,
    diagnostic_index: expectNonNegativeTraceInteger(obj.diagnostic_index, `${field}.diagnostic_index`),
  }
}

function parseTraceGeneratedAt(value: unknown, field: string): string {
  const timestamp = expectString("parseTraceResponse", value, field)
  if (
    Number.isNaN(Date.parse(timestamp)) ||
    !/(?:Z|[+-]00:00)$/.test(timestamp)
  ) {
    throw new Error(`parseTraceResponse: expected ${field} to be a UTC ISO-8601 timestamp`)
  }
  return timestamp
}

function parseTraceResult(value: unknown, field: string): TraceResult {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  let waterfall: TraceResult["waterfall"] = null
  if (obj.waterfall !== undefined && obj.waterfall !== null) {
    waterfall = Array.isArray(obj.waterfall)
      ? parseArray("parseTraceResponse", obj.waterfall, `${field}.waterfall`, parseWaterfallEntry)
      : parseWaterfallError(obj.waterfall, `${field}.waterfall`)
  }
  const steps = obj.steps === undefined
    ? []
    : parseArray("parseTraceResponse", obj.steps, `${field}.steps`, parseTraceStep)
  const omissions = parseArray(
    "parseTraceResponse",
    obj.omissions,
    `${field}.omissions`,
    parseTraceOmission,
  )
  const correlationDiagnostics = parseArray(
    "parseTraceResponse",
    obj.correlation_diagnostics,
    `${field}.correlation_diagnostics`,
    parseTraceCorrelationDiagnostic,
  )
  for (const omission of omissions) {
    const diagnostic = correlationDiagnostics[omission.diagnostic_index]
    if (!diagnostic) {
      throw new Error(
        `parseTraceResponse: omission for ${omission.node_id} references missing diagnostic ${omission.diagnostic_index}`,
      )
    }
    if (diagnostic.node_id !== omission.node_id) {
      throw new Error(
        `parseTraceResponse: omission for ${omission.node_id} references a diagnostic for ${diagnostic.node_id ?? "no node"}`,
      )
    }
  }

  return {
    target_node_id: expectString("parseTraceResponse", obj.target_node_id, `${field}.target_node_id`),
    row_index: expectNumber("parseTraceResponse", obj.row_index, `${field}.row_index`),
    column: optionalNullableString("parseTraceResponse", obj, "column"),
    output_value: obj.output_value,
    steps,
    omissions,
    row_id_column: optionalNullableString("parseTraceResponse", obj, "row_id_column"),
    row_id_value: obj.row_id_value,
    total_nodes_in_pipeline: optionalNumber("parseTraceResponse", obj, "total_nodes_in_pipeline"),
    nodes_in_trace: optionalNumber("parseTraceResponse", obj, "nodes_in_trace"),
    execution_ms: optionalNumber("parseTraceResponse", obj, "execution_ms"),
    waterfall,
    correlation_diagnostics: correlationDiagnostics,
    generated_at: parseTraceGeneratedAt(obj.generated_at, `${field}.generated_at`),
    pipeline_source: expectNullableString("parseTraceResponse", obj.pipeline_source, `${field}.pipeline_source`),
    execution_origin: expectStringLiteral(
      "parseTraceResponse",
      obj.execution_origin,
      `${field}.execution_origin`,
      ["fresh_execution", "trace_cache"],
    ),
  }
}

export function parseTraceResponse(value: unknown): TraceResponse {
  const obj = expectPlainObject("parseTraceResponse", value)
  return {
    status: expectString("parseTraceResponse", obj.status, "field `status`"),
    trace: parseTraceResult(obj.trace, "field `trace`"),
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

export function parsePreviewProgressResponse(value: unknown): PreviewProgressResponse {
  const p = "parsePreviewProgressResponse"
  const obj = expectPlainObject(p, value)
  const count = (field: "done" | "total") =>
    obj[field] === null || obj[field] === undefined ? null : expectNumber(p, obj[field], `field \`${field}\``)
  return {
    request_id: expectString(p, obj.request_id, "field `request_id`"),
    phase: expectStringLiteral(p, obj.phase, "field `phase`", ["preparing", "running"]),
    done: count("done"),
    total: count("total"),
    label: obj.label === undefined ? null : expectNullableString(p, obj.label, "field `label`"),
  }
}
// ---------------------------------------------------------------------------
// Explore contracts
// ---------------------------------------------------------------------------

const EXPLORE_PIVOT_MEMBER_KINDS = [
  "null",
  "string",
  "boolean",
  "integer",
  "float",
  "nan",
  "date",
  "datetime",
  "time",
  "decimal",
] as const
const PIVOT_INTEGER_PATTERN = /^-?(?:0|[1-9][0-9]*)$/
const PIVOT_DECIMAL_PATTERN = /^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:E[+-]?[0-9]+)?$/
const PIVOT_DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/
const PIVOT_TIME_PATTERN = /^(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})?$/

function expectFiniteNumber(parser: string, value: unknown, field: string): number {
  const parsed = expectNumber(parser, value, field)
  if (!Number.isFinite(parsed)) throw new Error(`${parser}: expected ${field} to be finite`)
  return parsed
}

function expectSafeInteger(parser: string, value: unknown, field: string): number {
  const parsed = expectFiniteNumber(parser, value, field)
  if (!Number.isSafeInteger(parsed)) {
    throw new Error(`${parser}: expected ${field} to be a safe integer`)
  }
  return parsed
}

function expectNonNegativeInteger(parser: string, value: unknown, field: string): number {
  const parsed = expectSafeInteger(parser, value, field)
  if (parsed < 0) {
    throw new Error(`${parser}: expected ${field} to be a non-negative integer`)
  }
  return parsed
}

function expectPositiveInteger(parser: string, value: unknown, field: string): number {
  const parsed = expectSafeInteger(parser, value, field)
  if (parsed < 1) {
    throw new Error(`${parser}: expected ${field} to be a positive integer`)
  }
  return parsed
}

function isValidPivotDate(value: string): boolean {
  const match = PIVOT_DATE_PATTERN.exec(value)
  if (match === null) return false
  const year = Number(match[1])
  const month = Number(match[2])
  const day = Number(match[3])
  if (year < 1) return false
  const candidate = new Date(0)
  candidate.setUTCFullYear(year, month - 1, day)
  candidate.setUTCHours(0, 0, 0, 0)
  return (
    candidate.getUTCFullYear() === year &&
    candidate.getUTCMonth() === month - 1 &&
    candidate.getUTCDate() === day
  )
}

function isValidPivotTime(value: string): boolean {
  const match = PIVOT_TIME_PATTERN.exec(value)
  if (match === null) return false
  const hour = Number(match[1])
  const minute = Number(match[2])
  const second = Number(match[3])
  if (hour > 23 || minute > 59 || second > 59) return false
  const offset = /([+-])(\d{2}):(\d{2})$/.exec(value)
  return offset === null || (Number(offset[2]) <= 23 && Number(offset[3]) <= 59)
}

function isValidPivotDateTime(value: string): boolean {
  const separator = value.indexOf("T")
  return (
    separator > 0 &&
    isValidPivotDate(value.slice(0, separator)) &&
    isValidPivotTime(value.slice(separator + 1))
  )
}

function expectPivotStringFormat(
  parser: string,
  value: unknown,
  field: string,
  description: string,
  predicate: (candidate: string) => boolean,
): string {
  const parsed = expectString(parser, value, field)
  if (!predicate(parsed)) throw new Error(`${parser}: expected ${field} to be ${description}`)
  return parsed
}

function parseExplorePivotMemberKey(value: unknown, field: string): ExplorePivotMemberKey {
  const parser = "parseExplorePivotMemberKey"
  const obj = expectPlainObject(parser, value, field)
  const kind = expectStringLiteral(
    parser,
    obj.kind,
    `${field}.kind`,
    EXPLORE_PIVOT_MEMBER_KINDS,
  )
  const memberValue = obj.value
  switch (kind) {
    case "null":
    case "nan":
      if (memberValue !== null) {
        throw new Error(`${parser}: expected ${field}.value to be null for ${kind}`)
      }
      return { kind, value: null }
    case "string":
      return { kind, value: expectString(parser, memberValue, `${field}.value`) }
    case "integer":
      return {
        kind,
        value: expectPivotStringFormat(
          parser,
          memberValue,
          `${field}.value`,
          "a canonical integer",
          (candidate) => PIVOT_INTEGER_PATTERN.test(candidate),
        ),
      }
    case "decimal":
      return {
        kind,
        value: expectPivotStringFormat(
          parser,
          memberValue,
          `${field}.value`,
          "a canonical finite decimal",
          (candidate) => PIVOT_DECIMAL_PATTERN.test(candidate),
        ),
      }
    case "date":
      return {
        kind,
        value: expectPivotStringFormat(
          parser,
          memberValue,
          `${field}.value`,
          "an ISO date",
          isValidPivotDate,
        ),
      }
    case "datetime":
      return {
        kind,
        value: expectPivotStringFormat(
          parser,
          memberValue,
          `${field}.value`,
          "an ISO datetime",
          isValidPivotDateTime,
        ),
      }
    case "time":
      return {
        kind,
        value: expectPivotStringFormat(
          parser,
          memberValue,
          `${field}.value`,
          "an ISO time",
          isValidPivotTime,
        ),
      }
    case "boolean":
      return { kind, value: expectBoolean(parser, memberValue, `${field}.value`) }
    case "float":
      return { kind, value: expectFiniteNumber(parser, memberValue, `${field}.value`) }
  }
}

// The generated explore validators own the pivot responses' structure. The UI
// narrows each member key to the value its kind carries and checks that every
// cell sits inside the declared matrix and names a declared value.
function explorePivotPathFromContract(
  path: GeneratedExplorePivotPath,
  field: string,
): ExplorePivotPath {
  return {
    ...path,
    members: path.members.map((member, index) =>
      parseExplorePivotMemberKey(member, `${field}.members[${index}]`),
    ),
  }
}

function explorePivotResultFromContract(
  result: GeneratedExplorePivotResult,
): ExplorePivotResult {
  const parser = "parseExplorePivotResult"
  const rowPaths = result.row_paths.map((path, index) =>
    explorePivotPathFromContract(path, `result.row_paths[${index}]`),
  )
  const columnPaths = result.column_paths.map((path, index) =>
    explorePivotPathFromContract(path, `result.column_paths[${index}]`),
  )
  const valueIds = new Set(result.values.map((identity) => identity.id))
  for (const cell of result.cells) {
    if (cell.row_index >= rowPaths.length || cell.column_index >= columnPaths.length) {
      throw new Error(`${parser}: pivot cell index is outside the declared matrix`)
    }
    if (!valueIds.has(cell.value_id)) {
      throw new Error(`${parser}: pivot cell references an unknown value id`)
    }
  }
  return {
    ...result,
    row_paths: rowPaths,
    column_paths: columnPaths,
    execution_metrics: optionalExecutionMetrics(parser, { execution_metrics: result.execution_metrics }),
  }
}

export function explorePivotRunFromContract(
  response: GeneratedExplorePivotRunResponse,
): ExplorePivotRunResponse {
  return {
    ...response,
    result: response.result === null ? null : explorePivotResultFromContract(response.result),
  }
}

export function explorePivotStatusFromContract(
  response: GeneratedExplorePivotStatusResponse,
): ExplorePivotStatusResponse {
  return {
    ...response,
    result: response.result === null ? null : explorePivotResultFromContract(response.result),
    execution_metrics: optionalExecutionMetrics(
      "parseExplorePivotStatusResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

export function writeOutputFromContract(
  response: GeneratedWriteOutputResponse,
): WriteOutputResponse {
  return {
    ...response,
    execution_metrics: optionalExecutionMetrics(
      "parseWriteOutputResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

export function nodeDataStatusFromContract(
  response: GeneratedNodeDataStatusResponse,
): NodeDataStatusResponse {
  return {
    ...response,
    execution_metrics: optionalExecutionMetrics(
      "parseNodeDataStatusResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

export function explorePivotMembersFromContract(
  response: GeneratedExplorePivotMembersResponse,
): ExplorePivotMembersResponse {
  return {
    ...response,
    members: response.members.map((member, index) => ({
      ...member,
      key: parseExplorePivotMemberKey(member.key, `members[${index}].key`),
    })),
  }
}

// ---------------------------------------------------------------------------
// Optimiser contracts
// ---------------------------------------------------------------------------

// The generated optimiser validators own the responses' structure, every typed
// frontier point and factor-table row included. The one frontier guard adds what
// JSON Schema cannot say: one summary per point (the UI applies a point's
// summary by index), every point of one mode, and every point's and summary's
// constraint-keyed maps holding exactly the frontier's constraint names.
function requireConstraintNames(
  keys: Record<string, unknown>,
  constraintNames: readonly string[],
  field: string,
): void {
  const names = Object.keys(keys)
  const exact = names.length === constraintNames.length
    && constraintNames.every((name) => Object.hasOwn(keys, name))
  if (!exact) {
    throw new Error(
      `parseOptimiserStatusResponse: expected ${field} to hold exactly the constraint names `
      + `[${constraintNames.join(", ")}], got [${names.join(", ")}]`,
    )
  }
}

function frontierFromContract(
  frontier: GeneratedOptimiserFrontierResponse,
  field: string,
): FrontierResponse {
  if (frontier.point_summaries.length !== frontier.points.length) {
    throw new Error(
      `parseOptimiserStatusResponse: expected ${field}.point_summaries to hold one summary per point, got ${frontier.point_summaries.length} for ${frontier.points.length} points`,
    )
  }
  const modes = [...new Set(frontier.points.map((point) => point.mode))]
  if (modes.length > 1) {
    throw new Error(
      `parseOptimiserStatusResponse: expected every ${field}.points entry to be of one mode, got ${modes.join(", ")}`,
    )
  }
  const names = frontier.constraint_names
  frontier.points.forEach((point, index) => {
    for (const map of ["thresholds", "bounds", "totals", "lambdas"] as const) {
      requireConstraintNames(point[map], names, `${field}.points[${index}].${map}`)
    }
  })
  frontier.point_summaries.forEach((summary, index) => {
    for (const map of ["constraints", "effective_bounds", "lambdas"] as const) {
      requireConstraintNames(summary[map], names, `${field}.point_summaries[${index}].${map}`)
    }
  })
  return frontier
}

export function optimiserStatusFromContract(
  response: GeneratedOptimiserStatusResponse,
): OptimiserStatusResponse {
  const result = response.result
  return {
    ...response,
    result: result === null
      ? null
      : {
          ...result,
          frontier: result.frontier === null ? null : frontierFromContract(result.frontier, "result.frontier"),
        },
    frontier: response.frontier === null ? null : frontierFromContract(response.frontier, "frontier"),
    execution_metrics: optionalExecutionMetrics(
      "parseOptimiserStatusResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

export function frontierStatusFromContract(
  response: GeneratedOptimiserFrontierStatusResponse,
): FrontierStatusResponse {
  return {
    ...response,
    result: response.result === null ? null : frontierFromContract(response.result, "result"),
    execution_metrics: optionalExecutionMetrics(
      "parseFrontierStatusResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

export function frontierAutoRangeStatusFromContract(
  response: GeneratedOptimiserFrontierAutoRangeStatusResponse,
): FrontierAutoRangeStatusResponse {
  return {
    ...response,
    execution_metrics: optionalExecutionMetrics(
      "parseFrontierAutoRangeStatusResponse",
      { execution_metrics: response.execution_metrics },
    ),
  }
}

// ---------------------------------------------------------------------------
// Databricks / cache / git contracts
// ---------------------------------------------------------------------------

export function parseJsonCacheSchemaInferenceResponse(value: unknown): { tables: Array<Record<string, unknown>> } {
  const obj = expectPlainObject("parseJsonCacheSchemaInferenceResponse", value)
  return { tables: parsePlainObjectArray("parseJsonCacheSchemaInferenceResponse", obj.tables, "field `tables`") }
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
