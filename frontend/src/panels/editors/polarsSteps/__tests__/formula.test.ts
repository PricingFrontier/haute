import { describe, expect, it } from "vitest"

import { FormulaError, callAtCaret, displayFormula, formulaText, parseFormula, renameColumnInFormula, withoutFormulaText } from "../formula"
import type { Expr, Operand } from "../types"

const col = (name: string): Operand => ({ kind: "column", name })
const num = (value: number): Operand => ({ kind: "literal", type: "number", value })
const ex = (expr: Expr): Operand => ({ kind: "expr", expr })
const binary = (left: Operand, op: Extract<Expr, { type: "binary" }>["op"], right: Operand): Extract<Expr, { type: "binary" }> => ({ type: "binary", left, op, right })

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
    expect(parse("-2 ** 2")).toEqual(binary(num(0), "-", ex(binary(num(2), "**", num(2)))))
    expect(parse("-premium ** 2")).toEqual(binary(num(0), "-", ex(binary(col("premium"), "**", num(2)))))
    expect(parse("(-2) ** 2")).toEqual(binary(num(-2), "**", num(2)))
    expect(parse("2 ** -2")).toEqual(binary(num(2), "**", num(-2)))
    expect(parse("2 ** -2 ** 2")).toEqual(binary(num(2), "**", ex(binary(num(0), "-", ex(binary(num(2), "**", num(2)))))))
    expect(parse("-premium")).toEqual(binary(num(0), "-", col("premium")))
    expect(parse("premium")).toEqual({ type: "operand", operand: col("premium") })
    expect(parse("1")).toEqual({ type: "operand", operand: num(1) })
    // a bare value typed as a formula still carries its text, so it stays a formula
    expect(parseFormula(" premium ")).toEqual({ type: "operand", operand: col("premium"), text: "premium" })
    expect(displayFormula(parseFormula("total_premium"))).toBe("total_premium")
  })

  it("renders negative bases with brackets so their trees survive a reparse", () => {
    const negativeBase = binary(num(-2), "**", num(2))
    const wrappedNegativeBase = binary(ex({ type: "operand", operand: num(-2) }), "**", num(2))
    const doublyWrappedNegativeBase = binary(ex({ type: "operand", operand: ex({ type: "operand", operand: num(-2) }) }), "**", num(2))
    expect(formulaText(negativeBase)).toBe("(-2) ** 2")
    expect(formulaText(wrappedNegativeBase)).toBe("(-2) ** 2")
    expect(formulaText(doublyWrappedNegativeBase)).toBe("(-2) ** 2")
    expect(withoutFormulaText(parseFormula(formulaText(negativeBase)!))).toEqual(negativeBase)
    expect(displayFormula({ ...negativeBase, text: "-2 ** 2" })).toBe("(-2) ** 2")
  })

  it("reads defined variables as variables and everything else as columns", () => {
    const parse = (text: string, variables?: string[]) => withoutFormulaText(parseFormula(text, variables))
    expect(parse("premium * rate", ["rate"])).toEqual(binary(col("premium"), "*", { kind: "variable", name: "rate" }))
    expect(parse("premium * rate")).toEqual(binary(col("premium"), "*", col("rate")))
    // a column that shares a variable's name is written in backticks
    expect(formulaText(binary(col("rate"), "*", { kind: "variable", name: "rate" }), ["rate"])).toBe("`rate` * rate")
    expect(parse("`rate` * rate", ["rate"])).toEqual(binary(col("rate"), "*", { kind: "variable", name: "rate" }))
  })

  it("renders names only where the formula grammar can preserve their identity", () => {
    const parse = (text: string, variables: string[]) => withoutFormulaText(parseFormula(text, variables))
    expect(formulaText({ type: "operand", operand: { kind: "variable", name: "round" } }, ["round"])).toBe("round")
    expect(parse("round", ["round"])).toEqual({ type: "operand", operand: { kind: "variable", name: "round" } })
    expect(formulaText({ type: "operand", operand: { kind: "variable", name: "date" } }, ["date"])).toBe("date")
    expect(parse("date", ["date"])).toEqual({ type: "operand", operand: { kind: "variable", name: "date" } })
    expect(formulaText({ type: "operand", operand: { kind: "variable", name: "true" } }, ["true"])).toBeNull()
    expect(displayFormula(parseFormula("rate + 1", ["rate"]), [])).toBeNull()
    expect(formulaText({ type: "operand", operand: { kind: "column", name: "has`tick" } })).toBeNull()
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
    ["(premium + 1", /Expected "\)" after number 1/],
    ["premium +", /ends too early/],
    ["premium 2", /Unexpected number 2/],
    ["nope(premium)", /Unknown function "nope"/],
    ["round(premium)", /takes 2 arguments/],
    ["round(premium, 'x')", /expects a number/],
    ["cast(premium, Enum)", /expects a type/],
    ["1e999", /finite number/],
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

  it.each([
    ["(premium + 1", 12],
    ["premium +", 9],
    ["premium 2", 8],
    ["nope(premium)", 0],
    ["round(premium)", 13],
    ["round(premium, 'x')", 15],
    ["premium $ 2", 8],
    ["'unclosed", 0],
  ])("says where reading stopped in %s", (text, position) => {
    try {
      parseFormula(text)
      throw new Error("expected a FormulaError")
    } catch (error) {
      expect(error).toBeInstanceOf(FormulaError)
      expect((error as FormulaError).position).toBe(position)
    }
  })

  it("reads function names in any case and keeps the catalogue's spelling in the text", () => {
    expect(parseFormula("ROUND(premium, 2)")).toMatchObject({ type: "function", fn: "round", text: "round(premium, 2)" })
    expect(parseFormula("Upper(region) + Lower(region)")).toMatchObject({ type: "binary", text: "upper(region) + lower(region)" })
  })
})

