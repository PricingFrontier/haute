/**
 * Summaries for collapsed step cards, as parts the card renders: column
 * names as chips, values in their code colours, operator words muted, and an
 * unset part as a placeholder naming what is missing ("column", "name"). A
 * step whose main column is not chosen yet is a single prompt ("Choose a
 * column…"). Values and formulas are printed in formula notation
 * (`premium * 1.05`, `'north'`, `date('2024-01-01')`), so a summary reads
 * like the formula box; expressions text cannot express (windows,
 * conditionals, text joins) are described in words. `summarizeStep` joins
 * the parts into plain text for titles and tests.
 */
import { AGGREGATIONS, COLUMNLESS_AGGREGATIONS, CONDITION_OPERATORS, FILL_STRATEGY_OPTIONS, WINDOW_AGGREGATIONS } from "./catalogue"
import { formulaText, literalText } from "./formula"
import type { AggregationSpec, Condition, Expr, LiteralOperand, Operand, Step } from "./types"

export type SummaryPart =
  | { kind: "text" | "op" | "column" | "code" | "missing" | "prompt"; text: string }
  | { kind: "value"; text: string; tone: "string" | "literal" }
  /** Punctuation that attaches to the part before it (", "). */
  | { kind: "sep"; text: string }

const text = (value: string): SummaryPart => ({ kind: "text", text: value })
const op = (value: string): SummaryPart => ({ kind: "op", text: value })
const code = (value: string): SummaryPart => ({ kind: "code", text: value })
const sep = (value: string): SummaryPart => ({ kind: "sep", text: value })
const missing = (what: string): SummaryPart => ({ kind: "missing", text: what })
const column = (name: string, what = "column"): SummaryPart => (name ? { kind: "column", text: name } : missing(what))
const prompt = (value: string): SummaryPart[] => [{ kind: "prompt", text: value }]

function valuePart(operand: LiteralOperand): SummaryPart {
  return { kind: "value", text: literalText(operand), tone: operand.type === "text" || operand.type === "date" ? "string" : "literal" }
}

/** `items` separated by commas. */
function list<T>(items: T[], part: (item: T) => SummaryPart[]): SummaryPart[] {
  return items.flatMap((item, index) => [...(index > 0 ? [sep(",")] : []), ...part(item)])
}

function operandParts(operand: Operand | undefined): SummaryPart[] {
  if (!operand) return [missing("value")]
  if (operand.kind === "literal") return [valuePart(operand)]
  if (operand.kind === "column") return [column(operand.name)]
  if (operand.kind === "variable") return [operand.name ? code(operand.name) : missing("variable")]
  const inner = exprParts(operand.expr)
  return operand.expr.type === "binary" && inner.length === 1 && inner[0].kind === "code" ? [code(`(${inner[0].text})`)] : inner
}

function conditionParts(condition: Condition): SummaryPart[] {
  const operator = CONDITION_OPERATORS.find((o) => o.value === condition.operator)
  const head = [column(condition.column), op(operator?.label ?? condition.operator)]
  if (operator?.takes === "none") return head
  if (operator?.takes === "values") {
    const values = condition.values ?? []
    return [...head, ...(values.length ? list(values, (v) => [valuePart(v)]) : [missing("values")])]
  }
  return [...head, ...operandParts(condition.value)]
}

function conditionsParts(conditions: Condition[], match: "all" | "any"): SummaryPart[] {
  return conditions.flatMap((c, index) => [...(index > 0 ? [op(match === "all" ? "and" : "or")] : []), ...conditionParts(c)])
}

