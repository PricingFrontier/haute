/**
 * Global constants: named, typed values every code box and step reads as
 * `global_constants.<name>`, one value per source where a constant is split.
 *
 * The graph store holds them as drafts — each value as the text the analyst
 * typed — so the Constants pane can show an invalid or missing value without
 * losing it. A split value is missing when its source has no key, or, for a
 * type other than text, when it is empty; an empty text value is a value. `constantsPayload` turns the valid drafts into the shape the
 * backend's `GlobalConstant` model accepts; save refuses drafts that do not
 * all validate.
 */

export const GLOBAL_CONSTANT_TYPES = ["integer", "float", "text", "boolean", "date"] as const
export type GlobalConstantType = (typeof GLOBAL_CONSTANT_TYPES)[number]
export type GlobalConstantValue = number | string | boolean

/** One constant as the backend stores it: exactly one of `value` and `by_source`. */
export interface GlobalConstant {
  name: string
  type: GlobalConstantType
  value?: GlobalConstantValue | null
  by_source?: Record<string, GlobalConstantValue> | null
}

/** One constant as the pane edits it. An empty split value is missing, not invalid. */
export interface GlobalConstantDraft {
  name: string
  type: GlobalConstantType
  split: boolean
  /** The uniform value, as typed. */
  value: string
  /** Each source's value, as typed; a source without a key is missing. */
  bySource: Record<string, string>
}

export interface GlobalConstantIssues {
  name?: string
  value?: string
  /** Per-source problems: an invalid value's reason, or "missing". */
  bySource: Record<string, string>
}

export const MISSING_VALUE = "missing"

const NAME_PATTERN = /^[A-Za-z][A-Za-z0-9_]*$/
/** Python's hard keywords, which no name node code binds can be. */
export const PYTHON_KEYWORDS: ReadonlySet<string> = new Set([
  "False", "None", "True", "and", "as", "assert", "async", "await", "break", "class",
  "continue", "def", "del", "elif", "else", "except", "finally", "for", "from", "global",
  "if", "import", "in", "is", "lambda", "nonlocal", "not", "or", "pass", "raise", "return",
  "try", "while", "with", "yield",
])
const INTEGER_PATTERN = /^[+-]?\d+$/
const FLOAT_PATTERN = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/
const DATE_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

function isRealDate(text: string): boolean {
  const match = DATE_PATTERN.exec(text)
  if (!match) return false
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])]
  const date = new Date(Date.UTC(year, month - 1, day))
  return (
    date.getUTCFullYear() === year && date.getUTCMonth() === month - 1 && date.getUTCDate() === day
  )
}

/** Why *text* is not a value of *type*, or null when it is. */
export function valueProblem(type: GlobalConstantType, text: string): string | null {
  switch (type) {
    case "integer":
      if (!INTEGER_PATTERN.test(text.trim())) return "Enter a whole number."
      // The backend refuses what a JavaScript number cannot hold exactly.
      return Number.isSafeInteger(Number(text.trim()))
        ? null
        : `Enter a whole number between -${Number.MAX_SAFE_INTEGER} and ${Number.MAX_SAFE_INTEGER}.`
    case "float":
      return FLOAT_PATTERN.test(text.trim()) && Number.isFinite(Number(text)) ? null : "Enter a number."
    case "boolean":
      return text === "true" || text === "false" ? null : "Choose true or false."
    case "date":
      return isRealDate(text) ? null : "Enter a real date as YYYY-MM-DD."
    case "text":
      return null
  }
}

/** Why *name* cannot name a constant, or null when it can. */
export function nameProblem(name: string): string | null {
  if (!name) return "Enter a name."
  if (!NAME_PATTERN.test(name)) {
    return "Start with a letter; use letters, digits and underscores."
  }
  if (PYTHON_KEYWORDS.has(name)) return `${name} is a Python keyword.`
  return null
}

function parsedValue(type: GlobalConstantType, text: string): GlobalConstantValue {
  switch (type) {
    case "integer":
      return Number.parseInt(text.trim(), 10)
    case "float":
      return Number(text.trim())
    case "boolean":
      return text === "true"
    default:
      return text
  }
}

function draftText(value: GlobalConstantValue | null | undefined): string {
  if (value === null || value === undefined) return ""
  return String(value)
}

/** The drafts the pane edits for constants a document loaded. */
export function constantDrafts(constants: readonly GlobalConstant[]): GlobalConstantDraft[] {
  return constants.map((constant) => {
    const split = constant.by_source !== null && constant.by_source !== undefined
    return {
      name: constant.name,
      type: constant.type,
      split,
      value: split ? "" : draftText(constant.value),
      bySource: split
        ? Object.fromEntries(
            Object.entries(constant.by_source ?? {}).map(([source, value]) => [source, draftText(value)]),
          )
        : {},
    }
  })
}

