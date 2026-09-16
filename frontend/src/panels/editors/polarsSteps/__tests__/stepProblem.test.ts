import { describe, expect, it } from "vitest"

import { columnsBeforeStep } from "../derivedColumns"
import { stepProblem, summarizeStep, variablesBefore } from "../catalogue"
import type { Step } from "../types"

const col = (name: string) => ({ kind: "column" as const, name })

describe("stepProblem", () => {
  it("accepts well-formed steps of every kind", () => {
    const steps: Step[] = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "is_null" }] },
      { id: "w", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a"), args: [{ kind: "literal", type: "number", value: 2 }] } },
      { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", match: "any", conditions: [{ column: "a", operator: "is_null" }], then: col("a"), otherwise: col("b") } },
      { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ column: "a", agg: "sum", name: "s" }] },
      { id: "j", kind: "join", input: "rates", how: "left", leftOn: ["k"], rightOn: ["k"], suffix: "_r" },
      { id: "fn", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "forward" } },
      { id: "v", kind: "variable", name: "x", value: { kind: "literal", type: "number", value: 1 } },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
  })

  it.each([
    ["function without args", { id: "w", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a") } }, /function arguments/],
    ["conditional without conditions", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", match: "all", then: col("a"), otherwise: col("b") } }, /conditions/],
    ["aggregation without name", { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ column: "a", agg: "sum" }] }, /missing "name"/],
    ["filter without conditions", { id: "f", kind: "filter", match: "all" }, /conditions/],
    [
      "membership with an unsupported literal type",
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "is_in", values: [{ kind: "literal", type: "string", value: "north" }] }] },
      /unsupported value type/,
    ],
    [
      "membership with a non-literal entry",
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "is_in", values: [{ kind: "column", name: "b" }] }] },
      /malformed values/,
    ],
    [
      "literal whose value does not match its type",
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "eq", value: { kind: "literal", type: "number", value: "12" } }] },
      /malformed value/,
    ],
    ["condition without column", { id: "f", kind: "filter", match: "all", conditions: [{ operator: "eq" }] }, /condition 1 is malformed/],
    ["unknown kind", { id: "x", kind: "explode" }, /Unknown step kind/],
    ["not an object", "nope", /not an object/],
  ])("rejects %s without throwing anywhere downstream", (_label, step, pattern) => {
    expect(stepProblem(step)).toMatch(pattern)
    const steps = [{ id: "s", kind: "source", input: "quotes" } as Step, step as Step, { id: "l", kind: "limit", n: 1 } as Step]
    expect(() => columnsBeforeStep(["a"], steps, 3)).not.toThrow()
    expect(() => variablesBefore(steps, 3)).not.toThrow()
    expect(() => summarizeStep(steps[2])).not.toThrow()
  })
})
