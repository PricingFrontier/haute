/** Runtime parsers for on-demand modelling train endpoints. */

import type {
  EbmTerm,
  EbmTermAxis,
  EvaluationPreview,
  TrainEstimate,
  TrainEstimateUnavailable,
  TrainResponse,
  TrainStatusResponse,
} from "../api/types"
import type {
  TrainEstimateResponse,
  TrainResponse as GeneratedTrainResponse,
} from "../generated/api-contracts.generated"
import { validateTrainEstimateResponse } from "../generated/api-contracts.modelling.validators.mjs"
import {
  validateTrainResponse,
  validateTrainStatusResponse,
} from "../generated/api-contracts.training.validators.mjs"
import { expectGeneratedContract } from "./generatedContractValidation"
import {
  expectArray,
  expectBoolean,
  expectInteger,
  expectNullableNumber,
  expectNullableString,
  expectNumber,
  expectPlainObject,
  expectString,
  expectStringLiteral,
  optionalExecutionMetrics,
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

function parsePdpGridPoint(
  value: unknown,
  field: string,
  featureType: string,
): NonNullable<NonNullable<TrainResponse["pdp_data"]>[number]["grid"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const rawValue = obj.value
  if (
    typeof rawValue !== "string"
    && typeof rawValue !== "number"
    && !(rawValue === null && featureType === "categorical")
  ) {
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
  const featureType = expectString("parseTrainResponse", obj.type, `${field}.type`)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    type: featureType,
    grid: obj.grid === undefined
      ? []
      : parseArray("parseTrainResponse", obj.grid, `${field}.grid`, (gridPoint, gridField) =>
          parsePdpGridPoint(gridPoint, gridField, featureType),
        ),
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
    std_error: expectNullableNumber("parseTrainResponse", obj.std_error, `${field}.std_error`),
    z_value: expectNullableNumber("parseTrainResponse", obj.z_value, `${field}.z_value`),
    p_value: expectNullableNumber("parseTrainResponse", obj.p_value, `${field}.p_value`),
    significance: expectNullableString("parseTrainResponse", obj.significance, `${field}.significance`),
  }
}

function parseGlmRelativityRow(value: unknown, field: string): NonNullable<TrainResponse["glm_relativities"]>[number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    feature: expectString("parseTrainResponse", obj.feature, `${field}.feature`),
    relativity: expectNumber("parseTrainResponse", obj.relativity, `${field}.relativity`),
    ci_lower: expectNullableNumber("parseTrainResponse", obj.ci_lower, `${field}.ci_lower`),
    ci_upper: expectNullableNumber("parseTrainResponse", obj.ci_upper, `${field}.ci_upper`),
  }
}

function parseGlmInference(value: Record<string, unknown>): NonNullable<TrainResponse["glm_inference"]> {
  const field = "glm_inference"
  return {
    status: expectString("parseTrainResponse", value.status, `${field}.status`),
    valid: expectBoolean("parseTrainResponse", value.valid, `${field}.valid`),
    standard_errors: expectNullableString("parseTrainResponse", value.standard_errors, `${field}.standard_errors`),
    reason: expectNullableString("parseTrainResponse", value.reason, `${field}.reason`),
  }
}

function parseEbmTermAxis(value: unknown, field: string): EbmTermAxis {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const type = expectStringLiteral(
    "parseTrainResponse",
    obj.type,
    `${field}.type`,
    ["nominal", "continuous"] as const,
  )
  return {
    feature: expectNonEmptyTrainString(obj.feature, `${field}.feature`),
    type,
    labels: parseArray("parseTrainResponse", obj.labels, `${field}.labels`, (label, labelField) =>
      expectString("parseTrainResponse", label, labelField),
    ),
    ...(obj.cuts == null
      ? {}
      : {
          cuts: parseArray("parseTrainResponse", obj.cuts, `${field}.cuts`, (cut, cutField) =>
            expectFiniteTrainNumber(cut, cutField),
          ),
        }),
  }
}

