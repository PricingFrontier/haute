/**
 * No code reads typed text as a number but the one reader.
 *
 * Text typed as a number is read by one grammar, the same in every field, in every browser
 * language and on the server (specs/frontend-shared high-level, "Typed numbers"):
 * `utils/typedNumbers.ts`, held with the server's `haute._typed_numbers` to one case file.
 * Each field once read its own text: `Number("1,5")` is NaN, `parseFloat("1,5")` is 1, and the
 * browser's own number input drops a character the browser's language does not use, so `1,5`
 * was 15 in an English browser and 1.5 in a German one. This scan, by the sources' types,
 * finds in the browser's code, but tests:
 *
 * - `parse`: `Number`, `parseFloat`, `parseInt`, `Number.parseFloat`, `Number.parseInt` or
 *   unary `+` given a value whose type may be text (a string, `any` or `unknown`), but a
 *   number's own text (`toFixed`, `toPrecision`, `toExponential`), which is never typed;
 * - `input`: an element with `type="number"`, the browser's own number input.
 *
 * Text that is not typed by anyone (a regular expression's matched digits, a select's own
 * option value, a style's or a data attribute's figure, an answer's field) is on `NOT_TYPED`
 * with its reason, keyed `path::scope::rule`.
 */

import { dirname, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { describe, expect, it } from "vitest"

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), "../../../..")
/** The reader itself: the grammar and the Constant's reading of it. */
const READERS = new Set(["frontend/src/utils/typedNumbers.ts", "frontend/src/utils/constantValues.ts"])

