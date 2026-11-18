/**
 * No browser code decides a column's kind from a type's name.
 *
 * The server's one kind module answers what kind each Polars type is and the generated
 * contract carries its table (`COLUMN_KINDS`), which `utils/polarsDtypes.ts` reads by the
 * type's base name. A list or pattern of type names kept beside it goes stale the day
 * Polars adds a type: `/^Float(?:32|64)$/` left `Float16` out (specs/io-layer, "Column
 * kinds"). This scan finds, anywhere under `src` but generated code and tests:
 *
 * - a number type's name written as text where code runs (`"Float64"`, an object key
 *   `Int64:`), which a comparison, a list or a lookup by name needs;
 * - a pattern matched against a type's name (`/^U?Int(?:8|16)$/`, `.startsWith("Float")`).
 *
 * A place that must name one type, such as a cast's default target, is on
 * `NAMED_TYPES` with its reason. The server's twin is `tests/test_column_kinds_by_hand.py`.
 */

import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { describe, expect, it } from "vitest"
import { COLUMN_KIND_GROUPS, COLUMN_KINDS } from "../../generated/api-contracts.column-kinds.generated"

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..")

const NUMBER_KINDS: ReadonlySet<string> = new Set(COLUMN_KIND_GROUPS.number)
const TYPE_NAMES: ReadonlySet<string> = new Set(Object.keys(COLUMN_KINDS))
const NUMBER_TYPE_NAMES: readonly string[] = Object.entries(COLUMN_KINDS)
  .filter(([, kind]) => NUMBER_KINDS.has(kind))
  .map(([name]) => name)
const REGEX_SYNTAX = /[\\()[\]|?*+^${}]/
const NUMBER_STEM = /u?int|float|decimal/i

/** `path::scope::rule` -> why the place names a type. */
const NAMED_TYPES: Record<string, string> = {
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::CAST_DTYPES::name":
    "the Change types targets, each one type a cast names (the server's CAST_DTYPES)",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::COMMON_DTYPES::name":
    "the cast targets the type picker lists first",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::DTYPE_WORDS::name":
    "the plain words for the common cast targets",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::CAST_REFUSALS::name":
    "the cast targets Polars 2 refuses for each source kind (the server's CAST_REFUSALS)",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::defaultArgFor::name":
    "a new cast() argument's default target",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::createStep::name":
    "a new Change types entry's default target",
  "frontend/src/panels/editors/polarsSteps/forms.tsx::CastForm::name":
    "a Change types entry added in the form: its default target",
  "frontend/src/panels/editors/polarsSteps/AddStepMenu.tsx::ENTRIES::name":
    "the Convert menu entry's default target",
  "frontend/src/panels/editors/polarsSteps/catalogue.ts::DATE_FORMAT_DEFAULTS::name":
    "the format a text column is parsed with for each of the two date cast targets",
  "frontend/src/panels/GlobalConstantsPanel.tsx::TYPE_LABELS::name":
    "the English label Decimal, for a float constant, not a type",
  "frontend/src/workbench/columnTypes.ts::COLUMN_TYPES::name":
    "the English label Decimal, for a float column, not a type",
  "frontend/src/workbench/fieldDatabaseIssues.ts::DATABASE_TYPES::name":
    "the English label Decimal, for a database float column, not a type",
  "frontend/src/panels/editors/rating/ratingTableUtils.ts::primitiveRatingKinds::name":
    "the persisted Rating factor descriptor kinds (the server's _RATING_PRIMITIVE_DESCRIPTOR_KINDS)",
  "frontend/src/panels/editors/rating/ratingTableUtils.ts::normaliseRatingFactorDtype::name":
    "reads a persisted descriptor's parameters, which its kind names",
}

type Finding = { line: number; scope: string; rule: "name" | "pattern"; text: string }

const OTHER_TYPE_NAMES: readonly string[] = [...TYPE_NAMES].filter((name) => !NUMBER_TYPE_NAMES.includes(name))

/** Whether *pattern* matches one of *names*: the whole name, or the start of one when it is not prose. */
function matchesAName(source: string, pattern: RegExp, names: readonly string[]): boolean {
  const whole = new RegExp(`^(?:${source})$`, pattern.flags)
  return names.some((name) => whole.test(name) || (!/\s/.test(source) && pattern.test(name)))
}

