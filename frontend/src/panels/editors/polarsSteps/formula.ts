/**
 * Formula text for the step builder: `(premium + tax) * 1.05 / 12`,
 * `round(premium / sum_insured * 1000, 3)`, `upper(region)`.
 *
 * `parseFormula` turns the text into the step schema (nested `binary`,
 * `function` and operand expressions) and `formulaText` turns such an
 * expression back into text. Columns are bare names (backticks for names
 * that are not identifiers), variables defined by earlier steps are bare
 * names too, text is quoted, `true`/`false`/`null` are keywords, dates are
 * written `date('2024-01-01')`, and functions take the catalogue's names in
 * any case (the kept text spells them as the catalogue does) with their extra
 * arguments as plain values. Operators follow Python: `+ - * / // % **`,
 * with `**` binding tightest and right-associative, and brackets group.
 * Expression types text cannot express (windows, conditionals, text joins)
 * make `formulaText` return null, and the structured editor takes over.
 */
import { CAST_DTYPES, FUNCTIONS, literal, type FunctionArg } from "./catalogue"
import type { BinaryOperator, CastDtype, Expr, FunctionName, LiteralOperand, Operand } from "./types"

const PRECEDENCE: Record<BinaryOperator, number> = { "+": 1, "-": 1, "*": 2, "/": 2, "//": 2, "%": 2, "**": 3 }
const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/
const KEYWORDS = new Set(["true", "false", "null", "date"])

/** A token with its span in the text, so errors and rewrites can point at it. */
type Token = (
  | { kind: "number"; value: number; text: string }
  | { kind: "string"; value: string }
  | { kind: "name"; value: string; quoted: boolean }
  | { kind: "op"; value: BinaryOperator }
  | { kind: "punct"; value: "(" | ")" | "," }
) & { start: number; end: number }

/** A formula that cannot be read; `position` is the 0-based character where reading stopped, when known. */
export class FormulaError extends Error {
  readonly position: number | null

  constructor(message: string, position: number | null = null) {
    super(message)
    this.position = position
  }
}

