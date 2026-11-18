/**
 * No panel holds its own copy of a stored value.
 *
 * A value being edited in a panel is held by the shared draft, `useCommittedDraft`
 * (`components/form/useCommittedDraft.ts`): it shows the stored value until the analyst
 * types, lets go of its copy when an Undo, a Redo or a change from outside moves the
 * value under it, and commits once, as one undo step, on blur or Enter or when a save
 * settles the open edits. A copy held any other way goes on showing, and sending, its
 * own value after an Undo (the search space box), or writes every keystroke as
 * its own undo step (the Banding grids). This scan finds, in the editor's
 * panels, nodes, components and hooks, but tests:
 *
 * - `field`: a typed `<input>` or `<textarea>` the component controls itself (a `value`
 *   with an `onChange`), which either writes the store on every keystroke or keeps a draft
 *   of its own; the shared fields are built on the shared draft;
 * - `seed`: state (`useState`, `useReducer`, `useRef`) whose first value is read from the
 *   component's props or from the graph store;
 * - `resync`: an effect that sets the component's state from its props or the graph;
 * - `adjust`: state set while rendering, the pattern the shared draft uses to drop a copy
 *   when the value under it changes.
 *
 * State that is only the page's own (an open menu, a search box, a dialog's form) or a
 * mirror a callback reads is on `OWN_STATE` with its reason, keyed `path::scope::rule`.
 */

import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { describe, expect, it } from "vitest"

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../../..")
const SCANNED = ["panels", "nodes", "components/form"]
/** The shared mechanism itself: the draft, the fields built on it, and row identity. */
const SHARED = new Set([
  "frontend/src/components/form/useCommittedDraft.ts",
  "frontend/src/components/form/CommittedTextField.tsx",
  "frontend/src/panels/editors/shared/useRowKeys.ts",
])

/** `path::scope::rule` -> why the state is the page's own, not a copy of a stored value. */
const OWN_STATE: Record<string, string> = {
  "frontend/src/components/form/SearchField.tsx::SearchField::field":
    "the shared search box: the text filters a list, its caller holds it, and it is never stored",
  "frontend/src/panels/assistant/AssistantPanel.tsx::AssistantPanel::adjust":
    "the assistant's message draft, filled once per fix request; a message, not a stored value",
  "frontend/src/panels/assistant/Composer.tsx::Composer::field":
    "the assistant's message box, the panel's own draft message",
  "frontend/src/panels/editors/_DatabricksSelector.tsx::WarehousePicker::seed":
    "whether the HTTP path is typed by hand: a mode, chosen once from the stored path",
  "frontend/src/panels/editors/_shared.tsx::FileBrowser::copy":
    "the browsed folder's listing, read from the file-list cache: an answer, not a stored value",
  "frontend/src/panels/editors/_shared.tsx::FileBrowser::seed":
    "the folder the file browser opens on and the file it highlights: browsing state of an open dialog",
  "frontend/src/panels/editors/ApiInputEditor.tsx::ApiInputEditor::adjust":
    "inferred tables staged for the analyst to confirm: a server's answer, dropped when the config changes",
  "frontend/src/panels/editors/banding/GenerateBandsDialog.tsx::CalendarGenerate::seed":
    "the Generate dialog's own form, started from the current bands; nothing is written until Generate",
  "frontend/src/panels/editors/banding/GenerateBandsDialog.tsx::NumericGenerate::seed":
    "the Generate dialog's own form, started from the current bands; nothing is written until Generate",
  "frontend/src/panels/editors/polarsSteps/fields.tsx::LiteralListField::seed":
    "the type and text of a value being added, before it is in the list",
  "frontend/src/panels/editors/polarsSteps/fields.tsx::MoreOptions::seed":
    "a disclosure, open at first when the saved step uses its options",
  "frontend/src/panels/editors/polarsSteps/forms.tsx::GroupByForm::seed":
    "a disclosure: whether the aggregations' row filters show",
  "frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts::useRenderedSteps::seed":
    "the server's rendering of the steps as code: an answer, fenced by the steps' revision",
  "frontend/src/panels/editors/shared/SearchableItemList.tsx::SearchableItemList::field":
    "the list's search box: the text filters the rows and is never stored",
  "frontend/src/panels/editors/shared/useSearchableList.ts::useSearchableList::adjust":
    "which row is selected, kept on the same item as a search narrows the list",
  "frontend/src/panels/editors/shared/useSearchableList.ts::useSearchableList::seed":
    "which row is selected, kept on the same item as a search narrows the list",
  "frontend/src/panels/explore/SchemaTableCard.tsx::SchemaTableCard::field":
    "the schema table's search box",
  "frontend/src/panels/git/useGitHistory.ts::useGitHistory::seed":
    "the git history listed for a branch: version control's answer, not the pipeline document",
  "frontend/src/panels/modelling/ColumnSelector.tsx::ColumnSelector::copy":
    "the dropdown's active option and where its menu is placed",
  "frontend/src/panels/modelling/FeatureBrowser.tsx::FeatureBrowser::field":
    "the feature list's search box",
  "frontend/src/panels/modelling/TermTableView.tsx::TermTableView::copy":
    "which axes a fitted table is shown by: a view of a training result",
  "frontend/src/panels/modelling/TermTableView.tsx::TermTableView::seed":
    "which axes a fitted table is shown by: a view of a training result",
  "frontend/src/panels/optimiser/OptimiserScenarioPanes.tsx::ScenarioQuoteNavigation::field":
    "the quote number to jump to in a solved scenario",
  "frontend/src/panels/optimiser/QuotesTab.tsx::QuotesTab::adjust":
    "which result's quotes are shown, reset when the target changes",
  "frontend/src/panels/optimiser/QuotesTab.tsx::QuotesTab::seed":
    "which result's quotes are shown, reset when the target changes",
  "frontend/src/panels/optimiser/useOptimiserScenarioData.ts::useOptimiserScenarioData::adjust":
    "the scenario chart's series and navigation: a view of a solve's result",
  "frontend/src/panels/optimiser/useOptimiserScenarioData.ts::useOptimiserScenarioData::seed":
    "the scenario chart's series and navigation: a view of a solve's result",
  "frontend/src/panels/OptimiserPreview.tsx::OptimiserPreview::adjust":
    "the analyst's review of a solve's result, reset for another result",
  "frontend/src/panels/OptimiserPreview.tsx::OptimiserPreview::seed":
    "the analyst's review of a solve's result, reset for another result",
  "frontend/src/panels/SortableValuesTable.tsx::ValuesTableSearch::field":
    "the values table's search box",
  "frontend/src/panels/TracePanel.tsx::TracePanel::adjust":
    "the trace story in focus, reset for another story",
  "frontend/src/panels/TracePanel.tsx::TracePanel::seed":
    "the trace story in focus, reset for another story",
  "frontend/src/panels/useRecordedNodeColumns.ts::useRecordedNodeColumns::adjust":
    "the columns a preview recorded for the node: a run's answer, kept per source and config",
}

