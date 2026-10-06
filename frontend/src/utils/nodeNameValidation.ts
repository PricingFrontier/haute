/**
 * The client half of a node rename, shared by the Rename dialog and the node
 * panel's header: the label's shape. Whether the name collides with another
 * node, a reserved name or a helper is the server's answer (the editor
 * identity request with the document's naming context).
 */

/** Maximum allowed length for a rename. Longer names break the breadcrumb
 *  bar, context menu, and code generation downstream. */
export const MAX_NODE_NAME_LENGTH = 200

/** Unsafe characters. These would corrupt the generated Python code, break
 *  markdown rendering, or produce invisible (control) glyphs:
 *    - `\u0000-\u001f` — all C0 control characters (includes \n, \t, \r, \0)
 *    - `\u007f`        — DEL control char
 *    - `` ` ``         — breaks markdown code spans and our template strings
 *
 *  Unicode letters, digits, punctuation, spaces, dashes, etc. are allowed
 *  freely — sanitisation for code-gen happens in a separate backend identity
 *  step (not here). */
// eslint-disable-next-line no-control-regex -- deliberately matching control chars
const UNSAFE_CHAR_REGEX = /[\u0000-\u001f\u007f`]/

/** Why a typed node name cannot be used, or null when its shape is valid. */
export function nodeNameIssue(raw: string): string | null {
  const trimmed = raw.trim()
  if (trimmed.length === 0) return "A node needs a name."
  if (trimmed.length > MAX_NODE_NAME_LENGTH) {
    return `A node name can be at most ${MAX_NODE_NAME_LENGTH} characters.`
  }
  if (UNSAFE_CHAR_REGEX.test(trimmed)) {
    return "A node name cannot contain control characters, line breaks or backticks."
  }
  return null
}

/** The trimmed name when its shape is valid; otherwise null. */
export function validateNodeName(raw: string): string | null {
  return nodeNameIssue(raw) === null ? raw.trim() : null
}
