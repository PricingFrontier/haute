import { describe, it, expect } from "vitest"
import { sanitizeName } from "../sanitizeName"

describe("sanitizeName", () => {
  it("converts spaces to underscores", () => {
    expect(sanitizeName("my node")).toBe("my_node")
  })

  it("converts hyphens to underscores", () => {
    expect(sanitizeName("my-node")).toBe("my_node")
  })

  it("converts mixed spaces and hyphens", () => {
    expect(sanitizeName("my node-name here")).toBe("my_node_name_here")
  })

  it("strips non-alphanumeric/underscore characters", () => {
    expect(sanitizeName("hello@world!")).toBe("helloworld")
    expect(sanitizeName("rate(%)")).toBe("rate")
    expect(sanitizeName("col#1&2")).toBe("col12")
  })

  it("trims leading and trailing whitespace", () => {
    expect(sanitizeName("  padded  ")).toBe("padded")
  })

  it("prefixes with node_ if name starts with a digit", () => {
    expect(sanitizeName("123abc")).toBe("node_123abc")
    expect(sanitizeName("0_start")).toBe("node_0_start")
  })

  it("does not prefix if name starts with a letter", () => {
    expect(sanitizeName("abc123")).toBe("abc123")
  })

  it("does not prefix if name starts with underscore", () => {
    expect(sanitizeName("_private")).toBe("_private")
  })

  it("returns unnamed_node for empty string", () => {
    expect(sanitizeName("")).toBe("unnamed_node")
  })

  it("returns unnamed_node for whitespace-only input", () => {
    expect(sanitizeName("   ")).toBe("unnamed_node")
  })

  it("returns unnamed_node when all characters are stripped", () => {
    expect(sanitizeName("@#$%")).toBe("unnamed_node")
  })

  it("preserves casing", () => {
    expect(sanitizeName("MyNode")).toBe("MyNode")
    expect(sanitizeName("UPPER")).toBe("UPPER")
  })

  it("handles underscores in input (preserved)", () => {
    expect(sanitizeName("already_valid")).toBe("already_valid")
  })

  it("handles digit after stripping leading special chars", () => {
    expect(sanitizeName("!1foo")).toBe("node_1foo")
  })

  // Non-ASCII encoding parity with backend _sanitize_func_name (#123)
  // Backend reference:
  //   _sanitize_func_name("café")    == "caf_xe9_"
  //   _sanitize_func_name("caf")     == "caf"       (so they must differ)
  //   _sanitize_func_name("用户1")   startswith "_x"  — valid identifier
  //   _sanitize_func_name is idempotent on ASCII-only outputs.
  it("reversibly encodes non-ASCII as _x<hex>_", () => {
    expect(sanitizeName("café")).toBe("caf_xe9_")
  })

  it("distinguishes non-ASCII collisions that the old stripping would merge", () => {
    expect(sanitizeName("café")).not.toBe(sanitizeName("caf"))
    expect(sanitizeName("café")).not.toBe(sanitizeName("cafó"))
  })

  it("handles CJK codepoints as valid identifiers", () => {
    const out = sanitizeName("用户1")
    // Must not contain the raw non-ASCII chars
    expect(out).not.toContain("用")
    // Must be a valid Python identifier shape (letters/digits/underscores only,
    // does not start with a digit).  JS has no isidentifier() so approximate:
    expect(out).toMatch(/^[A-Za-z_][A-Za-z0-9_]*$/)
  })

  it("handles astral-plane codepoints (emoji) without crashing", () => {
    // U+1F600 "😀" — surrogate pair in UTF-16, must be iterated as one codepoint.
    const out = sanitizeName("a😀b")
    expect(out).toMatch(/^a_x1f600_b$/)
  })

  it("is idempotent on ASCII-only outputs (re-sanitising a sanitised name is a no-op)", () => {
    const once = sanitizeName("my node")
    expect(sanitizeName(once)).toBe(once)
  })

  // Python-keyword node_-prefix parity with backend _sanitize_func_name (#class).
  // Backend reference (src/haute/_graph_utils.py → _sanitize_func_name):
  //   if keyword.iskeyword(name): name = f"node_{name}"
  // Without this rule the OUTPUT editor would persist source_port="class"
  // while the backend keys the frame "node_class" → mismatch.
  describe("Python-keyword node_ prefix (backend parity)", () => {
    // Mirror of Python 3.11–3.14 keyword.kwlist (hard keywords only).  Soft
    // keywords (match/case/type/_) are intentionally absent: keyword.iskeyword
    // returns False for them, so the backend does NOT prefix them either.
    const PYTHON_KEYWORDS = [
      "False", "None", "True", "and", "as", "assert", "async", "await",
      "break", "class", "continue", "def", "del", "elif", "else", "except",
      "finally", "for", "from", "global", "if", "import", "in", "is",
      "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
      "while", "with", "yield",
    ]

    it("prefixes a representative sample of keyword labels with node_", () => {
      expect(sanitizeName("class")).toBe("node_class")
      expect(sanitizeName("def")).toBe("node_def")
      expect(sanitizeName("return")).toBe("node_return")
      expect(sanitizeName("import")).toBe("node_import")
      expect(sanitizeName("lambda")).toBe("node_lambda")
      expect(sanitizeName("async")).toBe("node_async")
      expect(sanitizeName("await")).toBe("node_await")
      expect(sanitizeName("None")).toBe("node_None")
    })

    it("prefixes every Python keyword in keyword.kwlist", () => {
      for (const kw of PYTHON_KEYWORDS) {
        expect(sanitizeName(kw)).toBe(`node_${kw}`)
      }
    })

    it("does NOT prefix soft keywords (match/case/type/_) — backend uses iskeyword", () => {
      expect(sanitizeName("match")).toBe("match")
      expect(sanitizeName("case")).toBe("case")
      expect(sanitizeName("type")).toBe("type")
      expect(sanitizeName("_")).toBe("_")
    })

    it("applies the keyword rule only to the post-strip result", () => {
      // "class!" strips to "class" → keyword → node_class
      expect(sanitizeName("class!")).toBe("node_class")
      // " return " trims to "return" → keyword → node_return
      expect(sanitizeName("  return  ")).toBe("node_return")
      // "class node" → "class_node" is NOT a keyword → unchanged
      expect(sanitizeName("class node")).toBe("class_node")
      // "myclass" is not a keyword → unchanged
      expect(sanitizeName("myclass")).toBe("myclass")
    })
  })

  // sanitize(sanitize(x)) == sanitize(x) across every category, including
  // keywords: node_<keyword> is itself never a keyword, so the second pass
  // is a no-op (matches the backend's idempotency invariant).
  it("is idempotent across keyword / space / hyphen / non-ASCII inputs", () => {
    const inputs = [
      "class", "def", "return", "async", "None",
      "my node", "my-node", "my node-name here",
      "café", "用户1", "a😀b",
      "123abc", "!1foo", "rate(%)", "@#$%", "   ",
    ]
    for (const label of inputs) {
      const once = sanitizeName(label)
      expect(sanitizeName(once)).toBe(once)
    }
  })
})
