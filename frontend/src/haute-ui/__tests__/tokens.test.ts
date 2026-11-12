/**
 * haute-ui's stylesheets (specs/frontend-shared): plain CSS the pipeline editor and the
 * workbench's view both render. They may use only the tokens index.css declares and no
 * colour literal, and they keep the layout the toolbar, palette and side panel rely on.
 */
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it } from "vitest"

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const read = (file: string) =>
  readFileSync(path.resolve(KIT, file), "utf8").replace(/\/\*[\s\S]*?\*\//g, "")

const INDEX_CSS = read("../index.css")
const STYLESHEETS = {
  "toolbar.css": read("toolbar.css"),
  "button.css": read("button.css"),
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

const tokens = declarations(ruleBody(INDEX_CSS, ":root"))

/** The surface's keyboard-focus rule: at rest, and again for a focused button under the pointer. */
const FOCUS = '.toolbar-btn:focus-visible, .toolbar-btn:focus-visible:hover:not(:disabled):not([aria-disabled="true"])'

describe("haute-ui's stylesheets", () => {
  it("use only tokens index.css declares", () => {
    expect(tokens.size).toBeGreaterThan(0)
    const css = Object.values(STYLESHEETS).join("\n")
    const used = new Set([...css.matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]))
    expect([...used].filter((name) => !tokens.has(name))).toEqual([])
  })

  it.each(Object.entries(STYLESHEETS))("%s takes colours only from tokens", (_file, css) => {
    expect(css).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i)
  })

  it.each([
    // 180px palette + 1px border - 16px bar padding: what follows starts over the palette's edge.
    ["toolbar.css", ".toolbar-brand", "width", "165px"],
    ["toolbar.css", ".toolbar-column", "flex-direction", "column"],
    ["toolbar.css", ".toolbar-action", "width", "100%"],
    // Keyboard focus as the app's text fields draw it. The outline is unseen unless a
    // forced-colours mode, which drops the ring, colours it.
    ["toolbar.css", FOCUS, "outline", "2px solid transparent"],
    ["toolbar.css", FOCUS, "border-color", "var(--accent-ring)"],
    ["toolbar.css", FOCUS, "box-shadow", "0 0 0 2px var(--accent-soft)"],
    // The size the toolbar's buttons, links and statuses share.
    ["toolbar.css", ".toolbar-action", "font-size", "12px"],
    ["toolbar.css", ".toolbar-action", "padding", "4px 10px"],
    ["toolbar.css", ".toolbar-action", "border-radius", "6px"],
    // A label too long for its button is cut short, not an icon beside it.
    ["toolbar.css", ".toolbar-action svg", "flex-shrink", "0"],
    ["toolbar.css", ".toolbar-save-commit", "width", "100%"],
    ["toolbar.css", ".toolbar-fill", "flex", "1 1 0%"],
    // Disabled, a button fades by a third and no further, so an icon alone stays legible.
    ["button.css", ".btn:disabled, .btn-icon:disabled", "opacity", "0.67"],
    // Outside the toolbar the fill is as wide as its label.
    ["button.css", ".btn-fill", "flex", "none"],
    // Its label stays centred where a caller's layout stretches it.
    ["button.css", ".btn-fill", "justify-content", "center"],
    ["palette.css", ".palette", "width", "180px"],
    ["palette.css", ".palette-reveal", "width", "40px"],
    ["palette.css", ".palette-item-icon", "width", "24px"],
    ["side-panel.css", ".panel-drag-handle", "width", "4px"],
    ["side-panel.css", ".side-panel-header", "padding", "10px 12px"],
    // Every side panel's Close: a white X on solid red.
    ["side-panel.css", ".side-panel-close", "background", "var(--danger-solid)"],
    ["side-panel.css", ".side-panel-close", "color", "var(--text-on-accent)"],
  ] as const)("%s: %s keeps %s: %s", (file, selector, property, value) => {
    expect(declarations(ruleBody(STYLESHEETS[file], selector)).get(property)).toBe(value)
  })

  it("draws a kit button's keyboard focus as index.css's focus-ring draws a text field's", () => {
    const field = declarations(ruleBody(INDEX_CSS, ".focus-ring:focus-visible"))
    const button = declarations(ruleBody(STYLESHEETS["toolbar.css"], FOCUS))
    // The helper names an override first and falls back to the token the kit names.
    expect(field.get("border-color")).toBe(`var(--focus-ring-border, ${button.get("border-color")})`)
    expect(field.get("box-shadow")).toBe("0 0 0 2px var(--focus-ring-shadow, var(--accent-soft))")
    expect(button.get("box-shadow")).toBe("0 0 0 2px var(--accent-soft)")
  })

  // Equal in weight to the unavailable look, the focus rule wins by coming after it, so a
  // button that is unavailable but still focusable shows where the keyboard is.
  it("keeps the focus rule after the surface's other rules", () => {
    const css = STYLESHEETS["toolbar.css"]
    const focus = css.indexOf(".toolbar-btn:focus-visible,")
    const unavailable = css.indexOf('.toolbar-btn[aria-disabled="true"] {')
    const pressed = css.indexOf('.toolbar-btn[aria-pressed="true"]')
    // Each rule is found, or the comparison below would hold for a rule that is not there.
    expect(Math.min(focus, unavailable, pressed)).toBeGreaterThanOrEqual(0)
    expect(focus).toBeGreaterThan(unavailable)
    expect(focus).toBeGreaterThan(pressed)
  })
})