describe("renameColumnInFormula", () => {
  it("renames column references only, never quoted text, functions, keywords or variables", () => {
    expect(renameColumnInFormula('replace(primium, "primium", "discount")', "primium", "premium")).toBe('replace(premium, "primium", "discount")')
    expect(renameColumnInFormula("round(round, 2) + `round`", "round", "rnd")).toBe("round(rnd, 2) + rnd")
    expect(renameColumnInFormula("rate * rate_x", "rate", "base", ["rate"])).toBe("rate * rate_x")
    expect(renameColumnInFormula("a + b", "a", "sum insured")).toBe("`sum insured` + b")
  })

  it("leaves a function's plain-value arguments alone, a type named like the column included", () => {
    expect(renameColumnInFormula("cast(Float64, Float64)", "Float64", "Float32")).toBe("cast(Float32, Float64)")
    expect(renameColumnInFormula("round(abs(x), 2) + clip(x, 0, 1)", "x", "y")).toBe("round(abs(y), 2) + clip(y, 0, 1)")
    expect(renameColumnInFormula("(x + 1) * x", "x", "y")).toBe("(y + 1) * y")
  })
})

describe("callAtCaret", () => {
  it("names the catalogue function and argument the caret is in", () => {
    expect(callAtCaret("round(premium, ", 15)).toEqual({ fn: "round", arg: 1 })
    expect(callAtCaret("round(premium", 13)).toEqual({ fn: "round", arg: 0 })
    expect(callAtCaret("ROUND(premium, 2)", 15)).toEqual({ fn: "round", arg: 1 })
    expect(callAtCaret("replace(region, ',', ", 21)).toEqual({ fn: "replace", arg: 2 })
    expect(callAtCaret("abs(premium) + ", 15)).toBeNull()
    expect(callAtCaret("(premium + ", 11)).toBeNull()
    expect(callAtCaret("nope(premium, ", 14)).toBeNull()
  })
})
