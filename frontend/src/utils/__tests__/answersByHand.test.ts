/**
 * No browser code writes state after waiting for the server outside the late-answer helper.
 *
 * An answer is written to state through `utils/answers.ts` (specs/frontend-shared low-level,
 * "Late answers"), which checks, in the turn the answer settles, that what asked for it is
 * as it was: the newest request of its kind, the document, the graph, a node, a field being
 * typed in, the state it was asked under. A guard kept by hand after an `await` (a ticket, a
 * `cancelled` flag, a form compared) missed a case each time it was written: a repair applied
 * over a newer document, a catalog's schemas shown for another, a further page's `finally`
 * clearing another list's flag. This scan finds, anywhere under `src` but tests, generated
 * code and the helper itself, a function that writes state after a wait:
 *
 * - a wait is an `await`, or a callback given to `.then`, `.catch` or `.finally` (a function,
 *   or a writer passed by name); a loop that waits waits from its first statement on;
 * - a write is a call of a state setter (`set`, `setState`, a `set…` name, `dispatch`), of a
 *   store's action (through `getState()` or a store's `get()`), of a callback prop (`on…`), of
 *   a function a hook gave, of a function imported from `stores/` or `hooks/`, of a function of
 *   the same file that writes, or of `ask` and `answer`, which capture what an answer belongs
 *   to when they are called; or an assignment to a ref's `current` or a variable of the module.
 *   A method of the function's own local value (a `Map` it built) is not state.
 *
 * A handler given to `answer` or to an `Asked`'s `answer` (`ok`, `failed`, `stale`) and a
 * `pending` start unwaited: the helper runs them while the request is current. An `await` of
 * something that is no answer from the server (a question to the analyst, the clipboard, a
 * lazy import) is on `NOT_SERVER` with its reason.
 */

import { readdirSync, readFileSync } from "node:fs"
import { dirname, relative, resolve, sep } from "node:path"
import { fileURLToPath } from "node:url"
import ts from "typescript"
import { describe, expect, it } from "vitest"

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "../..")
/**
 * Not browser code a user's answer reaches: tests, generated code, and the API client, whose
 * functions send the requests and keep the transport's own state (a session, a retry).
 */
const SKIPPED_DIRECTORIES = new Set(["__tests__", "generated", "test-utils", "testSupport", "api"])
const SHARED = new Set(["frontend/src/utils/answers.ts"])

/** What an `await` or a `.then` may wait for without waiting for the server, by callee, with why. */
export const NOT_SERVER: Record<string, string> = {
  confirmed: "a question to the analyst, answered in the page",
  "useQuestionStore.getState().ask": "a question to the analyst, answered in the page",
  "navigator.clipboard.writeText": "the browser's clipboard",
  "navigator.clipboard.write": "the browser's clipboard",
  import: "a lazy import of the editor's own code",
  getLayoutedElements: "the layout engine, run in the page",
  parsePipelineDocumentUpdateFrame: "reads a frame the sync socket pushed, with validators loaded in the page",
  parsePipelineEditorDocument: "reads a document already answered, with validators loaded in the page",
}

/**
 * A function that calling after a wait does not make a write, by `path::name`, with why: it
 * writes only a memo of its own, which no one reads as the page's state, or it is a wait the
 * helper's own callers build on, which applies what it is given under the helper while its
 * caller's signal holds.
 */
export const NOT_STATE: Record<string, string> = {
  "frontend/src/hooks/jobPollingController.ts::waitForJob":
    "the one wait for a job: each poll's answer, and the caller's onStatus, under the wait and its caller's signal",
  "frontend/src/hooks/previewProgressPoller.ts::pollPreviewProgress":
    "polls a preview's progress: each answer, and the caller's onProgress, under the polling and its caller's signal",
  "frontend/src/stores/useGraphStore.ts::canvasGraph":
    "keeps the canvas as one object while its nodes and edges are the same, so readers can compare it",
  "frontend/src/types/pipelineDocument.ts::loadEditorDocumentValidators":
    "loads the editor document's generated validators once and keeps the module",
}

type Finding = { line: number; scope: string; text: string }

const WRITE_NAME = /^(?:set|setState|dispatch|set[A-Z]\w*|on[A-Z]\w*)$/
const HANDLER_KEYS = new Set(["ok", "failed", "stale"])
const STATE_HOOKS_VALUE_ONLY = new Set(["useRef", "useMemo", "useId", "useContext", "useDeferredValue", "useSyncExternalStore"])

type FunctionNode = ts.FunctionLikeDeclaration & { body?: ts.ConciseBody }

function isFunction(node: ts.Node): node is FunctionNode {
  return ts.isFunctionDeclaration(node) || ts.isFunctionExpression(node) || ts.isArrowFunction(node) || ts.isMethodDeclaration(node)
    || ts.isGetAccessorDeclaration(node) || ts.isSetAccessorDeclaration(node) || ts.isConstructorDeclaration(node)
}

