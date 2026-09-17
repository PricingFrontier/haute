import { describe, expect, it } from "vitest"

import {
  STEPPED_NODE_TYPES,
  isSteppedConfig,
  stepInputNames,
  steppedSurfaceFor,
} from "../polarsStepInputs"

describe("stepped surfaces", () => {
  it("maps each stepped node type to its start mode and input eligibility", () => {
    expect(STEPPED_NODE_TYPES).toEqual({
      polars: { start: "input", inputs: "edges" },
      dataInput: { start: "frame", inputs: "none" },
      externalFile: { start: "frame", inputs: "edges" },
      ratingStep: { start: "frame", inputs: "none" },
      modelScore: { start: "frame", inputs: "none" },
      scenarioExpander: { start: "frame", inputs: "none" },
    })
    expect(steppedSurfaceFor("polars")).toEqual({ start: "input", inputs: "edges" })
    expect(steppedSurfaceFor("output")).toBeUndefined()
    expect(steppedSurfaceFor("hasOwnProperty")).toBeUndefined()
  })

  it("derives the eligible step input names from the table, never from the chips", () => {
    expect(stepInputNames("polars", ["quotes", "rates"])).toEqual(["quotes", "rates"])
    expect(stepInputNames("dataInput", ["quotes"])).toEqual([])
    expect(stepInputNames("externalFile", ["quotes", "rates"])).toEqual(["quotes", "rates"])
    expect(stepInputNames("ratingStep", ["quotes"])).toEqual([])
    expect(() => stepInputNames("output", [])).toThrow('Node type "output" does not author steps.')
  })

  it("recognises a stepped config only on a stepped type with a steps list", () => {
    expect(isSteppedConfig("dataInput", { steps: [] })).toBe(true)
    expect(isSteppedConfig("dataInput", { code: "" })).toBe(false)
    expect(isSteppedConfig("scenarioExpander", { steps: 3 })).toBe(false)
    expect(isSteppedConfig("polars", null)).toBe(false)
  })
})
