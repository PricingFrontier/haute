/**
 * haute-ui's stylesheets (specs/frontend-shared). tokens.css copies index.css's
 * values for extensions, so a drifted copy would make an extension look unlike the
 * editor; the kit's stylesheets may use only tokens that copy provides, since an
 * extension has no others; and they keep the layout the toolbar and palette rely on.
 */
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const read = (file: string) =>
  readFileSync(path.resolve(KIT, file), "utf8").replace(/\/\*[\s\S]*?\*\//g, "")

const INDEX_CSS = read("../index.css")
const TOKENS_CSS = read("tokens.css")
const STYLESHEETS = {
  "toolbar.css": read("toolbar.css"),
  "palette.css": read("palette.css"),
  "side-panel.css": read("side-panel.css"),
  "dropdowns.css": read("dropdowns.css"),
}

/** The body of the first rule whose selector list is exactly `selector`. */
function ruleBody(css: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s*")
  const match = new RegExp(`(?:^|[}\\s])${escaped}\\s*\\{([^}]*)\\}`).exec(css)
  if (!match) throw new Error(`No rule for ${selector}`)
  return match[1]
}

/** A rule body's declarations, by property, with whitespace in values collapsed. */
function declarations(body: string): Map<string, string> {
  const found = new Map<string, string>()
  for (const [, property, value] of body.matchAll(/([\w-]+)\s*:\s*([^;]+);/g)) {
    found.set(property, value.trim().replace(/\s+/g, " "))
  }
  return found
}

const source = declarations(ruleBody(INDEX_CSS, ":root"))
const copy = declarations(ruleBody(TOKENS_CSS, ":root, :host"))

describe("haute-ui tokens.css", () => {
  it("copies index.css's value for every token", () => {
    expect(copy.size).toBeGreaterThan(0)
    const drifted = [...copy]
      .filter(([name, value]) => source.get(name) !== value)
      .map(([name, value]) => `${name}: ${value} (index.css: ${source.get(name) ?? "not declared"})`)
    expect(drifted).toEqual([])
  })

  it("provides every token the kit's stylesheets use", () => {
    const css = [TOKENS_CSS, ...Object.values(STYLESHEETS)].join("\n")
    const used = new Set([...css.matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]))
    expect([...used].filter((name) => !copy.has(name))).toEqual([])
  })
})

describe("haute-ui's stylesheets", () => {
  it.each(Object.entries(STYLESHEETS))("%s takes colours only from tokens", (_file, css) => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i)
  })

  it.each([
    // 180px palette + 1px border - 16px bar padding: what follows starts over the palette's edge.
    ["toolbar.css", ".toolbar-brand", "width", "165px"],
    ["toolbar.css", ".toolbar-column", "flex-direction", "column"],
    ["toolbar.css", ".toolbar-action", "width", "100%"],
    ["toolbar.css", ".toolbar-save-commit", "width", "100%"],
    ["toolbar.css", ".toolbar-fill", "flex", "1 1 0%"],
    ["palette.css", ".palette", "width", "180px"],
    ["palette.css", ".palette-reveal", "width", "40px"],
    ["palette.css", ".palette-item-icon", "width", "24px"],
    ["side-panel.css", ".panel-drag-handle", "width", "4px"],
    ["side-panel.css", ".side-panel-header", "padding", "10px 12px"],
  ] as const)("%s: %s keeps %s: %s", (file, selector, property, value) => {
    expect(declarations(ruleBody(STYLESHEETS[file], selector)).get(property)).toBe(value)
  })
})