function isLoop(node: ts.Node): node is ts.IterationStatement {
  return ts.isForStatement(node) || ts.isForOfStatement(node) || ts.isForInStatement(node) || ts.isWhileStatement(node) || ts.isDoStatement(node)
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

/** The text of a callee as dotted names (`navigator.clipboard.writeText`), or null. */
function dotted(expression: ts.Expression): string | null {
  if (ts.isIdentifier(expression)) return expression.text
  if (expression.kind === ts.SyntaxKind.ImportKeyword) return "import"
  if (ts.isPropertyAccessExpression(expression)) {
    const left = dotted(expression.expression)
    return left === null ? null : `${left}.${expression.name.text}`
  }
  // A store's state, read on the way: `useQuestionStore.getState().ask`.
  if (ts.isCallExpression(expression) && expression.arguments.length === 0 && ts.isPropertyAccessExpression(expression.expression)
    && expression.expression.name.text === "getState") {
    const store = dotted(expression.expression)
    return store === null ? null : `${store}()`
  }
  return null
}

/** The expression a call or property chain starts from: `a` in `a.b().c`. */
function rootOf(expression: ts.Expression): ts.Expression {
  let current = expression
  for (;;) {
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) current = current.expression
    else if (ts.isCallExpression(current)) current = current.expression
    else if (ts.isNonNullExpression(current) || ts.isParenthesizedExpression(current) || ts.isAsExpression(current)) current = current.expression
    else return current
  }
}

/** Whether a call chain passes through `getState()` or a store's `get()`. */
function throughStore(expression: ts.Expression): boolean {
  let current = expression
  for (;;) {
    if (ts.isCallExpression(current)) {
      const callee = current.expression
      if (ts.isPropertyAccessExpression(callee) && callee.name.text === "getState") return true
      if (ts.isIdentifier(callee) && callee.text === "get" && current.arguments.length === 0) return true
      current = callee
    } else if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) current = current.expression
    else if (ts.isNonNullExpression(current) || ts.isParenthesizedExpression(current)) current = current.expression
    else return false
  }
}

function functionName(node: ts.Node): string {
  if ((ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) && node.name) return node.name.getText()
  const parent = node.parent
  if (parent && ts.isVariableDeclaration(parent)) return parent.name.getText()
  if (parent && ts.isPropertyAssignment(parent)) return parent.name.getText()
  if (parent && ts.isCallExpression(parent)) {
    const holder = parent.parent
    // `const load = useCallback(async () => …)`
    if (holder && ts.isVariableDeclaration(holder)) return holder.name.getText()
    if (holder && ts.isPropertyAssignment(holder)) return holder.name.getText()
  }
  return ""
}

/** The nearest named function holding *node*, `outer.inner` when both are named. */
function scopeOf(node: ts.Node): string {
  const names: string[] = []
  for (let current: ts.Node | undefined = node; current; current = current.parent) {
    if (isFunction(current)) {
      const name = functionName(current)
      if (name) names.unshift(name)
    }
  }
  return names.slice(-2).join(".") || "<module>"
}

/** Whether *fn* is a handler or a `pending` the helper calls while its request is current. */
function isHelperCallback(fn: ts.Node): boolean {
  // `asked.apply(() => …)`: a write run now, while the request is current.
  if (fn.parent && ts.isCallExpression(fn.parent) && fn.parent.arguments[0] === fn) {
    const callee = fn.parent.expression
    if (ts.isPropertyAccessExpression(callee) && callee.name.text === "apply" && ts.isIdentifier(callee.expression)) return true
  }
  const property = fn.parent
  if (!property || !ts.isPropertyAssignment(property)) return false
  const key = property.name.getText()
  const literal = property.parent
  if (!ts.isObjectLiteralExpression(literal) || !literal.parent || !ts.isCallExpression(literal.parent)) return false
  const call = literal.parent
  const callee = dotted(call.expression) ?? ""
  const index = call.arguments.indexOf(literal)
  const isAnswer = callee === "answer" || callee.endsWith(".answer")
  if (HANDLER_KEYS.has(key)) return (callee === "answer" && index === 2) || (callee !== "answer" && isAnswer && index === 1)
  if (key === "pending") return (callee === "answer" && index === 3) || (callee === "ask" && index === 1)
  if (key === "inFlight") return callee === "answer" && index === 3
  return false
}

/** Whether *fn* is a callback given to `.then`, `.catch` or `.finally`. */
function isPromiseCallback(fn: ts.Node): ts.CallExpression | null {
  const call = fn.parent
  if (!call || !ts.isCallExpression(call) || !call.arguments.includes(fn as ts.Expression)) return null
  const callee = call.expression
  if (!ts.isPropertyAccessExpression(callee) || !["then", "catch", "finally"].includes(callee.name.text)) return null
  return call
}

