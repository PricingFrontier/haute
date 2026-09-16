/**
 * Formula text for the step builder: `(premium + tax) * 1.05 / 12`,
 * `round(premium / sum_insured * 1000, 3)`, `upper(region)`.
 *
 * `parseFormula` turns the text into the step schema (nested `binary`,
 * `function` and operand expressions) and `formulaText` turns such an
 * expression back into text. Columns are bare names (backticks for names
 * that are not identifiers), variables defined by earlier steps are bare
 * names too, text is quoted, `true`/`false`/`null` are keywords, dates are
 * written `date('2024-01-01')`, and functions take the catalogue's names
 * with their extra arguments as plain values. Operators follow Python:
 * `+ - * / // % **`, with `**` binding tightest and right-associative, and
 * brackets group. Expression types text cannot express (windows,
 * conditionals, text joins) make `formulaText` return null, and the
 * structured editor takes over.
 */
import { CAST_DTYPES, FUNCTIONS, literal, type FunctionArg } from "./catalogue"
import type { BinaryOperator, CastDtype, Expr, FunctionName, LiteralOperand, Operand } from "./types"

const PRECEDENCE: Record<BinaryOperator, number> = { "+": 1, "-": 1, "*": 2, "/": 2, "//": 2, "%": 2, "**": 3 }
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/
const KEYWORDS = new Set(["true", "false", "null", "date"])

type Token =
  | { kind: "number"; value: number; text: string }
  | { kind: "string"; value: string }
  | { kind: "name"; value: string; quoted: boolean }
  | { kind: "op"; value: BinaryOperator }
  | { kind: "punct"; value: "(" | ")" | "," }

export class FormulaError extends Error {}

function tokenize(text: string): Token[] {
  const tokens: Token[] = []
  let i = 0
  while (i < text.length) {
    const ch = text[i]
    if (/\s/.test(ch)) {
      i += 1
      continue
    }
    if (ch === "(" || ch === ")" || ch === ",") {
      tokens.push({ kind: "punct", value: ch })
      i += 1
      continue
    }
    if (text.startsWith("**", i) || text.startsWith("//", i)) {
      tokens.push({ kind: "op", value: text.slice(i, i + 2) as BinaryOperator })
      i += 2
      continue
    }
    if ("+-*/%".includes(ch)) {
      tokens.push({ kind: "op", value: ch as BinaryOperator })
      i += 1
      continue
    }
    if (/[0-9.]/.test(ch)) {
      const match = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(text.slice(i))
      if (!match) throw new FormulaError(`Unexpected "${ch}" at position ${i + 1}.`)
      const value = Number(match[0])
      if (!Number.isFinite(value)) throw new FormulaError(`"${match[0]}" is not a finite number.`)
      tokens.push({ kind: "number", value, text: match[0] })
      i += match[0].length
      continue
    }
    if (ch === "'" || ch === '"') {
      let j = i + 1
      let value = ""
      while (j < text.length && text[j] !== ch) {
        if (text[j] === "\\" && j + 1 < text.length) {
          value += text[j + 1]
          j += 2
        } else {
          value += text[j]
          j += 1
        }
      }
      if (j >= text.length) throw new FormulaError("Unclosed quote in the formula.")
      tokens.push({ kind: "string", value })
      i = j + 1
      continue
    }
    if (ch === "`") {
      const end = text.indexOf("`", i + 1)
      if (end < 0) throw new FormulaError("Unclosed backtick in the formula.")
      const name = text.slice(i + 1, end)
      if (!name) throw new FormulaError("Empty backticks in the formula.")
      tokens.push({ kind: "name", value: name, quoted: true })
      i = end + 1
      continue
    }
    const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i))
    if (name) {
      tokens.push({ kind: "name", value: name[0], quoted: false })
      i += name[0].length
      continue
    }
    throw new FormulaError(`Unexpected "${ch}" at position ${i + 1}.`)
  }
  return tokens
}

/** Wrap a parsed expression as an operand: plain operands stay bare. */
function asOperand(expr: Expr): Operand {
  return expr.type === "operand" ? expr.operand : { kind: "expr", expr }
}

