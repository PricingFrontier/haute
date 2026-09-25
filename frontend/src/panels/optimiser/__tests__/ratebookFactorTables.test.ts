import { describe, expect, it } from "vitest"
import { factorTablesCsv } from "../ratebookFactorTables"

describe("factorTablesCsv", () => {
  it("writes one row per level, prefixed by its factor, over the union of columns", () => {
    const csv = factorTablesCsv({
      age_band: [
        { __factor_group__: "17-25", optimal_scenario_value: 1.1, quote_count: 40 },
        { __factor_group__: "26+", optimal_scenario_value: 0.95 },
      ],
      region: [{ __factor_group__: "North, East", optimal_scenario_value: 1 }],
    })

    expect(csv.split("\n")).toEqual([
      "factor,__factor_group__,optimal_scenario_value,quote_count",
      "age_band,17-25,1.1,40",
      "age_band,26+,0.95,",
      'region,"North, East",1,',
    ])
  })
})