/** Whether *draft* holds a value for *source*: its key, and for a type other than text, text. */
export function holdsSourceValue(draft: GlobalConstantDraft, source: string): boolean {
  const text = draft.bySource[source]
  return text !== undefined && (text !== "" || draft.type === "text")
}

/** Each draft's problems, by index, keyed on the pipeline's *sources* for split values. */
export function constantIssues(
  drafts: readonly GlobalConstantDraft[],
  sources: readonly string[],
): GlobalConstantIssues[] {
  const counts = new Map<string, number>()
  for (const draft of drafts) counts.set(draft.name, (counts.get(draft.name) ?? 0) + 1)
  return drafts.map((draft) => {
    const issues: GlobalConstantIssues = { bySource: {} }
    const name = nameProblem(draft.name)
    if (name) issues.name = name
    else if ((counts.get(draft.name) ?? 0) > 1) issues.name = `${draft.name} is defined more than once.`
    if (!draft.split) {
      if (draft.value === "" && draft.type !== "text") issues.value = "Enter a value."
      else {
        const problem = valueProblem(draft.type, draft.value)
        if (problem) issues.value = problem
      }
      return issues
    }
    const keys = new Set([...sources, ...Object.keys(draft.bySource)])
    for (const source of keys) {
      if (!holdsSourceValue(draft, source)) {
        if (sources.includes(source)) issues.bySource[source] = MISSING_VALUE
        continue
      }
      const problem = valueProblem(draft.type, draft.bySource[source])
      if (problem) issues.bySource[source] = problem
    }
    return issues
  })
}

/** Whether *issues* refuses a save: an invalid name or value (a missing value does not). */
export function issuesRefuseSave(issues: GlobalConstantIssues): boolean {
  return (
    issues.name !== undefined
    || issues.value !== undefined
    || Object.values(issues.bySource).some((problem) => problem !== MISSING_VALUE)
  )
}

/** The first problem that refuses a save, named for a toast, or null. */
export function saveRefusal(
  drafts: readonly GlobalConstantDraft[],
  sources: readonly string[],
): string | null {
  const issues = constantIssues(drafts, sources)
  for (let index = 0; index < drafts.length; index += 1) {
    const issue = issues[index]
    if (!issuesRefuseSave(issue)) continue
    const label = drafts[index].name || `constant ${index + 1}`
    const problem = issue.name
      ?? issue.value
      ?? Object.entries(issue.bySource)
        .filter(([, reason]) => reason !== MISSING_VALUE)
        .map(([source, reason]) => `${source}: ${reason}`)[0]
    return `global constant ${label} is invalid - ${problem}`
  }
  return null
}

/**
 * The constants the backend accepts: every draft whose name and values are
 * valid, with each split constant's missing sources left out. Execution
 * requests carry these while the pane holds an invalid draft, so the nodes
 * that read it fail as reading an undefined constant.
 */
export function constantsPayload(drafts: readonly GlobalConstantDraft[]): GlobalConstant[] {
  const issues = constantIssues(drafts, [])
  const payload: GlobalConstant[] = []
  drafts.forEach((draft, index) => {
    if (issuesRefuseSave(issues[index])) return
    if (!draft.split) {
      payload.push({ name: draft.name, type: draft.type, value: parsedValue(draft.type, draft.value) })
      return
    }
    payload.push({
      name: draft.name,
      type: draft.type,
      by_source: Object.fromEntries(
        Object.entries(draft.bySource)
          .filter(([source]) => holdsSourceValue(draft, source))
          .map(([source, text]) => [source, parsedValue(draft.type, text)]),
      ),
    })
  })
  return payload
}

/** The split constants that hold a value for *source*. */
export function constantsWithSourceValue(
  drafts: readonly GlobalConstantDraft[],
  source: string,
): string[] {
  return drafts
    .filter((draft) => draft.split && holdsSourceValue(draft, source))
    .map((draft) => draft.name)
}

/** *drafts* without any split value for *source*. */
export function withoutSource(drafts: readonly GlobalConstantDraft[], source: string): GlobalConstantDraft[] {
  return drafts.map((draft) => {
    if (!draft.split || !(source in draft.bySource)) return draft
    const bySource = { ...draft.bySource }
    delete bySource[source]
    return { ...draft, bySource }
  })
}
