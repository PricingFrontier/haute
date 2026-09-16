import { describe, expect, it } from "vitest"

import { columnsBeforeStep } from "../derivedColumns"
import { canonicalStep, stepProblem, summarizeStep, variablesBefore } from "../catalogue"
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
