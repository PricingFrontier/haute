import { describe, expect, it } from "vitest"

import { FormulaError, displayFormula, formulaText, parseFormula, withoutFormulaText } from "../formula"
import type { Expr, Operand } from "../types"

const col = (name: string): Operand => ({ kind: "column", name })
const num = (value: number): Operand => ({ kind: "literal", type: "number", value })
const ex = (expr: Expr): Operand => ({ kind: "expr", expr })
const binary = (left: Operand, op: Extract<Expr, { type: "binary" }>["op"], right: Operand): Expr => ({ type: "binary", left, op, right })

describe("formula text", () => {
  it.each([
    "premium + tax",
    "(premium + tax) * 1.05 / 12",
    "premium + tax * 2",
    "premium - (tax + fee)",
    "premium * (tax // 3)",
    "(premium ** 2) * 3",
    "premium ** (tax ** 2)",
    "premium + tax + fee + levy",
    "round(premium / sum_insured * 1000, 3)",
    "upper(region)",
    "cast(premium, Int64)",
    "fill_null(premium, 0)",
    "replace(region, 'north', 'N')",
    "`my column` + 1",
    "'text' + region",
    "date('2024-01-01')",
    "-3 * premium",
  ])("round-trips %s", (text) => {
    expect(formulaText(parseFormula(text))).toBe(text)
  })

  it("keeps the text exactly as typed, brackets and spacing included", () => {
    const parsed = parseFormula("  premium + (tax * 2)  ")
    expect(parsed).toMatchObject({ type: "binary", text: "premium + (tax * 2)" })
    expect(displayFormula(parsed)).toBe("premium + (tax * 2)")
    expect(formulaText(parsed)).toBe("premium + tax * 2")
    expect(displayFormula(parseFormula("round((a + b), 2)"))).toBe("round((a + b), 2)")
    expect(withoutFormulaText(parseFormula("premium + (tax * 2)"))).toEqual(binary(col("premium"), "+", ex(binary(col("tax"), "*", num(2)))))
    // stale text that no longer describes the expression is not shown
    const annotated = (text: string): Expr => ({ type: "binary", left: col("premium"), op: "-", right: col("tax"), text })
    expect(displayFormula(annotated(""))).toBe("")
    expect(displayFormula(annotated("premium + tax"))).toBe("premium - tax")
    expect(displayFormula(annotated("(premium"))).toBe("premium - tax")
  })

  it("parses precedence and brackets into the nested schema", () => {
    const parse = (text: string) => withoutFormulaText(parseFormula(text))
    expect(parse("premium + tax * 2")).toEqual(binary(col("premium"), "+", ex(binary(col("tax"), "*", num(2)))))
    expect(parse("(premium + tax) * 2")).toEqual(binary(ex(binary(col("premium"), "+", col("tax"))), "*", num(2)))
    expect(parse("a - b - c")).toEqual(binary(ex(binary(col("a"), "-", col("b"))), "-", col("c")))
    expect(parse("a ** b ** c")).toEqual(binary(col("a"), "**", ex(binary(col("b"), "**", col("c")))))
    expect(parse("-premium")).toEqual(binary(num(0), "-", col("premium")))
    expect(parseFormula("premium")).toEqual({ type: "operand", operand: col("premium") })
    expect(parseFormula("1")).toEqual({ type: "operand", operand: num(1) })
  })

  it("reads defined variables as variables and everything else as columns", () => {
    const parse = (text: string, variables?: string[]) => withoutFormulaText(parseFormula(text, variables))
    expect(parse("premium * rate", ["rate"])).toEqual(binary(col("premium"), "*", { kind: "variable", name: "rate" }))
    expect(parse("premium * rate")).toEqual(binary(col("premium"), "*", col("rate")))
    // a column that shares a variable's name is written in backticks
    expect(formulaText(binary(col("rate"), "*", { kind: "variable", name: "rate" }), ["rate"])).toBe("`rate` * rate")
    expect(parse("`rate` * rate", ["rate"])).toEqual(binary(col("rate"), "*", { kind: "variable", name: "rate" }))
  })

  it("parses functions with their typed arguments", () => {
    const parse = (text: string) => withoutFormulaText(parseFormula(text))
    expect(parse("round(premium / 12, 2)")).toEqual({
      type: "function",
      fn: "round",
      operand: ex(binary(col("premium"), "/", num(12))),
      args: [num(2)],
    })
    expect(parse("cast(premium, 'Float32')")).toMatchObject({ fn: "cast", args: [{ kind: "literal", type: "text", value: "Float32" }] })
    expect(parse("slice(region, -1, 1)")).toMatchObject({ fn: "slice", args: [num(-1), num(1)] })
    expect(parse("abs(premium) + 1")).toEqual(binary(ex({ type: "function", fn: "abs", operand: col("premium"), args: [] }), "+", num(1)))
  })

  it.each([
    ["", /Enter a formula/],
    ["(premium + 1", /closing bracket/],
    ["premium +", /ends too early/],
    ["premium 2", /Unexpected number 2/],
    ["nope(premium)", /Unknown function "nope"/],
    ["round(premium)", /takes 2 arguments/],
    ["round(premium, 'x')", /expects a number/],
    ["cast(premium, Enum)", /expects a type/],
    ["'unclosed", /Unclosed quote/],
    ["premium $ 2", /Unexpected "\$"/],
  ])("explains why %s cannot be read", (text, message) => {
    expect(() => parseFormula(text)).toThrow(FormulaError)
    expect(() => parseFormula(text)).toThrow(message)
  })

  it("has no text for expressions the formula grammar cannot express", () => {
    expect(formulaText({ type: "window", agg: "mean", column: "premium", over: ["region"] })).toBeNull()
    expect(formulaText(binary(col("premium"), "-", ex({ type: "window", agg: "mean", column: "premium", over: ["region"] })))).toBeNull()
    expect(formulaText({ type: "concat", parts: [col("a"), col("b")], separator: "-" })).toBeNull()
  })
})
