/**
 * Where haute-ui's stylesheets sit in the cascade (specs/frontend-shared). index.css
 * imports the toolbar's, the palette's and the side panel's into Tailwind's components
 * layer, which the utilities layer outranks whatever a selector's specificity, so a layout
 * utility a caller writes beside a kit class takes effect. The native dropdowns' colours
 * are for every select, so they stay unlayered and win over utility classes. Pinned on
 * the compiled stylesheet, which is what the browser is given.
 */
import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { beforeAll, describe, expect, it } from "vitest"
import { compileIndexCss } from "../../test-utils/compiledIndexCss"

const KIT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const withoutComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, "")

interface Block {
  /** What precedes the block's brace: a selector list, or an at-rule such as `@layer components`. */
  prelude: string
  body: string
}

/**
 * A stylesheet's top-level blocks and its top-level statements, such as the layer order.
 * A brace inside a quoted string is text, and unbalanced braces are an error.
 */
function topLevel(css: string): { blocks: Block[]; statements: string[] } {
  const blocks: Block[] = []
  const statements: string[] = []
  let depth = 0
  let start = 0
  let open = 0
  let quote: string | null = null
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i]
    if (quote !== null) {
      if (ch === "\\") i += 1
      else if (ch === quote) quote = null
    } else if (ch === '"' || ch === "'") {
      quote = ch
    } else if (ch === "{") {
      if (depth === 0) open = i
      depth += 1
    } else if (ch === "}") {
      depth -= 1
      if (depth < 0) throw new Error(`Unbalanced "}" at offset ${i}`)
      if (depth === 0) {
        blocks.push({ prelude: css.slice(start, open).trim().replace(/\s+/g, " "), body: css.slice(open + 1, i) })
        start = i + 1
      }
    } else if (ch === ";" && depth === 0) {
      statements.push(css.slice(start, i).trim().replace(/\s+/g, " "))
      start = i + 1
    }
  }
  if (depth !== 0 || quote !== null) throw new Error("Unbalanced braces or quotes in the stylesheet")
  return { blocks, statements }
}

/** The kit's stylesheets of component classes: every one but the native dropdowns'. */
const COMPONENT_STYLESHEETS = readdirSync(KIT).filter((file) => file.endsWith(".css") && file !== "dropdowns.css")

/** The kit's own classes a stylesheet styles: those its rules' selectors name. */
function kitClasses(file: string): string[] {
  const names = new Set<string>()
  for (const block of topLevel(withoutComments(readFileSync(path.resolve(KIT, file), "utf8"))).blocks) {
    if (block.prelude.startsWith("@")) continue
    for (const [, name] of block.prelude.matchAll(/\.((?:toolbar|btn|palette|side-panel|panel-drag-handle)[\w-]*)/g)) names.add(name)
  }
  return [...names]
}

let compiled: { blocks: Block[]; statements: string[] }

/** Where a rule naming the class `name` is emitted: its layers, and "unlayered" for a top-level rule. */
function layersStyling(name: string): string[] {
  const named = new RegExp(`\\.${name}(?![\\w-])`)
  const found = new Set<string>()
  for (const block of compiled.blocks) {
    const layer = /^@layer ([\w-]+)$/.exec(block.prelude)
    if (layer) {
      // Within a layer, the rules' selectors alone, not their declarations.
      if (topLevel(block.body).blocks.some((rule) => named.test(rule.prelude))) found.add(layer[1])
    } else if (!block.prelude.startsWith("@") && named.test(block.prelude)) {
      found.add("unlayered")
    }
  }
  return [...found]
}

beforeAll(async () => {
  compiled = topLevel(withoutComments(await compileIndexCss(["flex-none", "w-auto"])))
})

describe("haute-ui's stylesheets in the cascade", () => {
  it("the utilities layer outranks the components layer", () => {
    const order = compiled.statements.find((statement) => statement.startsWith("@layer "))
    expect(order).toBeDefined()
    const layers = order!.replace("@layer ", "").split(",").map((name) => name.trim())
    expect(layers.indexOf("components")).toBeGreaterThanOrEqual(0)
    expect(layers.indexOf("utilities")).toBeGreaterThan(layers.indexOf("components"))
    expect(layersStyling("flex-none")).toEqual(["utilities"])
    expect(layersStyling("w-auto")).toEqual(["utilities"])
  })

  it("the kit has component stylesheets beside the dropdowns'", () => {
    expect(COMPONENT_STYLESHEETS).toEqual(expect.arrayContaining(["toolbar.css", "button.css", "palette.css", "side-panel.css"]))
    expect(readdirSync(KIT)).toContain("dropdowns.css")
  })

  // A stylesheet imported unlayered, or not imported at all, fails here for each of its classes.
  it.each(COMPONENT_STYLESHEETS)("every class %s styles is emitted in the components layer alone", (file) => {
    const classes = kitClasses(file)
    expect(classes.length).toBeGreaterThan(0)
    expect(classes.filter((name) => layersStyling(name).join() !== "components")).toEqual([])
  })

  // Tailwind's own reset names both elements in the base layer; the kit's rule is the top-level one.
  it.each([
    ["select", "--bg-input"],
    ["option", "--bg-elevated"],
  ])("the native dropdowns' %s rule stays unlayered", (selector, surface) => {
    const rule = compiled.blocks.find((block) => block.prelude === selector)
    expect(rule?.body).toContain(`var(${surface})`)
  })
})