type Rule = "field" | "seed" | "copy" | "adjust"
type Finding = { line: number; scope: string; rule: Rule; text: string }

const TYPED_INPUT_TYPES = new Set(["text", "number", "search", "date", "datetime-local", "time", "email", "url", "tel", "password", "month", "week"])
const GRAPH_READS = new Set(["useGraph", "useGraphStore"])
const STATE_HOOKS = new Set(["useState", "useReducer"])
const PURE_CALLS = new Set(["Number", "String", "Boolean", "parseInt", "parseFloat"])
const EFFECT_HOOKS = new Set(["useEffect", "useLayoutEffect"])

type ComponentFn = ts.FunctionDeclaration | ts.ArrowFunction | ts.FunctionExpression

function functionName(fn: ComponentFn): string | null {
  if (ts.isFunctionDeclaration(fn)) return fn.name?.text ?? null
  const parent = fn.parent
  if (ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) return parent.name.text
  // memo(function Name() {}) / forwardRef((props) => ...) assigned to a name.
  if (ts.isCallExpression(parent) && ts.isVariableDeclaration(parent.parent) && ts.isIdentifier(parent.parent.name)) {
    return parent.parent.name.text
  }
  if (ts.isFunctionExpression(fn) && fn.name) return fn.name.text
  return null
}

/** A component (a capitalised name) or a hook (`useX`): where React state lives. */
function isComponentOrHook(name: string | null): name is string {
  return name !== null && (/^[A-Z]/.test(name) || /^use[A-Z]/.test(name))
}

function bindingNames(name: ts.BindingName, into: Set<string>): void {
  if (ts.isIdentifier(name)) {
    into.add(name.text)
    return
  }
  for (const element of name.elements) {
    if (ts.isBindingElement(element)) bindingNames(element.name, into)
  }
}

/** Whether *node* is an identifier read as a value (not a property name or a key). */
function isValueReference(node: ts.Identifier): boolean {
  const parent = node.parent
  if (ts.isPropertyAccessExpression(parent) && parent.name === node) return false
  if (ts.isPropertyAssignment(parent) && parent.name === node) return false
  if (ts.isJsxAttribute(parent)) return false
  if (ts.isBindingElement(parent) && parent.propertyName === node) return false
  return true
}