/** `path::scope::rule` -> why the text it reads is not typed by anyone. */
const NOT_TYPED: Record<string, string> = {
  "frontend/src/app/route.ts::routeOf::parse": "the app's address: a quote's option number Haute wrote into it",
  "frontend/src/hooks/useDragResize.ts::availablePanelHeight::parse": "a laid-out panel's flex-grow, as the browser computes it",
  "frontend/src/panels/DataPreview.tsx::DataPreview::parse": "a row's index, written into its data attribute by the grid",
  "frontend/src/panels/GitPanel.tsx::GitPanelBranchScope.measure::parse": "a commit dot's position, written into its data attribute by the graph",
  "frontend/src/panels/OptimiserPreview.tsx::FrontierTab::parse": "a select's or a slider's own option value, a point's index",
  "frontend/src/panels/editors/ExploreChartsConfig.tsx::ExploreChartsConfig::parse": "the label rotation select's own option value",
  "frontend/src/panels/editors/banding/bandingUtils.ts::matchedDayNumber::parse": "a date's or a time's digits, matched by its pattern",
  "frontend/src/panels/editors/explorePivots/PivotFormattingSection.tsx::selectedDecimalPlaces::parse": "the decimal places select's own option value",
  "frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx::parseStepsError::parse": "a step's index in the server's error text, matched by its pattern",
  "frontend/src/panels/editors/polarsSteps/fields.tsx::decimalPlaces::parse": "the exponent of a number's own text",
  "frontend/src/panels/editors/polarsSteps/formula.ts::operandText::parse": "a formula's number literal, which the formula grammar tokenised",
  "frontend/src/panels/editors/polarsSteps/formula.ts::tokenize::parse": "a formula's number literal, by the formula grammar the server's expression parser shares",
  "frontend/src/panels/explore/PivotTableGrid.tsx::numericCellValue::parse": "a pivot answer's cell, a whole number past 2^53 sent as its digits",
  "frontend/src/panels/explore/chartConfig.ts::chartPosition::parse": "a chart's place, matched by its pattern from the chart's own id",
  "frontend/src/panels/explore/chartData.ts::numericCellValue::parse": "a chart answer's cell, a whole number past 2^53 sent as its digits",
  "frontend/src/panels/explore/pivotNumberFormat.ts::parseDecimalParts::parse": "the exponent of a number's own text, matched by its pattern",
  "frontend/src/panels/modelling/GLMCoefficientsTab.tsx::GLMCoefficientsTab::parse": "a fitted coefficient table's values, sorted as the training answer gives them",
  "frontend/src/panels/modelling/PdpTab.tsx::PdpChart::parse": "a partial dependence answer's grid value",
  "frontend/src/panels/modelling/ShapCurvesTab.tsx::CategoricalCurve.describe::parse": "a bar's index, the chart's own key",
  "frontend/src/panels/modelling/ShapCurvesTab.tsx::CategoricalCurve.onActivate::parse": "a bar's index, the chart's own key",
  "frontend/src/panels/modelling/ShapCurvesTab.tsx::CategoricalCurve.render::parse": "a bar's index, the chart's own key",
  "frontend/src/panels/modelling/TermTableView.tsx::AxisSelect::parse": "the axis select's own option value",
  "frontend/src/panels/modelling/TrainingRunSummary.tsx::TrainingRunSummary::parse": "a split's share, a setting the shared number field stores as a number",
  "frontend/src/panels/modelling/beeswarm.ts::parseHexColor::parse": "a colour's hexadecimal channels",
  "frontend/src/panels/modelling/trainingFitBudget.ts::trainingFitBudget::parse": "a fold or trial count, a setting the shared number field stores as a number",
  "frontend/src/panels/optimiser/OptimiserPublishSection.tsx::OptimiserPublishSection.renderPublishBody::parse": "the point select's own option value",
  "frontend/src/panels/optimiser/RatebookImpactBeeswarm.tsx::numericFeatureValue::parse": "a ratebook answer's feature value, matched by its pattern",
  "frontend/src/panels/optimiserScenarioStats.ts::readFiniteNumber::parse": "a solve answer's statistic",
  "frontend/src/trace/derivationTreeHelpers.ts::substitutionRestatesValue::parse": "a trace answer's substituted figure",
  "frontend/src/utils/chartHelpers.ts::decade::parse": "a power of ten built from an exponent",
  "frontend/src/utils/color.ts::withAlpha::parse": "a colour's hexadecimal channels",
  "frontend/src/utils/graphHelpers.ts::computeNextNodeId::parse": "a node id's counter, matched by its pattern",
  "frontend/src/utils/isoDate.ts::isTime::parse": "a time's digits, matched by its pattern",
  "frontend/src/utils/polarsDtypes.ts::decimalScale::parse": "a Decimal type's scale, matched by its pattern from the type's name",
  "frontend/src/utils/sheetValues.ts::pythonFloat::parse": "the exponent of a number's own text",
  "frontend/src/utils/sheetVisibility.ts::dayNumber::parse": "a day's digits, held to YYYY-MM-DD",
  "frontend/src/utils/trainingObjective.ts::evaluationConfigurationIssues::parse": "a fold count, a setting the shared number field stores as a number",
  "frontend/src/utils/trainingObjective.ts::trainingConfigurationIssues::parse": "a fold or trial count, a setting the shared number field stores as a number",
  "frontend/src/utils/workbenchForm.ts::cellAddress::parse": "a spreadsheet cell's row, the digits of an address like B12 matched by its pattern",
  "frontend/src/workbench/ShowWhenEditor.tsx::MatchSentence::parse": "the How many select's own option value",
  "frontend/src/workbench/panel/CollectionLook.tsx::CollectionLook::parse": "the text size select's own option value",
  "frontend/src/workbench/panel/TableLook.tsx::TableLook::parse": "the text size select's own option value",
  "frontend/src/workbench/panel/lookParts.tsx::HeaderRows::parse": "the header size select's own option value",
  "frontend/src/workbench/schema/ColumnDetails.tsx::ColumnDetails::parse": "the decimal places select's own option value",
}

type Rule = "parse" | "input"
type Finding = { line: number; text: string }

const PARSERS = new Set(["Number", "parseFloat", "parseInt"])
const OWN_TEXT = new Set(["toFixed", "toPrecision", "toExponential"])

function mayBeText(type: ts.Type): boolean {
  if (type.isUnion()) return type.types.some(mayBeText)
  return (type.flags & (ts.TypeFlags.StringLike | ts.TypeFlags.Any | ts.TypeFlags.Unknown)) !== 0
}

/** Whether *node* is a number's own text: `value.toFixed(2)`, never typed. */
function numbersOwnText(node: ts.Expression): boolean {
  const inner = ts.isParenthesizedExpression(node) ? node.expression : node
  return ts.isCallExpression(inner) && ts.isPropertyAccessExpression(inner.expression) && OWN_TEXT.has(inner.expression.name.text)
}

