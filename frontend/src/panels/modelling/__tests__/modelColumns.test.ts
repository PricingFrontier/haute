import { describe, expect, it } from "vitest"

import { modelColumnChoices } from "../modelColumns"

const columns = [
  { name: "claims", dtype: "Int64" },
  { name: "exposure", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "log_offset", dtype: "Float64" },
]
const names = (list: { name: string }[]) => list.map((column) => column.name)

describe("modelColumnChoices", () => {
  it("keeps each column in one role and offers only numeric weights and offsets", () => {
    const choices = modelColumnChoices(columns, { target: "claims", weight: "exposure", offset: "log_offset" })
    expect(names(choices.targetColumns)).toEqual(["claims", "region"])
    expect(names(choices.weightColumns)).toEqual(["exposure"])
    expect(names(choices.offsetColumns)).toEqual(["log_offset"])
  })

  it("offers every numeric column while nothing is chosen", () => {
    const choices = modelColumnChoices(columns, { target: "", weight: "", offset: "" })
    expect(names(choices.targetColumns)).toEqual(names(columns))
    expect(names(choices.weightColumns)).toEqual(["claims", "exposure", "log_offset"])
    expect(names(choices.offsetColumns)).toEqual(["claims", "exposure", "log_offset"])
  })
})
