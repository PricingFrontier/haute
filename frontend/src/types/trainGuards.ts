/** Runtime parsers for on-demand modelling train endpoints. */

import type { EvaluationPreview, TrainEstimate, TrainResponse, TrainStatusResponse } from "../api/types"
import { JOB_STATUS_VALUES } from "../api/types"
import {
  expectArray,
  expectBoolean,
  expectExactKeys,
  expectInteger,
  expectNumber,
  expectPlainObject,
  expectSchemaVersionOne,
  expectString,
  expectStringLiteral,
  isPlainObject,
  optionalBoolean,
  optionalExecutionMetrics,
  optionalNullableNumber,
  optionalNullableObject,
  optionalNullableString,
  optionalNumber,
  optionalNumberRecord,
  optionalString,
  parseArray,
  typeName,
} from "./guards"

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
  const hasDiagnosticError = obj.error !== undefined || obj.error_type !== undefined
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    type: expectString("parseTrainResponse", obj.type, `${field}.type`),
    grid: obj.grid === undefined ? [] : parseArray("parseTrainResponse", obj.grid, `${field}.grid`, parsePdpGridPoint),
    ...(hasDiagnosticError
      ? {
          error: expectString("parseTrainResponse", obj.error, `${field}.error`),
          error_type: expectString("parseTrainResponse", obj.error_type, `${field}.error_type`),
        }
      : {}),
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

function parseTrainFeatureSelectionCollection<T>(
  value: unknown,
  field: string,
  parseItem: (value: unknown, field: string) => T,
): { state: "available" | "truncated"; total_count: number; items: T[] } {
  const obj = expectPlainObject("parseTrainFeatureSelection", value, field)
  const state = expectStringLiteral("parseTrainFeatureSelection", obj.state, `${field}.state`, ["available", "truncated"])
  const items = expectArray("parseTrainFeatureSelection", obj.items, `${field}.items`).map((item, index) =>
    parseItem(item, `${field}.items[${index}]`),
  )
  if (items.length > 128) throw new Error(`parseTrainFeatureSelection: ${field} exceeds its 128-item cap`)
  const totalCount = expectInteger(obj.total_count, `${field}.total_count`, true)
  if ((state === "available" && totalCount !== items.length) || (state === "truncated" && totalCount <= items.length)) {
    throw new Error(`parseTrainFeatureSelection: ${field} count is inconsistent`)
  }
  return { state, total_count: totalCount, items }
}

export function parseTrainFeatureSelection(value: unknown): NonNullable<TrainResponse["feature_selection"]> {
  const obj = expectPlainObject("parseTrainFeatureSelection", value)
  if (expectInteger(obj.schema_version, "schema_version") !== 1) {
    throw new Error("parseTrainFeatureSelection: unsupported schema_version")
  }
  const features = parseTrainFeatureSelectionCollection(obj.features, "features", (item, field) =>
    expectString("parseTrainFeatureSelection", item, field),
  )
  const parseExcludedColumn = (item: unknown, field: string) => {
    const itemObj = expectPlainObject("parseTrainFeatureSelection", item, field)
    return {
      column: expectString("parseTrainFeatureSelection", itemObj.column, `${field}.column`),
      reason: expectStringLiteral("parseTrainFeatureSelection", itemObj.reason, `${field}.reason`, ["target", "weight", "offset", "fold", "identifier", "evaluation", "configured_exclusion", "not_selected", "not_in_formula"]),
    }
  }
  const retainedMetadata = parseTrainFeatureSelectionCollection(obj.retained_metadata, "retained_metadata", parseExcludedColumn)
  const excludedColumns = parseTrainFeatureSelectionCollection(obj.excluded_columns, "excluded_columns", parseExcludedColumn)
  if (new Set(features.items).size !== features.items.length) throw new Error("parseTrainFeatureSelection: feature names must be unique")
  for (const [name, collection] of [["retained_metadata", retainedMetadata], ["excluded_columns", excludedColumns]] as const) {
    if (new Set(collection.items.map((item) => item.column)).size !== collection.items.length) {
      throw new Error(`parseTrainFeatureSelection: ${name} columns must be unique`)
    }
  }
  const detailState = expectStringLiteral("parseTrainFeatureSelection", obj.detail_state, "detail_state", ["available", "truncated"])
  const expectedDetailState = [features.state, retainedMetadata.state, excludedColumns.state].includes("truncated") ? "truncated" : "available"
  if (detailState !== expectedDetailState) throw new Error("parseTrainFeatureSelection: detail_state is inconsistent")
  const featureCount = expectInteger(obj.feature_count, "feature_count", true)
  if (featureCount !== features.total_count) throw new Error("parseTrainFeatureSelection: feature_count is inconsistent")
  return {
    schema_version: 1,
    mode: expectStringLiteral("parseTrainFeatureSelection", obj.mode, "mode", ["explicit", "all_except", "glm_terms"]),
    feature_count: featureCount,
    detail_state: detailState,
    features,
    retained_metadata: retainedMetadata,
    excluded_columns: excludedColumns,
  }
}

function expectTrainInteger(
  value: unknown,
  field: string,
  minimum: number,
  maximum?: number,
): number {
  if (
    typeof value !== "number"
    || !Number.isSafeInteger(value)
    || value < minimum
    || (maximum !== undefined && value > maximum)
  ) {
    throw new Error(`parseTrainResponse: ${field} is outside its integer bounds`)
  }
  return value
}

function expectNonNegativeInteger(value: unknown, field: string): number {
  return expectTrainInteger(value, field, 0)
}

function expectFiniteTrainNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`parseTrainResponse: ${field} must be finite`)
  }
  return value
}

function expectMetricRecord(
  value: unknown,
  field: string,
  emptyAllowed = false,
): Record<string, number> {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const entries = Object.entries(obj)
  if (
    (!emptyAllowed && entries.length === 0)
    || entries.some(
      ([name, metric]) => (
        name.length === 0
        || typeof metric !== "number"
        || !Number.isFinite(metric)
      ),
    )
  ) {
    throw new Error(`parseTrainResponse: ${field} must contain finite named metrics`)
  }
  return Object.fromEntries(entries) as Record<string, number>
}