/**
 * The promise a `.then`, `.catch` or `.finally` waits on: its receiver. A link further down a
 * chain waits on what the links before it returned, so only the first link of a chain on a
 * wait for no answer (`import(...).then(...)`) waits for no answer.
 */
function awaited(call: ts.CallExpression): ts.Expression {
  return (call.expression as ts.PropertyAccessExpression).expression
}

/** Whether *statement* always leaves the function: it ends in a `return` or a `throw`. */
function leaves(statement: ts.Statement): boolean {
  if (ts.isReturnStatement(statement) || ts.isThrowStatement(statement)) return true
  if (ts.isBlock(statement)) {
    const last = statement.statements.at(-1)
    return last !== undefined && leaves(last)
  }
  return false
}

function isNotServer(expression: ts.Expression): boolean {
  let current = expression
  while (ts.isParenthesizedExpression(current) || ts.isAsExpression(current) || ts.isNonNullExpression(current)) current = current.expression
  if (!ts.isCallExpression(current)) return false
  const callee = dotted(current.expression)
  return callee !== null && callee in NOT_SERVER
}

/** What one module declares, imports and exports, for following a writer across modules. */
interface Module {
  readonly path: string
  readonly file: ts.SourceFile
  /** A local name a hook gave, called as a function. */
  readonly hookBound: Set<string>
  /** A local name bound by an import: the module it is from (null for a package) and its name there. */
  readonly imports: Map<string, { readonly from: string | null; readonly name: string }>
  readonly moduleVariables: Set<string>
  /** A function the module declares, by name. */
  readonly declared: Map<string, ts.Node>
  /** An exported name: a local name, or a name of another module re-exported. */
  readonly exports: Map<string, { readonly local: string } | { readonly from: string; readonly name: string }>
  readonly exportsAll: string[]
}

const EXTENSIONS = [".ts", ".tsx", "/index.ts", "/index.tsx"]

function resolveModule(from: string, specifier: string, known: ReadonlySet<string>): string | null {
  if (!specifier.startsWith(".")) return null
  const base = resolve(dirname(from), specifier)
  if (known.has(base)) return base
  for (const extension of EXTENSIONS) if (known.has(base + extension)) return base + extension
  return null
}

function readModule(path: string, source: string, known: ReadonlySet<string>): Module {
  const file = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, path.endsWith(".ts") ? ts.ScriptKind.TS : ts.ScriptKind.TSX)
  const module: Module = {
    path, file, hookBound: new Set(), imports: new Map(), moduleVariables: new Set(), declared: new Map(), exports: new Map(), exportsAll: [],
  }
  for (const statement of file.statements) {
    if (ts.isImportDeclaration(statement) && ts.isStringLiteral(statement.moduleSpecifier) && !statement.importClause?.isTypeOnly) {
      const specifier = statement.moduleSpecifier.text
      const from = resolveModule(path, specifier, known)
      const clause = statement.importClause
      // The helper's `ask` and `answer` capture what an answer belongs to as they are called.
      const helper = /(?:^|\/)utils\/answers$/.test(specifier)
      if (clause?.name) module.imports.set(clause.name.text, { from, name: "default" })
      if (clause?.namedBindings && ts.isNamedImports(clause.namedBindings)) {
        for (const element of clause.namedBindings.elements) {
          if (element.isTypeOnly) continue
          const name = (element.propertyName ?? element.name).text
          module.imports.set(element.name.text, { from: helper && (name === "ask" || name === "answer") ? "<helper>" : from, name })
        }
      }
      if (clause?.namedBindings && ts.isNamespaceImport(clause.namedBindings)) {
        module.imports.set(clause.namedBindings.name.text, { from, name: "*" })
      }
    }
    if (ts.isExportDeclaration(statement) && !statement.isTypeOnly) {
      const from = statement.moduleSpecifier && ts.isStringLiteral(statement.moduleSpecifier)
        ? resolveModule(path, statement.moduleSpecifier.text, known)
        : null
      if (!statement.exportClause) {
        if (from) module.exportsAll.push(from)
      } else if (ts.isNamedExports(statement.exportClause)) {
        for (const element of statement.exportClause.elements) {
          const name = (element.propertyName ?? element.name).text
          if (statement.moduleSpecifier) {
            if (from) module.exports.set(element.name.text, { from, name })
          } else module.exports.set(element.name.text, { local: name })
        }
      }
    }
    if (ts.isExportAssignment(statement) && ts.isIdentifier(statement.expression)) {
      module.exports.set("default", { local: statement.expression.text })
    }
    const exported = ts.canHaveModifiers(statement) && ts.getModifiers(statement)?.some((m) => m.kind === ts.SyntaxKind.ExportKeyword)
    const isDefault = ts.canHaveModifiers(statement) && ts.getModifiers(statement)?.some((m) => m.kind === ts.SyntaxKind.DefaultKeyword)
    if (ts.isFunctionDeclaration(statement) && statement.name && exported) {
      module.exports.set(isDefault ? "default" : statement.name.text, { local: statement.name.text })
    }
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if ((statement.declarationList.flags & ts.NodeFlags.Let) !== 0) bindingNames(declaration.name, module.moduleVariables)
        if (exported && ts.isIdentifier(declaration.name)) module.exports.set(declaration.name.text, { local: declaration.name.text })
      }
    }
  }
  const visitDeclarations = (node: ts.Node) => {
    if (ts.isVariableDeclaration(node) && node.initializer) {
      let init: ts.Expression = node.initializer
      while (ts.isAwaitExpression(init) || ts.isNonNullExpression(init) || ts.isParenthesizedExpression(init) || ts.isAsExpression(init)) init = init.expression
      if (ts.isCallExpression(init)) {
        const callee = dotted(init.expression) ?? ""
        const hook = callee.split(".").pop() ?? ""
        if (/^use[A-Z]/.test(hook) && !STATE_HOOKS_VALUE_ONLY.has(hook)) {
          if (hook === "useState" || hook === "useReducer") {
            if (ts.isArrayBindingPattern(node.name) && node.name.elements[1] && ts.isBindingElement(node.name.elements[1])) {
              bindingNames(node.name.elements[1].name, module.hookBound)
            }
          } else if (hook === "useCallback") {
            if (ts.isIdentifier(node.name) && init.arguments[0]) module.declared.set(node.name.text, init.arguments[0])
          } else {
            bindingNames(node.name, module.hookBound)
          }
        }
      }
      if (ts.isIdentifier(node.name) && (ts.isArrowFunction(node.initializer) || ts.isFunctionExpression(node.initializer))) {
        module.declared.set(node.name.text, node.initializer)
      }
    }
    if (ts.isFunctionDeclaration(node) && node.name) module.declared.set(node.name.text, node)
    ts.forEachChild(node, visitDeclarations)
  }
  visitDeclarations(file)
  return module
}

