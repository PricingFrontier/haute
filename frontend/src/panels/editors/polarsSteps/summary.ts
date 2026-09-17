/**
 * One-line summaries for step cards. Values and formulas are printed in
 * formula notation (`premium * 1.05`, `'north'`, `date('2024-01-01')`), so a
 * summary reads like the formula box; `?` stands for a name not yet filled
 * in, and expressions text cannot express (windows, conditionals, text
 * joins) are described in words.
 */
import { COLUMNLESS_AGGREGATIONS, CONDITION_OPERATORS } from "./catalogue"
import { formulaText, literalText } from "./formula"
import type { AggregationSpec, Condition, Expr, Operand, Step } from "./types"

function operandText(operand: Operand | undefined): string {
  if (!operand) return "?"
  if (operand.kind === "expr") return operand.expr.type === "binary" ? `(${exprText(operand.expr)})` : exprText(operand.expr)
  if (operand.kind === "literal") return literalText(operand)
  return operand.name || "?"
}

function aggregationText(entry: AggregationSpec): string {
  if ("dtype" in entry) return `*${entry.suffix || "?"} = ${entry.agg}(every ${entry.dtype})`
  const call = entry.agg === "len" ? "count()" : `${entry.agg}(${entry.column || "?"})`
  return `${entry.name || "?"} = ${call}${entry.where ? " where …" : ""}`
}

function columnsAndTypesText(columns: string[], dtypes: string[] | undefined): string {
  const parts = [...columns, ...(dtypes ?? []).map((d) => `every ${d}`)]
  return parts.join(", ") || "no columns"
}

function conditionText(condition: Condition): string {
  const op = CONDITION_OPERATORS.find((o) => o.value === condition.operator)
  const label = op?.label ?? condition.operator
  if (op?.takes === "none") return `${condition.column || "?"} ${label}`
  if (op?.takes === "values") return `${condition.column || "?"} ${label} ${(condition.values ?? []).map(operandText).join(", ")}`
  return `${condition.column || "?"} ${label} ${operandText(condition.value)}`
}

function exprText(expr: Expr): string {
  if (expr.type === "operand" || expr.type === "binary" || expr.type === "function") {
    // A formula typed as text is summarised exactly as typed; otherwise as
    // the formula grammar would print it. A formula holding an expression
    // text cannot express is spelled out part by part below.
    if (typeof expr.text === "string" && expr.text.length > 0) return expr.text
    const text = formulaText(expr)
    if (text !== null) return text
  }
  switch (expr.type) {
    case "operand":
      return operandText(expr.operand)
    case "binary":
      return `${operandText(expr.left)} ${expr.op} ${operandText(expr.right)}`
    case "function":
      return `${expr.fn}(${[operandText(expr.operand), ...expr.args.map(operandText)].join(", ")})`
    case "conditional":
      return `if ${expr.conditions.map(conditionText).join(expr.match === "all" ? " and " : " or ")} then ${operandText(expr.then)} else ${operandText(expr.otherwise)}`
    case "window": {
      const subject = COLUMNLESS_AGGREGATIONS.has(expr.agg) ? expr.agg : `${expr.agg} of ${expr.column || "?"}`
      const over = expr.over.length ? ` over ${expr.over.join(", ")}` : " over all rows"
      const order = expr.orderBy?.length ? ` ordered by ${expr.orderBy.map((k) => `${k.column || "?"}${k.descending ? " desc" : ""}`).join(", ")}` : ""
      return `${subject}${over}${order}`
    }
    case "concat":
      return `join(${expr.parts.map(operandText).join(", ")})`
  }
}

/** One line describing the step for its card header. */
export function summarizeStep(step: Step): string {
  switch (step.kind) {
    case "source":
      return step.input || "choose an input"
    case "filter":
      return step.conditions.map(conditionText).join(step.match === "all" ? " and " : " or ") || "no conditions"
    case "with_column":
      return `${step.name || "?"} = ${exprText(step.expr)}`
    case "select":
    case "drop":
      return columnsAndTypesText(step.columns, step.dtypes)
    case "rename":
      return step.renames.map((r) => `${r.from || "?"} → ${r.to || "?"}`).join(", ")
    case "cast":
      return step.casts.map((c) => `${c.column || "?"} → ${c.dtype}`).join(", ")
    case "sort":
      return step.keys.map((k) => `${k.column || "?"}${k.descending ? " desc" : ""}`).join(", ")
    case "unique":
      return step.columns.length ? `by ${step.columns.join(", ")}` : "all columns"
    case "group_by":
      return `${step.keys.length ? `by ${step.keys.join(", ")}` : "whole frame"}: ${step.aggregations.map(aggregationText).join(", ")}`
    case "join":
      return `${step.how} join ${step.input || "?"} on ${step.leftOn.join(", ") || (step.how === "cross" ? "everything" : "?")}${step.validate ? ` (${step.validate})` : ""}`
    case "concat":
      return step.inputs.join(", ") || "no inputs"
    case "fill_null":
      return `${step.columns.length ? step.columns.join(", ") : "all columns"} with ${step.fill.kind === "value" ? operandText(step.fill.value) : step.fill.strategy}`
    case "limit":
      return `${step.n} rows`
    case "variable":
      return `${step.name || "?"} = ${operandText(step.value)}`
    case "free_code":
      return step.code.split(/\r?\n/).find((line) => line.trim().length > 0)?.trim() || "Write Python code"
    case "pivot":
      return `${step.agg} of ${step.values || "?"} by ${step.index.join(", ") || "?"} into ${step.columns.map((c) => c.name || "?").join(", ") || "?"}`
    case "unpivot":
      return `${step.on.join(", ") || "?"} into ${step.variableName || "?"}/${step.valueName || "?"}`
  }
}
