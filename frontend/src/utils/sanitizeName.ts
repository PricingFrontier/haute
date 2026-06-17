/**
 * Convert a human label to a valid Python function name (preserves casing).
 *
 * This MUST stay in sync with the backend implementation:
 *   src/haute/_graph_utils.py → _sanitize_func_name()
 *
 * Both implementations follow the same rules:
 *   1. Trim whitespace.
 *   2. Replace spaces and hyphens with underscores.
 *   3. ASCII alnum / ``_`` survive; ASCII punctuation / control chars are
 *      stripped.
 *   4. Non-ASCII characters are reversibly encoded as ``_x<hex>_`` so
 *      distinct labels (e.g. ``café`` vs ``caf``) produce distinct
 *      identifiers.
 *   5. Prefix with "node_" if the result starts with a digit.
 *   6. Prefix with "node_" if the result is a Python keyword (e.g.
 *      ``class`` → ``node_class``) so the identifier stays valid Python.
 *   7. Fall back to "unnamed_node" if the result is empty.
 */

/**
 * Python's hard keywords — must match ``keyword.kwlist`` in the backend's
 * Python runtime (requires-python >= 3.11; this list is stable across
 * CPython 3.11–3.14).  The backend uses ``keyword.iskeyword(name)``, which
 * tests ONLY hard keywords — soft keywords (``match``, ``case``, ``type``,
 * ``_``) are NOT in this list and must NOT be added, or parity would break.
 * Source of truth: src/haute/_graph_utils.py → _sanitize_func_name() via
 * the stdlib ``keyword`` module.
 */
const PYTHON_KEYWORDS = new Set<string>([
  "False", "None", "True", "and", "as", "assert", "async", "await",
  "break", "class", "continue", "def", "del", "elif", "else", "except",
  "finally", "for", "from", "global", "if", "import", "in", "is",
  "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
  "while", "with", "yield",
])

/**
 * Leading/trailing run of characters where Python ``str.isspace()`` is true —
 * the exact set stripped by the backend's ``label.strip()``.  This is NOT the
 * same set as JS ``String.prototype.trim()``:
 *   - trim() strips U+FEFF (BOM/ZWNBSP); Python.strip() does NOT.
 *   - Python.strip() strips U+001C–U+001F (file/group/record/unit sep) and
 *     U+0085 (NEL); trim() does NOT.
 * Using trim() here diverged at the string edges (e.g. "﻿x﻿" →
 * "x" under trim, but "_xfeff_x_xfeff_" under the backend).  The class below
 * is enumerated directly from CPython's ``str.isspace()`` codepoints.
 */
const PY_WS =
  "\\t\\n\\x0b\\f\\r\\x1c\\x1d\\x1e\\x1f \\x85\\xa0" +
  "\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000"
const PY_STRIP_RE = new RegExp(`^[${PY_WS}]+|[${PY_WS}]+$`, "g")

export function sanitizeName(label: string): string {
  // Mirror the backend EXACTLY: strip Python-whitespace from the ends (see
  // PY_STRIP_RE), then replace only the literal space (U+0020) and hyphen
  // (U+002D) with underscores — NOT the broader regex ``\s`` class.  JS ``\s``
  // also matches tab/newline/CR/VT/FF and Unicode whitespace (NBSP U+00A0,
  // em-space U+2003, …); using it here diverged from Python, which drops ASCII
  // control whitespace and hex-encodes non-ASCII whitespace in the per-char
  // loop below.  Interior whitespace must fall through to the loop verbatim.
  let name = label.replace(PY_STRIP_RE, "").replace(/ /g, "_").replace(/-/g, "_")
  const encoded: string[] = []
  for (const c of name) {
    const code = c.codePointAt(0) ?? 0
    if (code < 128) {
      // ASCII: keep alnum/underscore, drop everything else (punctuation,
      // control chars).  Matches the backend's ``c.isascii() and
      // (c.isalnum() or c == "_")`` filter.
      if (/^[a-zA-Z0-9_]$/.test(c)) {
        encoded.push(c)
      }
    } else {
      // Non-ASCII: reversibly encode as ``_x<hex>_``.
      encoded.push(`_x${code.toString(16)}_`)
    }
  }
  name = encoded.join("")
  if (name && /^[0-9]/.test(name)) name = `node_${name}`
  // Mirror the backend's ``if keyword.iskeyword(name): name = f"node_{name}"``.
  // ``node_<keyword>`` is itself never a keyword, so this stays idempotent.
  if (PYTHON_KEYWORDS.has(name)) name = `node_${name}`
  return name || "unnamed_node"
}