class Parser {
  private index = 0
  private readonly tokens: Token[]
  private readonly variables: ReadonlySet<string>

  constructor(tokens: Token[], variables: ReadonlySet<string>) {
    this.tokens = tokens
    this.variables = variables
  }

  parse(): Expr {
    if (this.tokens.length === 0) throw new FormulaError("Enter a formula.")
    const expr = this.additive()
    if (this.index < this.tokens.length) throw new FormulaError(`Unexpected ${describe(this.tokens[this.index])}.`)
    return expr
  }

  private peek(): Token | undefined {
    return this.tokens[this.index]
  }

  private take(): Token {
    const token = this.tokens[this.index]
    if (!token) throw new FormulaError("The formula ends too early.")
    this.index += 1
    return token
  }

  private isOp(token: Token | undefined, ...ops: BinaryOperator[]): token is Extract<Token, { kind: "op" }> {
    return token?.kind === "op" && ops.includes(token.value)
  }

  private isPunct(token: Token | undefined, value: "(" | ")" | ","): boolean {
    return token?.kind === "punct" && token.value === value
  }

  private additive(): Expr {
    let left = this.multiplicative()
    while (this.isOp(this.peek(), "+", "-")) {
      const op = (this.take() as Extract<Token, { kind: "op" }>).value
      const right = this.multiplicative()
      left = { type: "binary", left: asOperand(left), op, right: asOperand(right) }
    }
    return left
  }

  private multiplicative(): Expr {
    let left = this.unary()
    while (this.isOp(this.peek(), "*", "/", "//", "%")) {
      const op = (this.take() as Extract<Token, { kind: "op" }>).value
      const right = this.unary()
      left = { type: "binary", left: asOperand(left), op, right: asOperand(right) }
    }
    return left
  }

  private power(): Expr {
    const base = this.primary()
    if (this.isOp(this.peek(), "**")) {
      this.take()
      const exponent = this.unary()
      return { type: "binary", left: asOperand(base), op: "**", right: asOperand(exponent) }
    }
    return base
  }

  private unary(): Expr {
    if (this.isOp(this.peek(), "-")) {
      this.take()
      const operand = this.unary()
      if (operand.type === "operand" && operand.operand.kind === "literal" && operand.operand.type === "number") {
        return { type: "operand", operand: literal("number", -(operand.operand.value as number)) }
      }
      return { type: "binary", left: literal("number", 0), op: "-", right: asOperand(operand) }
    }
    if (this.isOp(this.peek(), "+")) {
      this.take()
      return this.unary()
    }
    return this.power()
  }

  private primary(): Expr {
    const token = this.take()
    if (token.kind === "number") return { type: "operand", operand: literal("number", token.value) }
    if (token.kind === "string") return { type: "operand", operand: literal("text", token.value) }
    if (token.kind === "punct" && token.value === "(") {
      const inner = this.additive()
      if (!this.isPunct(this.peek(), ")")) throw new FormulaError("Missing a closing bracket.")
      this.take()
      return inner
    }
    if (token.kind === "name") {
      if (!token.quoted) {
        if (token.value === "true" || token.value === "false") return { type: "operand", operand: literal("boolean", token.value === "true") }
        if (token.value === "null") return { type: "operand", operand: literal("null", null) }
        if (this.isPunct(this.peek(), "(")) return this.call(token.value)
        if (this.variables.has(token.value)) return { type: "operand", operand: { kind: "variable", name: token.value } }
      }
      return { type: "operand", operand: { kind: "column", name: token.value } }
    }
    throw new FormulaError(`Unexpected ${describe(token)}.`)
  }