function unwrapped(expression: ts.Expression): ts.Expression {
  let current = expression
  while (ts.isParenthesizedExpression(current) || ts.isNonNullExpression(current) || ts.isAsExpression(current)) current = current.expression
  return current
}

/** Whether *expression* is a store's state: `useX.getState()`, a store's `get()`, or a chain from one. */
function readsStore(expression: ts.Expression): boolean {
  const current = unwrapped(expression)
  return ts.isCallExpression(current) && throughStore(current)
}

/** Whether *expression* is a member of a store's state: `useX.getState().save`. */
function readsStoreMember(expression: ts.Expression): boolean {
  const current = unwrapped(expression)
  return (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) && throughStore(current.expression)
}

interface Locals extends Set<string> {
  /** A local bound to a store's state (`const ui = useUIStore.getState()`): its methods write. */
  readonly stateHandles: Set<string>
  /** A local destructured from a store's state (`const { save } = get()`): calling it writes. */
  readonly stateActions: Set<string>
}

/** Names in scope in *fn*: its own, and those of the functions it is written in (a closure's). */
function scopeLocals(fn: FunctionNode): Locals {
  let scope = localsOf(fn)
  for (let outer = fn.parent; outer; outer = outer.parent) {
    if (isFunction(outer)) scope = merged(localsOf(outer as FunctionNode), scope)
  }
  return scope
}

/** Names local to *fn*: its parameters and what its body declares. */
function localsOf(fn: FunctionNode): Locals {
  const locals = Object.assign(new Set<string>(), { stateHandles: new Set<string>(), stateActions: new Set<string>() })
  for (const parameter of fn.parameters) bindingNames(parameter.name, locals)
  const visit = (node: ts.Node) => {
    if (node !== fn && isFunction(node)) return
    if (ts.isVariableDeclaration(node)) {
      bindingNames(node.name, locals)
      if (node.initializer && readsStore(node.initializer)) {
        if (ts.isIdentifier(node.name)) locals.stateHandles.add(node.name.text)
        else bindingNames(node.name, locals.stateActions)
      } else if (node.initializer && readsStoreMember(node.initializer)) {
        bindingNames(node.name, locals.stateActions)
      }
    }
    ts.forEachChild(node, visit)
  }
  if (fn.body) visit(fn.body)
  return locals
}

const NO_LOCALS: Locals = Object.assign(new Set<string>(), { stateHandles: new Set<string>(), stateActions: new Set<string>() })

function merged(outer: Locals, inner: Locals): Locals {
  return Object.assign(new Set([...outer, ...inner]), {
    stateHandles: new Set([...outer.stateHandles, ...inner.stateHandles]),
    stateActions: new Set([...outer.stateActions, ...inner.stateActions]),
  })
}

