/**
 * Runtime parsers for values that cross the JSON / DOM boundary.
 *
 * These helpers fail loudly when backend payloads drift away from the
 * shapes the UI actually consumes, instead of letting the app explode
 * later with "cannot read property X of undefined".
 */

import type { Node } from "@xyflow/react"

import { isDateTime, isDay, isTime } from "../utils/isoDate"

import {
  EXECUTION_STRATEGY_SCHEMA_VERSION,
  validateExecutionStrategyDiagnostic,
} from "../generated/api-contracts.execution-strategy-diagnostic.validators.mjs"
import type {
  ExecutionMetricsPayload,
  OptimiserFrontierAutoRangeStatusResponse as GeneratedOptimiserFrontierAutoRangeStatusResponse,
  OptimiserFrontierResponse as GeneratedOptimiserFrontierResponse,
  OptimiserStatusResponse as GeneratedOptimiserStatusResponse,
} from "../generated/api-contracts.generated"
import type {
  ExplorePivotMembersResponse as GeneratedExplorePivotMembersResponse,
  ExplorePivotPath as GeneratedExplorePivotPath,
  ExplorePivotResult as GeneratedExplorePivotResult,
  ExplorePivotRunResponse as GeneratedExplorePivotRunResponse,
  ExplorePivotStatusResponse as GeneratedExplorePivotStatusResponse,
  NodeDataStatusResponse as GeneratedNodeDataStatusResponse,
  PreviewNodeResponse as GeneratedPreviewNodeResponse,
  PipelineGraph as GeneratedPipelineGraph,
  TraceResponse as GeneratedTraceResponse,
  WriteOutputResponse as GeneratedWriteOutputResponse,
} from "../generated/api-contracts.generated"
import type {
  ExecutionMetrics,
  ExecutionStrategyDiagnostic,
  ExecutionStrategyBoundary,
  ExecutionStrategyBoundedCollection,
  ExecutionStrategyProjectionCause,
  ExecutionStrategyProvenance,
  ExecutionStrategyReason,
  ExplorePivotMemberKey,
  ExplorePivotMembersResponse,
  ExplorePivotPath,
  ExplorePivotResult,
  ExplorePivotRunResponse,
  ExplorePivotStatusResponse,
  FrontierAutoRangeStatusResponse,
  FrontierResponse,
  NodeDataStatusResponse,
  OptimiserStatusResponse,
  PreviewNodeResponse,
  WriteOutputResponse,
  TraceResponse,
} from "../api/types"
import {
  expectGeneratedContract,
  type GeneratedContractValidator,
} from "./generatedContractValidation"
import {
  PIPELINE_NODE_TYPES,
  type PipelineEdge,
  type PipelineGraph,
} from "./node"
import type {
  TraceCalculation,
  TraceExpression,
  TraceExpressionChainLink,
  TraceInputSource,
  TraceNodeDetail,
  TraceStep,
} from "./trace"

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

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


export function expectNumber(parser: string, value: unknown, field: string): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    throw new Error(`${parser}: expected ${field} to be a number, got ${value === undefined ? "missing" : typeName(value)}`)
  }
  return value
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