  private call(name: string): Expr {
    this.take() // (
    if (name === "date") {
      const value = this.take()
      if (value.kind !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.value)) throw new FormulaError("date() takes a date written as 'YYYY-MM-DD'.")
      if (!this.isPunct(this.take(), ")")) throw new FormulaError("date() takes one date.")
      return { type: "operand", operand: literal("date", value.value) }
    }
    const spec = FUNCTIONS.find((f) => f.value === name)
    if (!spec) throw new FormulaError(`Unknown function "${name}". Functions: ${FUNCTIONS.map((f) => f.value).join(", ")}.`)
    const operand = asOperand(this.additive())
    const args: LiteralOperand[] = []
    for (const arg of spec.args) {
      if (!this.isPunct(this.take(), ",")) throw new FormulaError(`${name}() takes ${spec.args.length + 1} arguments.`)
      args.push(this.literalArg(name, arg))
    }
    if (!this.isPunct(this.take(), ")")) throw new FormulaError(`${name}() takes ${spec.args.length + 1} arguments.`)
    return { type: "function", fn: name as FunctionName, operand, args }
  }

  private literalArg(fn: string, arg: FunctionArg): LiteralOperand {
    const token = this.take()
    const negative = this.isOp(token, "-")
    const value = negative ? this.take() : token
    switch (arg) {
      case "integer":
      case "int":
      case "number":
        if (value.kind !== "number") throw new FormulaError(`${fn}() expects a number here.`)
        return literal("number", negative ? -value.value : value.value)
      case "text":
        if (value.kind !== "string" || negative) throw new FormulaError(`${fn}() expects quoted text here.`)
        return literal("text", value.value)
      case "dtype": {
        const dtype = value.kind === "string" ? value.value : value.kind === "name" ? value.value : ""
        if (negative || !(CAST_DTYPES as string[]).includes(dtype)) throw new FormulaError(`${fn}() expects a type such as ${CAST_DTYPES.slice(0, 3).join(", ")}.`)
        return literal("text", dtype as CastDtype)
      }
      case "scalar":
        if (value.kind === "number") return literal("number", negative ? -value.value : value.value)
        if (value.kind === "string" && !negative) return literal("text", value.value)
        if (value.kind === "name" && !value.quoted && !negative && (value.value === "true" || value.value === "false")) return literal("boolean", value.value === "true")
        throw new FormulaError(`${fn}() expects a number, quoted text or true/false here.`)
    }
  }
}

function describe(token: Token): string {
  switch (token.kind) {
    case "number":
      return `number ${token.text}`
    case "string":
      return `text '${token.value}'`
    case "name":
      return `"${token.value}"`
    case "op":
      return `operator ${token.value}`
    case "punct":
      return `"${token.value}"`
  }
}

/**
 * Parse formula text; throws `FormulaError` with a plain-English message.
 * A formula or function result carries the trimmed text so the editor and
 * the card summary show it exactly as typed, brackets and all.
 */
export function parseFormula(text: string, variables: readonly string[] = []): Expr {
  const expr = new Parser(tokenize(text), new Set(variables)).parse()
  return isFormulaType(expr) ? { ...expr, text: text.trim() } : expr
}

/** Expression types a formula can produce and therefore carry the typed text. */
function isFormulaType(expr: Expr): expr is Extract<Expr, { type: "operand" | "binary" | "function" }> {
  return expr.type === "operand" || expr.type === "binary" || expr.type === "function"
}

/** Whether the expression was typed as a formula (it carries its text). */
export function typedAsFormula(expr: Expr): boolean {
  return isFormulaType(expr) && typeof expr.text === "string"
}

/** Deep copy of an expression with every `text` annotation removed. */
export function withoutFormulaText(expr: Expr): Expr {
  const strip = (operand: Operand): Operand => (operand.kind === "expr" ? { kind: "expr", expr: withoutFormulaText(operand.expr) } : operand)
  switch (expr.type) {
    case "binary":
      return { type: "binary", left: strip(expr.left), op: expr.op, right: strip(expr.right) }
    case "function":
      return { type: "function", fn: expr.fn, operand: strip(expr.operand), args: expr.args }
    case "operand":
      return { type: "operand", operand: strip(expr.operand) }
    case "conditional":
      return { ...expr, then: strip(expr.then), otherwise: strip(expr.otherwise) }
    case "concat":
      return { ...expr, parts: expr.parts.map(strip) }
    default:
      return expr
  }
}

/**
 * The text to show for an expression: what was typed, when that still
 * parses to this expression, else a fresh rendering; null when text cannot
 * express the expression.
 */