function exprParts(expr: Expr): SummaryPart[] {
  if (expr.type === "operand" || expr.type === "binary" || expr.type === "function") {
    // A formula typed as text is summarised exactly as typed; otherwise as
    // the formula grammar would print it.
    if (typeof expr.text === "string") return expr.text.length > 0 ? [code(expr.text)] : [missing("formula")]
    if (expr.type === "operand") return operandParts(expr.operand)
    const formula = formulaText(expr)
    if (formula !== null) return [code(formula)]
  }
  switch (expr.type) {
    case "binary":
      return [...operandParts(expr.left), op(expr.op), ...operandParts(expr.right)]
    case "function":
      return [code(expr.fn), ...operandParts(expr.operand)]
    case "conditional":
      return [text("if"), ...conditionsParts(expr.conditions, expr.match), text("then"), ...operandParts(expr.then), text("else"), ...operandParts(expr.otherwise)]
    case "window": {
      const label = WINDOW_AGGREGATIONS.find((a) => a.value === expr.agg)?.label ?? expr.agg
      const subject = COLUMNLESS_AGGREGATIONS.has(expr.agg) ? [text(label)] : [text(label), op("of"), column(expr.column)]
      const over = expr.over.length ? [op("over"), ...list(expr.over, (name) => [column(name)])] : [op("over all rows")]
      const order = expr.orderBy?.length ? [op("ordered by"), ...list(expr.orderBy, (k) => [column(k.column), ...(k.descending ? [op("desc")] : [])])] : []
      return [...subject, ...over, ...order]
    }
    case "concat":
      return [code(`join(${expr.parts.map((part) => partsText(operandParts(part))).join(", ")})`)]
  }
}

/** `name = function of column`, as the aggregation row reads. */
export function aggregationParts(entry: AggregationSpec): SummaryPart[] {
  const label = AGGREGATIONS.find((a) => a.value === entry.agg)?.label ?? entry.agg
  if ("dtype" in entry) return [entry.suffix ? code(`*${entry.suffix}`) : missing("suffix"), op("="), text(label), op("of every"), text(entry.dtype)]
  const subject = entry.agg === "len" ? [text(label)] : [text(label), op("of"), column(entry.column)]
  return [column(entry.name, "name"), op("="), ...subject, ...(entry.where ? [op("where …")] : [])]
}

function columnsAndTypes(columns: string[], dtypes: string[] | undefined): SummaryPart[] {
  const named = columns.filter((c) => c.length > 0)
  const types = dtypes ?? []
  if (named.length === 0 && types.length === 0) return prompt("Choose columns…")
  return [...list(named, (c) => [column(c)]), ...(named.length && types.length ? [sep(",")] : []), ...list(types, (d) => [op("every"), text(d)])]
}