function expectTrainKeys(
  obj: Record<string, unknown>,
  field: string,
  keys: readonly string[],
): void {
  const actual = Object.keys(obj).sort()
  const expected = [...keys].sort()
  if (
    actual.length !== expected.length
    || actual.some((key, index) => key !== expected[index])
  ) {
    throw new Error(`parseTrainResponse: ${field} has unexpected or missing fields`)
  }
}

function expectNonEmptyTrainString(value: unknown, field: string): string {
  const parsed = expectString("parseTrainResponse", value, field)
  if (parsed.length === 0) {
    throw new Error(`parseTrainResponse: ${field} must not be empty`)
  }
  return parsed
}

function expectTrainDigest(value: unknown, field: string): string {
  const parsed = expectNonEmptyTrainString(value, field)
  if (!/^[0-9a-f]{64}$/.test(parsed)) {
    throw new Error(`parseTrainResponse: ${field} must be a SHA-256 digest`)
  }
  return parsed
}

function expectFiniteJsonObject(
  value: unknown,
  field: string,
): Record<string, unknown> {
  const validate = (item: unknown, path: string): void => {
    if (
      item === null
      || typeof item === "string"
      || typeof item === "boolean"
    ) {
      return
    }
    if (typeof item === "number") {
      if (!Number.isFinite(item)) {
        throw new Error(`parseTrainResponse: ${path} must be finite JSON`)
      }
      return
    }
    if (Array.isArray(item)) {
      item.forEach((child, index) => validate(child, `${path}[${index}]`))
      return
    }
    if (
      isPlainObject(item)
      && (
        Object.getPrototypeOf(item) === Object.prototype
        || Object.getPrototypeOf(item) === null
      )
    ) {
      Object.entries(item).forEach(([key, child]) => validate(child, `${path}.${key}`))
      return
    }
    throw new Error(`parseTrainResponse: ${path} must contain only finite JSON values`)
  }

  const obj = expectPlainObject("parseTrainResponse", value, field)
  validate(obj, field)
  return obj
}

function numbersClose(left: number, right: number): boolean {
  return (
    Math.abs(left - right)
    <= 1e-12 * Math.max(1, Math.abs(left), Math.abs(right))
  )
}

function finiteJsonEquals(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (Array.isArray(left) || Array.isArray(right)) {
    return (
      Array.isArray(left)
      && Array.isArray(right)
      && left.length === right.length
      && left.every((item, index) => finiteJsonEquals(item, right[index]))
    )
  }
  if (!isPlainObject(left) || !isPlainObject(right)) return false
  const leftKeys = Object.keys(left).sort()
  const rightKeys = Object.keys(right).sort()
  return (
    leftKeys.length === rightKeys.length
    && leftKeys.every(
      (key, index) => (
        key === rightKeys[index]
        && finiteJsonEquals(left[key], right[key])
      ),
    )
  )
}

function sameMetricNames(
  left: Record<string, unknown>,
  right: Record<string, unknown>,
): boolean {
  const leftNames = Object.keys(left).sort()
  const rightNames = Object.keys(right).sort()
  return (
    leftNames.length === rightNames.length
    && leftNames.every((name, index) => name === rightNames[index])
  )
}

function parseEvaluationFit(value: unknown, field: string) {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  expectTrainKeys(
    obj,
    field,
    [
      "schema_version",
      "fit_index",
      "train_rows",
      "validation_rows",
      "metrics",
      "best_iteration",
    ],
  )
  return {
    schema_version: expectSchemaVersionOne(
      "parseTrainResponse",
      obj.schema_version,
      `${field}.schema_version`,
    ),
    fit_index: expectTrainInteger(obj.fit_index, `${field}.fit_index`, 0, 9),
    train_rows: expectTrainInteger(obj.train_rows, `${field}.train_rows`, 1),
    validation_rows: expectTrainInteger(
      obj.validation_rows,
      `${field}.validation_rows`,
      1,
    ),
    metrics: expectMetricRecord(obj.metrics, `${field}.metrics`),
    best_iteration: obj.best_iteration === null
      ? null
      : expectNonNegativeInteger(obj.best_iteration, `${field}.best_iteration`),
  }
}