/**
 * Whether *source* is a pattern matched against a type's name: a number type's in any
 * letter case (the editor's patterns were lower-cased), another type's as Polars writes it.
 */
function isTypePattern(source: string, flags: string): boolean {
  if (!REGEX_SYNTAX.test(source)) return false
  try {
    if (NUMBER_STEM.test(source)) {
      const pattern = new RegExp(source, flags.includes("i") ? flags : `${flags}i`)
      if (matchesAName(source, pattern, NUMBER_TYPE_NAMES)) return true
    }
    const named = OTHER_TYPE_NAMES.filter((name) => source.includes(name))
    return named.length > 0 && matchesAName(source, new RegExp(source, flags), named)
  } catch {
    return false
  }
}

/** Whether a literal holding *node* names two types or more: a list kept by hand. */
function inListOfTypes(node: ts.Node): boolean {
  const parent = node.parent
  const members = ts.isArrayLiteralExpression(parent)
    ? parent.elements
    : ts.isPropertyAssignment(parent) && ts.isObjectLiteralExpression(parent.parent) && parent.name === node
      ? parent.parent.properties.map((property) => property.name)
      : []
  const names = members.filter(
    (member) => member !== undefined && (ts.isStringLiteral(member) || ts.isIdentifier(member)) && TYPE_NAMES.has(member.text),
  )
  return names.length >= 2
}

function isTypeStem(text: string): boolean {
  return text.length >= 3 && !TYPE_NAMES.has(text) && NUMBER_TYPE_NAMES.some((name) => name.startsWith(text))
}

function inTypePosition(node: ts.Node): boolean {
  for (let current = node.parent; current; current = current.parent) {
    if (ts.isTypeNode(current)) return true
    if (ts.isStatement(current) || ts.isExpressionStatement(current)) return false
  }
  return false
}

function scopeOf(node: ts.Node): string {
  const names: string[] = []
  for (let current: ts.Node | undefined = node.parent; current; current = current.parent) {
    if (
      (ts.isFunctionDeclaration(current) || ts.isMethodDeclaration(current) || ts.isClassDeclaration(current)
        || ts.isVariableDeclaration(current) || ts.isPropertyAssignment(current))
      && current.name && (ts.isIdentifier(current.name) || ts.isStringLiteral(current.name))
      && (!ts.isPropertyAssignment(current) || names.length === 0)
    ) {
      if (!ts.isPropertyAssignment(current)) names.unshift(current.name.text)
    }
  }
  return names.join(".")
}

/** Every place in *source* that decides a column's kind from a type's name. */
export function kindDecisionsByHand(source: string, fileName = "source.tsx"): Finding[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const findings: Finding[] = []
  const report = (node: ts.Node, rule: Finding["rule"]) => {
    findings.push({
      line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
      scope: scopeOf(node),
      rule,
      text: node.getText(file),
    })
  }
  const visit = (node: ts.Node) => {
    if (ts.isRegularExpressionLiteral(node)) {
      const text = node.text
      const end = text.lastIndexOf("/")
      if (isTypePattern(text.slice(1, end), text.slice(end + 1))) report(node, "pattern")
    } else if (
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))
      && !ts.isImportDeclaration(node.parent) && !ts.isExportDeclaration(node.parent)
      && !inTypePosition(node)
    ) {
      const parent = node.parent
      const call = ts.isCallExpression(parent) ? parent : undefined
      const callee = call && ts.isPropertyAccessExpression(call.expression) ? call.expression.name.text : undefined
      const constructsPattern = (ts.isNewExpression(parent) || ts.isCallExpression(parent))
        && ts.isIdentifier(parent.expression) && parent.expression.text === "RegExp"
      if (NUMBER_TYPE_NAMES.includes(node.text) || (TYPE_NAMES.has(node.text) && inListOfTypes(node))) {
        report(node, "name")
      } else if (constructsPattern && isTypePattern(node.text, "")) report(node, "pattern")
      else if (callee && ["startsWith", "endsWith", "includes", "indexOf"].includes(callee) && isTypeStem(node.text)) {
        report(node, "pattern")
      }
    } else if (
      (ts.isPropertyAssignment(node) || ts.isPropertySignature(node) || ts.isShorthandPropertyAssignment(node))
      && node.name && (ts.isIdentifier(node.name) || ts.isStringLiteral(node.name))
      && (NUMBER_TYPE_NAMES.includes(node.name.text) || (TYPE_NAMES.has(node.name.text) && inListOfTypes(node.name)))
      && !inTypePosition(node)
      && !ts.isPropertySignature(node)
    ) {
      report(node.name, "name")
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return findings
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) {
      return entry.name === "__tests__" || entry.name === "generated" || entry.name === "test" ? [] : sourceFiles(path)
    }
    return /\.tsx?$/.test(entry.name) && !/\.(?:test|d)\.tsx?$/.test(entry.name) ? [path] : []
  })
}