function parseEbmTerm(value: unknown, field: string): EbmTerm {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  const kind = expectStringLiteral(
    "parseTrainResponse",
    obj.kind,
    `${field}.kind`,
    ["main", "interaction"] as const,
  )
  const axes = parseArray("parseTrainResponse", obj.axes, `${field}.axes`, parseEbmTermAxis)
  if (axes.length !== (kind === "main" ? 1 : 2)) {
    throw new Error(`parseTrainResponse: ${field}.axes must match the term kind`)
  }
  const score = (item: unknown, itemField: string) =>
    expectFiniteTrainNumber(item, itemField)
  const scores = kind === "main"
    ? parseArray("parseTrainResponse", obj.scores, `${field}.scores`, score)
    : parseArray("parseTrainResponse", obj.scores, `${field}.scores`, (row, rowField) =>
        parseArray("parseTrainResponse", row, rowField, score),
      )
  const rows = scores.length
  if (rows !== axes[0].labels.length || (kind === "interaction" && (scores as number[][]).some(
    (row) => row.length !== axes[1].labels.length,
  ))) {
    throw new Error(`parseTrainResponse: ${field}.scores must match its axes`)
  }
  return {
    term: expectNonEmptyTrainString(obj.term, `${field}.term`),
    features: parseArray("parseTrainResponse", obj.features, `${field}.features`, (name, nameField) =>
      expectNonEmptyTrainString(name, nameField),
    ),
    kind,
    importance: expectFiniteTrainNumber(obj.importance, `${field}.importance`),
    axes,
    scores,
  }
}

function parseGlmSmoothTerm(value: unknown, field: string): TrainResponse["glm_smooth_terms"][number] {
  const obj = expectPlainObject("parseTrainResponse", value, field)
  return {
    term: expectString("parseTrainResponse", obj.term, `${field}.term`),
    k: expectNumber("parseTrainResponse", obj.k, `${field}.k`),
    edf: expectNumber("parseTrainResponse", obj.edf, `${field}.edf`),
    lambda: expectNumber("parseTrainResponse", obj.lambda, `${field}.lambda`),
  }
}

function parseGlmRegularization(value: Record<string, unknown>): NonNullable<TrainResponse["glm_regularization"]> {
  const field = "glm_regularization"
  return {
    penalty: expectStringLiteral("parseTrainResponse", value.penalty, `${field}.penalty`, ["ridge", "lasso", "elastic_net"]),
    mode: expectStringLiteral("parseTrainResponse", value.mode, `${field}.mode`, ["cross_validation", "fixed"]),
    alpha: expectNumber("parseTrainResponse", value.alpha, `${field}.alpha`),
    l1_ratio: expectNullableNumber("parseTrainResponse", value.l1_ratio, `${field}.l1_ratio`),
    n_nonzero: expectNumber("parseTrainResponse", value.n_nonzero, `${field}.n_nonzero`),
    cv_folds: expectNullableNumber("parseTrainResponse", value.cv_folds, `${field}.cv_folds`),
    cv_selection: expectNullableString("parseTrainResponse", value.cv_selection, `${field}.cv_selection`),
    cv_seed: expectNullableNumber("parseTrainResponse", value.cv_seed, `${field}.cv_seed`),
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

function expectFiniteTrainNumber(value: unknown, field: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`parseTrainResponse: ${field} must be finite`)
  }
  return value
}

function expectNonEmptyTrainString(value: unknown, field: string): string {
  const parsed = expectString("parseTrainResponse", value, field)
  if (parsed.length === 0) {
    throw new Error(`parseTrainResponse: ${field} must not be empty`)
  }
  return parsed
}