function parseEvaluationReport(value: unknown): NonNullable<TrainResponse["evaluation"]> {
  const obj = expectPlainObject("parseTrainResponse", value, "evaluation")
  expectTrainKeys(
    obj,
    "evaluation",
    [
      "schema_version",
      "strategy",
      "validation_method",
      "validation_fit_count",
      "fit_count",
      "development_rows",
      "final_test_rows",
      "selection_fits",
      "selection_metrics",
      "plan_sha256",
      "results_sha256",
      "plan_path",
      "results_path",
      "report_path",
      "summary",
    ],
  )
  const validationFitCount = expectTrainInteger(
    obj.validation_fit_count,
    "evaluation.validation_fit_count",
    0,
    10,
  )
  const validationMethod = expectStringLiteral(
    "parseTrainResponse",
    obj.validation_method,
    "evaluation.validation_method",
    ["none", "single", "cross_validation"] as const,
  )
  if (
    (validationMethod === "none" && validationFitCount !== 0)
    || (validationMethod === "single" && validationFitCount !== 1)
    || (
      validationMethod === "cross_validation"
      && (validationFitCount < 2 || validationFitCount > 10)
    )
  ) {
    throw new Error(
      "parseTrainResponse: evaluation validation_fit_count is inconsistent with validation_method",
    )
  }

  const selectionFits = parseArray(
    "parseTrainResponse",
    obj.selection_fits,
    "evaluation.selection_fits",
    parseEvaluationFit,
  )
  if (
    selectionFits.length !== validationFitCount
    || selectionFits.some((fit, index) => fit.fit_index !== index)
  ) {
    throw new Error("parseTrainResponse: evaluation selection fits must be contiguous")
  }

  const rawMetrics = expectPlainObject(
    "parseTrainResponse",
    obj.selection_metrics,
    "evaluation.selection_metrics",
  )
  const selectionMetrics = Object.fromEntries(
    Object.entries(rawMetrics).map(([name, raw]) => {
      if (name.length === 0) {
        throw new Error("parseTrainResponse: evaluation metric names must not be empty")
      }
      const metricSummary = expectPlainObject(
        "parseTrainResponse",
        raw,
        `evaluation.selection_metrics.${name}`,
      )
      expectTrainKeys(
        metricSummary,
        `evaluation.selection_metrics.${name}`,
        ["mean", "stddev", "min", "max", "fit_count", "validation_rows"],
      )
      const parsedSummary = {
        mean: expectFiniteTrainNumber(metricSummary.mean, `${name}.mean`),
        stddev: expectFiniteTrainNumber(metricSummary.stddev, `${name}.stddev`),
        min: expectFiniteTrainNumber(metricSummary.min, `${name}.min`),
        max: expectFiniteTrainNumber(metricSummary.max, `${name}.max`),
        fit_count: expectTrainInteger(metricSummary.fit_count, `${name}.fit_count`, 1),
        validation_rows: expectTrainInteger(
          metricSummary.validation_rows,
          `${name}.validation_rows`,
          1,
        ),
      }
      if (parsedSummary.stddev < 0 || parsedSummary.min > parsedSummary.max) {
        throw new Error("parseTrainResponse: invalid evaluation metric summary")
      }
      return [name, parsedSummary]
    }),
  )

  const rawSummary = expectPlainObject(
    "parseTrainResponse",
    obj.summary,
    "evaluation.summary",
  )
  expectTrainKeys(
    rawSummary,
    "evaluation.summary",
    [
      "development_rows",
      "test_rows",
      "validation_fit_count",
      "development_group_count",
      "test_group_count",
      "development_date_count",
      "test_date_count",
    ],
  )
  const nullableCount = (name: string) => (
    rawSummary[name] === null
      ? null
      : expectNonNegativeInteger(rawSummary[name], `evaluation.summary.${name}`)
  )
  const summary = {
    development_rows: expectTrainInteger(
      rawSummary.development_rows,
      "evaluation.summary.development_rows",
      1,
    ),
    test_rows: expectNonNegativeInteger(
      rawSummary.test_rows,
      "evaluation.summary.test_rows",
    ),
    validation_fit_count: expectTrainInteger(
      rawSummary.validation_fit_count,
      "evaluation.summary.validation_fit_count",
      0,
      10,
    ),
    development_group_count: nullableCount("development_group_count"),
    test_group_count: nullableCount("test_group_count"),
    development_date_count: nullableCount("development_date_count"),
    test_date_count: nullableCount("test_date_count"),
  }
  const strategy = expectStringLiteral(
    "parseTrainResponse",
    obj.strategy,
    "evaluation.strategy",
    ["random", "group", "temporal"] as const,
  )
  const developmentRows = expectTrainInteger(
    obj.development_rows,
    "evaluation.development_rows",
    1,
  )
  const finalTestRows = expectNonNegativeInteger(
    obj.final_test_rows,
    "evaluation.final_test_rows",
  )
  if (
    summary.development_rows !== developmentRows
    || summary.test_rows !== finalTestRows
    || summary.validation_fit_count !== validationFitCount
  ) {
    throw new Error("parseTrainResponse: evaluation summary counts disagree with report")
  }

  const groupCounts = [
    summary.development_group_count,
    summary.test_group_count,
  ] as const
  const dateCounts = [
    summary.development_date_count,
    summary.test_date_count,
  ] as const
  if (
    (strategy === "random" && [...groupCounts, ...dateCounts].some((count) => count !== null))
    || (
      strategy === "group"
      && (
        groupCounts.some((count) => count === null)
        || dateCounts.some((count) => count !== null)
        || groupCounts[0] === 0
        || (finalTestRows > 0) !== (groupCounts[1]! > 0)
      )
    )
    || (
      strategy === "temporal"
      && (
        dateCounts.some((count) => count === null)
        || groupCounts.some((count) => count !== null)
        || dateCounts[0] === 0
        || (finalTestRows > 0) !== (dateCounts[1]! > 0)
      )
    )
  ) {
    throw new Error("parseTrainResponse: evaluation strategy counts are inconsistent")
  }

  const metricNames = Object.keys(selectionMetrics)
  if (validationFitCount === 0) {
    if (metricNames.length !== 0) {
      throw new Error(
        "parseTrainResponse: evaluation selection_metrics require validation",
      )
    }
  } else {
    if (metricNames.length === 0) {
      throw new Error(
        "parseTrainResponse: evaluation selection_metrics are required with validation",
      )
    }
    if (
      selectionFits.some((fit) => !sameMetricNames(fit.metrics, selectionMetrics))
    ) {
      throw new Error(
        "parseTrainResponse: evaluation fit metric names must match summaries",
      )
    }
    const totalRows = selectionFits.reduce(
      (total, fit) => total + fit.validation_rows,
      0,
    )
    for (const name of metricNames) {
      const metricSummary = selectionMetrics[name]!
      const values = selectionFits.map((fit) => fit.metrics[name]!)
      const weightedMean = selectionFits.reduce(
        (total, fit) => total + fit.metrics[name]! * fit.validation_rows,
        0,
      ) / totalRows
      const weightedVariance = selectionFits.reduce(
        (total, fit) => (
          total
          + fit.validation_rows * (fit.metrics[name]! - weightedMean) ** 2
        ),
        0,
      ) / totalRows
      if (
        metricSummary.fit_count !== validationFitCount
        || metricSummary.validation_rows !== totalRows
        || !numbersClose(metricSummary.mean, weightedMean)
        || !numbersClose(metricSummary.stddev, Math.sqrt(weightedVariance))
        || !numbersClose(metricSummary.min, Math.min(...values))
        || !numbersClose(metricSummary.max, Math.max(...values))
      ) {
        throw new Error(
          "parseTrainResponse: evaluation aggregate disagrees with selection fits",
        )
      }
    }
  }

  return {
    schema_version: expectSchemaVersionOne(
      "parseTrainResponse",
      obj.schema_version,
      "evaluation.schema_version",
    ),
    strategy,
    validation_method: validationMethod,
    validation_fit_count: validationFitCount,
    fit_count: expectTrainInteger(obj.fit_count, "evaluation.fit_count", 1, 201),
    development_rows: developmentRows,
    final_test_rows: finalTestRows,
    selection_fits: selectionFits,
    selection_metrics: selectionMetrics,
    plan_sha256: expectTrainDigest(obj.plan_sha256, "evaluation.plan_sha256"),
    results_sha256: expectTrainDigest(
      obj.results_sha256,
      "evaluation.results_sha256",
    ),
    plan_path: expectNonEmptyTrainString(obj.plan_path, "evaluation.plan_path"),
    results_path: expectNonEmptyTrainString(
      obj.results_path,
      "evaluation.results_path",
    ),
    report_path: expectNonEmptyTrainString(
      obj.report_path,
      "evaluation.report_path",
    ),
    summary,
  }
}

