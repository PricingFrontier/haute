import { describe, expect, it } from "vitest"

import { columnsBeforeStep } from "../derivedColumns"
import { canonicalStep, stepProblem, summarizeStep, variablesBefore } from "../catalogue"
import type { Operand, Step } from "../types"

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
      {
        id: "ow",
        kind: "with_column",
        name: "rn",
        expr: { type: "window", agg: "row_number", column: "", over: ["k"], orderBy: [{ column: "a", descending: true }] },
      },
      { id: "rk", kind: "with_column", name: "rk", expr: { type: "window", agg: "dense_rank", column: "a", over: [], descending: true } },
      { id: "q", kind: "with_column", name: "q", expr: { type: "window", agg: "quantile", column: "a", over: ["k"], quantile: 0.9 } },
      { id: "cc", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), { kind: "literal", type: "text", value: "-" }, col("b")], separator: "" } },
      {
        id: "nl",
        kind: "with_column",
        name: "m",
        expr: { type: "conditional", match: "all", conditions: [{ column: "a", operator: "matches", value: { kind: "literal", type: "text", value: "^c" } }], then: col("a"), otherwise: { kind: "literal", type: "null", value: null } },
      },
      {
        id: "gw",
        kind: "group_by",
        keys: [],
        aggregations: [
          { column: "a", agg: "quantile", name: "p50", quantile: 0.5 },
          { column: "a", agg: "sum", name: "north", where: { match: "all", conditions: [{ column: "k", operator: "eq", value: { kind: "literal", type: "text", value: "north" } }] } },
        ],
      },
      { id: "jv", kind: "join", input: "rates", how: "left", leftOn: ["k"], rightOn: ["k"], suffix: "_r", validate: "m:1", maintainOrder: "left" },
      { id: "un", kind: "unique", columns: ["k"], keep: "none" },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
  })

  it("accepts the renderer's own shorthand shapes and canonicalises them for the forms", () => {
    const shorthand: Step[] = [
      { id: "n", kind: "with_column", name: "m", expr: { type: "operand", operand: { kind: "literal", type: "null" } as never } },
      { id: "r", kind: "with_column", name: "rn", expr: { type: "window", agg: "row_number", over: ["k"], orderBy: [{ column: "a" }] } as never },
      { id: "c", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), col("b")] } as never },
    ]
    for (const step of shorthand) expect(stepProblem(step)).toBeNull()
    expect(canonicalStep(shorthand[0])).toEqual({ ...shorthand[0], expr: { type: "operand", operand: { kind: "literal", type: "null", value: null } } })
    expect(canonicalStep(shorthand[1])).toEqual({
      ...shorthand[1],
      expr: { type: "window", agg: "row_number", column: "", over: ["k"], orderBy: [{ column: "a", descending: false }] },
    })
    expect(canonicalStep(shorthand[2])).toEqual({ ...shorthand[2], expr: { type: "concat", parts: [col("a"), col("b")], separator: "" } })
    expect(stepProblem({ id: "r", kind: "with_column", name: "s", expr: { type: "window", agg: "sum", over: ["k"] } })).toMatch(/missing its aggregate/)
    expect(stepProblem({ id: "r", kind: "with_column", name: "s", expr: { type: "window", agg: "sum", column: "a", over: [], orderBy: [{ column: "a", descending: "yes" }] } })).toMatch(
      /malformed direction/,
    )
  })

  it("accepts nested expressions up to the renderer's depth and refuses them in literal-only positions", () => {
    const nested = (depth: number): Operand => {
      let operand: Operand = { kind: "literal", type: "number", value: 1 }
      for (let i = 0; i < depth; i += 1) operand = { kind: "expr", expr: { type: "binary", left: operand, op: "+", right: { kind: "literal", type: "number", value: 1 } } }
      return operand
    }
    const withOperand = (operand: Operand): Step => ({ id: "w", kind: "with_column", name: "n", expr: { type: "operand", operand } })
    expect(stepProblem(withOperand(nested(11)))).toBeNull()
    expect(stepProblem(withOperand(nested(12)))).toMatch(/nests more than 12 levels/)
    const ratio: Step = {
      id: "r",
      kind: "with_column",
      name: "rate",
      expr: { type: "function", fn: "round", operand: { kind: "expr", expr: { type: "binary", left: col("a"), op: "/", right: col("b") } }, args: [{ kind: "literal", type: "number", value: 3 }] },
    }
    expect(stepProblem(ratio)).toBeNull()
    expect(summarizeStep(ratio)).toBe("rate = round((a / b), 3)")
    expect(stepProblem({ id: "v", kind: "variable", name: "x", value: nested(1) })).toMatch(/plain value/)
    expect(stepProblem({ id: "f", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a"), args: [nested(1)] } })).toMatch(/function arguments/)
    expect(stepProblem({ id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "gt", value: { kind: "expr", expr: { type: "nope" } } }] })).toMatch(/unknown expression type/)
  })

  it("accepts reshaping steps and dtype selectors and summarises them", () => {
    const steps: Step[] = [
      { id: "s", kind: "select", columns: ["region"], dtypes: ["Float64", "Int64"] },
      { id: "d", kind: "drop", columns: [], dtypes: ["String"] },
      { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "", agg: "len", name: "n" }, { dtype: "Float64", agg: "mean", suffix: "_mean" }] },
      { id: "p", kind: "pivot", index: ["region"], on: "channel", columns: [{ value: { kind: "literal", type: "text", value: "web" }, name: "web" }], values: "premium", agg: "mean" },
      { id: "u", kind: "unpivot", on: ["premium", "sum_insured"], index: ["quote_id"], variableName: "measure", valueName: "value" },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
    expect(summarizeStep(steps[0])).toBe("region, every Float64, every Int64")
    expect(summarizeStep(steps[2])).toBe("whole frame: n = count(), *_mean = mean(every Float64)")
    expect(summarizeStep(steps[3])).toBe("mean of premium by region into web")
    expect(summarizeStep(steps[4])).toBe("premium, sum_insured into measure/value")
    expect(columnsBeforeStep(["a", "b"], steps, 5)).toEqual(["quote_id", "measure", "value"])
    expect(columnsBeforeStep(["a", "b"], [steps[0]], 1)).toEqual(["region", "a", "b"])
    expect(columnsBeforeStep(["a", "b"], [steps[2]], 1)).toEqual(["n"])
    expect(columnsBeforeStep(["a", "b"], [steps[3]], 1)).toEqual(["region", "web"])
    expect(stepProblem({ id: "g", kind: "group_by", keys: [], aggregations: [{ dtype: "Float64", agg: "mean", suffix: "_m", name: "x" }] })).toMatch(/mixes a column type/)
    expect(stepProblem({ id: "g", kind: "group_by", keys: [], aggregations: [{ agg: "mean", name: "x" }] })).toMatch(/missing "column"/)
    expect(stepProblem({ id: "p", kind: "pivot", index: [], on: "c", columns: [{ value: col("c"), name: "n" }], values: "a", agg: "sum" })).toMatch(/plain value/)
    expect(stepProblem({ id: "p", kind: "pivot", index: [], on: "c", columns: [], values: "a", agg: "std" })).toMatch(/unsupported aggregate/)
    expect(stepProblem({ id: "u", kind: "unpivot", on: "premium", index: [], variableName: "m", valueName: "v" })).toMatch(/missing its "on" setting/)
  })

  it("counts filter and fill nesting from the nested expression, like the renderer", () => {
    const nested = (depth: number): Operand => {
      let operand: Operand = { kind: "literal", type: "number", value: 1 }
      for (let i = 0; i < depth; i += 1) operand = { kind: "expr", expr: { type: "binary", left: operand, op: "+", right: { kind: "literal", type: "number", value: 1 } } }
      return operand
    }
    const filter = (value: Operand): Step => ({ id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "gt", value }] })
    const fill = (value: Operand): Step => ({ id: "n", kind: "fill_null", columns: ["a"], fill: { kind: "value", value } })
    expect(stepProblem(filter(nested(12)))).toBeNull()
    expect(stepProblem(filter(nested(13)))).toMatch(/nests more than 12 levels/)
    expect(stepProblem(fill(nested(12)))).toBeNull()
    expect(stepProblem(fill(nested(13)))).toMatch(/nests more than 12 levels/)
    // A filter condition value carrying the renderer's columnless-window shorthand is canonicalised.
    const shorthand = filter({ kind: "expr", expr: { type: "window", agg: "len", over: [] } as never })
    expect(stepProblem(shorthand)).toBeNull()
    expect(canonicalStep(shorthand)).toEqual(filter({ kind: "expr", expr: { type: "window", agg: "len", column: "", over: [] } }))
  })

  it("summarises the extended vocabulary in plain words", () => {
    expect(
      summarizeStep({ id: "ow", kind: "with_column", name: "rn", expr: { type: "window", agg: "row_number", column: "", over: ["k"], orderBy: [{ column: "a", descending: true }] } }),
    ).toBe("rn = row_number over k ordered by a desc")
    expect(summarizeStep({ id: "t", kind: "with_column", name: "tot", expr: { type: "window", agg: "sum", column: "a", over: [] } })).toBe("tot = sum of a over all rows")
    expect(
      summarizeStep({ id: "cc", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), { kind: "literal", type: "null", value: null }], separator: "|" } }),
    ).toBe("key = join(a, null)")
    expect(
      summarizeStep({ id: "g", kind: "group_by", keys: [], aggregations: [{ column: "a", agg: "sum", name: "s", where: { match: "all", conditions: [] } }, { column: "", agg: "len", name: "n" }] }),
    ).toBe("whole frame: s = sum(a) where …, n = count()")
    expect(summarizeStep({ id: "j", kind: "join", input: "rates", how: "left", leftOn: ["k"], rightOn: ["k"], suffix: "_r", validate: "m:1" })).toBe("left join rates on k (m:1)")
  })

  it.each([
    ["function without args", { id: "w", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a") } }, /function arguments/],
    ["conditional without conditions", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", match: "all", then: col("a"), otherwise: col("b") } }, /conditions/],
    ["aggregation without name", { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ column: "a", agg: "sum" }] }, /missing "column" or "name"/],
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
    ["window with a malformed order list", { id: "w", kind: "with_column", name: "n", expr: { type: "window", agg: "row_number", column: "", over: ["k"], orderBy: [{ descending: true }] } }, /order row 1 is missing "column"/],
    ["window with a text quantile", { id: "w", kind: "with_column", name: "n", expr: { type: "window", agg: "quantile", column: "a", over: [], quantile: "0.5" } }, /malformed quantile/],
    ["concat without parts", { id: "w", kind: "with_column", name: "n", expr: { type: "concat", separator: "" } }, /missing its parts/],
    ["concat with a malformed part", { id: "w", kind: "with_column", name: "n", expr: { type: "concat", parts: [col("a"), "b"], separator: "" } }, /part 2 is missing its value/],
    ["null literal carrying a value", { id: "w", kind: "with_column", name: "n", expr: { type: "operand", operand: { kind: "literal", type: "null", value: 0 } } }, /malformed value/],
    ["aggregation with a malformed row filter", { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "a", agg: "sum", name: "s", where: { match: "all" } }] }, /filter is missing its conditions/],
    ["join with a malformed validation", { id: "j", kind: "join", input: "r", how: "left", leftOn: [], rightOn: [], suffix: "", validate: 1 }, /malformed validation/],
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