// The generated validators own the training responses' structure, including
// the evaluation and tuning reports, whose invariants the server checks once,
// where their artifacts are produced and reloaded. The server model leaves the
// diagnostic rows and loss history as open objects, so the UI shapes them here.
function trainResponseFromContract(response: GeneratedTrainResponse): TrainResponse {
  const p = "parseTrainResponse"
  return {
    ...response,
    feature_importance: parseArray(p, response.feature_importance, "feature_importance", parseFeatureImportanceRow),
    loss_history: parseArray(p, response.loss_history, "loss_history", parseLossHistoryEntry),
    double_lift: parseArray(p, response.double_lift, "double_lift", parseDoubleLiftRow),
    shap_summary: parseArray(p, response.shap_summary, "shap_summary", parseShapSummaryRow),
    feature_importance_loss: parseArray(p, response.feature_importance_loss, "feature_importance_loss", parseFeatureImportanceRow),
    ave_per_feature: parseArray(p, response.ave_per_feature, "ave_per_feature", parseAvePerFeatureRow),
    residuals_histogram: parseArray(p, response.residuals_histogram, "residuals_histogram", parseResidualHistogramRow),
    actual_vs_predicted: parseArray(p, response.actual_vs_predicted, "actual_vs_predicted", parseActualVsPredictedRow),
    lorenz_curve: parseArray(p, response.lorenz_curve, "lorenz_curve", parseLorenzCurvePoint),
    lorenz_curve_perfect: parseArray(p, response.lorenz_curve_perfect, "lorenz_curve_perfect", parseLorenzCurvePoint),
    pdp_data: parseArray(p, response.pdp_data, "pdp_data", parsePdpFeatureRow),
    glm_coefficients: parseArray(p, response.glm_coefficients, "glm_coefficients", parseGlmCoefficientRow),
    glm_relativities: parseArray(p, response.glm_relativities, "glm_relativities", parseGlmRelativityRow),
    glm_inference: response.glm_inference === null ? null : parseGlmInference(response.glm_inference),
    glm_smooth_terms: parseArray(p, response.glm_smooth_terms, "glm_smooth_terms", parseGlmSmoothTerm),
    glm_regularization: response.glm_regularization === null
      ? null
      : parseGlmRegularization(response.glm_regularization),
    ebm_terms: parseArray(p, response.ebm_terms, "ebm_terms", parseEbmTerm),
    diagnostics_errors: parseArray(p, response.diagnostics_errors, "diagnostics_errors", parseTrainDiagnosticsError),
    feature_selection: response.feature_selection === null
      ? null
      : parseTrainFeatureSelection(response.feature_selection),
  }
}

export function parseTrainResponse(value: unknown): TrainResponse {
  return trainResponseFromContract(expectGeneratedContract("TrainResponse", validateTrainResponse, value))
}

export function parseTrainStatusResponse(value: unknown): TrainStatusResponse {
  const status = expectGeneratedContract("TrainStatusResponse", validateTrainStatusResponse, value)
  return {
    ...status,
    train_loss_history: parseArray(
      "parseTrainStatusResponse",
      status.train_loss_history,
      "train_loss_history",
      parseLossHistoryEntry,
    ),
    result: status.result === null ? null : trainResponseFromContract(status.result),
    execution_metrics: optionalExecutionMetrics(
      "parseTrainStatusResponse",
      { execution_metrics: status.execution_metrics },
      "execution_metrics",
    ),
    feature_selection: status.feature_selection === null
      ? null
      : parseTrainFeatureSelection(status.feature_selection),
  }
}

