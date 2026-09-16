import { describe, expect, it } from "vitest"

import { FormulaError, formulaText, parseFormula } from "../formula"
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

  it("parses precedence and brackets into the nested schema", () => {
    expect(parseFormula("premium + tax * 2")).toEqual(binary(col("premium"), "+", ex(binary(col("tax"), "*", num(2)))))
    expect(parseFormula("(premium + tax) * 2")).toEqual(binary(ex(binary(col("premium"), "+", col("tax"))), "*", num(2)))
    expect(parseFormula("a - b - c")).toEqual(binary(ex(binary(col("a"), "-", col("b"))), "-", col("c")))
    expect(parseFormula("a ** b ** c")).toEqual(binary(col("a"), "**", ex(binary(col("b"), "**", col("c")))))
    expect(parseFormula("-premium")).toEqual(binary(num(0), "-", col("premium")))
    expect(parseFormula("premium")).toEqual({ type: "operand", operand: col("premium") })
    expect(parseFormula("1")).toEqual({ type: "operand", operand: num(1) })
  })

  it("reads defined variables as variables and everything else as columns", () => {
    expect(parseFormula("premium * rate", ["rate"])).toEqual(binary(col("premium"), "*", { kind: "variable", name: "rate" }))
    expect(parseFormula("premium * rate")).toEqual(binary(col("premium"), "*", col("rate")))
    // a column that shares a variable's name is written in backticks
    expect(formulaText(binary(col("rate"), "*", { kind: "variable", name: "rate" }), ["rate"])).toBe("`rate` * rate")
    expect(parseFormula("`rate` * rate", ["rate"])).toEqual(binary(col("rate"), "*", { kind: "variable", name: "rate" }))
  })

  it("parses functions with their typed arguments", () => {
    expect(parseFormula("round(premium / 12, 2)")).toEqual({
      type: "function",
      fn: "round",
      operand: ex(binary(col("premium"), "/", num(12))),
      args: [num(2)],
    })
    expect(parseFormula("cast(premium, 'Float32')")).toMatchObject({ fn: "cast", args: [{ kind: "literal", type: "text", value: "Float32" }] })
    expect(parseFormula("slice(region, -1, 1)")).toMatchObject({ fn: "slice", args: [num(-1), num(1)] })
    expect(parseFormula("abs(premium) + 1")).toEqual(binary(ex({ type: "function", fn: "abs", operand: col("premium"), args: [] }), "+", num(1)))
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
