import { describe, it, expect } from "vitest"
import {
  asModelScoreDetail,
  modelScoreTitle,
  modelScorePrediction,
  modelScoreFeatureColumns,
  resolveContributionFeatureValue,
} from "../modelScoreHelpers"
import type {
  ModelScoreContributionDetail,
  ModelScoreExplanationDetail,
  ModelScoreNodeDetail,
  TraceNodeDetail,
} from "../../types/trace"

const baseDetail = (overrides: Partial<ModelScoreNodeDetail> = {}): ModelScoreNodeDetail => ({
  detail_type: "model_score",
  ...overrides,
})

const contribution = (
  overrides: Partial<ModelScoreContributionDetail> = {},
): ModelScoreContributionDetail => ({
  feature: "age",
  shap_value: 1,
  ...overrides,
})

describe("asModelScoreDetail", () => {
  it("returns the same detail object", () => {
    const detail = { detail_type: "model_score" } as TraceNodeDetail
    expect(asModelScoreDetail(detail)).toBe(detail)
  })
})

describe("modelScoreTitle", () => {
  it("returns default when no identity", () => {
    expect(modelScoreTitle(baseDetail())).toBe("Model Score")
  })

  it("includes version when registered_model and version are set", () => {
    const detail = baseDetail({
      model_identity: { registered_model: "claims_glm", version: "3" },
    })
    expect(modelScoreTitle(detail)).toBe("Model: claims_glm v3")
  })

  it("omits version when registered_model has no version", () => {
    const detail = baseDetail({ model_identity: { registered_model: "claims_glm" } })
    expect(modelScoreTitle(detail)).toBe("Model: claims_glm")
  })

  it("falls back to run_id when no registered_model", () => {
    const detail = baseDetail({ model_identity: { run_id: "run-42" } })
    expect(modelScoreTitle(detail)).toBe("Model run: run-42")
  })

  it("falls back to source_type when no registered_model or run_id", () => {
    const detail = baseDetail({ model_identity: { source_type: "catboost" } })
    expect(modelScoreTitle(detail)).toBe("Model source: catboost")
  })

  it("returns default when identity is present but empty", () => {
    const detail = baseDetail({ model_identity: {} })
    expect(modelScoreTitle(detail)).toBe("Model Score")
  })
})

describe("modelScorePrediction", () => {
  it("reports a prediction when prediction_value key is present", () => {
    const detail = baseDetail({ prediction_value: 0.42 })
    expect(modelScorePrediction(detail)).toEqual({ hasPrediction: true, value: 0.42 })
  })

  it("treats an explicit null prediction_value as present", () => {
    const detail = baseDetail({ prediction_value: null })
    expect(modelScorePrediction(detail)).toEqual({ hasPrediction: true, value: null })
  })

  it("reports no prediction when key is absent", () => {
    expect(modelScorePrediction(baseDetail())).toEqual({
      hasPrediction: false,
      value: undefined,
    })
  })
})

describe("modelScoreFeatureColumns", () => {
  it("returns the columns when present and non-empty", () => {
    const cols = ["age", "region"]
    expect(modelScoreFeatureColumns(baseDetail({ feature_columns: cols }))).toBe(cols)
  })

  it("returns an empty array when feature_columns is an empty array", () => {
    expect(modelScoreFeatureColumns(baseDetail({ feature_columns: [] }))).toEqual([])
  })

  it("returns an empty array when feature_columns is missing", () => {
    expect(modelScoreFeatureColumns(baseDetail())).toEqual([])
  })

  it("returns an empty array when feature_columns is not an array", () => {
    const detail = baseDetail({
      feature_columns: "nope" as unknown as string[],
    })
    expect(modelScoreFeatureColumns(detail)).toEqual([])
  })
})

describe("resolveContributionFeatureValue", () => {
  it("prefers the contribution's own feature_value", () => {
    const detail = baseDetail({ feature_values: { age: 10 } })
    const explanation: ModelScoreExplanationDetail = { feature_values: { age: 20 } }
    const c = contribution({ feature_value: 99 })
    expect(resolveContributionFeatureValue(detail, explanation, c)).toEqual({
      hasValue: true,
      value: 99,
    })
  })

  it("returns an explicit null feature_value from the contribution", () => {
    const c = contribution({ feature_value: null })
    expect(resolveContributionFeatureValue(baseDetail(), undefined, c)).toEqual({
      hasValue: true,
      value: null,
    })
  })

  it("falls back to explanation.feature_values when contribution lacks one", () => {
    const explanation: ModelScoreExplanationDetail = { feature_values: { age: 20 } }
    const c = contribution()
    expect(resolveContributionFeatureValue(baseDetail(), explanation, c)).toEqual({
      hasValue: true,
      value: 20,
    })
  })

  it("falls back to detail.feature_values when explanation has no matching key", () => {
    const detail = baseDetail({ feature_values: { age: 10 } })
    const explanation: ModelScoreExplanationDetail = { feature_values: { region: "x" } }
    const c = contribution()
    expect(resolveContributionFeatureValue(detail, explanation, c)).toEqual({
      hasValue: true,
      value: 10,
    })
  })

  it("uses detail.feature_values when explanation is undefined", () => {
    const detail = baseDetail({ feature_values: { age: 10 } })
    const c = contribution()
    expect(resolveContributionFeatureValue(detail, undefined, c)).toEqual({
      hasValue: true,
      value: 10,
    })
  })

  it("returns hasValue false when no source has the feature", () => {
    const detail = baseDetail({ feature_values: { region: "x" } })
    const explanation: ModelScoreExplanationDetail = { feature_values: { other: 1 } }
    const c = contribution()
    expect(resolveContributionFeatureValue(detail, explanation, c)).toEqual({
      hasValue: false,
      value: undefined,
    })
  })

  it("returns hasValue false when nothing provides feature values", () => {
    const c = contribution()
    expect(resolveContributionFeatureValue(baseDetail(), undefined, c)).toEqual({
      hasValue: false,
      value: undefined,
    })
  })
})