// The generated validator owns the estimate's structure; these are the
// cross-field rules the UI relies on (TrainEstimateResponse and
// EvaluationPreviewPayload enforce the same ones on the server).
function checkEvaluationPreview(preview: EvaluationPreview): void {
  const selectionBounds = [
    preview.min_selection_train_rows,
    preview.max_selection_train_rows,
    preview.min_selection_validation_rows,
    preview.max_selection_validation_rows,
  ]
  if (preview.validation_method === "none") {
    if (preview.validation_fit_count !== 0 || selectionBounds.some((value) => value !== undefined)) {
      throw new Error("parseTrainEstimateResponse: no-validation preview must not contain selection bounds")
    }
  } else {
    const expectedCount = preview.validation_method === "single" ? 1 : preview.validation_fit_count
    if (
      preview.validation_fit_count !== expectedCount
      || (preview.validation_method === "cross_validation" && preview.validation_fit_count < 2)
      || selectionBounds.some((value) => value === undefined)
    ) {
      throw new Error("parseTrainEstimateResponse: validated preview has inconsistent fit count or row bounds")
    }
    if (
      preview.min_selection_train_rows! > preview.max_selection_train_rows!
      || preview.min_selection_validation_rows! > preview.max_selection_validation_rows!
    ) {
      throw new Error("parseTrainEstimateResponse: evaluation preview minimums must not exceed maximums")
    }
  }

  if (preview.strategy === "group") {
    if (preview.development_group_count === undefined || preview.final_test_group_count === undefined) {
      throw new Error("parseTrainEstimateResponse: group preview requires group counts")
    }
  } else if (preview.development_group_count !== undefined || preview.final_test_group_count !== undefined) {
    throw new Error("parseTrainEstimateResponse: only group preview may contain group counts")
  }

  if (preview.strategy === "temporal") {
    if (
      preview.development_date_range === undefined
      || (preview.final_test_rows > 0) !== (preview.final_test_date_range !== undefined)
    ) {
      throw new Error("parseTrainEstimateResponse: temporal preview has inconsistent date ranges")
    }
  } else if (preview.development_date_range !== undefined || preview.final_test_date_range !== undefined) {
    throw new Error("parseTrainEstimateResponse: only temporal preview may contain date ranges")
  }
}

function checkUnavailable(
  unavailable: TrainEstimateResponse["unavailable"],
): TrainEstimateUnavailable | null {
  if (unavailable === null) return null
  const { reason, blocking_node_id: blockingNodeId } = unavailable
  if (reason === "row_count_unprovable") {
    if (!blockingNodeId) {
      throw new Error("parseTrainEstimateResponse: row_count_unprovable names the blocking node")
    }
    return { reason, blocking_node_id: blockingNodeId }
  }
  if (blockingNodeId !== null) {
    throw new Error("parseTrainEstimateResponse: schema_unresolvable names no blocking node")
  }
  return { reason, blocking_node_id: null }
}

export function parseTrainEstimateResponse(value: unknown): TrainEstimate {
  const response = expectGeneratedContract("TrainEstimateResponse", validateTrainEstimateResponse, value)
  const preview = response.evaluation_preview ?? null
  if (preview !== null) checkEvaluationPreview(preview)
  const estimate: TrainEstimate = {
    ...response,
    unavailable: checkUnavailable(response.unavailable),
    evaluation_preview: preview,
  }
  // An estimate that cannot size its input has no memory figure to misread,
  // and an available one has every figure.
  const figures = [estimate.estimated_mb, estimate.training_mb, estimate.bytes_per_row]
  if (estimate.unavailable === null) {
    if (estimate.total_rows === null || figures.some((figure) => figure === null)) {
      throw new Error("parseTrainEstimateResponse: an available estimate requires a row total and memory figures")
    }
    return estimate
  }
  if (figures.some((figure) => figure !== null)) {
    throw new Error("parseTrainEstimateResponse: an unavailable estimate has no memory figures")
  }
  if (estimate.was_downsampled || estimate.warning !== null) {
    throw new Error("parseTrainEstimateResponse: an unavailable estimate has no downsampling verdict or warning")
  }
  if (estimate.gpu_vram_estimated_mb !== null || estimate.gpu_vram_available_mb !== null || estimate.gpu_warning !== null) {
    throw new Error("parseTrainEstimateResponse: an unavailable estimate has no GPU VRAM check")
  }
  if ((estimate.total_rows === null) !== (estimate.unavailable.reason === "row_count_unprovable")) {
    throw new Error("parseTrainEstimateResponse: only a row_count_unprovable estimate lacks a row total")
  }
  return estimate
}
