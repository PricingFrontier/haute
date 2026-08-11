/**
 * Runtime parsers for values that cross the JSON / DOM boundary.
 *
 * These helpers fail loudly when backend payloads drift away from the
 * shapes the UI actually consumes, instead of letting the app explode
 * later with "cannot read property X of undefined".
 */

import type { Node } from "@xyflow/react"

import type {
  DatabricksCatalogsResponse,
  DatabricksSchemasResponse,
  DatabricksTablesResponse,
  DatabricksWarehousesResponse,
  DissolveSubmodelResponse,
  ExecutionAdmission,
  ExecutionMemoryPressureEvent,
  ExecutionMetrics,
  ExecutionColumnWidth,
  ExecutionColumnWidths,
  ExecutionStreamabilityEvidence,
  ExecutionStrategyDiagnostic,
  ExecutionStrategyBoundary,
  ExecutionStrategyBoundedCollection,
  ExecutionStrategyProvenance,
  ExecutionStrategyReason,
  ExecutionStageMetrics,
  ExploreCacheReport,
  ExploreCategoricalColumnProfile,
  ExploreColumnStat,
  ExploreDataQualityIssue,
  ExploreDataQualitySummary,
  ExploreDistinctValueCount,
  ExploreOverviewSummary,
  ExploreRunResponse,
  ExploreStatusResponse,
  ExplorePivotCell,
  ExplorePivotFailure,
  ExplorePivotMemberKey,
  ExplorePivotMemberOption,
  ExplorePivotMembersResponse,
  ExplorePivotPath,
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
  ExplorePivotValueIdentity,
  FrontierAutoRangeResponse,
  FrontierAutoRangeStartResponse,
  FrontierAutoRangeStatusResponse,
  FrontierStatusResponse,
  FrontierPoint,
  FrontierResponse,
  FrontierSelectResponse,
  GitArchiveResponse,
  GitDeleteBranchResponse,
  GitCommitResponse,
  GitMilestoneEntry,
  GitMilestonesResponse,
  GitCommitRef,
  GitCommitContext,
  GitMoveResponse,
  GitFileChange,
  GitLedgerSave,
  GitLedgerSavesResponse,
  GitManagedBranch,
  GitWorkingBranchesResponse,
  GitRestoreResponse,
  GitUndeleteResponse,
  GitCreateWorkingBranchResponse,
  GitPrefs,
  GitBranchAwayResponse,
  GitFastForwardResponse,
  GitGraphResponse,
  GitMilestoneFork,
  GitRemote,
  GitRemoteLeg,
  GitRemotesResponse,
  GitPushRejection,
  GitPushResponse,
  GitSetIdentityResponse,
  GitBindStorageResponse,
  GitForkStorageResponse,
  GitStorageBind,
  GitStorageClaim,
  GitUpstreamStatus,
  GitSetWorkingBranchResponse,
  GitStorageSync,
  GitWorkingBranchResponse,
  IoCapabilitiesResponse,
  IoCapabilityGroup,
  OutputDestinationResponse,
  IoFieldCapability,
  IoFormatCapability,
  IoInputCapability,
  IoOutputCapability,
  InputCacheBuildResponse,
  InputCacheCancelResponse,
  InputCacheGeneration,
  InputCacheJobStatusResponse,
  InputCacheProgress,
  InputCacheSnapshotResponse,
  JsonCacheBuildResponse,
  FileListItem,
  JsonCacheProgressResponse,
  JsonCacheStatusResponse,
  MlflowCheckResponse,
  MlflowExperiment,
  MlflowLogResponse,
  MlflowModel,
  MlflowModelVersion,
  MlflowRun,
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
  WriteOutputResponse,
  SubmodelCreateResponse,
  SubmodelGraphResponse,
  TraceResponse,
  UtilityDeleteResponse,
  UtilityFile,
  UtilityListResponse,
  UtilityReadResponse,
  UtilityWriteResult,
} from "../api/types"
import { JOB_STATUS_VALUES } from "../api/types"
import {
  PIPELINE_NODE_TYPES,
  type BackendNodeStatus,
  type ColumnInfo,
  type PipelineEdge,
} from "./node"
import type {
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

export function optionalNumberRecord(
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

function parseSchemaWarning(value: unknown, field: string): { column: string; status: string } {
  const obj = expectPlainObject("parsePreviewNodeResponse", value, field)
  return {
    column: expectString("parsePreviewNodeResponse", obj.column, `${field}.column`),
    status: expectString("parsePreviewNodeResponse", obj.status, `${field}.status`),
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

function parseExecutionMetrics(
  parser: string,
  value: unknown,
  field: string,
): ExecutionMetrics {
  const obj = expectPlainObject(parser, value, field)
  const admission = optionalNullableObject(parser, obj, "admission")
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
    bytes_read: parseOptionalNullableNonNegativeNumber(obj, "bytes_read", field),
    bytes_written: parseOptionalNullableNonNegativeNumber(obj, "bytes_written", field),
    estimated_bytes: parseOptionalNullableNonNegativeNumber(obj, "estimated_bytes", field),
    observed_peak_rss_bytes: parseOptionalNullableNonNegativeNumber(obj, "observed_peak_rss_bytes", field),
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

const STRATEGY_STATUS = {
  projected: "projected",
  "schema-all-except": "projected",
  "full-width-admitted-eager": "admitted_eager",
  "unprojected-streaming-boundary": "boundary",
  "materialisation-boundary": "boundary",
  unsupported: "rejected",
  "not-planned": "not_planned",
} as const

const DETAIL_STATES = ["available", "unavailable", "truncated"] as const

export function expectInteger(value: unknown, field: string, nonNegative = false): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || (nonNegative && value < 0)) {
    throw new Error(`execution strategy: expected ${field} to be a ${nonNegative ? "non-negative " : ""}integer`)
  }
  return value
}

function expectOptionalNullableDiagnosticString(obj: Record<string, unknown>, key: string): string | null | undefined {
  const value = obj[key]
  if (value === undefined || value === null || typeof value === "string") return value
  throw new Error(`execution strategy: expected ${key} to be a string or null`)
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

function parseDiagnosticCollection<T>(
  value: unknown,
  name: string,
  cap: number,
  parseItem: (value: unknown, field: string) => T,
  sortKey: (item: T) => (number | string)[],
): ExecutionStrategyBoundedCollection<T> {
  const obj = expectPlainObject("execution strategy", value, name)
  const state = expectStringLiteral("execution strategy", obj.state, `${name}.state`, DETAIL_STATES)
  const items = expectArray("execution strategy", obj.items, `${name}.items`).map((item, index) => parseItem(item, `${name}.items[${index}]`))
  if (items.length > cap) throw new Error(`execution strategy: ${name} exceeds its ${cap}-item cap`)
  const totalCount = obj.total_count === null ? null : expectInteger(obj.total_count, `${name}.total_count`, true)
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

/** Parses the additive V1 strategy diagnostic. Unknown versions are unavailable. */
export function parseExecutionStrategyDiagnostic(value: unknown): ExecutionStrategyDiagnostic | null {
  if (value === undefined || value === null) return null
  const obj = expectPlainObject("execution strategy", value)
  if (expectInteger(obj.schema_version, "schema_version") !== 1) return null
  const strategy = expectStringLiteral("execution strategy", obj.strategy, "strategy", Object.keys(STRATEGY_STATUS) as (keyof typeof STRATEGY_STATUS)[])
  const status = expectStringLiteral("execution strategy", obj.status, "status", ["projected", "admitted_eager", "boundary", "rejected", "not_planned"])
  if (status !== STRATEGY_STATUS[strategy]) throw new Error("execution strategy: status does not match strategy")
  const profile = expectStringLiteral("execution strategy", obj.profile, "profile", ["preview_eager", "lazy_sink", "training_prep", "optimiser_setup", "explore_analysis", "auto_range", "deploy_live", "deploy_batch", "chunked_map_reduce"])
  const boundedness = expectStringLiteral("execution strategy", obj.boundedness, "boundedness", ["bounded", "unbounded", "unknown"])
  const reasonCode = expectString("execution strategy", obj.reason_code, "reason_code")
  const boundaries = parseDiagnosticCollection<ExecutionStrategyBoundary>(obj.boundaries, "boundaries", 32, (item, field) => {
    const itemObj = expectPlainObject("execution strategy", item, field)
    return {
      topological_rank: expectInteger(itemObj.topological_rank, `${field}.topological_rank`, true),
      node_id: expectString("execution strategy", itemObj.node_id, `${field}.node_id`),
      operator: expectString("execution strategy", itemObj.operator, `${field}.operator`),
      boundary_kind: expectStringLiteral("execution strategy", itemObj.boundary_kind, `${field}.boundary_kind`, ["unprojected-streaming-boundary", "materialisation-boundary"]),
    }
  }, (item) => [item.topological_rank, item.node_id, item.operator, item.boundary_kind])
  const reasons = parseDiagnosticCollection<ExecutionStrategyReason>(obj.reasons, "reasons", 32, (item, field) => {
    const itemObj = expectPlainObject("execution strategy", item, field)
    const message = expectOptionalNullableDiagnosticString(itemObj, "message")
    if (message !== undefined && message !== null && message.length > 512) throw new Error(`execution strategy: ${field}.message exceeds 512 characters`)
    return { reason_code: expectString("execution strategy", itemObj.reason_code, `${field}.reason_code`), topological_rank: itemObj.topological_rank === undefined || itemObj.topological_rank === null ? null : expectInteger(itemObj.topological_rank, `${field}.topological_rank`, true), node_id: expectOptionalNullableDiagnosticString(itemObj, "node_id") ?? null, operator: expectOptionalNullableDiagnosticString(itemObj, "operator") ?? null, ...(message === undefined ? {} : { message }), ...(itemObj.parent_node_id === undefined ? {} : { parent_node_id: expectOptionalNullableDiagnosticString(itemObj, "parent_node_id") }) }
  }, (item) => [item.topological_rank ?? Number.MAX_SAFE_INTEGER, item.node_id ?? "", item.reason_code, item.operator ?? ""])
  const provenance = parseDiagnosticCollection<ExecutionStrategyProvenance>(obj.provenance, "provenance", 128, (item, field) => {
    const itemObj = expectPlainObject("execution strategy", item, field)
    return { column: expectString("execution strategy", itemObj.column, `${field}.column`), origin_kind: expectStringLiteral("execution strategy", itemObj.origin_kind, `${field}.origin_kind`, ["seed", "contract", "expression", "join_key", "conservative_boundary"]), ...(itemObj.source_node_id === undefined ? {} : { source_node_id: expectOptionalNullableDiagnosticString(itemObj, "source_node_id") }), ...(itemObj.source_column === undefined ? {} : { source_column: expectOptionalNullableDiagnosticString(itemObj, "source_column") }) }
  }, (item) => [item.column, item.origin_kind, item.source_node_id ?? "", item.source_column ?? ""])
  const detailState = expectStringLiteral("execution strategy", obj.detail_state, "detail_state", DETAIL_STATES)
  const expectedDetailState = [boundaries.state, reasons.state, provenance.state].reduce((worst, state) => DETAIL_STATES.indexOf(state) > DETAIL_STATES.indexOf(worst) ? state : worst)
  if (detailState !== expectedDetailState) throw new Error("execution strategy: detail_state is inconsistent")
  const remediation = expectOptionalNullableDiagnosticString(obj, "remediation")
  if (remediation !== undefined && remediation !== null && remediation.length > 512) throw new Error("execution strategy: remediation exceeds 512 characters")
  const estimatedPeakBytes = obj.estimated_peak_bytes === undefined || obj.estimated_peak_bytes === null ? obj.estimated_peak_bytes : expectInteger(obj.estimated_peak_bytes, "estimated_peak_bytes", true)
  const headroomBytes = obj.headroom_bytes === undefined || obj.headroom_bytes === null ? obj.headroom_bytes : expectInteger(obj.headroom_bytes, "headroom_bytes", true)
  const assumptions = obj.assumptions === undefined ? undefined : expectArray("execution strategy", obj.assumptions, "assumptions").map((assumption, index) => expectString("execution strategy", assumption, `assumptions[${index}]`))
  return { schema_version: 1, status, strategy, profile, boundedness, reason_code: reasonCode, detail_state: detailState, boundaries, reasons, provenance, ...(obj.blocking_node_id === undefined ? {} : { blocking_node_id: expectOptionalNullableDiagnosticString(obj, "blocking_node_id") }), ...(obj.blocking_operator === undefined ? {} : { blocking_operator: expectOptionalNullableDiagnosticString(obj, "blocking_operator") }), ...(remediation === undefined ? {} : { remediation }), ...(estimatedPeakBytes === undefined ? {} : { estimated_peak_bytes: estimatedPeakBytes }), ...(headroomBytes === undefined ? {} : { headroom_bytes: headroomBytes }), ...(assumptions === undefined ? {} : { assumptions }) }
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
    status: optionalString("parseSavePipelineResponse", obj, "status", "saved"),
    file: expectString("parseSavePipelineResponse", obj.file, "field `file`"),
    pipeline_name: expectString("parseSavePipelineResponse", obj.pipeline_name, "field `pipeline_name`"),
    warnings: optionalStringArray("parseSavePipelineResponse", obj, "warnings"),
    git_sha: optionalNullableString("parseSavePipelineResponse", obj, "git_sha"),
    identity_required: optionalBoolean("parseSavePipelineResponse", obj, "identity_required"),
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
    expression,
    calculation,
    node_detail,
    row_lineage_type: optionalNullableString("parseTraceResponse", obj, "row_lineage_type"),
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
      ["fresh_execution", "preview_cache", "trace_cache"],
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

/** Validate a `/api/pipeline/write-output` response — brings writeOutput in line with
 *  every sibling data endpoint (preview, save, trace) that runtime-checks its
 *  wire body instead of casting it. */
export function parseWriteOutputResponse(value: unknown): WriteOutputResponse {
  const obj = expectPlainObject("parseWriteOutputResponse", value)
  return {
    status: expectString("parseWriteOutputResponse", obj.status, "field `status`"),
    message: optionalString("parseWriteOutputResponse", obj, "message"),
    row_count: optionalNumber("parseWriteOutputResponse", obj, "row_count"),
    path: optionalString("parseWriteOutputResponse", obj, "path"),
    format: optionalString("parseWriteOutputResponse", obj, "format", "parquet"),
    execution_metrics: optionalExecutionMetrics("parseWriteOutputResponse", obj),
  }
}

export function parseOutputDestinationResponse(
  value: unknown,
): OutputDestinationResponse {
  const obj = expectPlainObject("parseOutputDestinationResponse", value)
  return {
    path: expectString(
      "parseOutputDestinationResponse",
      obj.path,
      "field `path`",
    ),
    format: expectString(
      "parseOutputDestinationResponse",
      obj.format,
      "field `format`",
    ),
    suffix_mismatch: expectBoolean(
      "parseOutputDestinationResponse",
      obj.suffix_mismatch,
      "field `suffix_mismatch`",
    ),
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

const IO_INPUT_MODES = ["scan", "read"] as const
const IO_OUTPUT_MODES = ["sink", "write"] as const
const IO_FORMAT_GROUPS = ["file", "database", "lakehouse", "inline"] as const
const IO_GROUPS = ["file", "database", "lakehouse", "databricks", "inline"] as const
const IO_FIELD_KINDS = ["path", "connection", "text", "query", "table", "records"] as const
const IO_CACHE_MODES = ["direct", "snapshot"] as const
const BUILD_CLASSES = ["bounded", "admitted_eager", "unsupported"] as const
const INPUT_CACHE_PHASES = ["queued", "building", "publishing", "completed", "failed", "cancelled"] as const
const INPUT_CACHE_SNAPSHOT_STATES = ["missing", "building", "ready", "corrupt", "failed"] as const
const INPUT_CACHE_FRESHNESS = ["fresh", "stale", "unknown"] as const
function parseInputCacheStringRecord(parser: string, value: unknown, field: string): Record<string, string> {
  const obj = expectPlainObject(parser, value, field)
  return Object.fromEntries(Object.entries(obj).map(([key, item]) => [key, expectString(parser, item, `${field}.${key}`)]))
}

function parseIoInputCapability(value: unknown, field: string): IoInputCapability {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value, field)
  return { modes: parseArray(p, obj.modes, `${field}.modes`, (v, f) => expectStringLiteral(p, v, f, IO_INPUT_MODES)), arguments: parseArrayRecord(p, obj.arguments, `${field}.arguments`, (v, f) => expectString(p, v, f)), engines_missing: parseStringArray(p, obj.engines_missing, `${field}.engines_missing`), cache_mode: expectStringLiteral(p, obj.cache_mode, `${field}.cache_mode`, IO_CACHE_MODES), direct_bounded: expectBoolean(p, obj.direct_bounded, `${field}.direct_bounded`), needs_schema_when_bounded: expectBoolean(p, obj.needs_schema_when_bounded, `${field}.needs_schema_when_bounded`), snapshot_build: expectStringLiteral(p, obj.snapshot_build, `${field}.snapshot_build`, BUILD_CLASSES), cached_read: expectBoolean(p, obj.cached_read, `${field}.cached_read`) }
}

function parseIoOutputCapability(value: unknown, field: string): IoOutputCapability {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value, field)
  return { modes: parseArray(p, obj.modes, `${field}.modes`, (v, f) => expectStringLiteral(p, v, f, IO_OUTPUT_MODES)), arguments: parseArrayRecord(p, obj.arguments, `${field}.arguments`, (v, f) => expectString(p, v, f)), engines_missing: parseStringArray(p, obj.engines_missing, `${field}.engines_missing`), native_sink: expectBoolean(p, obj.native_sink, `${field}.native_sink`), eager_writer: expectBoolean(p, obj.eager_writer, `${field}.eager_writer`), publication: expectStringLiteral(p, obj.publication, `${field}.publication`, ["atomic_file", "transactional"]) }
}

function parseIoFormatCapability(value: unknown, field: string): IoFormatCapability {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value, field)
  return { name: expectString(p, obj.name, `${field}.name`), label: expectString(p, obj.label, `${field}.label`), group: expectStringLiteral(p, obj.group, `${field}.group`, IO_FORMAT_GROUPS), extensions: parseStringArray(p, obj.extensions, `${field}.extensions`), unstable: expectBoolean(p, obj.unstable, `${field}.unstable`), input: obj.input === null ? null : parseIoInputCapability(obj.input, `${field}.input`), output: obj.output === null ? null : parseIoOutputCapability(obj.output, `${field}.output`) }
}

function parseIoFieldCapability(value: unknown, field: string): IoFieldCapability {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value, field)
  return { name: expectString(p, obj.name, `${field}.name`), label: expectString(p, obj.label, `${field}.label`), kind: expectStringLiteral(p, obj.kind, `${field}.kind`, IO_FIELD_KINDS), required: expectBoolean(p, obj.required, `${field}.required`) }
}

function parseIoCapabilityGroup(value: unknown, field: string): IoCapabilityGroup {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value, field)
  return { name: expectStringLiteral(p, obj.name, `${field}.name`, IO_GROUPS), label: expectString(p, obj.label, `${field}.label`), input_available: expectBoolean(p, obj.input_available, `${field}.input_available`), output_available: expectBoolean(p, obj.output_available, `${field}.output_available`), cache_modes: parseArray(p, obj.cache_modes, `${field}.cache_modes`, (v, f) => expectStringLiteral(p, v, f, IO_CACHE_MODES)), input_fields: parseArray(p, obj.input_fields, `${field}.input_fields`, parseIoFieldCapability), output_fields: parseArray(p, obj.output_fields, `${field}.output_fields`, parseIoFieldCapability), formats: parseArray(p, obj.formats, `${field}.formats`, parseIoFormatCapability) }
}

export function parseIoCapabilitiesResponse(value: unknown): IoCapabilitiesResponse {
  const p = "parseIoCapabilitiesResponse"
  const obj = expectPlainObject(p, value)
  return { schema_version: expectSchemaVersionOne(p, obj.schema_version, "field `schema_version`"), groups: parseArray(p, obj.groups, "field `groups`", parseIoCapabilityGroup) }
}

function parseInputCacheProgress(value: unknown, field: string): InputCacheProgress {
  const p = "parseInputCacheJobStatusResponse"; const obj = expectPlainObject(p, value, field)
  return { phase: expectStringLiteral(p, obj.phase, `${field}.phase`, INPUT_CACHE_PHASES), rows: expectNumber(p, obj.rows, `${field}.rows`), batches: expectNumber(p, obj.batches, `${field}.batches`), bytes: expectNumber(p, obj.bytes, `${field}.bytes`), elapsed_seconds: expectNumber(p, obj.elapsed_seconds, `${field}.elapsed_seconds`) }
}
function parseInputCacheGeneration(value: unknown, field: string): InputCacheGeneration {
  const p = "parseInputCacheSnapshotResponse"; const obj = expectPlainObject(p, value, field)
  return { generation_id: expectString(p, obj.generation_id, `${field}.generation_id`), row_count: expectNumber(p, obj.row_count, `${field}.row_count`), column_count: expectNumber(p, obj.column_count, `${field}.column_count`), columns: parseInputCacheStringRecord(p, obj.columns, `${field}.columns`), size_bytes: expectNumber(p, obj.size_bytes, `${field}.size_bytes`), created_at: expectNumber(p, obj.created_at, `${field}.created_at`), build_class: expectStringLiteral(p, obj.build_class, `${field}.build_class`, BUILD_CLASSES) }
}
export function parseInputCacheBuildResponse(value: unknown): InputCacheBuildResponse { const p = "parseInputCacheBuildResponse"; const obj = expectPlainObject(p, value); return { schema_version: expectSchemaVersionOne(p, obj.schema_version, "field `schema_version`"), job_id: expectString(p, obj.job_id, "field `job_id`"), identity_digest: expectString(p, obj.identity_digest, "field `identity_digest`"), status: expectStringLiteral(p, obj.status, "field `status`", ["running"]), joined: expectBoolean(p, obj.joined, "field `joined`") } }
export function parseInputCacheSnapshotResponse(value: unknown): InputCacheSnapshotResponse { const p = "parseInputCacheSnapshotResponse"; const obj = expectPlainObject(p, value); return { schema_version: expectSchemaVersionOne(p, obj.schema_version, "field `schema_version`"), identity_digest: expectString(p, obj.identity_digest, "field `identity_digest`"), state: expectStringLiteral(p, obj.state, "field `state`", INPUT_CACHE_SNAPSHOT_STATES), freshness: expectStringLiteral(p, obj.freshness, "field `freshness`", INPUT_CACHE_FRESHNESS), generation: obj.generation === null ? null : parseInputCacheGeneration(obj.generation, "field `generation`") } }
export function parseInputCacheJobStatusResponse(value: unknown): InputCacheJobStatusResponse { const p = "parseInputCacheJobStatusResponse"; const obj = expectPlainObject(p, value); return { schema_version: expectSchemaVersionOne(p, obj.schema_version, "field `schema_version`"), job_id: expectString(p, obj.job_id, "field `job_id`"), identity_digest: expectString(p, obj.identity_digest, "field `identity_digest`"), status: expectStringLiteral(p, obj.status, "field `status`", JOB_STATUS_VALUES), terminal_reason: expectNullableString(p, obj.terminal_reason, "field `terminal_reason`"), message: expectString(p, obj.message, "field `message`"), refresh: expectBoolean(p, obj.refresh, "field `refresh`"), build_class: expectStringLiteral(p, obj.build_class, "field `build_class`", BUILD_CLASSES), progress: parseInputCacheProgress(obj.progress, "field `progress`"), snapshot: obj.snapshot === null ? null : parseInputCacheSnapshotResponse(obj.snapshot), error_code: expectNullableString(p, obj.error_code, "field `error_code`") } }
export function parseInputCacheCancelResponse(value: unknown): InputCacheCancelResponse { const p = "parseInputCacheCancelResponse"; const obj = expectPlainObject(p, value); return { schema_version: expectSchemaVersionOne(p, obj.schema_version, "field `schema_version`"), job_id: expectString(p, obj.job_id, "field `job_id`"), cancellation_requested: expectBoolean(p, obj.cancellation_requested, "field `cancellation_requested`"), status: expectStringLiteral(p, obj.status, "field `status`", JOB_STATUS_VALUES) } }

// ---------------------------------------------------------------------------
// Explore contracts
// ---------------------------------------------------------------------------

const EXPLORE_RUN_STATUSES = ["started", "running", "completed"] as const
const EXPLORE_COLUMN_KINDS = ["Numeric", "Text", "Temporal", "Boolean", "Nested", "Other"] as const

function parseExploreColumnStat(value: unknown, field: string): ExploreColumnStat {
  const parser = "parseExploreColumnStat"
  const obj = expectPlainObject(parser, value, field)
  return {
    name: expectString(parser, obj.name, `${field}.name`),
    dtype: expectString(parser, obj.dtype, `${field}.dtype`),
    kind: expectStringLiteral(parser, obj.kind, `${field}.kind`, EXPLORE_COLUMN_KINDS),
    null_count: expectNumber(parser, obj.null_count, `${field}.null_count`),
    nan_count: optionalNullableNumber(parser, obj, "nan_count"),
    distinct_count: expectNullableNumber(parser, obj.distinct_count, `${field}.distinct_count`),
    min_value: optionalNullableString(parser, obj, "min_value"),
    p25_value: optionalNullableString(parser, obj, "p25_value"),
    median_value: optionalNullableString(parser, obj, "median_value"),
    mean_value: optionalNullableString(parser, obj, "mean_value"),
    p75_value: optionalNullableString(parser, obj, "p75_value"),
    max_value: optionalNullableString(parser, obj, "max_value"),
    std_value: optionalNullableString(parser, obj, "std_value"),
    zero_count: optionalNullableNumber(parser, obj, "zero_count"),
    negative_count: optionalNullableNumber(parser, obj, "negative_count"),
    unique_ratio: expectNullableNumber(parser, obj.unique_ratio, `${field}.unique_ratio`),
    is_high_cardinality: expectBoolean(parser, obj.is_high_cardinality, `${field}.is_high_cardinality`),
    is_identifier_candidate: expectBoolean(parser, obj.is_identifier_candidate, `${field}.is_identifier_candidate`),
    text_min_length: expectNullableNumber(parser, obj.text_min_length, `${field}.text_min_length`),
    text_mean_length: expectNullableNumber(parser, obj.text_mean_length, `${field}.text_mean_length`),
    text_max_length: expectNullableNumber(parser, obj.text_max_length, `${field}.text_max_length`),
    temporal_span: expectNullableString(parser, obj.temporal_span, `${field}.temporal_span`),
  }
}

function parseExploreDataQualityIssue(
  value: unknown,
  field: string,
): ExploreDataQualityIssue {
  const parser = "parseExploreOverviewSummary"
  const obj = expectPlainObject(parser, value, field)
  return {
    severity: expectStringLiteral(parser, obj.severity, `${field}.severity`, ["warning", "danger"] as const),
    label: expectString(parser, obj.label, `${field}.label`),
    detail: expectString(parser, obj.detail, `${field}.detail`),
  }
}

function parseExploreDataQualitySummary(
  value: unknown,
  field: string,
): ExploreDataQualitySummary {
  const parser = "parseExploreOverviewSummary"
  const obj = expectPlainObject(parser, value, field)
  return {
    issue_count: expectNumber(parser, obj.issue_count, `${field}.issue_count`),
    issues: parseArray(parser, obj.issues, `${field}.issues`, parseExploreDataQualityIssue),
    duplicate_row_count: expectNullableNumber(parser, obj.duplicate_row_count, `${field}.duplicate_row_count`),
    duplicate_ratio: expectNullableNumber(parser, obj.duplicate_ratio, `${field}.duplicate_ratio`),
  }
}

function parseExploreDistinctValueCount(
  value: unknown,
  field: string,
): ExploreDistinctValueCount {
  const parser = "parseExploreOverviewSummary"
  const obj = expectPlainObject(parser, value, field)
  return {
    value: expectNullableString(parser, obj.value, `${field}.value`),
    count: expectNumber(parser, obj.count, `${field}.count`),
  }
}

function parseExploreCategoricalColumnProfile(
  value: unknown,
  field: string,
): ExploreCategoricalColumnProfile {
  const parser = "parseExploreOverviewSummary"
  const obj = expectPlainObject(parser, value, field)
  return {
    field: expectString(parser, obj.field, `${field}.field`),
    distinct_count: expectNullableNumber(parser, obj.distinct_count, `${field}.distinct_count`),
    expandable: expectBoolean(parser, obj.expandable, `${field}.expandable`),
    values_truncated: optionalBoolean(parser, obj, "values_truncated"),
    values: parseArray(parser, obj.values, `${field}.values`, parseExploreDistinctValueCount),
  }
}

function parseExploreOverviewSummary(value: unknown, field: string): ExploreOverviewSummary {
  const parser = "parseExploreOverviewSummary"
  const obj = expectPlainObject(parser, value, field)
  return {
    data_quality: parseExploreDataQualitySummary(obj.data_quality, `${field}.data_quality`),
    categorical_summary: parseArray(
      parser,
      obj.categorical_summary,
      `${field}.categorical_summary`,
      parseExploreCategoricalColumnProfile,
    ),
  }
}

export function parseExploreCacheReport(value: unknown): ExploreCacheReport {
  const obj = expectPlainObject("parseExploreCacheReport", value)
  return {
    status: expectStringLiteral("parseExploreCacheReport", obj.status, "field `status`", ["ok"] as const),
    node_id: expectString("parseExploreCacheReport", obj.node_id, "field `node_id`"),
    upstream_node_id: expectString("parseExploreCacheReport", obj.upstream_node_id, "field `upstream_node_id`"),
    source: expectString("parseExploreCacheReport", obj.source, "field `source`"),
    dataframe_cache_key: expectString(
      "parseExploreCacheReport",
      obj.dataframe_cache_key,
      "field `dataframe_cache_key`",
    ),
    row_count: expectNumber("parseExploreCacheReport", obj.row_count, "field `row_count`"),
    column_count: expectNumber("parseExploreCacheReport", obj.column_count, "field `column_count`"),
    generated_at: expectNumber("parseExploreCacheReport", obj.generated_at, "field `generated_at`"),
    columns: parseArray("parseExploreCacheReport", obj.columns, "field `columns`", parseExploreColumnStat),
    overview_summary: parseExploreOverviewSummary(obj.overview_summary, "field `overview_summary`"),
    execution_metrics: optionalExecutionMetrics("parseExploreCacheReport", obj, "execution_metrics"),
  }
}

export function parseExploreRunResponse(value: unknown): ExploreRunResponse {
  const obj = expectPlainObject("parseExploreRunResponse", value)
  return {
    status: expectStringLiteral("parseExploreRunResponse", obj.status, "field `status`", EXPLORE_RUN_STATUSES),
    job_id: optionalNullableString("parseExploreRunResponse", obj, "job_id"),
    cached: optionalBoolean("parseExploreRunResponse", obj, "cached"),
    message: optionalString("parseExploreRunResponse", obj, "message"),
    result: obj.result === undefined || obj.result === null ? null : parseExploreCacheReport(obj.result),
  }
}

export function parseExploreStatusResponse(value: unknown): ExploreStatusResponse {
  const obj = expectPlainObject("parseExploreStatusResponse", value)
  return {
    status: expectStringLiteral("parseExploreStatusResponse", obj.status, "field `status`", JOB_STATUS_VALUES),
    progress: optionalNumber("parseExploreStatusResponse", obj, "progress"),
    message: optionalString("parseExploreStatusResponse", obj, "message"),
    result: obj.result === undefined || obj.result === null ? null : parseExploreCacheReport(obj.result),
    terminal_reason: optionalNullableString("parseExploreStatusResponse", obj, "terminal_reason"),
    execution_metrics: optionalExecutionMetrics("parseExploreStatusResponse", obj, "execution_metrics"),
  }
}

const EXPLORE_PIVOT_RUN_STATUSES = ["started", "completed", "cache_required"] as const
const EXPLORE_PIVOT_MEMBER_STATUSES = ["ok", "cache_required", "error"] as const
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
const EXPLORE_PIVOT_AGGREGATIONS = [
  "sum",
  "count",
  "average",
  "min",
  "max",
  "median",
  "distinct_count",
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

function parseExplorePivotFailure(value: unknown, field: string): ExplorePivotFailure {
  const parser = "parseExplorePivotFailure"
  const obj = expectPlainObject(parser, value, field)
  const rawDimensions = expectPlainObject(parser, obj.dimensions, `${field}.dimensions`)
  const dimensions = Object.fromEntries(
    Object.entries(rawDimensions).map(([key, dimension]) => {
      if (typeof dimension === "string") return [key, dimension]
      return [key, expectSafeInteger(parser, dimension, `${field}.dimensions.${key}`)]
    }),
  )
  return {
    reason_code: expectString(parser, obj.reason_code, `${field}.reason_code`),
    message: expectString(parser, obj.message, `${field}.message`),
    remediation: expectString(parser, obj.remediation, `${field}.remediation`),
    dimensions,
  }
}

function parseNullablePivotFailure(
  value: unknown,
  field: string,
): ExplorePivotFailure | null {
  return value === null ? null : parseExplorePivotFailure(value, field)
}

function parseExplorePivotPath(value: unknown, field: string): ExplorePivotPath {
  const parser = "parseExplorePivotResult"
  const obj = expectPlainObject(parser, value, field)
  return {
    members: parseArray(parser, obj.members, `${field}.members`, parseExplorePivotMemberKey),
    is_grand_total: expectBoolean(parser, obj.is_grand_total, `${field}.is_grand_total`),
  }
}

function parseExplorePivotValueIdentity(
  value: unknown,
  field: string,
): ExplorePivotValueIdentity {
  const parser = "parseExplorePivotResult"
  const obj = expectPlainObject(parser, value, field)
  return {
    id: expectString(parser, obj.id, `${field}.id`),
    field: expectString(parser, obj.field, `${field}.field`),
    aggregation: expectStringLiteral(
      parser,
      obj.aggregation,
      `${field}.aggregation`,
      EXPLORE_PIVOT_AGGREGATIONS,
    ),
  }
}

function parseExplorePivotCell(value: unknown, field: string): ExplorePivotCell {
  const parser = "parseExplorePivotResult"
  const obj = expectPlainObject(parser, value, field)
  const rawValue = obj.value
  if (
    rawValue !== null &&
    typeof rawValue !== "string" &&
    typeof rawValue !== "boolean" &&
    typeof rawValue !== "number"
  ) {
    throw new Error(`${parser}: expected ${field}.value to be a scalar or null`)
  }
  return {
    row_index: expectNonNegativeInteger(parser, obj.row_index, `${field}.row_index`),
    column_index: expectNonNegativeInteger(parser, obj.column_index, `${field}.column_index`),
    value_id: expectString(parser, obj.value_id, `${field}.value_id`),
    value:
      typeof rawValue === "number"
        ? expectFiniteNumber(parser, rawValue, `${field}.value`)
        : rawValue,
  }
}

function parseExplorePivotResult(value: unknown, field: string): ExplorePivotResult {
  const parser = "parseExplorePivotResult"
  const obj = expectPlainObject(parser, value, field)
  const rowPaths = parseArray(
    parser,
    obj.row_paths,
    `${field}.row_paths`,
    parseExplorePivotPath,
  )
  const columnPaths = parseArray(
    parser,
    obj.column_paths,
    `${field}.column_paths`,
    parseExplorePivotPath,
  )
  const values = parseArray(
    parser,
    obj.values,
    `${field}.values`,
    parseExplorePivotValueIdentity,
  )
  const cells = parseArray(parser, obj.cells, `${field}.cells`, parseExplorePivotCell)
  const valueIds = new Set(values.map((identity) => identity.id))
  for (const cell of cells) {
    if (cell.row_index >= rowPaths.length || cell.column_index >= columnPaths.length) {
      throw new Error(`${parser}: pivot cell index is outside the declared matrix`)
    }
    if (!valueIds.has(cell.value_id)) {
      throw new Error(`${parser}: pivot cell references an unknown value id`)
    }
  }
  return {
    version: expectSchemaVersionOne(parser, obj.version, `${field}.version`),
    node_id: expectString(parser, obj.node_id, `${field}.node_id`),
    pivot_id: expectString(parser, obj.pivot_id, `${field}.pivot_id`),
    source: expectString(parser, obj.source, `${field}.source`),
    dataframe_cache_key: expectString(
      parser,
      obj.dataframe_cache_key,
      `${field}.dataframe_cache_key`,
    ),
    calculation_key: expectString(parser, obj.calculation_key, `${field}.calculation_key`),
    row_fields: parseArray(parser, obj.row_fields, `${field}.row_fields`, (item, itemField) =>
      expectString(parser, item, itemField),
    ),
    column_fields: parseArray(
      parser,
      obj.column_fields,
      `${field}.column_fields`,
      (item, itemField) => expectString(parser, item, itemField),
    ),
    values,
    row_paths: rowPaths,
    column_paths: columnPaths,
    cells,
    warnings: parseArray(parser, obj.warnings, `${field}.warnings`, (item, itemField) =>
      expectString(parser, item, itemField),
    ),
    generated_at: expectFiniteNumber(parser, obj.generated_at, `${field}.generated_at`),
    execution_metrics:
      obj.execution_metrics === null
        ? null
        : parseExecutionMetrics(parser, obj.execution_metrics, `${field}.execution_metrics`),
  }
}

export function parseExplorePivotRunResponse(value: unknown): ExplorePivotRunResponse {
  const parser = "parseExplorePivotRunResponse"
  const obj = expectPlainObject(parser, value)
  return {
    status: expectStringLiteral(
      parser,
      obj.status,
      "field `status`",
      EXPLORE_PIVOT_RUN_STATUSES,
    ),
    job_id: expectNullableString(parser, obj.job_id, "field `job_id`"),
    cached: expectBoolean(parser, obj.cached, "field `cached`"),
    message: expectString(parser, obj.message, "field `message`"),
    result:
      obj.result === null ? null : parseExplorePivotResult(obj.result, "field `result`"),
    failure: parseNullablePivotFailure(obj.failure, "field `failure`"),
  }
}

export function parseExplorePivotStatusResponse(value: unknown): ExplorePivotStatusResponse {
  const parser = "parseExplorePivotStatusResponse"
  const obj = expectPlainObject(parser, value)
  return {
    status: expectStringLiteral(parser, obj.status, "field `status`", JOB_STATUS_VALUES),
    progress: expectFiniteNumber(parser, obj.progress, "field `progress`"),
    message: expectString(parser, obj.message, "field `message`"),
    result:
      obj.result === null ? null : parseExplorePivotResult(obj.result, "field `result`"),
    failure: parseNullablePivotFailure(obj.failure, "field `failure`"),
    terminal_reason: expectNullableString(parser, obj.terminal_reason, "field `terminal_reason`"),
    execution_metrics:
      obj.execution_metrics === null
        ? null
        : parseExecutionMetrics(parser, obj.execution_metrics, "field `execution_metrics`"),
  }
}

function parseExplorePivotMemberOption(
  value: unknown,
  field: string,
): ExplorePivotMemberOption {
  const parser = "parseExplorePivotMembersResponse"
  const obj = expectPlainObject(parser, value, field)
  return {
    key: parseExplorePivotMemberKey(obj.key, `${field}.key`),
    label: expectString(parser, obj.label, `${field}.label`),
    count: expectNonNegativeInteger(parser, obj.count, `${field}.count`),
  }
}

export function parseExplorePivotMembersResponse(value: unknown): ExplorePivotMembersResponse {
  const parser = "parseExplorePivotMembersResponse"
  const obj = expectPlainObject(parser, value)
  return {
    status: expectStringLiteral(
      parser,
      obj.status,
      "field `status`",
      EXPLORE_PIVOT_MEMBER_STATUSES,
    ),
    field: expectNullableString(parser, obj.field, "field `field`"),
    members: parseArray(
      parser,
      obj.members,
      "field `members`",
      parseExplorePivotMemberOption,
    ),
    failure: parseNullablePivotFailure(obj.failure, "field `failure`"),
  }
}

export function parseMlflowCheckResponse(value: unknown): MlflowCheckResponse {
  const obj = expectPlainObject("parseMlflowCheckResponse", value)
  const mlflowInstalled = expectBoolean("parseMlflowCheckResponse", obj.mlflow_installed, "field `mlflow_installed`")
  const mlflowImportable = expectBoolean(
    "parseMlflowCheckResponse",
    obj.mlflow_importable,
    "field `mlflow_importable`",
  )
  return {
    mlflow_installed: mlflowInstalled,
    mlflow_importable: mlflowImportable,
    tracking_configured: expectBoolean(
      "parseMlflowCheckResponse",
      obj.tracking_configured,
      "field `tracking_configured`",
    ),
    backend: optionalString("parseMlflowCheckResponse", obj, "backend"),
    databricks_host: optionalString("parseMlflowCheckResponse", obj, "databricks_host"),
    detail: optionalString("parseMlflowCheckResponse", obj, "detail"),
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
    job_id: optionalNullableString("parseOptimiserStatusResponse", obj, "job_id"),
  }
}

export function parseFrontierStatusResponse(value: unknown): FrontierStatusResponse {
  const obj = expectPlainObject("parseFrontierStatusResponse", value)
  return {
    status: expectStringLiteral(
      "parseFrontierStatusResponse",
      obj.status,
      "field `status`",
      JOB_STATUS_VALUES,
    ),
    progress: optionalNumber("parseFrontierStatusResponse", obj, "progress"),
    message: optionalString("parseFrontierStatusResponse", obj, "message"),
    elapsed_seconds: optionalNumber("parseFrontierStatusResponse", obj, "elapsed_seconds"),
    result: obj.result == null ? null : parseFrontierResponse(obj.result, "result"),
    terminal_reason: optionalNullableString("parseFrontierStatusResponse", obj, "terminal_reason"),
    error_code: optionalNullableString("parseFrontierStatusResponse", obj, "error_code"),
    http_status_code: optionalNullableNumber("parseFrontierStatusResponse", obj, "http_status_code"),
    error_detail: obj.error_detail,
    execution_metrics: optionalExecutionMetrics("parseFrontierStatusResponse", obj, "execution_metrics"),
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

export function parseFrontierAutoRangeStartResponse(value: unknown): FrontierAutoRangeStartResponse {
  const obj = expectPlainObject("parseFrontierAutoRangeStartResponse", value)
  return {
    status: expectStringLiteral(
      "parseFrontierAutoRangeStartResponse",
      obj.status,
      "field `status`",
      ["started", "error"],
    ),
    job_id: optionalNullableString("parseFrontierAutoRangeStartResponse", obj, "job_id"),
    error: optionalNullableString("parseFrontierAutoRangeStartResponse", obj, "error"),
  }
}

export function parseFrontierAutoRangeStatusResponse(value: unknown): FrontierAutoRangeStatusResponse {
  const obj = expectPlainObject("parseFrontierAutoRangeStatusResponse", value)
  return {
    status: expectStringLiteral(
      "parseFrontierAutoRangeStatusResponse",
      obj.status,
      "field `status`",
      JOB_STATUS_VALUES,
    ),
    progress: optionalNumber("parseFrontierAutoRangeStatusResponse", obj, "progress"),
    message: optionalString("parseFrontierAutoRangeStatusResponse", obj, "message"),
    elapsed_seconds: optionalNumber("parseFrontierAutoRangeStatusResponse", obj, "elapsed_seconds"),
    result: obj.result == null ? null : parseFrontierAutoRangeResponse(obj.result),
    terminal_reason: optionalNullableString("parseFrontierAutoRangeStatusResponse", obj, "terminal_reason"),
    error_code: optionalNullableString("parseFrontierAutoRangeStatusResponse", obj, "error_code"),
    http_status_code: optionalNullableNumber("parseFrontierAutoRangeStatusResponse", obj, "http_status_code"),
    error_detail: obj.error_detail,
    execution_metrics: optionalExecutionMetrics("parseFrontierAutoRangeStatusResponse", obj, "execution_metrics"),
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
    status: expectStringLiteral("parseOptimiserStatusResponse", obj.status, "field `status`", JOB_STATUS_VALUES),
    progress: optionalNumber("parseOptimiserStatusResponse", obj, "progress"),
    message: optionalString("parseOptimiserStatusResponse", obj, "message"),
    elapsed_seconds: optionalNumber("parseOptimiserStatusResponse", obj, "elapsed_seconds"),
    result: obj.result === undefined || obj.result === null ? null : parseOptimiserSolveResult(obj.result, "field `result`"),
    frontier: obj.frontier === undefined || obj.frontier === null ? null : parseFrontierResponse(obj.frontier, "field `frontier`"),
    terminal_reason: optionalNullableString("parseOptimiserStatusResponse", obj, "terminal_reason"),
    execution_metrics: optionalExecutionMetrics("parseOptimiserStatusResponse", obj, "execution_metrics"),
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
    skipped_records: optionalNumber("parseJsonCacheBuildResponse", obj, "skipped_records"),
    skipped_rows: optionalNumberRecord("parseJsonCacheBuildResponse", obj, "skipped_rows"),
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
    skipped_records: optionalNumber("parseJsonCacheStatusResponse", obj, "skipped_records"),
    skipped_rows: optionalNumberRecord("parseJsonCacheStatusResponse", obj, "skipped_rows"),
  }
}

export function parseHauteSessionResponse(value: unknown): { ok: boolean } {
  const obj = expectPlainObject("parseHauteSessionResponse", value)
  return { ok: expectBoolean("parseHauteSessionResponse", obj.ok, "field `ok`") }
}

export function parseOutputAssembleDryRunResponse(value: unknown): { status: string; document: unknown[]; row_count: number; error?: string | null } {
  const obj = expectPlainObject("parseOutputAssembleDryRunResponse", value)
  const error = obj.error
  if (error !== undefined && error !== null && typeof error !== "string") {
    throw new Error(`parseOutputAssembleDryRunResponse: expected field \`error\` to be a string or null, got ${typeName(error)}`)
  }
  return {
    status: expectString("parseOutputAssembleDryRunResponse", obj.status, "field `status`"),
    document: expectArray("parseOutputAssembleDryRunResponse", obj.document, "field `document`"),
    row_count: expectNumber("parseOutputAssembleDryRunResponse", obj.row_count, "field `row_count`"),
    ...(error === undefined ? {} : { error }),
  }
}

export function parseJsonCacheDeleteResponse(value: unknown): { cached: boolean; data_path: string } {
  const obj = expectPlainObject("parseJsonCacheDeleteResponse", value)
  return {
    cached: expectBoolean("parseJsonCacheDeleteResponse", obj.cached, "field `cached`"),
    data_path: expectString("parseJsonCacheDeleteResponse", obj.data_path, "field `data_path`"),
  }
}

export function parseJsonCacheSchemaInferenceResponse(value: unknown): { tables: Array<Record<string, unknown>> } {
  const obj = expectPlainObject("parseJsonCacheSchemaInferenceResponse", value)
  return { tables: parsePlainObjectArray("parseJsonCacheSchemaInferenceResponse", obj.tables, "field `tables`") }
}

export function parseMlflowExperiments(value: unknown): MlflowExperiment[] {
  return parseArray("parseMlflowExperiments", value, "response", (item, field) => {
    const obj = expectPlainObject("parseMlflowExperiments", item, field)
    return {
      experiment_id: expectString("parseMlflowExperiments", obj.experiment_id, `${field}.experiment_id`),
      name: expectString("parseMlflowExperiments", obj.name, `${field}.name`),
    }
  })
}

export function parseMlflowRuns(value: unknown): MlflowRun[] {
  return parseArray("parseMlflowRuns", value, "response", (item, field) => {
    const obj = expectPlainObject("parseMlflowRuns", item, field)
    return {
      run_id: expectString("parseMlflowRuns", obj.run_id, `${field}.run_id`),
      run_name: expectString("parseMlflowRuns", obj.run_name, `${field}.run_name`),
      metrics: parseNumberRecord("parseMlflowRuns", obj.metrics, `${field}.metrics`),
      artifacts: parseStringArray("parseMlflowRuns", obj.artifacts, `${field}.artifacts`),
      ...(obj.status === undefined ? {} : { status: expectString("parseMlflowRuns", obj.status, `${field}.status`) }),
      ...(obj.start_time === undefined ? {} : { start_time: expectNullableNumber("parseMlflowRuns", obj.start_time, `${field}.start_time`) }),
      ...(obj.params === undefined ? {} : { params: parseStringRecord("parseMlflowRuns", obj.params, `${field}.params`) }),
    }
  })
}

export function parseMlflowModels(value: unknown): MlflowModel[] {
  return parseArray("parseMlflowModels", value, "response", (item, field) => {
    const obj = expectPlainObject("parseMlflowModels", item, field)
    return {
      name: expectString("parseMlflowModels", obj.name, `${field}.name`),
      latest_versions: parseArray("parseMlflowModels", obj.latest_versions, `${field}.latest_versions`, (version, versionField) => {
        const versionObj = expectPlainObject("parseMlflowModels", version, versionField)
        return {
          version: expectString("parseMlflowModels", versionObj.version, `${versionField}.version`),
          status: expectString("parseMlflowModels", versionObj.status, `${versionField}.status`),
          run_id: expectString("parseMlflowModels", versionObj.run_id, `${versionField}.run_id`),
        }
      }),
    }
  })
}

export function parseMlflowModelVersions(value: unknown): MlflowModelVersion[] {
  return parseArray("parseMlflowModelVersions", value, "response", (item, field) => {
    const obj = expectPlainObject("parseMlflowModelVersions", item, field)
    return {
      version: expectString("parseMlflowModelVersions", obj.version, `${field}.version`),
      run_id: expectString("parseMlflowModelVersions", obj.run_id, `${field}.run_id`),
      status: expectString("parseMlflowModelVersions", obj.status, `${field}.status`),
      description: expectString("parseMlflowModelVersions", obj.description, `${field}.description`),
      ...(obj.params === undefined ? {} : { params: parseStringRecord("parseMlflowModelVersions", obj.params, `${field}.params`) }),
      ...(obj.creation_timestamp === undefined ? {} : { creation_timestamp: expectNullableNumber("parseMlflowModelVersions", obj.creation_timestamp, `${field}.creation_timestamp`) }),
    }
  })
}

export function parseFileListResponse(value: unknown): { items?: FileListItem[] } {
  const obj = expectPlainObject("parseFileListResponse", value)
  if (obj.items === undefined) return {}
  return {
    items: parseArray("parseFileListResponse", obj.items, "field `items`", (item, field) => {
      const itemObj = expectPlainObject("parseFileListResponse", item, field)
      return {
        name: expectString("parseFileListResponse", itemObj.name, `${field}.name`),
        path: expectString("parseFileListResponse", itemObj.path, `${field}.path`),
        type: expectStringLiteral("parseFileListResponse", itemObj.type, `${field}.type`, ["file", "directory"]),
        ...(itemObj.size === undefined ? {} : { size: expectNullableNumber("parseFileListResponse", itemObj.size, `${field}.size`) }),
      }
    }),
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

const WORKING_BRANCH_STATES = ["git-unavailable", "no-repository", "unset", "detached", "invalid", "divergent", "ready"] as const

const STORAGE_STATES = ["unsupported", "unbound", "bound"] as const

const SYNC_STATES = ["synced", "pending", "failed"] as const

const SYNC_FAILURES = ["transport", "rejected", "config"] as const

/** Older backends omit the storage surface entirely — default to "unsupported"
 *  (hide the surface) rather than throw, and treat `sync` as absent (null). */
function parseGitStorageSync(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
): GitStorageSync | null {
  const value = obj[key]
  if (value === undefined || value === null) return null
  const syncObj = expectPlainObject(parser, value, `field \`${key}\``)
  return {
    state: expectStringLiteral(parser, syncObj.state, `field \`${key}.state\``, SYNC_STATES),
    pending: optionalNumber(parser, syncObj, "pending"),
    failure: expectNullableStringLiteral(
      parser,
      syncObj.failure === undefined ? null : syncObj.failure,
      `field \`${key}.failure\``,
      SYNC_FAILURES,
    ),
    message: optionalNullableString(parser, syncObj, "message"),
  }
}

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
    head_sha: optionalNullableString("parseGitWorkingBranchResponse", obj, "head_sha"),
    storage:
      obj.storage === undefined
        ? "unsupported"
        : expectStringLiteral("parseGitWorkingBranchResponse", obj.storage, "field `storage`", STORAGE_STATES),
    storage_remote: optionalNullableString("parseGitWorkingBranchResponse", obj, "storage_remote"),
    storage_forked_from: optionalNullableString(
      "parseGitWorkingBranchResponse",
      obj,
      "storage_forked_from",
    ),
    sync: parseGitStorageSync("parseGitWorkingBranchResponse", obj, "sync"),
    storage_bind: parseGitStorageBind("parseGitWorkingBranchResponse", obj, "storage_bind"),
  }
}

const BIND_STATES = ["idle", "running", "succeeded", "failed"] as const

const BIND_OUTCOMES = ["adopted", "restart-required"] as const

/** Older backends omit the async-bind surface — read it as absent (null)
 *  rather than throw, so a stale server still renders the rest. */
function parseGitStorageBind(
  parser: string,
  obj: Record<string, unknown>,
  key: string,
): GitStorageBind | null {
  const value = obj[key]
  if (value === undefined || value === null) return null
  const bindObj = expectPlainObject(parser, value, `field \`${key}\``)
  return {
    state: expectStringLiteral(parser, bindObj.state, `field \`${key}.state\``, BIND_STATES),
    outcome: expectNullableStringLiteral(
      parser,
      bindObj.outcome === undefined ? null : bindObj.outcome,
      `field \`${key}.outcome\``,
      BIND_OUTCOMES,
    ),
    message: optionalNullableString(parser, bindObj, "message"),
    claim: gitStorageClaimFromDetail(bindObj.claim),
    remote_url: optionalNullableString(parser, bindObj, "remote_url"),
  }
}

/** Lenient reader for the structured 409 body a claimed bind returns.
 *  Returns null when the payload is not claim-shaped (e.g. a plain-string
 *  detail from an older backend) so callers fall back to generic error text. */
export function gitStorageClaimFromDetail(value: unknown): GitStorageClaim | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null
  const obj = value as Record<string, unknown>
  if (typeof obj.app_name !== "string" || !obj.app_name) return null
  if (typeof obj.message !== "string" || !obj.message) return null
  return {
    app_name: obj.app_name,
    user: typeof obj.user === "string" && obj.user ? obj.user : null,
    refreshed_at: typeof obj.refreshed_at === "string" && obj.refreshed_at ? obj.refreshed_at : null,
    message: obj.message,
  }
}

export function parseGitForkStorageResponse(value: unknown): GitForkStorageResponse {
  const obj = expectPlainObject("parseGitForkStorageResponse", value)
  return {
    outcome: expectStringLiteral("parseGitForkStorageResponse", obj.outcome, "field `outcome`", [
      "forked",
    ] as const),
    target_url: expectString("parseGitForkStorageResponse", obj.target_url, "field `target_url`"),
    parent_url: expectString("parseGitForkStorageResponse", obj.parent_url, "field `parent_url`"),
    parent_generation: expectNumber(
      "parseGitForkStorageResponse",
      obj.parent_generation,
      "field `parent_generation`",
    ),
    message: expectString("parseGitForkStorageResponse", obj.message, "field `message`"),
  }
}

export function parseGitUpstreamStatusResponse(value: unknown): GitUpstreamStatus {
  const obj = expectPlainObject("parseGitUpstreamStatusResponse", value)
  return {
    parent_url: expectString("parseGitUpstreamStatusResponse", obj.parent_url, "field `parent_url`"),
    parent_generation: expectNumber(
      "parseGitUpstreamStatusResponse",
      obj.parent_generation,
      "field `parent_generation`",
    ),
    working: parseGitRemoteLeg(obj.working, "working"),
    ledger: parseGitRemoteLeg(obj.ledger, "ledger"),
    can_fast_forward: expectBoolean(
      "parseGitUpstreamStatusResponse",
      obj.can_fast_forward,
      "field `can_fast_forward`",
    ),
    checked_at: expectString("parseGitUpstreamStatusResponse", obj.checked_at, "field `checked_at`"),
    message: expectString("parseGitUpstreamStatusResponse", obj.message, "field `message`"),
  }
}

export function parseGitBindStorageResponse(value: unknown): GitBindStorageResponse {
  const obj = expectPlainObject("parseGitBindStorageResponse", value)
  return {
    outcome: expectStringLiteral("parseGitBindStorageResponse", obj.outcome, "field `outcome`", [
      "pending",
    ] as const),
    remote_url: expectString("parseGitBindStorageResponse", obj.remote_url, "field `remote_url`"),
    message: expectString("parseGitBindStorageResponse", obj.message, "field `message`"),
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

export function parseGitMoveResponse(value: unknown): GitMoveResponse {
  const obj = expectPlainObject("parseGitMoveResponse", value)
  return {
    sha: expectString("parseGitMoveResponse", obj.sha, "field `sha`"),
    short_sha: expectString("parseGitMoveResponse", obj.short_sha, "field `short_sha`"),
    prior_branch: expectString("parseGitMoveResponse", obj.prior_branch, "field `prior_branch`"),
    is_detached: expectBoolean("parseGitMoveResponse", obj.is_detached, "field `is_detached`"),
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
    is_root: optionalBoolean("parseGitMilestonesResponse", obj, "is_root", false),
  }
}

export function parseGitMilestonesResponse(value: unknown): GitMilestonesResponse {
  const obj = expectPlainObject("parseGitMilestonesResponse", value)
  return {
    working_branch: optionalNullableString("parseGitMilestonesResponse", obj, "working_branch"),
    entries: optionalArray("parseGitMilestonesResponse", obj, "entries", parseGitMilestoneEntry),
  }
}

export function parseGitGraphResponse(value: unknown): GitGraphResponse {
  const obj = expectPlainObject("parseGitGraphResponse", value)
  return {
    working_branch: expectNullableString("parseGitGraphResponse", obj.working_branch, "field `working_branch`"),
    order: parseStringArray("parseGitGraphResponse", obj.order, "field `order`"),
    branches: parseArray("parseGitGraphResponse", obj.branches, "field `branches`", (branch, field) => {
      const branchObj = expectPlainObject("parseGitGraphResponse", branch, field)
      return {
        name: expectString("parseGitGraphResponse", branchObj.name, `${field}.name`),
        is_archived: expectBoolean("parseGitGraphResponse", branchObj.is_archived, `${field}.is_archived`),
        is_current: expectBoolean("parseGitGraphResponse", branchObj.is_current, `${field}.is_current`),
        tip_sha: expectString("parseGitGraphResponse", branchObj.tip_sha, `${field}.tip_sha`),
        fork_point_sha: expectNullableString("parseGitGraphResponse", branchObj.fork_point_sha, `${field}.fork_point_sha`),
        fork_of: expectNullableString("parseGitGraphResponse", branchObj.fork_of, `${field}.fork_of`),
        fork_source_sha: expectNullableString("parseGitGraphResponse", branchObj.fork_source_sha, `${field}.fork_source_sha`),
        fork_credit_sha: expectNullableString("parseGitGraphResponse", branchObj.fork_credit_sha, `${field}.fork_credit_sha`),
        truncated: expectBoolean("parseGitGraphResponse", branchObj.truncated, `${field}.truncated`),
        entries: parseArray("parseGitGraphResponse", branchObj.entries, `${field}.entries`, (entry, entryField) => {
          const entryObj = expectPlainObject("parseGitGraphResponse", entry, entryField)
          return {
            sha: expectString("parseGitGraphResponse", entryObj.sha, `${entryField}.sha`),
            short_sha: expectString("parseGitGraphResponse", entryObj.short_sha, `${entryField}.short_sha`),
            message: expectString("parseGitGraphResponse", entryObj.message, `${entryField}.message`),
            timestamp: expectString("parseGitGraphResponse", entryObj.timestamp, `${entryField}.timestamp`),
            version_label: expectNullableString("parseGitGraphResponse", entryObj.version_label, `${entryField}.version_label`),
            ...(entryObj.is_root === undefined ? {} : { is_root: expectBoolean("parseGitGraphResponse", entryObj.is_root, `${entryField}.is_root`) }),
            parents: parseStringArray("parseGitGraphResponse", entryObj.parents, `${entryField}.parents`),
          }
        }),
      }
    }),
  }
}

function parseGitCommitRef(value: unknown, field: string): GitCommitRef {
  const obj = expectPlainObject("parseGitCommitContext", value, field)
  return {
    sha: expectString("parseGitCommitContext", obj.sha, `${field}.sha`),
    short_sha: expectString("parseGitCommitContext", obj.short_sha, `${field}.short_sha`),
    message: expectString("parseGitCommitContext", obj.message, `${field}.message`),
    version_label: optionalNullableString("parseGitCommitContext", obj, "version_label"),
    is_root: optionalBoolean("parseGitCommitContext", obj, "is_root", false),
  }
}

export function parseGitCommitContext(value: unknown): GitCommitContext {
  const obj = expectPlainObject("parseGitCommitContext", value)
  return {
    sha: expectString("parseGitCommitContext", obj.sha, "field `sha`"),
    short_sha: expectString("parseGitCommitContext", obj.short_sha, "field `short_sha`"),
    message: expectString("parseGitCommitContext", obj.message, "field `message`"),
    timestamp: expectString("parseGitCommitContext", obj.timestamp, "field `timestamp`"),
    is_root: optionalBoolean("parseGitCommitContext", obj, "is_root", false),
    is_milestone: optionalBoolean("parseGitCommitContext", obj, "is_milestone", false),
    version_label: optionalNullableString("parseGitCommitContext", obj, "version_label"),
    nearest_milestone: parseGitCommitRef(obj.nearest_milestone, "nearest_milestone"),
    distance: expectNumber("parseGitCommitContext", obj.distance, "field `distance`"),
    delta_from_base: optionalNullableNumber("parseGitCommitContext", obj, "delta_from_base"),
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

export function parseGitUndeleteResponse(value: unknown): GitUndeleteResponse {
  const obj = expectPlainObject("parseGitUndeleteResponse", value)
  return {
    status: expectString("parseGitUndeleteResponse", obj.status, "status"),
    branch: expectString("parseGitUndeleteResponse", obj.branch, "branch"),
  }
}

const LEG_STATUSES: ReadonlySet<string> = new Set([
  "untracked", "unknown", "synced", "ahead", "behind", "diverged",
])

function parseGitRemoteLeg(value: unknown, field: string): GitRemoteLeg {
  const obj = expectPlainObject("parseGitRemotesResponse", value, field)
  const status = expectString("parseGitRemotesResponse", obj.status, `${field}.status`)
  if (!LEG_STATUSES.has(status)) {
    throw new Error(`parseGitRemotesResponse: ${field}.status has unexpected value \`${status}\``)
  }
  return {
    status: status as GitRemoteLeg["status"],
    ahead: optionalNullableNumber("parseGitRemotesResponse", obj, "ahead"),
    behind: optionalNullableNumber("parseGitRemotesResponse", obj, "behind"),
  }
}

function parseGitRemote(value: unknown, field: string): GitRemote {
  const obj = expectPlainObject("parseGitRemotesResponse", value, field)
  return {
    name: expectString("parseGitRemotesResponse", obj.name, `${field}.name`),
    url: optionalNullableString("parseGitRemotesResponse", obj, "url"),
    working: obj.working == null
      ? null
      : parseGitRemoteLeg(obj.working, `${field}.working`),
    ledger: obj.ledger == null
      ? null
      : parseGitRemoteLeg(obj.ledger, `${field}.ledger`),
  }
}

export function parseGitRemotesResponse(value: unknown): GitRemotesResponse {
  const obj = expectPlainObject("parseGitRemotesResponse", value)
  return {
    remotes: optionalArray("parseGitRemotesResponse", obj, "remotes", parseGitRemote),
    working_branch: optionalNullableString("parseGitRemotesResponse", obj, "working_branch"),
  }
}

export function parseGitPushResponse(value: unknown): GitPushResponse {
  const obj = expectPlainObject("parseGitPushResponse", value)
  return {
    remote: expectString("parseGitPushResponse", obj.remote, "remote"),
    working_branch: expectString("parseGitPushResponse", obj.working_branch, "working_branch"),
    ledger_branch: expectString("parseGitPushResponse", obj.ledger_branch, "ledger_branch"),
    pushed_refs: optionalStringArray("parseGitPushResponse", obj, "pushed_refs"),
    default_branch: expectString("parseGitPushResponse", obj.default_branch, "default_branch"),
    bootstrapped_default: expectBoolean(
      "parseGitPushResponse",
      obj.bootstrapped_default,
      "bootstrapped_default",
    ),
  }
}

export function parseGitFastForwardResponse(value: unknown): GitFastForwardResponse {
  const obj = expectPlainObject("parseGitFastForwardResponse", value)
  return {
    remote: expectString("parseGitFastForwardResponse", obj.remote, "remote"),
    working_branch: expectString(
      "parseGitFastForwardResponse",
      obj.working_branch,
      "working_branch",
    ),
    fast_forwarded: optionalStringArray("parseGitFastForwardResponse", obj, "fast_forwarded"),
  }
}

export function parseGitBranchAwayResponse(value: unknown): GitBranchAwayResponse {
  const obj = expectPlainObject("parseGitBranchAwayResponse", value)
  return {
    working_branch: expectString(
      "parseGitBranchAwayResponse",
      obj.working_branch,
      "working_branch",
    ),
    set_aside_as: expectString("parseGitBranchAwayResponse", obj.set_aside_as, "set_aside_as"),
  }
}

/** Parse a 409 push-rejection body; non-matching discriminators return null. */
export function parseGitPushRejection(value: unknown): GitPushRejection | null {
  if (!isPlainObject(value) || value.status !== "rejected_diverged") return null
  return {
    status: "rejected_diverged",
    remote: expectString("parseGitPushRejection", value.remote, "remote"),
    working: parseGitRemoteLeg(value.working, "working"),
    ledger: value.ledger == null ? null : parseGitRemoteLeg(value.ledger, "ledger"),
    message: expectString("parseGitPushRejection", value.message, "message"),
    is_rewrite: value.is_rewrite === undefined
      ? false
      : expectBoolean("parseGitPushRejection", value.is_rewrite, "is_rewrite"),
  }
}

/** Parse a 409 milestone-fork body; non-matching discriminators return null. */
export function parseGitMilestoneFork(value: unknown): GitMilestoneFork | null {
  if (!isPlainObject(value) || value.status !== "would_fork") return null
  return {
    status: "would_fork",
    remote: expectString("parseGitMilestoneFork", value.remote, "remote"),
    working: parseGitRemoteLeg(value.working, "working"),
    message: expectString("parseGitMilestoneFork", value.message, "message"),
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