/** The parts describing a step on its collapsed card. */
export function summaryParts(step: Step): SummaryPart[] {
  switch (step.kind) {
    case "source":
      return step.input ? [code(step.input)] : prompt("Choose an input…")
    case "filter":
      if (step.conditions.length === 0) return [text("no conditions")]
      if (step.conditions.every((c) => !c.column)) return prompt("Choose a column…")
      return conditionsParts(step.conditions, step.match)
    case "with_column":
      if (!step.name && exprParts(step.expr).every((p) => p.kind === "missing")) return prompt("Choose a name…")
      return [column(step.name, "name"), op("="), ...exprParts(step.expr)]
    case "select":
    case "drop":
      return columnsAndTypes(step.columns, step.dtypes)
    case "rename":
      if (step.renames.every((r) => !r.from)) return prompt("Choose a column…")
      return list(step.renames, (r) => [column(r.from), op("→"), column(r.to, "name")])
    case "cast":
      if (step.casts.every((c) => !c.column)) return prompt("Choose a column…")
      return list(step.casts, (c) => [column(c.column), op("→"), text(c.dtype)])
    case "sort":
      if (step.keys.every((k) => !k.column)) return prompt("Choose a column…")
      return list(step.keys, (k) => [column(k.column), ...(k.descending ? [op("desc")] : [])])
    case "unique":
      return step.columns.length ? [op("by"), ...list(step.columns, (c) => [column(c)])] : [text("all columns")]
    case "group_by":
      return [
        ...(step.keys.length ? [op("by"), ...list(step.keys, (k) => [column(k)])] : [text("whole frame")]),
        sep(":"),
        ...list(step.aggregations, aggregationParts),
      ]
    case "join": {
      if (!step.input) return prompt("Choose an input…")
      const keys = step.how === "cross" ? [text("every row")] : step.leftOn.length
        ? list(step.leftOn.map((left, i) => [left, step.rightOn[i] ?? ""] as const), ([left, right]) =>
            left === right ? [column(left)] : [column(left), op("="), column(right, "key")])
        : [missing("keys")]
      return [text(`${step.how} join`), code(step.input), op("on"), ...keys, ...(step.validate ? [op(`(${step.validate})`)] : [])]
    }
    case "concat":
      return step.inputs.length ? list(step.inputs, (name) => [code(name)]) : prompt("Choose inputs…")
    case "fill_null": {
      const target = step.columns.length ? list(step.columns, (c) => [column(c)]) : [text("all columns")]
      const chosen = step.fill
      const fill = chosen.kind === "value"
        ? operandParts(chosen.value)
        : [text(FILL_STRATEGY_OPTIONS.find((s) => s.value === chosen.strategy)?.label ?? chosen.strategy)]
      return [...target, op("with"), ...fill]
    }
    case "limit":
      return [op("first"), { kind: "value", text: String(step.n), tone: "literal" }, text("rows")]
    case "variable":
      return [step.name ? code(step.name) : missing("name"), op("="), ...operandParts(step.value)]
    case "free_code": {
      const first = step.code.split(/\r?\n/).find((line) => line.trim().length > 0)?.trim()
      return first ? [code(first)] : prompt("Write Python code")
    }
    case "pivot":
      if (!step.on) return prompt("Choose a column…")
      return [
        text(step.agg), op("of"), column(step.values), op("by"),
        ...(step.index.length ? list(step.index, (c) => [column(c)]) : [missing("index")]),
        op("into"),
        ...(step.columns.length ? list(step.columns, (c) => [column(c.name, "name")]) : [missing("columns")]),
      ]
    case "unpivot":
      if (step.on.length === 0) return prompt("Choose columns…")
      return [...list(step.on, (c) => [column(c)]), op("into"), column(step.variableName, "name"), op("/"), column(step.valueName, "name")]
  }
}

/**
 * What an unfinished step still needs, in plain words, for the common
 * half-built states ("a formula", "a name for aggregation 2"); null when the
 * step has none of them, so the renderer's own message is shown instead.
 */
export function unfinishedPart(step: Step): string | null {
  switch (step.kind) {
    case "with_column":
      if (!step.name) return "a name for the new column"
      if (step.expr.type === "binary" && step.expr.text === "") return "a formula"
      return null
    case "filter": {
      const index = step.conditions.findIndex((c) => !c.column)
      return index < 0 ? null : step.conditions.length > 1 ? `a column for condition ${index + 1}` : "a column to test"
    }
    case "group_by":
      for (const [index, entry] of step.aggregations.entries()) {
        if ("dtype" in entry) continue
        if (entry.agg !== "len" && !entry.column) return `a column for aggregation ${index + 1}`
        if (!entry.name) return `a name for aggregation ${index + 1}`
      }
      return null
    case "sort":
      return step.keys.some((k) => !k.column) ? "a column to sort by" : null
    case "rename":
      return step.renames.some((r) => !r.from || !r.to) ? "a column and its new name" : null
    case "cast":
      return step.casts.some((c) => !c.column) ? "a column to change" : null
    case "select":
      return step.columns.length === 0 && !step.dtypes?.length ? "the columns to keep" : null
    case "drop":
      return step.columns.length === 0 && !step.dtypes?.length ? "the columns to drop" : null
    case "join":
      if (!step.input) return "an input to join"
      return step.how !== "cross" && (step.leftOn.length === 0 || step.leftOn.some((k, i) => !k || !step.rightOn[i])) ? "the key columns to match on" : null
    default:
      return null
  }
}

/** The parts as plain text: a card's title and the words a screen reader hears. */
export function partsText(parts: SummaryPart[]): string {
  return parts.reduce((acc, part) => (part.kind === "sep" ? `${acc}${part.text}` : acc ? `${acc} ${part.text}` : part.text), "")
}

/** One line describing the step for its card header. */
export function summarizeStep(step: Step): string {
  return partsText(summaryParts(step))
}