function parseTuningReport(value: unknown): NonNullable<TrainResponse["tuning"]> {
  const obj = expectPlainObject("parseTrainResponse", value, "tuning")
  expectTrainKeys(
    obj,
    "tuning",
    [
      "schema_version",
      "plan_sha256",
      "trials_sha256",
      "evaluation_plan_sha256",
      "metric",
      "direction",
      "baseline_objective",
      "winner_trial_index",
      "winner_objective",
      "improvement",
      "best_sampled_params",
      "final_params",
      "final_tree_count",
      "trial_count",
      "trial_fit_count",
      "total_fit_count",
      "trials",
      "plan_path",
      "trials_path",
      "report_path",
    ],
  )
  const trials = parseArray(
    "parseTrainResponse",
    obj.trials,
    "tuning.trials",
    (raw, field) => {
      const trial = expectPlainObject("parseTrainResponse", raw, field)
      expectTrainKeys(
        trial,
        field,
        [
          "schema_version",
          "trial_index",
          "label",
          "sampled_params",
          "resolved_params",
          "fits",
          "aggregate_metrics",
          "objective",
          "elapsed_seconds",
        ],
      )
      const fits = parseArray(
        "parseTrainResponse",
        trial.fits,
        `${field}.fits`,
        parseEvaluationFit,
      )
      if (fits.length < 1 || fits.length > 10) {
        throw new Error(`parseTrainResponse: ${field}.fits must contain 1 to 10 fits`)
      }
      const elapsedSeconds = expectFiniteTrainNumber(
        trial.elapsed_seconds,
        `${field}.elapsed_seconds`,
      )
      if (elapsedSeconds < 0) {
        throw new Error(`parseTrainResponse: ${field}.elapsed_seconds must be non-negative`)
      }
      return {
        schema_version: expectSchemaVersionOne(
          "parseTrainResponse",
          trial.schema_version,
          `${field}.schema_version`,
        ),
        trial_index: expectTrainInteger(
          trial.trial_index,
          `${field}.trial_index`,
          0,
          199,
        ),
        label: expectStringLiteral(
          "parseTrainResponse",
          trial.label,
          `${field}.label`,
          ["baseline", "sampled"] as const,
        ),
        sampled_params: expectFiniteJsonObject(
          trial.sampled_params,
          `${field}.sampled_params`,
        ),
        resolved_params: expectFiniteJsonObject(
          trial.resolved_params,
          `${field}.resolved_params`,
        ),
        fits,
        aggregate_metrics: expectMetricRecord(
          trial.aggregate_metrics,
          `${field}.aggregate_metrics`,
        ),
        objective: expectFiniteTrainNumber(trial.objective, `${field}.objective`),
        elapsed_seconds: elapsedSeconds,
      }
    },
  )
  const trialCount = expectTrainInteger(obj.trial_count, "tuning.trial_count", 5, 50)
  if (
    trials.length !== trialCount
    || trials.some((trial, index) => trial.trial_index !== index)
  ) {
    throw new Error("parseTrainResponse: tuning trials must be contiguous and ordered")
  }
  const baseline = trials[0]!
  if (
    baseline.label !== "baseline"
    || Object.keys(baseline.sampled_params).length !== 0
    || trials.slice(1).some(
      (trial) => (
        trial.label !== "sampled"
        || Object.keys(trial.sampled_params).length === 0
      ),
    )
  ) {
    throw new Error(
      "parseTrainResponse: tuning trials must start with one empty baseline",
    )
  }
  for (const trial of trials) {
    const expectedResolved = {
      ...baseline.resolved_params,
      ...trial.sampled_params,
    }
    if (!finiteJsonEquals(trial.resolved_params, expectedResolved)) {
      throw new Error(
        "parseTrainResponse: resolved parameters must equal baseline plus sampled parameters",
      )
    }
  }

  const baselineFitCount = baseline.fits.length
  for (const trial of trials) {
    if (
      !sameMetricNames(trial.aggregate_metrics, baseline.aggregate_metrics)
      || trial.fits.length !== baselineFitCount
      || trial.fits.some((fit, index) => fit.fit_index !== index)
      || trial.fits.some(
        (fit) => !sameMetricNames(fit.metrics, trial.aggregate_metrics),
      )
    ) {
      throw new Error(
        "parseTrainResponse: tuning trials must use the same contiguous evaluation fits and metrics",
      )
    }
    const totalValidationRows = trial.fits.reduce(
      (total, fit) => total + fit.validation_rows,
      0,
    )
    for (const [name, aggregate] of Object.entries(trial.aggregate_metrics)) {
      const weightedMean = trial.fits.reduce(
        (total, fit) => total + fit.metrics[name]! * fit.validation_rows,
        0,
      ) / totalValidationRows
      if (!numbersClose(aggregate, weightedMean)) {
        throw new Error(
          "parseTrainResponse: tuning aggregate disagrees with validation fits",
        )
      }
    }
  }

  const metric = expectNonEmptyTrainString(obj.metric, "tuning.metric")
  if (!(metric in baseline.aggregate_metrics)) {
    throw new Error("parseTrainResponse: tuning metric is missing from trial metrics")
  }
  if (
    trials.some(
      (trial) => !numbersClose(trial.objective, trial.aggregate_metrics[metric]!),
    )
  ) {
    throw new Error(
      "parseTrainResponse: tuning objectives must equal the selected aggregate metric",
    )
  }

  const direction = expectStringLiteral(
    "parseTrainResponse",
    obj.direction,
    "tuning.direction",
    ["maximize", "minimize"] as const,
  )
  const canonicalMetric = metric
    .trim()
    .toLowerCase()
    .replaceAll(" ", "_")
    .replaceAll("-", "_")
    .replaceAll("Â²", "2")
  const expectedDirection = (
    ["gini", "auc", "r2"].includes(canonicalMetric)
      ? "maximize"
      : [
          "rmse",
          "mae",
          "mse",
          "logloss",
          "poisson_deviance",
          "tweedie_deviance",
        ].includes(canonicalMetric)
        ? "minimize"
        : null
  )
  if (expectedDirection === null || direction !== expectedDirection) {
    throw new Error(
      "parseTrainResponse: tuning metric direction is not server-owned",
    )
  }
  let winner = baseline
  for (const trial of trials.slice(1)) {
    if (
      (direction === "maximize" && trial.objective > winner.objective)
      || (direction === "minimize" && trial.objective < winner.objective)
    ) {
      winner = trial
    }
  }
  const baselineObjective = expectFiniteTrainNumber(
    obj.baseline_objective,
    "tuning.baseline_objective",
  )
  const winnerTrialIndex = expectTrainInteger(
    obj.winner_trial_index,
    "tuning.winner_trial_index",
    0,
    199,
  )
  const winnerObjective = expectFiniteTrainNumber(
    obj.winner_objective,
    "tuning.winner_objective",
  )
  const improvement = expectFiniteTrainNumber(
    obj.improvement,
    "tuning.improvement",
  )
  const expectedImprovement = direction === "maximize"
    ? winner.objective - baseline.objective
    : baseline.objective - winner.objective
  if (
    !numbersClose(baselineObjective, baseline.objective)
    || winnerTrialIndex !== winner.trial_index
    || !numbersClose(winnerObjective, winner.objective)
    || improvement < 0
    || !numbersClose(improvement, expectedImprovement)
  ) {
    throw new Error(
      "parseTrainResponse: tuning baseline, winner, or improvement is inconsistent",
    )
  }
  const bestSampledParams = expectFiniteJsonObject(
    obj.best_sampled_params,
    "tuning.best_sampled_params",
  )
  if (!finiteJsonEquals(bestSampledParams, winner.sampled_params)) {
    throw new Error(
      "parseTrainResponse: best sampled parameters must equal the winning trial",
    )
  }
  const iterationCeiling = (
    winner.resolved_params.iterations === undefined
      ? 1000
      : expectTrainInteger(
          winner.resolved_params.iterations,
          "tuning winner iterations",
          1,
        )
  )
  if (winner.fits.some((fit) => fit.best_iteration === null)) {
    throw new Error(
      "parseTrainResponse: winning tuning fits must retain best_iteration",
    )
  }
  const weightedTreeCounts = winner.fits
    .map((fit) => ({
      treeCount: fit.best_iteration! + 1,
      rows: fit.validation_rows,
    }))
    .sort((left, right) => left.treeCount - right.treeCount)
  const threshold = weightedTreeCounts.reduce(
    (total, item) => total + item.rows,
    0,
  ) / 2
  let cumulativeRows = 0
  let expectedTreeCount = weightedTreeCounts.at(-1)!.treeCount
  for (const item of weightedTreeCounts) {
    cumulativeRows += item.rows
    if (cumulativeRows >= threshold) {
      expectedTreeCount = item.treeCount
      break
    }
  }
  expectedTreeCount = Math.min(expectedTreeCount, iterationCeiling)
  const finalTreeCount = expectTrainInteger(
    obj.final_tree_count,
    "tuning.final_tree_count",
    1,
  )
  const finalParams = expectFiniteJsonObject(
    obj.final_params,
    "tuning.final_params",
  )
  const expectedFinalParams = { ...winner.resolved_params }
  for (const key of [
    "early_stopping_rounds",
    "od_pval",
    "od_type",
    "od_wait",
    "use_best_model",
  ]) {
    delete expectedFinalParams[key]
  }
  expectedFinalParams.iterations = expectedTreeCount
  if (
    finalTreeCount !== expectedTreeCount
    || !finiteJsonEquals(finalParams, expectedFinalParams)
  ) {
    throw new Error(
      "parseTrainResponse: final parameter projection must be derived from the winning validation fits",
    )
  }

  const trialFitCount = expectTrainInteger(
    obj.trial_fit_count,
    "tuning.trial_fit_count",
    5,
    200,
  )
  const totalFitCount = expectTrainInteger(
    obj.total_fit_count,
    "tuning.total_fit_count",
    6,
    201,
  )
  if (
    trialFitCount !== trials.reduce((count, trial) => count + trial.fits.length, 0)
    || totalFitCount !== trialFitCount + 1
  ) {
    throw new Error("parseTrainResponse: tuning fit counts are inconsistent")
  }

  return {
    schema_version: expectSchemaVersionOne(
      "parseTrainResponse",
      obj.schema_version,
      "tuning.schema_version",
    ),
    plan_sha256: expectTrainDigest(obj.plan_sha256, "tuning.plan_sha256"),
    trials_sha256: expectTrainDigest(obj.trials_sha256, "tuning.trials_sha256"),
    evaluation_plan_sha256: expectTrainDigest(
      obj.evaluation_plan_sha256,
      "tuning.evaluation_plan_sha256",
    ),
    metric,
    direction,
    baseline_objective: baselineObjective,
    winner_trial_index: winnerTrialIndex,
    winner_objective: winnerObjective,
    improvement,
    best_sampled_params: bestSampledParams,
    final_params: finalParams,
    final_tree_count: finalTreeCount,
    trial_count: trialCount,
    trial_fit_count: trialFitCount,
    total_fit_count: totalFitCount,
    trials,
    plan_path: expectNonEmptyTrainString(obj.plan_path, "tuning.plan_path"),
    trials_path: expectNonEmptyTrainString(obj.trials_path, "tuning.trials_path"),
    report_path: expectNonEmptyTrainString(obj.report_path, "tuning.report_path"),
  }
}

