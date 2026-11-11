import { describe, expect, it } from "vitest"

import { columnNames, columnsBeforeStep, type ColumnSource } from "../derivedColumns"
import { canonicalStep, stepProblem, variablesBefore } from "../catalogue"
import { summarizeStep } from "../summary"
import type { Operand, Step } from "../types"

const col = (name: string) => ({ kind: "column" as const, name })
/** A frame-mode source over columns a and b. */
const AB: ColumnSource = { inputs: {}, frame: [{ name: "a", dtype: "Int64" }, { name: "b", dtype: "Int64" }], freeCode: new Map() }
const namesBefore = (steps: Step[], index: number) => columnNames(columnsBeforeStep(AB, steps, index))

describe("stepProblem", () => {
  it("accepts well-formed steps of every kind", () => {
    const steps: Step[] = [
      { id: "s", kind: "source", input: "quotes" },
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "is_null" }] },
      { id: "w", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a"), args: [{ kind: "literal", type: "number", value: 2 }] } },
      { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", branches: [{ match: "any", conditions: [{ column: "a", operator: "is_null" }], then: col("a") }], otherwise: col("b") } },
      { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ column: "a", agg: "sum", name: "s" }] },
      { id: "j", kind: "join", input: "rates", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r", maintain_order: "left_right" },
      { id: "fn", kind: "fill_null", columns: [], fill: { kind: "strategy", strategy: "forward" } },
      { id: "v", kind: "variable", name: "x", value: { kind: "literal", type: "number", value: 1 } },
      {
        id: "ow",
        kind: "with_column",
        name: "rn",
        expr: { type: "window", agg: "row_number", over: ["k"], order_by: [{ column: "a", descending: true }] },
      },
      { id: "rk", kind: "with_column", name: "rk", expr: { type: "window", agg: "dense_rank", of: { kind: "column", name: "a" }, over: [], descending: true } },
      { id: "q", kind: "with_column", name: "q", expr: { type: "window", agg: "quantile", of: { kind: "column", name: "a" }, over: ["k"], quantile: 0.9 } },
      { id: "cc", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), { kind: "literal", type: "text", value: "-" }, col("b")], separator: "" } },
      {
        id: "nl",
        kind: "with_column",
        name: "m",
        expr: { type: "conditional", branches: [{ match: "all", conditions: [{ column: "a", operator: "matches", value: { kind: "literal", type: "text", value: "^c" } }], then: col("a") }], otherwise: { kind: "literal", type: "null", value: null } },
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
      { id: "jv", kind: "join", input: "rates", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r", validate: "m:1", maintain_order: "left" },
      { id: "un", kind: "unique", columns: ["k"], keep: "none" },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
  })

  it("accepts a global constant as a condition value, a formula operand and a function argument", () => {
    const constant = { kind: "constant" as const, name: "threshold" }
    const steps: Step[] = [
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "value", operator: "gt", value: constant }] },
      { id: "w", kind: "with_column", name: "x", expr: { type: "binary", left: col("a"), op: "*", right: constant } },
      { id: "r", kind: "with_column", name: "y", expr: { type: "function", fn: "round", operand: col("a"), args: [constant] } },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
    expect(stepProblem({ id: "b", kind: "filter", match: "all", conditions: [{ column: "v", operator: "gt", value: { kind: "constant" } as never }] })).toMatch(/malformed value/)
  })

  it("accepts the renderer's own shorthand shapes and canonicalises them for the forms", () => {
    const shorthand: Step[] = [
      { id: "n", kind: "with_column", name: "m", expr: { type: "operand", operand: { kind: "literal", type: "null" } as never } },
      { id: "r", kind: "with_column", name: "rn", expr: { type: "window", agg: "row_number", over: ["k"], order_by: [{ column: "a" }] } as never },
      { id: "c", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), col("b")] } as never },
    ]
    // An order key without a direction is shorthand; a row number has no value.
    for (const step of shorthand) expect(stepProblem(step)).toBeNull()
    expect(canonicalStep(shorthand[0])).toEqual({ ...shorthand[0], expr: { type: "operand", operand: { kind: "literal", type: "null", value: null } } })
    expect(canonicalStep(shorthand[1])).toEqual({
      ...shorthand[1],
      expr: { type: "window", agg: "row_number", over: ["k"], order_by: [{ column: "a", descending: false }] },
    })
    expect(canonicalStep(shorthand[2])).toEqual({ ...shorthand[2], expr: { type: "concat", parts: [col("a"), col("b")], separator: "" } })
    expect(stepProblem({ id: "r", kind: "with_column", name: "s", expr: { type: "window", agg: "sum", of: { kind: "column", name: "a" }, over: [], order_by: [{ column: "a", descending: "yes" }] } })).toMatch(
      /malformed direction/,
    )
  })

  it("needs a window's value, a column or an expression, except for a row count and a row number", () => {
    const window = (agg: string, of?: unknown): Step => ({ id: "w", kind: "with_column", name: "s", expr: { type: "window", agg, ...(of ? { of } : {}), over: ["k"] } as never })
    expect(stepProblem(window("sum", col("a")))).toBeNull()
    expect(stepProblem(window("cum_sum", { kind: "expr", expr: { type: "binary", left: col("a"), op: "*", right: col("b") } }))).toBeNull()
    expect(stepProblem(window("len"))).toBeNull()
    expect(stepProblem(window("sum"))).toMatch(/missing the value it aggregates/)
    expect(stepProblem(window("len", col("a")))).toMatch(/counts rows and takes no value/)
    expect(stepProblem(window("mean", { kind: "literal", type: "number", value: 1 }))).toMatch(/aggregates a column or an expression/)
    expect(stepProblem(window("max", { kind: "expr", expr: { type: "binary", left: col("a") } }))).toMatch(/missing its operator/)
  })

  it("checks a window's row filter like an aggregation's", () => {
    const window = (where: unknown): Step => ({ id: "w", kind: "with_column", name: "s", expr: { type: "window", agg: "sum", of: { kind: "column", name: "a" }, over: ["k"], where } as never })
    expect(stepProblem(window({ match: "all", conditions: [{ column: "k", operator: "is_null" }] }))).toBeNull()
    expect(stepProblem(window("rows"))).toMatch(/malformed row filter/)
    expect(stepProblem(window({ conditions: [] }))).toMatch(/malformed row filter/)
    expect(stepProblem(window({ match: "all", conditions: [{ column: 3 }] }))).toMatch(/condition 1 is malformed/)
  })

  it("accepts nested expressions up to the renderer's depth and refuses them in literal-only positions", () => {
    const nested = (depth: number): Operand => {
      let operand: Operand = { kind: "literal", type: "number", value: 1 }
      // Alternating operators: a chain of one operator would count as one level.
      for (let i = 0; i < depth; i += 1) operand = { kind: "expr", expr: { type: "binary", left: operand, op: i % 2 ? "-" : "+", right: { kind: "literal", type: "number", value: 1 } } }
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
    expect(summarizeStep(ratio)).toBe("rate = round(a / b, 3)")
    expect(stepProblem({ id: "v", kind: "variable", name: "x", value: nested(1) })).toMatch(/plain value/)
    expect(stepProblem({ id: "f", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a"), args: [nested(1)] } })).toMatch(/function arguments/)
    expect(stepProblem({ id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "gt", value: { kind: "expr", expr: { type: "nope" } } }] })).toMatch(/unknown expression type/)
  })

  it("counts a chain of one operator as one level, like the renderer", () => {
    type Binary = Extract<Step, { kind: "with_column" }>["expr"]
    const chain = (terms: number, op: "+" | "-" | "*" | "/" | "//" | "%" | "**", side: "left" | "right" = "left"): Binary => {
      let expr: Binary = { type: "binary", left: col("c0"), op, right: col("c1") }
      for (let i = 2; i < terms; i += 1) {
        const nested: Operand = { kind: "expr", expr }
        expr = side === "left" ? { type: "binary", left: nested, op, right: col(`c${i}`) } : { type: "binary", left: col(`c${i}`), op, right: nested }
      }
      return expr
    }
    const step = (expr: Binary): Step => ({ id: "w", kind: "with_column", name: "t", expr })
    for (const op of ["+", "-", "*", "/", "//", "%"] as const) expect(stepProblem(step(chain(20, op)))).toBeNull()
    // A right-nested chain keeps every group, and `**` chains are bracketed: each counts.
    expect(stepProblem(step(chain(14, "+", "right")))).toMatch(/nests more than 12 levels/)
    expect(stepProblem(step(chain(14, "**")))).toMatch(/nests more than 12 levels/)
    // A twenty-term sum still fits under eleven enclosing functions.
    let deep: Binary = chain(20, "+")
    for (let i = 0; i < 11; i += 1) deep = { type: "function", fn: "abs", operand: { kind: "expr", expr: deep }, args: [] }
    expect(stepProblem(step(deep))).toBeNull()
  })

  it("keeps a statement step's comment and refuses one on a code step", () => {
    expect(stepProblem({ id: "l", kind: "limit", n: 5, comment: "# keep five\n" })).toBeNull()
    expect(canonicalStep({ id: "f", kind: "filter", match: "all", conditions: [], comment: "# x\n" })).toMatchObject({ comment: "# x\n" })
    expect(stepProblem({ id: "l", kind: "limit", n: 5, comment: 3 } as never)).toMatch(/malformed comment/)
    expect(stepProblem({ id: "c", kind: "free_code", code: "x = 1", comment: "# x\n" } as never)).toMatch(/malformed comment/)
  })

  it("accepts reshaping steps and dtype selectors and summarises them", () => {
    const steps: Step[] = [
      { id: "s", kind: "select", columns: ["region"], dtypes: ["Float64", "Int64"] },
      { id: "d", kind: "drop", columns: [], dtypes: ["String"] },
      { id: "g", kind: "group_by", keys: [], aggregations: [{ agg: "len", name: "n" }, { dtype: "Float64", agg: "mean", suffix: "_mean" }] },
      { id: "p", kind: "pivot", index: ["region"], on: "channel", columns: [{ value: { kind: "literal", type: "text", value: "web" }, name: "web" }], values: "premium", agg: "mean" },
      { id: "u", kind: "unpivot", on: ["premium", "sum_insured"], index: ["quote_id"], variable_name: "measure", value_name: "value" },
    ]
    for (const step of steps) expect(stepProblem(step)).toBeNull()
    expect(summarizeStep(steps[0])).toBe("region, every Float64, every Int64")
    expect(summarizeStep(steps[2])).toBe("whole frame: n = row count, *_mean = mean of every Float64")
    expect(summarizeStep(steps[3])).toBe("mean of premium by region into web")
    expect(summarizeStep(steps[4])).toBe("premium, sum_insured into measure / value")
    expect(namesBefore(steps, 5)).toEqual(["quote_id", "measure", "value"])
    expect(namesBefore([steps[0]], 1)).toEqual(["region", "a", "b"])
    expect(namesBefore([steps[2]], 1)).toEqual(["n"])
    expect(namesBefore([steps[3]], 1)).toEqual(["region", "web"])
    expect(stepProblem({ id: "g", kind: "group_by", keys: [], aggregations: [{ dtype: "Float64", agg: "mean", suffix: "_m", name: "x" }] })).toMatch(/mixes a column type/)
    expect(stepProblem({ id: "g", kind: "group_by", keys: [], aggregations: [{ agg: "mean", name: "x" }] })).toMatch(/missing its column/)
    expect(stepProblem({ id: "p", kind: "pivot", index: [], on: "c", columns: [{ value: col("c"), name: "n" }], values: "a", agg: "sum" })).toMatch(/plain value/)
    expect(stepProblem({ id: "p", kind: "pivot", index: [], on: "c", columns: [], values: "a", agg: "std" })).toMatch(/unsupported aggregate/)
    expect(stepProblem({ id: "u", kind: "unpivot", on: "premium", index: [], variable_name: "m", value_name: "v" })).toMatch(/missing its "on" setting/)
  })

  it("counts filter and fill nesting from the nested expression, like the renderer", () => {
    const nested = (depth: number): Operand => {
      let operand: Operand = { kind: "literal", type: "number", value: 1 }
      // Alternating operators: a chain of one operator would count as one level.
      for (let i = 0; i < depth; i += 1) operand = { kind: "expr", expr: { type: "binary", left: operand, op: i % 2 ? "-" : "+", right: { kind: "literal", type: "number", value: 1 } } }
      return operand
    }
    const filter = (value: Operand): Step => ({ id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "gt", value }] })
    const fill = (value: Operand): Step => ({ id: "n", kind: "fill_null", columns: ["a"], fill: { kind: "value", value } })
    expect(stepProblem(filter(nested(12)))).toBeNull()
    expect(stepProblem(filter(nested(13)))).toMatch(/nests more than 12 levels/)
    expect(stepProblem(fill(nested(12)))).toBeNull()
    expect(stepProblem(fill(nested(13)))).toMatch(/nests more than 12 levels/)
    // A row count in a filter condition's value is canonical as saved, with no value of its own.
    const shorthand = filter({ kind: "expr", expr: { type: "window", agg: "len", over: [] } as never })
    expect(stepProblem(shorthand)).toBeNull()
    expect(canonicalStep(shorthand)).toEqual(filter({ kind: "expr", expr: { type: "window", agg: "len", over: [] } }))
  })

  it("summarises the extended vocabulary in plain words", () => {
    expect(
      summarizeStep({ id: "ow", kind: "with_column", name: "rn", expr: { type: "window", agg: "row_number", over: ["k"], order_by: [{ column: "a", descending: true }] } }),
    ).toBe("rn = row number over k ordered by a desc")
    expect(summarizeStep({ id: "t", kind: "with_column", name: "tot", expr: { type: "window", agg: "sum", of: { kind: "column", name: "a" }, over: [] } })).toBe("tot = sum of a over all rows")
    expect(
      summarizeStep({ id: "cc", kind: "with_column", name: "key", expr: { type: "concat", parts: [col("a"), { kind: "literal", type: "null", value: null }], separator: "|" } }),
    ).toBe("key = join(a, null)")
    expect(
      summarizeStep({ id: "g", kind: "group_by", keys: [], aggregations: [{ column: "a", agg: "sum", name: "s", where: { match: "all", conditions: [] } }, { column: "", agg: "len", name: "n" }] }),
    ).toBe("whole frame: s = sum of a where …, n = row count")
    expect(summarizeStep({ id: "j", kind: "join", input: "rates", how: "left", left_on: ["k"], right_on: ["k"], suffix: "_r", validate: "m:1", maintain_order: "left_right" })).toBe("left join rates on k (m:1)")
  })

  it.each([
    ["function without args", { id: "w", kind: "with_column", name: "n", expr: { type: "function", fn: "round", operand: col("a") } }, /function arguments/],
    ["conditional without conditions", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", branches: [{ match: "all", then: col("a") }], otherwise: col("b") } }, /conditions/],
    ["sum across without parts", { id: "s", kind: "with_column", name: "t", expr: { type: "sum_horizontal" } }, /missing its parts/],
    ["sum across with a malformed part", { id: "s", kind: "with_column", name: "t", expr: { type: "sum_horizontal", parts: [col("a"), "b"] } }, /part 2 is missing its value/],
    ["coalesce without parts", { id: "f", kind: "with_column", name: "t", expr: { type: "coalesce" } }, /missing its parts/],
    ["coalesce with malformed text", { id: "f", kind: "with_column", name: "t", expr: { type: "coalesce", parts: [col("a"), col("b")], text: 3 } }, /malformed formula text/],
    ["conditional without branches", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", branches: [], otherwise: col("b") } }, /missing its branches/],
    ["conditional in the old shape", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", match: "all", conditions: [], then: col("a"), otherwise: col("b") } }, /missing its branches/],
    ["later branch without a match", { id: "c", kind: "with_column", name: "c", expr: { type: "conditional", branches: [{ match: "all", conditions: [], then: col("a") }, { conditions: [], then: col("a") }], otherwise: col("b") } }, /branch 2 is missing its match mode/],
    ["aggregation without name", { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ column: "a", agg: "sum" }] }, /missing its name/],
    ["row count without name", { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ agg: "len" }] }, /missing its name/],
    ["aggregation without column", { id: "g", kind: "group_by", keys: ["k"], aggregations: [{ agg: "sum", name: "t" }] }, /missing its column/],
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
    ["window with a malformed order list", { id: "w", kind: "with_column", name: "n", expr: { type: "window", agg: "row_number", over: ["k"], order_by: [{ descending: true }] } }, /order row 1 is missing "column"/],
    ["window with a text quantile", { id: "w", kind: "with_column", name: "n", expr: { type: "window", agg: "quantile", of: { kind: "column", name: "a" }, over: [], quantile: "0.5" } }, /malformed quantile/],
    ["concat without parts", { id: "w", kind: "with_column", name: "n", expr: { type: "concat", separator: "" } }, /missing its parts/],
    ["concat with a malformed part", { id: "w", kind: "with_column", name: "n", expr: { type: "concat", parts: [col("a"), "b"], separator: "" } }, /part 2 is missing its value/],
    ["null literal carrying a value", { id: "w", kind: "with_column", name: "n", expr: { type: "operand", operand: { kind: "literal", type: "null", value: 0 } } }, /malformed value/],
    ["aggregation with a malformed row filter", { id: "g", kind: "group_by", keys: [], aggregations: [{ column: "a", agg: "sum", name: "s", where: { match: "all" } }] }, /filter is missing its conditions/],
    ["join with a malformed validation", { id: "j", kind: "join", input: "r", how: "left", left_on: [], right_on: [], suffix: "", maintain_order: "left_right", validate: 1 }, /malformed validation/],
    ["join without a row order", { id: "j", kind: "join", input: "r", how: "left", left_on: [], right_on: [], suffix: "" }, /maintain_order/],
    ["join with an unknown row order", { id: "j", kind: "join", input: "r", how: "left", left_on: [], right_on: [], suffix: "", maintain_order: "sideways" }, /unknown row order/],
    ["cast with a malformed format", { id: "c", kind: "cast", strict: true, casts: [{ column: "a", dtype: "Date", format: 7 }] }, /malformed format/],
    [
      "membership with an unknown number type",
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "is_in", values: [], number_type: "complex" }] },
      /unknown number type/,
    ],
    ["unknown kind", { id: "x", kind: "explode" }, /Unknown step kind/],
    ["prototype kind", { id: "x", kind: "constructor" }, /Unknown step kind/],
    ["prototype kind toString", { id: "x", kind: "toString" }, /Unknown step kind/],
    ["prototype kind __proto__", { id: "x", kind: "__proto__" }, /Unknown step kind/],
    [
      "prototype literal type",
      { id: "f", kind: "filter", match: "all", conditions: [{ column: "a", operator: "eq", value: { kind: "literal", type: "constructor", value: 1 } }] },
      /unsupported value type/,
    ],
    ["not an object", "nope", /not an object/],
  ])("rejects %s without throwing anywhere downstream", (_label, step, pattern) => {
    expect(stepProblem(step)).toMatch(pattern)
    const steps = [{ id: "s", kind: "source", input: "quotes" } as Step, step as Step, { id: "l", kind: "limit", n: 1 } as Step]
    expect(() => columnsBeforeStep({ inputs: { quotes: [{ name: "a", dtype: "Int64" }] }, frame: [], freeCode: new Map() }, steps, 3)).not.toThrow()
    expect(() => variablesBefore(steps, 3)).not.toThrow()
    expect(() => summarizeStep(steps[2])).not.toThrow()
  })
})
