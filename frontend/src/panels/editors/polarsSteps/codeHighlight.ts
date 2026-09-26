/**
 * Static Python highlighting for the generated-code panel, with the same
 * parser and colour tokens code mode uses, so the one-way switch to code does
 * not change how the code looks.
 */
import { pythonLanguage } from "@codemirror/lang-python"
import { highlightCode, tagHighlighter, tags } from "@lezer/highlight"

import { SYNTAX_COLORS } from "../../../theme/colors"

const TONES = {
  keyword: SYNTAX_COLORS.keyword,
  literal: SYNTAX_COLORS.literal,
  string: SYNTAX_COLORS.string,
  function: SYNTAX_COLORS.function,
  property: SYNTAX_COLORS.property,
  operator: SYNTAX_COLORS.operator,
  bracket: SYNTAX_COLORS.bracket,
  self: SYNTAX_COLORS.self,
  comment: "var(--text-muted)",
} as const

type Tone = keyof typeof TONES

const highlighter = tagHighlighter([
  { tag: [tags.keyword, tags.controlKeyword, tags.definitionKeyword, tags.operatorKeyword, tags.modifier], class: "keyword" },
  { tag: [tags.bool, tags.null, tags.number], class: "literal" },
  { tag: [tags.string, tags.special(tags.string)], class: "string" },
  { tag: tags.comment, class: "comment" },
  { tag: tags.function(tags.variableName), class: "function" },
  { tag: tags.propertyName, class: "property" },
  { tag: [tags.operator, tags.punctuation], class: "operator" },
  { tag: tags.bracket, class: "bracket" },
  { tag: tags.self, class: "self" },
])

export type CodeToken = { text: string; color: string | null }

/** The code split into lines of coloured tokens. */
export function highlightPython(code: string): CodeToken[][] {
  const lines: CodeToken[][] = [[]]
  const tree = pythonLanguage.parser.parse(code)
  highlightCode(
    code,
    tree,
    highlighter,
    (text, classes) => {
      const tone = classes.split(" ").find((c): c is Tone => c in TONES)
      lines[lines.length - 1].push({ text, color: tone ? TONES[tone] : null })
    },
    () => lines.push([]),
  )
  return lines
}
