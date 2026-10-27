import { FoldVertical, ListFilter, Calculator, CaseSensitive } from "lucide-react"
import { describe, expect, it } from "vitest"

import { CALCULATIONS, UNLISTED_FUNCTIONS, calculationEntry, calculationExpr, calculationLabel, calculationOf, stepIcon, stepTitle } from "../calculations"
import { FUNCTIONS, exprProblem, literal } from "../catalogue"
import { parseFormula } from "../formula"
import type { Expr, Operand, Step } from "../types"

const col = (name: string): Operand => ({ kind: "column", name })
const clip: Expr = { type: "function", fn: "clip", operand: col("load"), args: [literal("number", 0.8), literal("number", 1.2)] }
const withColumn = (expr: Expr): Step => ({ id: "w", kind: "with_column", name: "out", expr })

describe("the Computed as calculations", () => {
  it("place every catalogue function once, listed or left to formulas", () => {
    const listed = CALCULATIONS.map((c) => c.id).filter((id) => FUNCTIONS.some((f) => f.value === id))
    expect(new Set(listed).size).toBe(listed.length)
    expect([...listed, ...UNLISTED_FUNCTIONS].sort()).toEqual(FUNCTIONS.map((f) => f.value).sort())
    expect(listed.filter((id) => UNLISTED_FUNCTIONS.has(id as never))).toEqual([])
  })

  it("start an expression the step check accepts and name it back", () => {
    for (const calculation of CALCULATIONS) {
      const expr = calculationExpr(calculation.id)
      expect(exprProblem(expr, "The expression"), calculation.id).toBeNull()
      expect(calculationOf(expr), calculation.id).toBe(calculation.id)
    }
  })

  it("read a typed formula and an unlisted function a formula shows as the Formula", () => {
    expect(calculationOf(parseFormula("clip(load, 0.8, 1.2)"))).toBe("formula")
    expect(calculationOf({ type: "function", fn: "try_cast", operand: col("district"), args: [literal("text", "Int32")] })).toBe("formula")
    const unshowable: Expr = { type: "function", fn: "fill_null", operand: { kind: "expr", expr: { type: "window", agg: "mean", of: col("load"), over: ["region"] } }, args: [literal("number", 0)] }
    expect(calculationOf(unshowable)).toBe("fill_null")
    expect(calculationEntry("fill_null").name).toBe("Fill missing with")
  })

  it("list a qualifier after the name where the name alone would mislead", () => {
    expect(calculationLabel(calculationEntry("divide"))).toBe("Divide (missing where the divisor is zero)")
    expect(calculationLabel(calculationEntry("clip"))).toBe("Clip")
  })

  it("carry the computed value and same-kind arguments, never wrapping", () => {
    expect(calculationExpr("round", clip)).toEqual({ type: "function", fn: "round", operand: col("load"), args: [literal("number", 0)] })
    const replace: Expr = { type: "function", fn: "replace", operand: col("code"), args: [literal("text", "-"), literal("text", "")] }
    expect(calculationExpr("replace_all", replace)).toEqual({ ...replace, fn: "replace_all" })
    expect(calculationExpr("sum_across", { type: "window", agg: "sum", of: col("premium"), over: ["policy_id"] })).toEqual({ type: "sum_horizontal", parts: [col("premium"), col("")] })
    expect(calculationExpr("window", { type: "operand", operand: literal("number", 5) })).toEqual({ type: "window", agg: "sum", of: col(""), over: [] })
    expect(calculationExpr("formula", clip)).toEqual({ ...clip, text: "clip(load, 0.8, 1.2)" })
    expect(calculationExpr("formula", calculationExpr("if_then"))).toEqual(calculationExpr("formula"))
    const typed = parseFormula("clip(load, 0.8, 1.2)")
    expect(calculationExpr("clip", typed)).toEqual(clip)
  })

  it("title and icon an Add column card by its calculation and any other card by its kind", () => {
    expect(stepTitle(withColumn(clip))).toBe("Clip")
    expect(stepIcon(withColumn(clip))).toBe(FoldVertical)
    expect(stepTitle(withColumn(parseFormula("clip(load, 0.8, 1.2)")))).toBe("Formula")
    expect(stepIcon(withColumn(parseFormula("premium * 2")))).toBe(Calculator)
    expect(stepTitle(withColumn(calculationExpr("strip")))).toBe("Trim whitespace")
    expect(stepIcon(withColumn(calculationExpr("strip")))).toBe(CaseSensitive)
    const filter: Step = { id: "f", kind: "filter", match: "all", conditions: [] }
    expect(stepTitle(filter)).toBe("Filter rows")
    expect(stepIcon(filter)).toBe(ListFilter)
  })
})