function repositoryFindings(): Map<string, Finding[]> {
  const found = new Map<string, Finding[]>()
  for (const path of sourceFiles(SRC)) {
    const rel = `frontend/src/${relative(SRC, path).split(sep).join("/")}`
    for (const finding of kindDecisionsByHand(readFileSync(path, "utf-8"), path)) {
      const key = `${rel}::${finding.scope}::${finding.rule}`
      found.set(key, [...(found.get(key) ?? []), finding])
    }
  }
  return found
}

describe("column kinds decided by hand", () => {
  it("no browser code outside the generated table decides a kind from a type's name", () => {
    const unexplained = Object.fromEntries(
      [...repositoryFindings()]
        .filter(([key]) => !(key in NAMED_TYPES))
        .map(([key, findings]) => [key, findings.map((finding) => `line ${finding.line}: ${finding.text}`)]),
    )
    expect(
      unexplained,
      "Ask utils/polarsDtypes (columnKind and its groups) for a column's kind. A place that "
      + "must name one type, such as a cast, goes on NAMED_TYPES with its reason.",
    ).toEqual({})
  })

  it("every named-type place is still in the source", () => {
    const found = repositoryFindings()
    expect(Object.keys(NAMED_TYPES).filter((key) => !found.has(key))).toEqual([])
  })

  it.each([
    ["the float pattern the editor held", 'if (/^Float(?:32|64)$/.test(d)) return "float"'],
    ["the integer pattern the editor held", 'if (/^U?Int(?:8|16|32|64|128)$/.test(d)) return "integer"'],
    ["a lower-case numeric pattern", "return /^(?:u?int(?:8|16|32|64|128)|float(?:32|64))$/.test(normalised)"],
    ["a list of float names", 'const FLOATS = ["Float32", "Float64"]'],
    ["a comparison with a type name", 'if (dtype === "Int64") return true'],
    ["a lookup keyed by type name", 'const colours = { Float64: "text-emerald-400" }'],
    ["a prefix test", 'return dtype.startsWith("Float")'],
    ["a pattern built from text", 'const pattern = new RegExp("^Float\\\\d+$")'],
    [
      "a list of type names held as property values",
      'const FLOATS = [{ type: "Float32" }, { type: "Float64" }]\nconst isFloat = (d) => FLOATS.some((t) => t.type === d)',
    ],
    ["a list of text type names", 'return ["String", "Categorical"].includes(dtype)'],
    ["a pattern over date type names", "return /^(?:Date|Datetime)/.test(dtype)"],
    ["a lookup keyed by two type names", 'const colours = { Date: "text-rose-400", Datetime: "text-rose-400" }'],
  ])("the scan finds %s", (_label, source) => {
    expect(kindDecisionsByHand(source)).not.toEqual([])
  })

  it.each([
    ["a kind asked of the module", 'if (columnKind(dtype) === "float") return true'],
    ["a type name in a type position", 'type CastDtype = "Float32" | "Float64"'],
    ["a date pattern", "if (/^\\d{4}-\\d{2}-\\d{2}$/.test(value)) return true"],
    ["a message naming a type", 'throw new Error("Cast it upstream to Int64 (or Float64) first.")'.replace("Int64", "a whole number")],
  ])("the scan leaves %s", (_label, source) => {
    expect(kindDecisionsByHand(source)).toEqual([])
  })
})