export function parseTrainResponse(value: unknown): TrainResponse {
  const obj = expectPlainObject("parseTrainResponse", value)
  const legacyFields = ["metrics", "train_rows", "validation_rows", "holdout_rows", "holdout_metrics", "cross_validation"]
  if (legacyFields.some((field) => field in obj)) {
    throw new Error("parseTrainResponse: legacy training result fields are not supported")
  }
  const rawRegularization = optionalNullableObject("parseTrainResponse", obj, "glm_regularization_path")
  const status = expectStringLiteral("parseTrainResponse", obj.status, "field `status`", ["started", "completed", "error"])
  const evaluation = obj.evaluation === undefined ? undefined : parseEvaluationReport(obj.evaluation)
  const tuning = obj.tuning === undefined ? undefined : parseTuningReport(obj.tuning)
  const diagnosticMetrics = expectMetricRecord(
    obj.diagnostic_metrics,
    "diagnostic_metrics",
    status !== "completed",
  )
  const finalTestMetrics = expectMetricRecord(
    obj.final_test_metrics,
    "final_test_metrics",
    true,
  )
  const developmentRows = expectNonNegativeInteger(
    obj.development_rows,
    "development_rows",
  )
  const finalTestRows = expectNonNegativeInteger(
    obj.final_test_rows,
    "final_test_rows",
  )
  const diagnosticsSet = expectStringLiteral(
    "parseTrainResponse",
    obj.diagnostics_set,
    "diagnostics_set",
    ["development", "final_test"] as const,
  )
  if (status === "completed" && evaluation === undefined) throw new Error("parseTrainResponse: completed training requires evaluation")
  if (status !== "completed" && (evaluation !== undefined || tuning !== undefined)) throw new Error("parseTrainResponse: evaluation and tuning are present only for completed training")
  if (status === "completed") {
    if (
      developmentRows !== evaluation!.development_rows
      || finalTestRows !== evaluation!.final_test_rows
    ) {
      throw new Error(
        "parseTrainResponse: response row counts must match evaluation",
      )
    }
    if (finalTestRows > 0) {
      const metricNamesMatch = sameMetricNames(
        diagnosticMetrics,
        finalTestMetrics,
      )
      if (
        diagnosticsSet !== "final_test"
        || !metricNamesMatch
        || Object.keys(diagnosticMetrics).some(
          (name) => diagnosticMetrics[name] !== finalTestMetrics[name],
        )
      ) {
        throw new Error(
          "parseTrainResponse: final-test diagnostics are inconsistent",
        )
      }
    } else if (
      diagnosticsSet !== "development"
      || Object.keys(finalTestMetrics).length !== 0
    ) {
      throw new Error(
        "parseTrainResponse: development diagnostics are inconsistent",
      )
    }
    if (tuning === undefined) {
      if (evaluation!.fit_count !== evaluation!.validation_fit_count + 1) {
        throw new Error("parseTrainResponse: evaluation fit_count is inconsistent")
      }
    } else if (
      evaluation!.fit_count !== tuning.total_fit_count
      || evaluation!.plan_sha256 !== tuning.evaluation_plan_sha256
    ) {
      throw new Error(
        "parseTrainResponse: tuning fit count or evaluation digest is inconsistent",
      )
    }
  }

  return {
    status,
    job_id: obj.job_id === null ? null : expectString("parseTrainResponse", obj.job_id, "job_id"),
    diagnostic_metrics: diagnosticMetrics,
    final_test_metrics: finalTestMetrics,
    feature_importance: parseArray("parseTrainResponse", obj.feature_importance, "feature_importance", parseFeatureImportanceRow),
    model_path: expectString("parseTrainResponse", obj.model_path, "model_path"),
    development_rows: developmentRows,
    final_test_rows: finalTestRows,
    diagnostics_set: diagnosticsSet,
    features: parseArray("parseTrainResponse", obj.features, "features", (item, field) => expectString("parseTrainResponse", item, field)),
    cat_features: parseArray("parseTrainResponse", obj.cat_features, "cat_features", (item, field) => expectString("parseTrainResponse", item, field)),
    error: obj.error === null ? null : expectString("parseTrainResponse", obj.error, "error"),
    best_iteration: obj.best_iteration === null ? null : expectNonNegativeInteger(obj.best_iteration, "best_iteration"),
    loss_history: parseArray("parseTrainResponse", obj.loss_history, "loss_history", parseLossHistoryEntry),
    loss_history_truncated: expectBoolean("parseTrainResponse", obj.loss_history_truncated, "loss_history_truncated"),
    double_lift: parseArray("parseTrainResponse", obj.double_lift, "double_lift", parseDoubleLiftRow),
    shap_summary: parseArray("parseTrainResponse", obj.shap_summary, "shap_summary", parseShapSummaryRow),
    feature_importance_loss: parseArray("parseTrainResponse", obj.feature_importance_loss, "feature_importance_loss", parseFeatureImportanceRow),
    ave_per_feature: parseArray("parseTrainResponse", obj.ave_per_feature, "ave_per_feature", parseAvePerFeatureRow),
    residuals_histogram: parseArray("parseTrainResponse", obj.residuals_histogram, "residuals_histogram", parseResidualHistogramRow),
    residuals_stats: expectMetricRecord(obj.residuals_stats, "residuals_stats", true),
    actual_vs_predicted: parseArray("parseTrainResponse", obj.actual_vs_predicted, "actual_vs_predicted", parseActualVsPredictedRow),
    lorenz_curve: parseArray("parseTrainResponse", obj.lorenz_curve, "lorenz_curve", parseLorenzCurvePoint),
    lorenz_curve_perfect: parseArray("parseTrainResponse", obj.lorenz_curve_perfect, "lorenz_curve_perfect", parseLorenzCurvePoint),
    pdp_data: parseArray("parseTrainResponse", obj.pdp_data, "pdp_data", parsePdpFeatureRow),
    warning: obj.warning === null ? null : expectString("parseTrainResponse", obj.warning, "warning"),
    total_source_rows: obj.total_source_rows === null ? null : expectNonNegativeInteger(obj.total_source_rows, "total_source_rows"),
    glm_coefficients: parseArray("parseTrainResponse", obj.glm_coefficients, "glm_coefficients", parseGlmCoefficientRow),
    glm_relativities: parseArray("parseTrainResponse", obj.glm_relativities, "glm_relativities", parseGlmRelativityRow),
    glm_fit_statistics: expectMetricRecord(obj.glm_fit_statistics, "glm_fit_statistics", true),
    glm_regularization_path: rawRegularization === null
      ? null
      : {
          selected_alpha: rawRegularization.selected_alpha === undefined ? undefined : expectNumber("parseTrainResponse", rawRegularization.selected_alpha, "field `glm_regularization_path.selected_alpha`"),
          n_nonzero: rawRegularization.n_nonzero === undefined ? undefined : expectNumber("parseTrainResponse", rawRegularization.n_nonzero, "field `glm_regularization_path.n_nonzero`"),
        },
    diagnostics_errors: parseArray("parseTrainResponse", obj.diagnostics_errors, "diagnostics_errors", parseTrainDiagnosticsError),
    feature_selection: obj.feature_selection === null
      ? null
      : parseTrainFeatureSelection(obj.feature_selection),
    evaluation,
    tuning,
  }
}