function readsAny(node: ts.Node, names: ReadonlySet<string>): boolean {
  let found = false
  const visit = (current: ts.Node) => {
    if (found) return
    if (ts.isIdentifier(current) && (names.has(current.text) || GRAPH_READS.has(current.text)) && isValueReference(current)) {
      found = true
      return
    }
    if (
      ts.isCallExpression(current) && ts.isIdentifier(current.expression) && GRAPH_READS.has(current.expression.text)
    ) {
      found = true
      return
    }
    ts.forEachChild(current, visit)
  }
  visit(node)
  return found
}

function calleeName(call: ts.CallExpression): string | null {
  const callee = call.expression
  if (ts.isIdentifier(callee)) return callee.text
  if (ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === "React") {
    return callee.name.text
  }
  return null
}

type Objects = ReadonlyMap<string, ts.ObjectLiteralExpression>

/**
 * An attribute's value: written on the element (`value={…}`, `type="text"`), or given in an
 * object the element spreads (`{...common}`) that the component declares. Present with no
 * value (`disabled`) is `true`; absent is `undefined`.
 */
function attributeValue(element: ts.JsxOpeningLikeElement, name: string, objects: Objects): ts.Expression | true | undefined {
  let found: ts.Expression | true | undefined
  for (const property of element.attributes.properties) {
    if (ts.isJsxAttribute(property) && property.name.getText() === name) {
      const init = property.initializer
      found = !init ? true : ts.isJsxExpression(init) ? init.expression ?? true : init
    } else if (ts.isJsxSpreadAttribute(property) && ts.isIdentifier(property.expression)) {
      const spread = objects.get(property.expression.text)
      const member = spread?.properties.find(
        (candidate) => candidate.name !== undefined && candidate.name.getText().replace(/["']/g, "") === name,
      )
      if (member && ts.isPropertyAssignment(member)) found = member.initializer
      else if (member && ts.isShorthandPropertyAssignment(member)) found = member.name
    }
  }
  return found
}

/** A typed `<input>` (or a `<textarea>`) controlled by the component itself. */
function isOwnTypedField(element: ts.JsxOpeningLikeElement, objects: Objects): boolean {
  const tag = element.tagName.getText()
  if (tag !== "input" && tag !== "textarea") return false
  if (!attributeValue(element, "value", objects) || !attributeValue(element, "onChange", objects)) return false
  if (tag === "textarea") return true
  const type = attributeValue(element, "type", objects)
  if (type === undefined || type === true) return true
  if (ts.isStringLiteral(type)) return TYPED_INPUT_TYPES.has(type.text)
  return true
}

/** The state a component shows as a field's value: `value={draft}`, `value={draft ?? stored}`. */
function fieldValueStates(fn: ComponentFn, states: ReadonlyMap<string, string>): Set<string> {
  const shown = new Set<string>()
  const names = new Set(states.keys())
  const visit = (node: ts.Node) => {
    if (ts.isJsxAttribute(node) && node.name.getText() === "value" && node.initializer && ts.isJsxExpression(node.initializer)) {
      const expression = node.initializer.expression
      if (expression) {
        for (const name of names) if (readsAny(expression, new Set([name]))) shown.add(states.get(name) as string)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(fn)
  return shown
}

/** Each `useState`'s value name and its setter, by value name. */
function stateSetters(fn: ComponentFn): Map<string, string> {
  const states = new Map<string, string>()
  const visit = (node: ts.Node) => {
    if (
      ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer)
      && calleeName(node.initializer) === "useState" && ts.isArrayBindingPattern(node.name)
    ) {
      const [value, setter] = node.name.elements
      if (
        value && setter && ts.isBindingElement(value) && ts.isBindingElement(setter)
        && ts.isIdentifier(value.name) && ts.isIdentifier(setter.name)
      ) {
        states.set(value.name.text, setter.name.text)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(fn)
  return states
}

/** Every place in *source* where a component or hook holds its own copy of a stored value. */
export function draftsByHand(source: string, fileName = "source.tsx"): Finding[] {
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const findings: Finding[] = []
  const report = (node: ts.Node, scope: string, rule: Rule) => {
    findings.push({
      line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
      scope,
      rule,
      text: node.getText(file).split("\n")[0].slice(0, 120),
    })
  }

  const inspect = (fn: ComponentFn, scope: string) => {
    // What the function was given, and what it read from the graph: the stored values.
    const stored = new Set<string>()
    for (const parameter of fn.parameters) bindingNames(parameter.name, stored)
    const setters = new Set<string>()
    const body = fn.body
    if (!body) return
    const statements = ts.isBlock(body) ? body.statements : []

    /** The setter a call is to, when it sets the component's own state. */
    const setterCalled = (node: ts.Node): ts.CallExpression | null =>
      ts.isCallExpression(node) && ts.isIdentifier(node.expression) && setters.has(node.expression.text) ? node : null

    /** Whether a field's change handler does more than set the component's own state. */
    const writesOnChange = (handler: ts.Expression): boolean => {
      if (ts.isIdentifier(handler)) return !setters.has(handler.text)
      let writes = false
      const visit = (current: ts.Node) => {
        if (writes) return
        if (ts.isCallExpression(current) && !setterCalled(current)) {
          const callee = current.expression
          if (ts.isIdentifier(callee) && !PURE_CALLS.has(callee.text)) writes = true
          if (ts.isPropertyAccessExpression(callee)) {
            let root: ts.Expression = callee
            while (ts.isPropertyAccessExpression(root) || ts.isCallExpression(root)) {
              root = ts.isPropertyAccessExpression(root) ? root.expression : root.expression
            }
            if (ts.isIdentifier(root) && stored.has(root.text)) writes = true
          }
        }
        ts.forEachChild(current, visit)
      }
      visit(handler)
      return writes
    }

    // A setter whose state a field shows: setting it from a stored value, in a handler
    // too, makes a draft.
    const fieldSetters = fieldValueStates(fn, stateSetters(fn))
    const objects = new Map<string, ts.ObjectLiteralExpression>()
    const counted = new Set<ts.Node>()
    const adjusted = counted
    const walk = (node: ts.Node, topLevel: boolean) => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer && ts.isObjectLiteralExpression(node.initializer)) {
        objects.set(node.name.text, node.initializer)
      }
      // A nested component or hook is inspected on its own.
      if ((ts.isFunctionDeclaration(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node)) && node !== fn) {
        const name = functionName(node)
        if (isComponentOrHook(name) && (ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node.parent))) return
        ts.forEachChild(node, (child) => walk(child, false))
        return
      }
      if (ts.isVariableDeclaration(node) && node.initializer) {
        const init = node.initializer
        if (ts.isCallExpression(init) && STATE_HOOKS.has(calleeName(init) ?? "")) {
          const hook = calleeName(init)
          const seed = hook === "useReducer" ? init.arguments[1] : init.arguments[0]
          if (seed && readsAny(seed, stored)) report(init, scope, "seed")
          if (hook === "useState" && ts.isArrayBindingPattern(node.name)) {
            const setter = node.name.elements[1]
            if (setter && ts.isBindingElement(setter) && ts.isIdentifier(setter.name)) setters.add(setter.name.text)
          }
          return
        }
        if (readsAny(init, stored)) bindingNames(node.name, stored)
      }
      if (topLevel && ts.isIfStatement(node)) {
        let adjusts = false
        const visit = (current: ts.Node) => {
          if (ts.isFunctionLike(current)) return
          if (setterCalled(current)) {
            adjusts = true
            adjusted.add(current)
          }
          ts.forEachChild(current, visit)
        }
        visit(node.thenStatement)
        if (adjusts) report(node, scope, "adjust")
      }
      // State set from a stored value as an effect runs: an answer that arrives later, in a
      // callback, is an answer, not a copy.
      if (ts.isCallExpression(node) && EFFECT_HOOKS.has(calleeName(node) ?? "")) {
        const effect = node.arguments[0]
        const body = effect && (ts.isArrowFunction(effect) || ts.isFunctionExpression(effect)) ? effect.body : undefined
        let copies = false
        const visit = (current: ts.Node) => {
          if (ts.isFunctionLike(current)) return
          const call = setterCalled(current)
          if (call && call.arguments.some((argument) => readsAny(argument, stored))) {
            copies = true
            counted.add(current)
          }
          ts.forEachChild(current, visit)
        }
        if (body) {
          if (ts.isBlock(body)) body.statements.forEach(visit)
          else visit(body)
        }
        if (copies) report(node, scope, "copy")
      }
      const setter = setterCalled(node)
      if (
        setter && !counted.has(setter) && fieldSetters.has((setter.expression as ts.Identifier).text)
        && setter.arguments.some((argument) => readsAny(argument, stored))
      ) {
        report(node, scope, "copy")
      }
      if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) && isOwnTypedField(node, objects)) {
        const handler = attributeValue(node, "onChange", objects)
        if (handler === true || handler === undefined || writesOnChange(handler)) report(node, scope, "field")
      }
      ts.forEachChild(node, (child) => walk(child, false))
    }
    for (const statement of statements) walk(statement, true)
    if (!ts.isBlock(body)) walk(body, false)
  }

  const visit = (node: ts.Node) => {
    if (ts.isFunctionDeclaration(node) || ts.isArrowFunction(node) || ts.isFunctionExpression(node)) {
      const name = functionName(node)
      if (isComponentOrHook(name)) inspect(node, name)
    }
    ts.forEachChild(node, visit)
  }
  visit(file)
  return findings
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sourceFiles(path)
    return /\.tsx?$/.test(entry.name) && !/\.(?:test|d)\.tsx?$/.test(entry.name) ? [path] : []
  })
}