/** Every function in the modules that writes state after a wait outside the helper, by module. */
export function answersByHandIn(sources: ReadonlyMap<string, string>, notState: ReadonlySet<string> = new Set()): Map<string, Finding[]> {
  const known = new Set(sources.keys())
  const modules = new Map<string, Module>()
  for (const [path, source] of sources) modules.set(path, readModule(path, source, known))

  // A module's functions that write, `path#name`, found until no more are.
  const writers = new Set<string>()
  const exportTarget = (path: string, name: string, seen = new Set<string>()): string | null => {
    const key = `${path}#${name}`
    if (seen.has(key)) return null
    seen.add(key)
    const module = modules.get(path)
    if (!module) return null
    const exported = module.exports.get(name)
    if (exported && "local" in exported) {
      const imported = module.imports.get(exported.local)
      if (imported?.from && imported.from !== "<helper>") return exportTarget(imported.from, imported.name, seen)
      return `${path}#${exported.local}`
    }
    if (exported) return exportTarget(exported.from, exported.name, seen)
    for (const from of module.exportsAll) {
      const found = exportTarget(from, name, seen)
      if (found) return found
    }
    return null
  }
  /** Whether calling the local name *name* of *module* writes. */
  const namedWriter = (module: Module, name: string): boolean => {
    if (WRITE_NAME.test(name) || module.hookBound.has(name) || writers.has(`${module.path}#${name}`)) return true
    const imported = module.imports.get(name)
    if (!imported) return false
    if (imported.from === "<helper>") return true
    if (imported.from === null || imported.name === "*") return false
    const target = exportTarget(imported.from, imported.name)
    return target !== null && writers.has(target)
  }
  /** Whether *callee*, called, writes: a name, or a property reached from a name. */
  const writesWhenCalled = (module: Module, callee: ts.Expression, locals: Locals): boolean => {
    if (ts.isIdentifier(callee)) {
      const name = callee.text
      if (locals.stateActions.has(name) || module.hookBound.has(name)) return true
      // A value of the function's own is not state; a writer it is given (`onDone`) is.
      if (locals.has(name) && !module.declared.has(name)) return WRITE_NAME.test(name)
      return namedWriter(module, name)
    }
    if (ts.isPropertyAccessExpression(callee) || ts.isElementAccessExpression(callee)) {
      const method = ts.isPropertyAccessExpression(callee) ? callee.name.text : ""
      // Reading a store, or listening to it, writes nothing.
      if (method === "getState" || method === "subscribe") return false
      if (throughStore(callee)) return true
      const root = rootOf(callee)
      const rootName = ts.isIdentifier(root) ? root.text : null
      if (rootName !== null && locals.stateHandles.has(rootName)) return true
      if (rootName !== null && !locals.has(rootName)) {
        if (module.hookBound.has(rootName)) return true
        const imported = module.imports.get(rootName)
        // A function of a module imported whole: `history.recordSwitch(…)`.
        if (imported?.name === "*" && imported.from && ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression)) {
          const target = exportTarget(imported.from, method)
          if (target !== null && writers.has(target)) return true
        }
      }
      if (WRITE_NAME.test(method)) return rootName === null || !locals.has(rootName) || /^on[A-Z]/.test(method)
    }
    return false
  }
  const isWrite = (module: Module, node: ts.Node, locals: Locals): boolean => {
    if (ts.isCallExpression(node)) return writesWhenCalled(module, node.expression, locals)
    const target = ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment
      && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment
      ? node.left
      : (ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node))
        && (node.operator === ts.SyntaxKind.PlusPlusToken || node.operator === ts.SyntaxKind.MinusMinusToken)
        ? node.operand
        : null
    if (target === null) return false
    if (ts.isPropertyAccessExpression(target) && target.name.text === "current") return true
    return ts.isIdentifier(target) && module.moduleVariables.has(target.text) && !locals.has(target.text)
  }
  /**
   * Whether *fn* is a callback given to a wait primitive on `NOT_STATE` (`waitForJob`'s
   * `onStatus`), which applies it under its own wait and its caller's signal.
   */
  const isPrimitiveCallback = (module: Module, fn: ts.Node): boolean => {
    let argument: ts.Node = fn
    if (fn.parent && ts.isPropertyAssignment(fn.parent) && ts.isObjectLiteralExpression(fn.parent.parent)) argument = fn.parent.parent
    const call = argument.parent
    if (!call || !ts.isCallExpression(call) || !call.arguments.includes(argument as ts.Expression) || !ts.isIdentifier(call.expression)) return false
    const name = call.expression.text
    const imported = module.imports.get(name)
    const target = imported?.from && imported.name !== "*" ? exportTarget(imported.from, imported.name) : `${module.path}#${name}`
    return target !== null && notState.has(target)
  }
  const guarded = (module: Module, fn: ts.Node): boolean => isHelperCallback(fn) || isPrimitiveCallback(module, fn)
  const writesAnywhere = (module: Module, node: ts.Node): boolean => {
    let found = false
    const visit = (current: ts.Node, locals: Locals) => {
      // What the helper, or a wait primitive, runs while its request is current is no write
      // of the function's.
      if (found || (current !== node && isFunction(current) && guarded(module, current))) return
      const inner = current !== node && isFunction(current) ? merged(locals, localsOf(current)) : locals
      if (isWrite(module, current, inner)) {
        found = true
        return
      }
      ts.forEachChild(current, (child) => visit(child, inner))
    }
    visit(node, isFunction(node) ? localsOf(node as FunctionNode) : NO_LOCALS)
    return found
  }
  for (let grew = true; grew;) {
    grew = false
    for (const module of modules.values()) {
      for (const [name, fn] of module.declared) {
        const key = `${module.path}#${name}`
        if (writers.has(key) || notState.has(key) || !writesAnywhere(module, fn)) continue
        writers.add(key)
        grew = true
      }
    }
  }

  const found = new Map<string, Finding[]>()
  for (const module of modules.values()) {
    const findings: Finding[] = []
    const file = module.file
    const report = (node: ts.Node) => {
      findings.push({
        line: file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1,
        scope: scopeOf(node),
        text: node.getText(file).split("\n")[0].slice(0, 120),
      })
    }
    const waitsIn = (node: ts.Node): boolean => {
      let waits = false
      const visit = (current: ts.Node) => {
        if (waits || (current !== node && isFunction(current))) return
        if (ts.isAwaitExpression(current) && !isNotServer(current.expression)) waits = true
        ts.forEachChild(current, visit)
      }
      visit(node)
      return waits
    }
    // A function made after a wait runs after it, unless the helper runs it while current.
    const madeAfterWait = new Set<ts.Node>()
    const inspect = (fn: FunctionNode) => {
      if (!fn.body) return
      const locals = scopeLocals(fn)
      const promiseCall = isPromiseCallback(fn)
      let waited = !guarded(module, fn) && (madeAfterWait.has(fn) || (promiseCall !== null && !isNotServer(awaited(promiseCall))))
      const walk = (node: ts.Node) => {
        if (node !== fn && isFunction(node)) {
          // A function declaration is made as its scope starts, wherever it is written.
          if (waited && !ts.isFunctionDeclaration(node)) madeAfterWait.add(node)
          return
        }
        // A branch that waits and then leaves the function leaves what follows it unwaited.
        if (ts.isIfStatement(node)) {
          const before = waited
          walk(node.expression)
          const afterCondition = waited
          walk(node.thenStatement)
          const thenWaited = leaves(node.thenStatement) ? afterCondition : waited
          waited = afterCondition
          if (node.elseStatement) walk(node.elseStatement)
          const elseWaited = node.elseStatement && leaves(node.elseStatement) ? afterCondition : waited
          waited = before || thenWaited || elseWaited
          return
        }
        if (isLoop(node) && waitsIn(node)) waited = true
        if (ts.isAwaitExpression(node)) {
          walk(node.expression)
          if (!isNotServer(node.expression)) waited = true
          return
        }
        // What a call or an assignment evaluates first, its callee and arguments or its value,
        // may wait: `setRows(await fetchRows())` writes after the wait.
        ts.forEachChild(node, walk)
        if (waited && isWrite(module, node, locals)) report(node)
        // A writer passed: to `.then` (`promise.then(setRows)`), or to anything after a wait.
        if (ts.isCallExpression(node)) {
          const thenCall = ts.isPropertyAccessExpression(node.expression)
            && ["then", "catch", "finally"].includes(node.expression.name.text) && !isNotServer(awaited(node))
          if (thenCall || waited) {
            for (const argument of node.arguments) {
              // A value a hook gave is a writer when it is called; passed on, `.then` calls it.
              const given = ts.isIdentifier(argument) && module.hookBound.has(argument.text) && !WRITE_NAME.test(argument.text)
              if ((ts.isIdentifier(argument) || ts.isPropertyAccessExpression(argument))
                && (thenCall || !given) && writesWhenCalled(module, argument, locals)) report(argument)
            }
          }
        }
      }
      walk(fn.body)
    }
    const visit = (node: ts.Node) => {
      if (isFunction(node)) inspect(node)
      ts.forEachChild(node, visit)
    }
    visit(file)
    if (findings.length > 0) found.set(module.path, findings)
  }
  return found
}