export function parseTrainStatusResponse(value: unknown): TrainStatusResponse {
  const obj = expectPlainObject("parseTrainStatusResponse", value)
  const phase = obj.phase === undefined || obj.phase === null ? null : expectStringLiteral("parseTrainStatusResponse", obj.phase, "phase", ["planning", "trial_fit", "trial_complete", "final_fit", "publication", "completed"] as const)
  const progressInteger = (field: string, minimum: number, maximum?: number) => {
    if (obj[field] === undefined || obj[field] === null) return null
    const parsed = expectNonNegativeInteger(obj[field], `status.${field}`)
    if (parsed < minimum || (maximum !== undefined && parsed > maximum)) throw new Error(`parseTrainStatusResponse: ${field} is outside bounds`)
    return parsed
  }
  const trial_index = progressInteger("trial_index", 1)
  const trial_count = progressInteger("trial_count", 5, 50)
  const fold_index = progressInteger("fold_index", 1)
  const fold_count = progressInteger("fold_count", 1, 10)
  const completed_fits = progressInteger("completed_fits", 0)
  const total_fits = progressInteger("total_fits", 1, 201)
  const best_objective = obj.best_objective === undefined || obj.best_objective === null
    ? null
    : expectFiniteTrainNumber(obj.best_objective, "status.best_objective")
  if (phase === null && [trial_index, trial_count, fold_index, fold_count, completed_fits, total_fits, best_objective].some((item) => item !== null)) throw new Error("parseTrainStatusResponse: tuning progress fields require phase")
  if (phase !== null) {
    if ([trial_count, fold_count, completed_fits, total_fits].some((item) => item === null)) throw new Error("parseTrainStatusResponse: phase requires progress counts")
    if (phase === "trial_fit" && (trial_index === null || fold_index === null)) throw new Error("parseTrainStatusResponse: trial_fit requires trial and fold indices")
    if (phase === "trial_complete" && (trial_index === null || fold_index !== null)) throw new Error("parseTrainStatusResponse: trial_complete requires only a trial index")
    if (["planning", "final_fit", "publication", "completed"].includes(phase) && (trial_index !== null || fold_index !== null)) throw new Error("parseTrainStatusResponse: phase must not contain trial/fold indices")
    if (
      (trial_index !== null && trial_index > trial_count!)
      || (fold_index !== null && fold_index > fold_count!)
    ) throw new Error("parseTrainStatusResponse: tuning progress index exceeds its count")
    if (completed_fits! > total_fits!) throw new Error("parseTrainStatusResponse: completed_fits exceeds total_fits")
    if (total_fits !== trial_count! * fold_count! + 1) {
      throw new Error(
        "parseTrainStatusResponse: total_fits must equal the bounded trial fit count plus final fit",
      )
    }
  }
  return {
    status: expectStringLiteral("parseTrainStatusResponse", obj.status, "field `status`", JOB_STATUS_VALUES),
    progress: optionalNumber("parseTrainStatusResponse", obj, "progress"),
    message: optionalString("parseTrainStatusResponse", obj, "message"),
    iteration: optionalNumber("parseTrainStatusResponse", obj, "iteration"),
    total_iterations: optionalNumber("parseTrainStatusResponse", obj, "total_iterations"),
    train_loss: optionalNumberRecord("parseTrainStatusResponse", obj, "train_loss"),
    train_loss_history: obj.train_loss_history === undefined
      ? undefined
      : parseArray(
        "parseTrainStatusResponse",
        obj.train_loss_history,
        "field `train_loss_history`",
        parseLossHistoryEntry,
      ),
    train_loss_history_truncated: obj.train_loss_history_truncated === undefined
      ? undefined
      : expectBoolean("parseTrainStatusResponse", obj.train_loss_history_truncated, "field `train_loss_history_truncated`"),
    elapsed_seconds: optionalNumber("parseTrainStatusResponse", obj, "elapsed_seconds"),
    result: obj.result === undefined || obj.result === null ? null : parseTrainResponse(obj.result),
    warning: optionalNullableString("parseTrainStatusResponse", obj, "warning"),
    terminal_reason: optionalNullableString("parseTrainStatusResponse", obj, "terminal_reason"),
    error_code: optionalNullableString("parseTrainStatusResponse", obj, "error_code"),
    http_status_code: optionalNullableNumber("parseTrainStatusResponse", obj, "http_status_code"),
    error_detail: obj.error_detail,
    execution_metrics: optionalExecutionMetrics("parseTrainStatusResponse", obj, "execution_metrics"),
    feature_selection: obj.feature_selection === undefined || obj.feature_selection === null
      ? null
      : parseTrainFeatureSelection(obj.feature_selection),
    phase,
    trial_index,
    trial_count,
    fold_index,
    fold_count,
    completed_fits,
    total_fits,
    best_objective,
  }
}