export function displayFormula(expr: Expr, variables: readonly string[] = []): string | null {
  // Empty text is a formula box nothing has been typed into yet.
  if (expr.type === "binary" && expr.text === "") return ""
  if (isFormulaType(expr) && typeof expr.text === "string") {
    try {
      const reparsed = withoutFormulaText(parseFormula(expr.text, variables))
      if (JSON.stringify(reparsed) === JSON.stringify(withoutFormulaText(expr))) return expr.text
    } catch {
      // fall through to a fresh rendering
    }
  }
  return formulaText(expr, variables)
}

function quoteText(value: string): string {
  return `'${value.replace(/\\/g, "\\\\").replace(/'/g, "\\'")}'`
}

function nameText(name: string, variables: ReadonlySet<string>, isVariable: boolean): string | null {
  // A name not yet filled in reads as a placeholder rather than empty backticks.
  if (name.length === 0) return "?"
  if (name.includes("`")) return null
  if (isVariable) {
    if (!variables.has(name) || !IDENTIFIER.test(name) || name === "true" || name === "false" || name === "null") return null
    return name
  }
  const plain = IDENTIFIER.test(name) && !KEYWORDS.has(name) && !variables.has(name)
  return plain ? name : `\`${name}\``
}

/** A literal in formula notation: `12`, `'north'`, `true`, `null`, `date('2024-01-01')`. */
export function literalText(operand: LiteralOperand): string {
  switch (operand.type) {
    case "number":
      return String(operand.value)
    case "text":
      return quoteText(String(operand.value))
    case "boolean":
      return operand.value ? "true" : "false"
    case "null":
      return "null"
    case "date":
      return `date(${quoteText(String(operand.value))})`
  }
}

function operandText(operand: Operand, variables: ReadonlySet<string>, parent: { op: BinaryOperator; side: "left" | "right" } | null): string | null {
  switch (operand.kind) {
    case "literal": {
      const text = literalText(operand)
      return parent?.op === "**" && parent.side === "left" && operand.type === "number" && Number(operand.value) < 0 ? `(${text})` : text
    }
    case "column":
      return nameText(operand.name, variables, false)
    case "variable":
      return nameText(operand.name, variables, true)
    case "expr": {
      if (operand.expr.type === "operand") return operandText(operand.expr.operand, variables, parent)
      const inner = exprText(operand.expr, variables)
      if (inner === null) return null
      if (operand.expr.type !== "binary") return inner
      // Text only has to re-parse to the same tree: a tighter-binding child
      // needs no brackets on either side, an equal one only on the right.
      const child = operand.expr.op
      const bracket =
        parent === null
        || child === "**"
        || parent.op === "**"
        || PRECEDENCE[child] < PRECEDENCE[parent.op]
        || (parent.side === "right" && PRECEDENCE[child] === PRECEDENCE[parent.op])
      return bracket ? `(${inner})` : inner
    }
  }
}

function exprText(expr: Expr, variables: ReadonlySet<string>): string | null {
  switch (expr.type) {
    case "operand":
      return operandText(expr.operand, variables, null)
    case "binary": {
      const left = operandText(expr.left, variables, { op: expr.op, side: "left" })
      const right = operandText(expr.right, variables, { op: expr.op, side: "right" })
      return left === null || right === null ? null : `${left} ${expr.op} ${right}`
    }
    case "function": {
      const receiver = expr.operand.kind === "expr" ? exprText(expr.operand.expr, variables) : operandText(expr.operand, variables, null)
      if (receiver === null) return null
      const spec = FUNCTIONS.find((f) => f.value === expr.fn)
      const args = expr.args.map((arg, index) => (spec?.args[index] === "dtype" ? String(arg.value) : literalText(arg)))
      return `${expr.fn}(${[receiver, ...args].join(", ")})`
    }
    default:
      return null
  }
}

/**
 * The formula text for an expression, or null when it contains something
 * text cannot express (a window, conditional or text join).
 */
export function formulaText(expr: Expr, variables: readonly string[] = []): string | null {
  return exprText(expr, new Set(variables))
}