/** Every function in *source* that writes state after a wait outside the helper. */
export function answersByHand(source: string, fileName = "/src/source.tsx"): Finding[] {
  const path = fileName.startsWith("/") ? fileName : `/src/${fileName}`
  return answersByHandIn(new Map([[path, source]])).get(path) ?? []
}

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return SKIPPED_DIRECTORIES.has(entry.name) ? [] : sourceFiles(path)
    return /\.tsx?$/.test(entry.name) && !/\.(?:test|spec|d)\.tsx?$/.test(entry.name) ? [path] : []
  })
}

function repositoryFindings(): Map<string, Finding[]> {
  const sources = new Map<string, string>()
  for (const path of sourceFiles(SRC)) sources.set(path, readFileSync(path, "utf-8"))
  const notState = new Set(Object.keys(NOT_STATE).map((key) => {
    const [rel, name] = key.split("::")
    return `${resolve(SRC, "..", "..", rel)}#${name}`
  }))
  const found = new Map<string, Finding[]>()
  for (const [path, findings] of answersByHandIn(sources, notState)) {
    const rel = `frontend/src/${relative(SRC, path).split(sep).join("/")}`
    if (SHARED.has(rel)) continue
    for (const finding of findings) {
      const key = `${rel}::${finding.scope}`
      found.set(key, [...(found.get(key) ?? []), finding])
    }
  }
  return found
}

