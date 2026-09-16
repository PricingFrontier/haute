import { describe, expect, it } from "vitest"

import {
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

  it("derives final selections for CatBoost and GLM", () => {
    const eligible = columns.filter(({ name }) => ["age", "region"].includes(name))

    expect(
      finalSelectedFeatureNames({ exclude: ["region"] }, eligible, "catboost"),
    ).toEqual(new Set(["age"]))
    expect(
      finalSelectedFeatureNames(
        {
          terms: { region: { type: "categorical" }, a2: { type: "expression", expr: "age ** 2" } },
          exclude: ["age"],
        },
        eligible,
        "glm",
      ),
    ).toEqual(new Set(["region", "age"]))
    expect(
      finalSelectedFeatureNames(
        { terms: {}, interactions: [{ factors: ["age", "region"], include_main: true }] },
        eligible,
        "glm",
      ),
    ).toEqual(new Set(["age", "region"]))
  })

})