/** The catalogue function a name refers to, whatever its case. */
export function functionNamed(name: string): (typeof FUNCTIONS)[number] | undefined {
  const lower = name.toLowerCase()
  return FUNCTIONS.find((f) => f.value === lower)
}

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
      tokens.push({ kind: "punct", value: ch, start: i, end: i + 1 })
      i += 1
      continue
    }
    if (text.startsWith("**", i) || text.startsWith("//", i)) {
      tokens.push({ kind: "op", value: text.slice(i, i + 2) as BinaryOperator, start: i, end: i + 2 })
      i += 2
      continue
    }
    if ("+-*/%".includes(ch)) {
      tokens.push({ kind: "op", value: ch as BinaryOperator, start: i, end: i + 1 })
      i += 1
      continue
    }
    if (/[0-9.]/.test(ch)) {
      const match = /^(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?/.exec(text.slice(i))
      if (!match) throw new FormulaError(`Unexpected "${ch}" at position ${i + 1}.`, i)
      const value = Number(match[0])
      if (!Number.isFinite(value)) throw new FormulaError(`"${match[0]}" is not a finite number.`, i)
      tokens.push({ kind: "number", value, text: match[0], start: i, end: i + match[0].length })
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
      if (j >= text.length) throw new FormulaError("Unclosed quote in the formula.", i)
      tokens.push({ kind: "string", value, start: i, end: j + 1 })
      i = j + 1
      continue
    }
    if (ch === "`") {
      const end = text.indexOf("`", i + 1)
      if (end < 0) throw new FormulaError("Unclosed backtick in the formula.", i)
      const name = text.slice(i + 1, end)
      if (!name) throw new FormulaError("Empty backticks in the formula.", i)
      tokens.push({ kind: "name", value: name, quoted: true, start: i, end: end + 1 })
      i = end + 1
      continue
    }
    const name = /^[A-Za-z_][A-Za-z0-9_]*/.exec(text.slice(i))
    if (name) {
      tokens.push({ kind: "name", value: name[0], quoted: false, start: i, end: i + name[0].length })
      i += name[0].length
      continue
    }
    throw new FormulaError(`Unexpected "${ch}" at position ${i + 1}.`, i)
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
  private readonly length: number
  /** Function-name tokens and the catalogue spelling they stand for. */
  readonly calls: Array<{ start: number; end: number; name: string }> = []

  constructor(tokens: Token[], variables: ReadonlySet<string>, length: number) {
    this.tokens = tokens
    this.variables = variables
    this.length = length
  }

  parse(): Expr {
    if (this.tokens.length === 0) throw new FormulaError("Enter a formula.", 0)
    const expr = this.additive()
    const extra = this.tokens[this.index]
    if (extra) throw new FormulaError(`Unexpected ${describe(extra)}.`, extra.start)
    return expr
  }

  /** Where the next token starts, or the end of the text. */
  private here(): number {
    return this.tokens[this.index]?.start ?? this.length
  }

  private peek(): Token | undefined {
    return this.tokens[this.index]
  }

  private take(): Token {
    const token = this.tokens[this.index]
    if (!token) throw new FormulaError("The formula ends too early.", this.length)
    this.index += 1
    return token
  }

  /** The token just taken, described for "after …" messages. */
  private previous(): string {
    const token = this.tokens[this.index - 1]
    return token ? describe(token) : "the start"
  }

  private isOp(token: Token | undefined, ...ops: BinaryOperator[]): token is Extract<Token, { kind: "op" }> {
    return token?.kind === "op" && ops.includes(token.value)
  }

  private isPunct(token: Token | undefined, value: "(" | ")" | ","): boolean {
    return token?.kind === "punct" && token.value === value
  }

  /** Take a `,` or `)` as expected, or explain what is missing and where. */
  private expect(value: ")" | ",", message: string): void {
    const at = this.here()
    if (!this.isPunct(this.peek(), value)) throw new FormulaError(message, at)
    this.take()
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
      this.expect(")", `Expected ")" after ${this.previous()}.`)
      return inner
    }
    if (token.kind === "name") {
      if (!token.quoted) {
        if (token.value === "true" || token.value === "false") return { type: "operand", operand: literal("boolean", token.value === "true") }
        if (token.value === "null") return { type: "operand", operand: literal("null", null) }
        if (this.isPunct(this.peek(), "(")) return this.call(token)
        if (this.variables.has(token.value)) return { type: "operand", operand: { kind: "variable", name: token.value } }
      }
      return { type: "operand", operand: { kind: "column", name: token.value } }
    }
    throw new FormulaError(`Unexpected ${describe(token)}.`, token.start)
  }

  private call(token: Extract<Token, { kind: "name" }>): Expr {
    const name = token.value
    this.take() // (
    if (name === "date") {
      const value = this.take()
      if (value.kind !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.value)) throw new FormulaError("date() takes a date written as 'YYYY-MM-DD'.", value.start)
      this.expect(")", "date() takes one date.")
      return { type: "operand", operand: literal("date", value.value) }
    }
    const spec = functionNamed(name)
    if (!spec) throw new FormulaError(`Unknown function "${name}". Functions: ${FUNCTIONS.map((f) => f.value).join(", ")}.`, token.start)
    this.calls.push({ start: token.start, end: token.end, name: spec.value })
    const operand = asOperand(this.additive())
    const args: LiteralOperand[] = []
    const count = `${spec.value}() takes ${spec.args.length + 1} arguments.`
    for (const arg of spec.args) {
      this.expect(",", count)
      args.push(this.literalArg(spec.value, arg))
    }
    this.expect(")", spec.args.length === 0 ? `Expected ")" after ${this.previous()}: ${spec.value}() takes one argument.` : count)
    return { type: "function", fn: spec.value as FunctionName, operand, args }
  }

  private literalArg(fn: string, arg: FunctionArg): LiteralOperand {
    const token = this.take()
    const negative = this.isOp(token, "-")
    const value = negative ? this.take() : token
    switch (arg) {
      case "integer":
      case "int":
      case "number":
        if (value.kind !== "number") throw new FormulaError(`${fn}() expects a number here.`, value.start)
        return literal("number", negative ? -value.value : value.value)
      case "text":
        if (value.kind !== "string" || negative) throw new FormulaError(`${fn}() expects quoted text here.`, value.start)
        return literal("text", value.value)
      case "dtype": {
        const dtype = value.kind === "string" ? value.value : value.kind === "name" ? value.value : ""
        if (negative || !(CAST_DTYPES as string[]).includes(dtype)) throw new FormulaError(`${fn}() expects a type such as ${CAST_DTYPES.slice(0, 3).join(", ")}.`, value.start)
        return literal("text", dtype as CastDtype)
      }
      case "scalar":
        if (value.kind === "number") return literal("number", negative ? -value.value : value.value)
        if (value.kind === "string" && !negative) return literal("text", value.value)
        if (value.kind === "name" && !value.quoted && !negative && (value.value === "true" || value.value === "false")) return literal("boolean", value.value === "true")
        throw new FormulaError(`${fn}() expects a number, quoted text or true/false here.`, value.start)
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
 * Parse formula text; throws `FormulaError` with a plain-English message and
 * the position where reading stopped. A formula or function result carries
 * the trimmed text so the editor and the card summary show it as typed,
 * brackets and all, with function names spelled as the catalogue does.
 */
export function parseFormula(text: string, variables: readonly string[] = []): Expr {
  const parser = new Parser(tokenize(text), new Set(variables), text.length)
  const expr = parser.parse()
  if (!isFormulaType(expr)) return expr
  let spelled = text
  for (const call of [...parser.calls].sort((a, b) => b.start - a.start)) {
    spelled = `${spelled.slice(0, call.start)}${call.name}${spelled.slice(call.end)}`
  }
  return { ...expr, text: spelled.trim() }
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
 * `text` with every reference to the column `from` renamed to `to`. Only
 * column references change: quoted text, function names, keywords and
 * earlier variables that happen to share the name are left alone.
 */
export function renameColumnInFormula(text: string, from: string, to: string, variables: readonly string[] = []): string {
  const tokens = tokenize(text)
  const known = new Set(variables)
  const insert = nameText(to, known, false) ?? to
  // Which argument of which call each token sits in: a catalogue function's
  // arguments after the first are plain values (a type such as Float64, a
  // number, text), never columns.
  const calls: Array<{ fn: boolean; arg: number }> = []
  const spans: Array<{ start: number; end: number }> = []
  tokens.forEach((token, index) => {
    const next = tokens[index + 1]
    const opensCall = next?.kind === "punct" && next.value === "("
    if (token.kind === "punct") {
      if (token.value === "(") {
        const previous = tokens[index - 1]
        const fn = previous?.kind === "name" && !previous.quoted && functionNamed(previous.value) !== undefined
        calls.push({ fn, arg: 0 })
      } else if (token.value === ")") calls.pop()
      else if (calls.length > 0) calls[calls.length - 1].arg += 1
      return
    }
    if (token.kind !== "name" || token.value !== from) return
    const inCall = calls[calls.length - 1]
    if (inCall?.fn && inCall.arg > 0) return
    if (!token.quoted && (KEYWORDS.has(token.value) || known.has(token.value) || opensCall)) return
    spans.push({ start: token.start, end: token.end })
  })
  let renamed = text
  for (const span of spans.reverse()) renamed = `${renamed.slice(0, span.start)}${insert}${renamed.slice(span.end)}`
  return renamed
}

/**
 * The formula text for an expression, or null when it contains something
 * text cannot express (a window, conditional or text join).
 */
export function formulaText(expr: Expr, variables: readonly string[] = []): string | null {
  return exprText(expr, new Set(variables))
}

/**
 * The catalogue function and argument the caret is in, for the argument tip:
 * `round(premium, |)` is `round`, argument 1 (0 is the value itself). Quoted
 * text and backticked names are skipped; null outside a known call.
 */
export function callAtCaret(text: string, caret: number): { fn: string; arg: number } | null {
  const stack: Array<{ fn: string | null; arg: number }> = []
  let i = 0
  while (i < caret) {
    const ch = text[i]
    if (ch === "'" || ch === '"' || ch === "`") {
      const close = text.indexOf(ch, i + 1)
      if (close < 0 || close >= caret) return null
      i = close + 1
      continue
    }
    if (ch === "(") {
      const name = /([A-Za-z_][A-Za-z0-9_]*)\s*$/.exec(text.slice(0, i))
      stack.push({ fn: name ? name[1] : null, arg: 0 })
    } else if (ch === ")") {
      stack.pop()
    } else if (ch === "," && stack.length > 0) {
      stack[stack.length - 1].arg += 1
    }
    i += 1
  }
  const top = stack[stack.length - 1]
  const spec = top?.fn ? functionNamed(top.fn) : undefined
  return spec && top ? { fn: spec.value, arg: top.arg } : null
}