// ---------------------------------------------------------------------------
// Explore contracts
// ---------------------------------------------------------------------------


export function parseTrainEstimateResponse(value: unknown): TrainEstimate {
  const obj = expectPlainObject("parseTrainEstimateResponse", value)
  const parseDateRange = (range: unknown, field: string) => {
    const rangeObj = expectPlainObject("parseTrainEstimateResponse", range, field)
    expectExactKeys("parseTrainEstimateResponse", rangeObj, field, ["start", "end"])
    const start = expectString("parseTrainEstimateResponse", rangeObj.start, `${field}.start`)
    const end = expectString("parseTrainEstimateResponse", rangeObj.end, `${field}.end`)
    if (start.length === 0 || end.length === 0) {
      throw new Error(`parseTrainEstimateResponse: ${field} boundaries must not be empty`)
    }
    return {
      start,
      end,
    }
  }
  const parseEvaluationPreview = (preview: unknown): EvaluationPreview => {
    const previewObj = expectPlainObject("parseTrainEstimateResponse", preview, "evaluation_preview")
    const optionalKeys = [
      "min_selection_train_rows",
      "max_selection_train_rows",
      "min_selection_validation_rows",
      "max_selection_validation_rows",
      "development_group_count",
      "final_test_group_count",
      "development_date_range",
      "final_test_date_range",
    ] as const
    const boundedInteger = (
      value: unknown,
      field: string,
      minimum: number,
      maximum?: number,
    ): number => {
      if (
        typeof value !== "number"
        || !Number.isSafeInteger(value)
        || value < minimum
        || (maximum !== undefined && value > maximum)
      ) {
        throw new Error(
          `parseTrainEstimateResponse: ${field} must be an integer from ${minimum}`
          + (maximum === undefined ? "" : ` through ${maximum}`),
        )
      }
      return value
    }
    expectExactKeys("parseTrainEstimateResponse", previewObj, "evaluation_preview", [
      "schema_version",
      "strategy",
      "validation_method",
      "development_rows",
      "final_test_rows",
      "validation_fit_count",
      ...optionalKeys.filter((key) => previewObj[key] !== undefined),
    ])
    const strategy = expectStringLiteral(
      "parseTrainEstimateResponse",
      previewObj.strategy,
      "evaluation_preview.strategy",
      ["random", "group", "temporal"],
    )
    const validationMethod = expectStringLiteral(
      "parseTrainEstimateResponse",
      previewObj.validation_method,
      "evaluation_preview.validation_method",
      ["none", "single", "cross_validation"],
    )
    const result: EvaluationPreview = {
      schema_version: expectSchemaVersionOne("parseTrainEstimateResponse", previewObj.schema_version, "evaluation_preview.schema_version"),
      strategy,
      validation_method: validationMethod,
      development_rows: boundedInteger(previewObj.development_rows, "evaluation_preview.development_rows", 1),
      final_test_rows: boundedInteger(previewObj.final_test_rows, "evaluation_preview.final_test_rows", 0),
      validation_fit_count: boundedInteger(previewObj.validation_fit_count, "evaluation_preview.validation_fit_count", 0, 10),
    }
    if (previewObj.min_selection_train_rows !== undefined) result.min_selection_train_rows = boundedInteger(previewObj.min_selection_train_rows, "evaluation_preview.min_selection_train_rows", 1)
    if (previewObj.max_selection_train_rows !== undefined) result.max_selection_train_rows = boundedInteger(previewObj.max_selection_train_rows, "evaluation_preview.max_selection_train_rows", 1)
    if (previewObj.min_selection_validation_rows !== undefined) result.min_selection_validation_rows = boundedInteger(previewObj.min_selection_validation_rows, "evaluation_preview.min_selection_validation_rows", 1)
    if (previewObj.max_selection_validation_rows !== undefined) result.max_selection_validation_rows = boundedInteger(previewObj.max_selection_validation_rows, "evaluation_preview.max_selection_validation_rows", 1)
    if (previewObj.development_group_count !== undefined) result.development_group_count = boundedInteger(previewObj.development_group_count, "evaluation_preview.development_group_count", 1)
    if (previewObj.final_test_group_count !== undefined) result.final_test_group_count = boundedInteger(previewObj.final_test_group_count, "evaluation_preview.final_test_group_count", 0)
    if (previewObj.development_date_range !== undefined) result.development_date_range = parseDateRange(previewObj.development_date_range, "evaluation_preview.development_date_range")
    if (previewObj.final_test_date_range !== undefined) result.final_test_date_range = parseDateRange(previewObj.final_test_date_range, "evaluation_preview.final_test_date_range")

    const selectionBounds = [
      result.min_selection_train_rows,
      result.max_selection_train_rows,
      result.min_selection_validation_rows,
      result.max_selection_validation_rows,
    ]
    if (validationMethod === "none") {
      if (result.validation_fit_count !== 0 || selectionBounds.some((value) => value !== undefined)) {
        throw new Error("parseTrainEstimateResponse: no-validation preview must not contain selection bounds")
      }
    } else {
      const expectedCount = validationMethod === "single" ? 1 : result.validation_fit_count
      if (
        result.validation_fit_count !== expectedCount
        || (validationMethod === "cross_validation" && result.validation_fit_count < 2)
        || selectionBounds.some((value) => value === undefined)
      ) {
        throw new Error("parseTrainEstimateResponse: validated preview has inconsistent fit count or row bounds")
      }
      if (
        result.min_selection_train_rows! > result.max_selection_train_rows!
        || result.min_selection_validation_rows! > result.max_selection_validation_rows!
      ) {
        throw new Error("parseTrainEstimateResponse: evaluation preview minimums must not exceed maximums")
      }
    }

    if (strategy === "group") {
      if (
        result.development_group_count === undefined
        || result.final_test_group_count === undefined
      ) {
        throw new Error("parseTrainEstimateResponse: group preview requires group counts")
      }
    } else if (
      result.development_group_count !== undefined
      || result.final_test_group_count !== undefined
    ) {
      throw new Error("parseTrainEstimateResponse: only group preview may contain group counts")
    }

    if (strategy === "temporal") {
      if (
        result.development_date_range === undefined
        || (result.final_test_rows > 0) !== (result.final_test_date_range !== undefined)
      ) {
        throw new Error("parseTrainEstimateResponse: temporal preview has inconsistent date ranges")
      }
    } else if (
      result.development_date_range !== undefined
      || result.final_test_date_range !== undefined
    ) {
      throw new Error("parseTrainEstimateResponse: only temporal preview may contain date ranges")
    }
    return result
  }
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
    evaluation_preview: obj.evaluation_preview === undefined || obj.evaluation_preview === null
      ? null
      : parseEvaluationPreview(obj.evaluation_preview),
  }
}