const scopes = (source: string, fileName?: string) => answersByHand(source, fileName).map((finding) => finding.scope)

describe("the scan finds a write after a wait", () => {
  it("finds a ticket checked by hand after an await", () => {
    expect(scopes(`
      const listAsked = latest()
      export const useList = create((set) => ({
        load: async (query) => {
          const ticket = listAsked.take()
          const page = await fetchQuotes(query)
          if (!listAsked.is(ticket)) return
          set({ quotes: page.quotes })
        },
      }))`, "list.ts")).toEqual(["useList.load"])
  })

  it("finds a cancelled flag kept by an effect", () => {
    expect(scopes(`
      export function Picker({ path }) {
        const [rows, setRows] = useState([])
        useEffect(() => {
          let cancelled = false
          fetchRows(path).then((data) => { if (!cancelled) setRows(data) })
          return () => { cancelled = true }
        }, [path])
        return null
      }`)).toEqual(["Picker"])
  })

  it("finds a finally that clears a flag, and a catch that says why", () => {
    expect(scopes(`
      export const useList = create((set, get) => ({
        more: async () => {
          set({ loadingMore: true })
          try {
            const page = await fetchQuotes(get().query)
            set({ quotes: page.quotes })
          } catch (error) {
            set({ error: String(error) })
          } finally {
            set({ loadingMore: false })
          }
        },
      }))`, "list.ts")).toEqual(["useList.more", "useList.more", "useList.more"])
  })

  it("finds a store's action, a prop callback, a hook's function and a same-file writer", () => {
    const findings = answersByHand(`
      import useGraphStore from "../stores/useGraphStore"
      export function Dialog({ onApplied }) {
        const addToast = useToastStore((s) => s.addToast)
        const [busy, setBusy] = useState(false)
        const reload = () => setBusy(false)
        const apply = async () => {
          const response = await applyRepair()
          useGraphStore.getState().markSaved()
          onApplied(response)
          addToast("success", "done")
          reload()
        }
        return null
      }`)
    expect(findings.map((finding) => finding.text)).toEqual([
      "useGraphStore.getState().markSaved()",
      "onApplied(response)",
      'addToast("success", "done")',
      "reload()",
    ])
  })

  it("finds a writer given to .then by name, and a write in a loop that waits", () => {
    expect(scopes(`
      export function Rows() {
        const [rows, setRows] = useState([])
        const load = () => { fetchRows().then(setRows) }
        const each = async (files) => {
          for (const file of files) {
            setRows([])
            await runOne(file)
          }
        }
        return null
      }`)).toEqual(["Rows.load", "Rows.each"])
  })

  it("finds a write in a callback made after a wait, and a writer passed to one", () => {
    expect(scopes(`
      export function Rows() {
        const [rows, setRows] = useState([])
        const load = async () => {
          const found = await fetchRows()
          found.forEach((row) => setRows((all) => [...all, row]))
          found.forEach(setRows)
        }
        return null
      }`)).toEqual(["Rows.load", "Rows.load"])
  })

  it("finds a write whose argument waits, a chained .then after a lazy import, and a store's action reached through a local", () => {
    expect(answersByHand(`
      import useUIStore from "../stores/useUIStore"
      import * as history from "../utils/history"
      export function Panel() {
        const [rows, setRows] = useState([])
        const read = async () => {
          setRows(await fetchRows())
        }
        const lazily = () => {
          import("../api").then((api) => api.fetchRows()).then(setRows)
        }
        const leave = async () => {
          const ui = useUIStore.getState()
          const { completeUtilityLeave } = useUIStore.getState()
          await saveDraft()
          ui.completeUtilityLeave()
          completeUtilityLeave()
        }
        const later = () => {
          const ui = useUIStore.getState()
          const complete = useUIStore.getState().completeUtilityLeave
          return saveDraft().then(() => {
            ui.completeUtilityLeave()
            complete()
          })
        }
        const record = () => request().then(useUIStore.getState().noteSwitch)
        return null
      }`).map((finding) => finding.text)).toEqual([
      "setRows(await fetchRows())",
      "setRows",
      "ui.completeUtilityLeave()",
      "completeUtilityLeave()",
      "ui.completeUtilityLeave()",
      "complete()",
      "useUIStore.getState().noteSwitch",
    ])
  })

  it("passes a write after a branch that waits and leaves, and finds one after a branch that waits and stays", () => {
    expect(scopes(`
      export const useRun = create((set) => ({
        each: async (result) => {
          if (result.moved) {
            await relist()
            return
          }
          set({ result })
        },
        stays: async (result) => {
          if (result.moved) await relist()
          set({ result })
        },
      }))`, "run.ts")).toEqual(["useRun.stays"])
  })

  it("finds a ref and a module variable written after a wait, and a request asked after one", () => {
    expect(scopes(`
      import { answer } from "../utils/answers"
      let newest = 0
      export function useLatest(ref) {
        const read = async () => {
          await fetchRows()
          ref.current = null
          newest += 1
          answer({ document: true }, fetchMore(), { ok: () => undefined })
        }
      }`, "latest.ts")).toEqual(["useLatest.read", "useLatest.read", "useLatest.read"])
  })
})

