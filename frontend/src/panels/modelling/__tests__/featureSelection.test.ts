import { describe, expect, it } from "vitest"

import {
  finalSelectedFeatureNames,
  selectedFeatureColumns,
  roleColumnReasons,
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

  it("derives the final CatBoost selection from the ticked features", () => {
    const eligible = columns.filter(({ name }) => ["age", "region"].includes(name))

    expect(finalSelectedFeatureNames({ feature_columns: ["age"] }, eligible)).toEqual(new Set(["age"]))
    // Features are opt-in: nothing ticked selects nothing.
    expect(finalSelectedFeatureNames({}, eligible)).toEqual(new Set())
  })

  it("keeps a ticked role column dormant, as the backend's selected_feature_columns does", () => {
    expect(selectedFeatureColumns({ target: "age", feature_columns: ["age", "region", "region"] }))
      .toEqual(["region"])
    expect(selectedFeatureColumns({ feature_columns: ["age", "region"] })).toEqual(["age", "region"])
  })

  it("names each column's modelling role", () => {
    expect(roleColumnReasons({
      target: "target",
      weight: "weight",
      offset: "target",
      id_columns: ["id", "weight"],
      evaluation: { strategy: "group", group_column: "group" },
    })).toEqual(new Map([
      ["target", "target"],
      ["weight", "weight"],
      ["id", "identifier"],
      ["group", "evaluation"],
    ]))
  })

})