function repositoryFindings(): Map<string, Finding[]> {
  const found = new Map<string, Finding[]>()
  for (const directory of SCANNED) {
    for (const path of sourceFiles(resolve(SRC, directory))) {
      const rel = `frontend/src/${relative(SRC, path).split(sep).join("/")}`
      if (SHARED.has(rel)) continue
      for (const finding of draftsByHand(readFileSync(path, "utf-8"), path)) {
        const key = `${rel}::${finding.scope}::${finding.rule}`
        found.set(key, [...(found.get(key) ?? []), finding])
      }
    }
  }
  return found
}

describe("the scan finds a copy held by hand", () => {
  it("finds a typed field the component controls itself", () => {
    const findings = draftsByHand(`
      export function Cell({ value, onUpdate }) {
        return <input type="text" value={value} onChange={(e) => onUpdate(e.target.value)} />
      }`)
    expect(findings.map((f) => [f.scope, f.rule])).toEqual([["Cell", "field"]])
  })

  it("finds state seeded from a prop, through a value derived from it", () => {
    const findings = draftsByHand(`
      export function Box({ config }) {
        const text = format(config.search_space)
        const [draft, setDraft] = useState(text)
        return <CommittedTextArea value={draft} onCommit={setDraft} />
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["seed"])
  })

  it("finds state seeded from the graph store", () => {
    const findings = draftsByHand(`
      function useLabel(id) {
        const [label] = useState(() => useGraphStore.getState().nodes[0].data.label)
        return label
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["seed"])
  })

  it("finds a field whose value and change handler come in a spread object", () => {
    const findings = draftsByHand(`
      export function Grid({ rules, onUpdate }) {
        return rules.map((rule, i) => {
          const common = { value: rule.value, onChange: (e) => onUpdate(i, e.target.value) }
          return <input type="text" {...common} />
        })
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["field"])
  })

  it("finds a field's state set from a stored value in a handler", () => {
    const findings = draftsByHand(`
      export function Formula({ formula }) {
        const [name, setName] = useState("")
        const [error, setError] = useState(null)
        return <>
          <button onClick={() => { setName(formula.name); setError(formula.error) }}>Edit</button>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </>
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["copy"])
  })

  it("finds an effect that copies a prop into state", () => {
    const findings = draftsByHand(`
      const Terms = ({ terms }) => {
        const [json, setJson] = useState("")
        useEffect(() => { setJson(JSON.stringify(terms)) }, [terms])
        return null
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["copy"])
  })

  it("finds state adjusted while rendering", () => {
    const findings = draftsByHand(`
      export function NumberCell({ value }) {
        const [draft, setDraft] = useState(null)
        const [seen, setSeen] = useState("")
        if (seen !== value) { setSeen(value); setDraft(null) }
        return null
      }`)
    expect(findings.map((f) => f.rule)).toEqual(["adjust"])
  })

  it("finds nothing in a field built on the shared draft, nor in the page's own state", () => {
    const findings = draftsByHand(`
      export function Suffix({ config, onUpdate }) {
        const [open, setOpen] = useState(false)
        const [query, setQuery] = useState("")
        return <>
          <CommittedTextField value={config.suffix} onCommit={(v) => onUpdate("suffix", v)} />
          <input type="checkbox" checked={open} onChange={() => setOpen(!open)} />
          <SearchField value={query} onChange={setQuery} />
        </>
      }`)
    expect(findings).toEqual([])
  })
})

describe("no panel holds its own copy of a stored value", () => {
  const found = repositoryFindings()

  it("every copy found is the page's own state, named with its reason", () => {
    const unlisted = [...found.entries()]
      .filter(([key]) => !(key in OWN_STATE))
      .map(([key, findings]) => `${key} (line ${findings.map((f) => f.line).join(", ")}): ${findings[0].text}`)
    expect(unlisted.join("\n")).toBe("")
  })

  it("every named exception is still found", () => {
    expect(Object.keys(OWN_STATE).filter((key) => !found.has(key))).toEqual([])
  })
})