/** The name of the function, method or variable *node* sits in, outermost first. */
function scopeOf(node: ts.Node): string {
  const names: string[] = []
  for (let at: ts.Node | undefined = node.parent; at; at = at.parent) {
    if ((ts.isFunctionDeclaration(at) || ts.isMethodDeclaration(at) || ts.isClassDeclaration(at)) && at.name) names.unshift(at.name.getText())
    else if ((ts.isArrowFunction(at) || ts.isFunctionExpression(at)) && ts.isVariableDeclaration(at.parent)) names.unshift(at.parent.name.getText())
    else if (ts.isVariableDeclaration(at) && ts.isSourceFile(at.parent.parent.parent)) names.unshift(at.name.getText())
    else if (ts.isPropertyAssignment(at) && (ts.isArrowFunction(at.initializer) || ts.isFunctionExpression(at.initializer))) names.unshift(at.name.getText())
  }
  return names.length === 0 ? "module" : [...new Set(names)].join(".")
}

function scan(): Record<string, Finding[]> {
  const config = ts.readConfigFile(resolve(FRONTEND, "tsconfig.app.json"), ts.sys.readFile)
  const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, FRONTEND)
  const files = parsed.fileNames.filter((file) => !/[\\/]__tests__[\\/]|\.test\.|\.spec\./.test(file))
  const program = ts.createProgram(files, { ...parsed.options, noEmit: true })
  const checker = program.getTypeChecker()
  const wanted = new Set(files.map((file) => resolve(file)))
  const found: Record<string, Finding[]> = {}
  for (const source of program.getSourceFiles()) {
    if (!wanted.has(resolve(source.fileName))) continue
    const path = relative(resolve(FRONTEND, ".."), resolve(source.fileName)).split(sep).join("/")
    if (READERS.has(path)) continue
    const note = (node: ts.Node, rule: Rule) => {
      const key = `${path}::${scopeOf(node)}::${rule}`
      const line = source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1
      ;(found[key] ??= []).push({ line, text: node.getText(source).replace(/\s+/g, " ").slice(0, 80) })
    }
    const visit = (node: ts.Node) => {
      let argument: ts.Expression | undefined
      if (ts.isCallExpression(node) && node.arguments.length > 0) {
        const callee = node.expression
        const named = ts.isIdentifier(callee) && PARSERS.has(callee.text)
        const method = ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === "Number" && (callee.name.text === "parseFloat" || callee.name.text === "parseInt")
        if (named || method) argument = node.arguments[0]
      } else if (ts.isPrefixUnaryExpression(node) && node.operator === ts.SyntaxKind.PlusToken) {
        argument = node.operand
      }
      if (argument !== undefined && !numbersOwnText(argument) && mayBeText(checker.getTypeAtLocation(argument))) note(node, "parse")
      if (ts.isJsxAttribute(node) && node.name.getText(source) === "type" && node.initializer !== undefined) {
        const value = ts.isStringLiteral(node.initializer) ? node.initializer.text : ts.isJsxExpression(node.initializer) && node.initializer.expression !== undefined && ts.isStringLiteralLike(node.initializer.expression) ? node.initializer.expression.text : null
        if (value === "number") note(node, "input")
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
  }
  return found
}

describe("typed numbers by hand", () => {
  const found = scan()

  it("reads typed text as a number only through the one reader", () => {
    const unlisted = Object.fromEntries(
      Object.entries(found)
        .filter(([key]) => !(key in NOT_TYPED))
        .map(([key, findings]) => [key, findings.map((finding) => `line ${finding.line}: ${finding.text}`)]),
    )
    expect(
      unlisted,
      "Read typed text with utils/typedNumbers (readTypedNumber, readTypedWhole), utils/numberRule (readNumberDraft) or the shared NumberField. Text no one types goes on NOT_TYPED with its reason.",
    ).toEqual({})
  }, 120_000)

  it("lists only exceptions that still exist", () => {
    expect(Object.keys(NOT_TYPED).filter((key) => !(key in found))).toEqual([])
  })
})
