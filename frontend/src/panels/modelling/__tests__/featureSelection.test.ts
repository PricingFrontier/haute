import { describe, expect, it } from "vitest"

import {
  cleanupFeatureDependencies,
  finalSelectedFeatureNames,
  roleColumns,
  type ModellingColumn,
} from "../featureSelection"

const columns: ModellingColumn[] = [
  { name: "target", dtype: "Float64" },
  { name: "weight", dtype: "Float64" },
  { name: "offset", dtype: "Float64" },
  { name: "fold", dtype: "Int64" },
  { name: "id", dtype: "String" },
  { name: "date", dtype: "Date" },
  { name: "group", dtype: "String" },
  { name: "age", dtype: "Int64" },
  { name: "region", dtype: "String" },
]

describe("feature-selection transitions", () => {
  it("treats only the active evaluation key, plus metadata roles, as non-features", () => {
    const base = {
      target: "target",
      weight: "weight",
      offset: "offset",
      fold_column: "fold",
      id_columns: ["id"],
    }

    expect(
      roleColumns({
        ...base,
        evaluation: {
          strategy: "temporal",
          date_column: "date",
          group_column: "group",
        },
      }),
    ).toEqual(new Set(["target", "weight", "offset", "fold", "id", "date"]))
    expect(
      roleColumns({
        ...base,
        evaluation: {
          strategy: "group",
          date_column: "date",
          group_column: "group",
        },
      }),
    ).toEqual(new Set(["target", "weight", "offset", "fold", "id", "group"]))
    expect(
      roleColumns({
        ...base,
        evaluation: {
          strategy: "random",
          date_column: "date",
          group_column: "group",
        },
      }),
    ).toEqual(new Set(["target", "weight", "offset", "fold", "id"]))
    expect(
      roleColumns({
        ...base,
        evaluation: {
          strategy: "temporal",
          date_column: "date",
        },
      }),
    ).toEqual(new Set(["target", "weight", "offset", "fold", "id", "date"]))
  })

  it("derives final selections for CatBoost and both GLM modes", () => {
    const eligible = columns.filter(({ name }) => ["age", "region"].includes(name))

    expect(
      finalSelectedFeatureNames({ exclude: ["region"] }, eligible, "catboost"),
    ).toEqual(new Set(["age"]))
    expect(
      finalSelectedFeatureNames(
        {
          terms: { region: { type: "categorical" } },
          exclude: [],
        },
        eligible,
        "glm",
      ),
    ).toEqual(new Set(["region"]))
    expect(
      finalSelectedFeatureNames(
        { all_factors: true, exclude: ["age"] },
        eligible,
        "glm",
      ),
    ).toEqual(new Set(["region"]))
  })

  it("removes only affected dependency entries", () => {
    expect(
      cleanupFeatureDependencies(
        {
          monotone_constraints: { age: 1, severity: -1 },
          terms: {
            age: { type: "linear" },
            severity: { type: "linear" },
          },
          interactions: [
            { factors: ["age", "severity"], include_main: true },
            { factors: ["severity", "region"], include_main: false },
          ],
          custom: "untouched",
        },
        ["age"],
      ),
    ).toEqual({
      monotone_constraints: { severity: -1 },
      terms: { severity: { type: "linear" } },
      interactions: [
        { factors: ["severity", "region"], include_main: false },
      ],
    })
    expect(cleanupFeatureDependencies({ custom: true }, ["age"])).toEqual({})
  })

})