describe("the scan follows a writer across modules", () => {
  it("finds a writer imported from another module, under another name or re-exported, and passes a reader", () => {
    const found = answersByHandIn(new Map([
      ["/src/stores/graph.ts", `export default create(() => ({}))`],
      ["/src/utils/history.ts", `
        import useGraphStore from "../stores/graph"
        export function recordSwitch(entry) { useGraphStore.getState().pushVcEntry(entry) }
        export function label(entry) { return entry.name }`],
      ["/src/utils/index.ts", `export { recordSwitch as record } from "./history"`],
      ["/src/panel.tsx", `
        import { recordSwitch, label as nameOf } from "./utils/history"
        import * as history from "./utils/history"
        import { record } from "./utils"
        export async function switchBranch() {
          const entry = await gitSwitch()
          nameOf(entry)
          history.label(entry)
          recordSwitch(entry)
          record(entry)
          history.recordSwitch(entry)
        }`],
    ]))
    expect(found.get("/src/panel.tsx")?.map((finding) => finding.text)).toEqual([
      "recordSwitch(entry)",
      "record(entry)",
      "history.recordSwitch(entry)",
    ])
    expect(found.has("/src/utils/history.ts")).toBe(false)
  })
})

describe("the scan passes the helper's handlers", () => {
  it("passes writes made in the helper's handlers and pending", () => {
    expect(scopes(`
      const listAsked = requestKind()
      export const useList = create((set, get) => ({
        more: async () => {
          await answer({ under: listAsked }, fetchQuotes(get().query), {
            ok: (page) => set({ quotes: page.quotes }),
            failed: (error) => set({ error: String(error) }),
          }, { pending: (waiting) => set({ loadingMore: waiting }) })
        },
        load: async () => {
          const asked = ask({ newest: listAsked })
          await asked.answer(fetchQuotes(), { ok: (page) => set({ quotes: page.quotes }) })
          return "done"
        },
      }))`, "list.ts")).toEqual([])
  })

  it("passes a wait for no answer from the server, and a method of a local value", () => {
    expect(scopes(`
      export function Panel({ file }) {
        const [busy, setBusy] = useState(false)
        const remove = async () => {
          if (!(await confirmed({ title: "Delete?" }))) return
          setBusy(true)
        }
        const read = async () => {
          const seen = new Map()
          const rows = await answerless()
          seen.set(file, rows)
          return seen
        }
        return null
      }`)).toEqual([])
  })

  it("still finds a write after a handler's own wait", () => {
    expect(scopes(`
      export const useList = create((set) => ({
        save: () => answer({ document: true }, saveQuote(), {
          ok: async () => {
            await fetchQuotes()
            set({ saved: true })
          },
        }),
      }))`, "list.ts")).toEqual(["save.ok"])
  })
})

describe("the browser source", () => {
  it("lists as no state only functions that are there", () => {
    for (const key of Object.keys(NOT_STATE)) {
      const [rel, name] = key.split("::")
      const source = readFileSync(resolve(SRC, "..", "..", rel), "utf-8")
      expect(source, key).toMatch(new RegExp(`(?:function|const|let)\\s+${name}\\b`))
    }
  })

  it("writes no state after a wait outside the helper", () => {
    const findings = repositoryFindings()
    const unlisted = [...findings.entries()].map(([key, found]) => `${key} (line ${found[0].line}: ${found[0].text})`)
    expect(unlisted).toEqual([])
  })
})