function parseStringArray(parser: string, value: unknown, field: string): string[] {
  return parseArray(parser, value, field, (item, itemField) => expectString(parser, item, itemField))
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

/** The shared execution-metrics module's validator, loaded beside a response group's. */
export type ExecutionMetricsValidator = GeneratedContractValidator<ExecutionMetricsPayload>

/**
 * A response's execution metrics, checked through the shared execution-metrics module, with
 * the strategy diagnostic normalised as `parseExecutionStrategyDiagnostic` gives it.
 */
export function executionMetricsFromContract(
  validate: ExecutionMetricsValidator,
  value: unknown,
): ExecutionMetrics | null {
  if (value === null || value === undefined) return null
  const metrics = expectGeneratedContract("ExecutionMetricsPayload", validate, value)
  return { ...metrics, execution_strategy: parseExecutionStrategyDiagnostic(metrics.execution_strategy) }
}

// ---------------------------------------------------------------------------
// Pipeline graph + preview contracts
// ---------------------------------------------------------------------------

const PIPELINE_NODE_TYPE_VALUES = Object.values(PIPELINE_NODE_TYPES)
















/**
 * A canonical graph as the editor's React Flow graph. The generated validators own its
 * structure; the editor adds that every node's React Flow type is a pipeline node type,
 * since it renders a node through the component registered for that type.
 */
export function pipelineGraphFromContract(graph: GeneratedPipelineGraph, field = "graph"): PipelineGraph {
  graph.nodes.forEach((node, index) => {
    if (!PIPELINE_NODE_TYPE_VALUES.some((nodeType) => nodeType === node.type)) {
      throw new Error(`${field}.nodes[${index}].type: ${JSON.stringify(node.type)} is not a pipeline node type`)
    }
  })
  return { ...graph, nodes: graph.nodes as unknown as Node[], edges: graph.edges as PipelineEdge[] }
}

/** A response that answers with a canonical graph, its graph read as the editor's. */
export function withPipelineGraph<T extends { graph: GeneratedPipelineGraph }>(
  response: T,
): Omit<T, "graph"> & { graph: PipelineGraph } {
  return { ...response, graph: pipelineGraphFromContract(response.graph) }
}

/** A preview response, its execution metrics checked through the shared module. */
export function previewFromContract(
  response: GeneratedPreviewNodeResponse,
  validateMetrics: ExecutionMetricsValidator,
): PreviewNodeResponse {
  return {
    ...response,
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
  }
}

// ---------------------------------------------------------------------------
// Trace contracts
// ---------------------------------------------------------------------------


// The generated trace validator owns the trace's structure. The server leaves a step's
// expression and calculation open, so the UI shapes them here.
export function traceFromContract(response: GeneratedTraceResponse): TraceResponse {
  return {
    ...response,
    trace: {
      ...response.trace,
      steps: response.trace.steps.map((step, index): TraceStep => {
        const field = `trace.steps[${index}]`
        return {
          ...step,
          expression: step.expression === null
            ? null
            : parseTraceExpression(step.expression, `${field}.expression`),
          calculation: step.calculation === null
            ? null
            : parseTraceCalculation(step.calculation, `${field}.calculation`),
          node_detail: step.node_detail as TraceNodeDetail | null,
        }
      }),
    },
  }
}

function parseTraceExpression(value: unknown, field: string): TraceExpression {
  const obj = expectPlainObject("parseTraceResponse", value, field)
  return {
    expression_text: expectString("parseTraceResponse", obj.expression_text, `${field}.expression_text`),
    expression_type: expectString("parseTraceResponse", obj.expression_type, `${field}.expression_type`),
    referenced_columns: obj.referenced_columns === undefined ? [] : parseStringArray("parseTraceResponse", obj.referenced_columns, `${field}.referenced_columns`),
  }
}

function parseTraceCalculation(value: unknown, field: string): TraceCalculation {
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
): TraceExpressionChainLink[] {
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














// ---------------------------------------------------------------------------
// Schema + modelling contracts
// ---------------------------------------------------------------------------


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

function expectFiniteNumber(parser: string, value: unknown, field: string): number {
  const parsed = expectNumber(parser, value, field)
  if (!Number.isFinite(parsed)) throw new Error(`${parser}: expected ${field} to be finite`)
  return parsed
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
          isDay,
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
          isDateTime,
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
          isTime,
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
  validateMetrics: ExecutionMetricsValidator,
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
    execution_metrics: executionMetricsFromContract(validateMetrics, result.execution_metrics),
  }
}

export function explorePivotRunFromContract(
  response: GeneratedExplorePivotRunResponse,
  validateMetrics: ExecutionMetricsValidator,
): ExplorePivotRunResponse {
  return {
    ...response,
    result: response.result === null ? null : explorePivotResultFromContract(response.result, validateMetrics),
  }
}

export function explorePivotStatusFromContract(
  response: GeneratedExplorePivotStatusResponse,
  validateMetrics: ExecutionMetricsValidator,
): ExplorePivotStatusResponse {
  return {
    ...response,
    result: response.result === null ? null : explorePivotResultFromContract(response.result, validateMetrics),
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
  }
}

export function writeOutputFromContract(
  response: GeneratedWriteOutputResponse,
  validateMetrics: ExecutionMetricsValidator,
): WriteOutputResponse {
  return {
    ...response,
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
  }
}

export function nodeDataStatusFromContract(
  response: GeneratedNodeDataStatusResponse,
  validateMetrics: ExecutionMetricsValidator,
): NodeDataStatusResponse {
  return {
    ...response,
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
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
  validateMetrics: ExecutionMetricsValidator,
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
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
  }
}

export function frontierAutoRangeStatusFromContract(
  response: GeneratedOptimiserFrontierAutoRangeStatusResponse,
  validateMetrics: ExecutionMetricsValidator,
): FrontierAutoRangeStatusResponse {
  return {
    ...response,
    execution_metrics: executionMetricsFromContract(validateMetrics, response.execution_metrics),
  }
}

// ---------------------------------------------------------------------------
// Databricks / cache / git contracts
// ---------------------------------------------------------------------------


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
