/**
 * The kit's catalogue (specs/frontend-shared): its front page, a page to a level found by
 * the address, and on the controls' page every shape and state of the kit's buttons.
 */
import { readdirSync, readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { act, cleanup, render, screen, within } from "@testing-library/react"
import { Catalogue } from "../Catalogue"

function go(address: string) {
  act(() => {
    window.location.hash = address
    window.dispatchEvent(new HashChangeEvent("hashchange"))
  })
}

describe("the haute-ui catalogue", () => {
  beforeEach(() => {
    window.location.hash = ""
  })
  afterEach(cleanup)

  it("opens on the front page, which leads to each level", () => {
    render(<Catalogue />)

    expect(screen.getByRole("heading", { level: 1, name: "haute-ui" })).toBeInTheDocument()
    const bar = within(screen.getByRole("navigation", { name: "Catalogue" }))
    expect(bar.getByRole("link", { name: "Front page" })).toHaveAttribute("aria-current", "page")
    expect(bar.getByRole("link", { name: "Controls" })).not.toHaveAttribute("aria-current")
    expect(within(screen.getByRole("main")).getByRole("link", { name: "Controls" })).toHaveAttribute("href", "#/controls")
  })

  it("shows the page the address names, and the front page for an address naming none", () => {
    render(<Catalogue />)

    expect(document.activeElement).toBe(document.body)

    go("#/controls")
    expect(screen.getByRole("heading", { level: 2, name: "Button" })).toBeInTheDocument()
    // The page takes the focus, so the change is announced and read from the top.
    expect(document.activeElement).toBe(screen.getByRole("main", { name: "Controls" }))
    expect(within(screen.getByRole("navigation", { name: "Catalogue" })).getByRole("link", { name: "Controls" })).toHaveAttribute("aria-current", "page")

    go("#/nowhere")
    expect(screen.queryByRole("heading", { level: 2, name: "Button" })).toBeNull()
    expect(within(screen.getByRole("navigation", { name: "Catalogue" })).getByRole("link", { name: "Front page" })).toHaveAttribute("aria-current", "page")
  })

  it("draws the button in each shape, disabled, pressed and as a link, through the kit's own components", () => {
    window.location.hash = "#/controls"
    render(<Catalogue />)
    const button = within(screen.getByRole("region", { name: "Button" }))

    // Each shape, by the classes the kit's Button gives it.
    expect(button.getAllByRole("button", { name: "Retry" })[0].className).toBe("toolbar-btn btn")
    expect(button.getByRole("button", { name: "Refresh the list" }).className).toBe("toolbar-btn btn-icon")
    expect(button.getByRole("button", { name: "New quote" }).className).toBe("toolbar-fill toolbar-fill-save btn-fill")

    expect(button.getAllByRole("button").filter((sample) => (sample as HTMLButtonElement).disabled)).toHaveLength(3)
    expect(button.getAllByRole("button", { pressed: true })).toHaveLength(2)

    const links = button.getAllByRole("link")
    expect(links.map((link) => link.className)).toEqual(["toolbar-btn btn", "toolbar-fill toolbar-fill-save btn-fill"])
    for (const link of links) expect(link).toHaveAttribute("href")

    expect(button.getByRole("button", { name: "Save note" })).toHaveClass("ml-auto")
    expect(button.getByRole("button", { name: "Replace" })).toHaveClass("self-start")
  })

  it("draws the toolbar's button at rest, compact, disabled, pressed, as a link and laid out by its caller", () => {
    window.location.hash = "#/controls"
    render(<Catalogue />)
    const toolbar = within(screen.getByRole("region", { name: "Toolbar button" }))

    expect(toolbar.getAllByRole("button", { name: "Build" })).toHaveLength(3)
    expect(toolbar.getByRole("button", { name: "Zoom In" })).toHaveClass("toolbar-action-compact")
    expect(toolbar.getAllByRole("button").filter((sample) => (sample as HTMLButtonElement).disabled)).toHaveLength(1)
    expect(toolbar.getAllByRole("button", { pressed: true })).toHaveLength(1)
    expect(toolbar.getByRole("link", { name: "Quotes" }).className).toBe("toolbar-btn toolbar-action w-auto")
    expect(toolbar.getByRole("button", { name: "development" })).toHaveClass("toolbar-action", "justify-between")
    expect(toolbar.getByRole("button", { name: "Workbench" })).toHaveClass("toolbar-action-compact", "justify-start")
  })

  it("draws the toolbar's status as a label, with what is pressed inside it", () => {
    window.location.hash = "#/controls"
    render(<Catalogue />)
    const status = within(screen.getByRole("region", { name: "Toolbar status" }))

    const label = status.getByText("Checking Git…")
    expect(label.tagName).toBe("SPAN")
    expect(label.className).toBe("toolbar-btn toolbar-action")
    expect(status.getByRole("button", { name: "Retry" }).parentElement).toHaveClass("toolbar-action")
  })

  it("says under every sample what draws it", () => {
    window.location.hash = "#/controls"
    render(<Catalogue />)

    const samples = screen.getAllByRole("figure")
    expect(samples.length).toBeGreaterThan(10)
    for (const sample of samples) expect(sample.querySelector("figcaption")?.textContent).toMatch(/^<(Button|ButtonLink|ToolbarButton|ToolbarLink|ToolbarStatus)\b/)
  })
})

// What the catalogue draws against what the kit holds and the screens use.
describe("the catalogue's pieces", () => {
  const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..")
  const withoutComments = (code: string) => code.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "")

  function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const file = path.join(dir, entry.name)
      if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sources(file)
      return /\.tsx?$/.test(entry.name) ? [file] : []
    })
  }

  /** The names in a `{ a, type B, c as d }` list, as their module exports them. */
  const listed = (list: string) =>
    list
      .split(",")
      .map((name) => name.trim().replace(/^type\s+/, "").replace(/\s+as\s+.*$/, ""))
      .filter(Boolean)

  /** The names each of `files` imports from the kit's entry. */
  function kitImports(files: string[]): Set<string> {
    const names = new Set<string>()
    for (const file of files) {
      const code = withoutComments(readFileSync(file, "utf8"))
      for (const [, list] of code.matchAll(/import\s*\{([^}]*)\}\s*from\s*"[^"]*\/haute-ui"/g)) listed(list).forEach((name) => names.add(name))
    }
    return names
  }

  /**
   * The components and values the kit's entry exports. The entry re-exports by name
   * (`export { a, b } from "./file"`); an export in any other form is one this would
   * miss, so it is an error rather than a piece that is neither drawn nor listed.
   */
  function kitExports(): string[] {
    const entry = withoutComments(readFileSync(path.join(FRONTEND, "src/haute-ui/index.ts"), "utf8"))
    const named = /export\s*\{([^}]*)\}\s*from\s*"[^"]+"/g
    const typed = /export\s+type\s*\{[^}]*\}\s*from\s*"[^"]+"/g
    const unread = entry.replace(typed, "").replace(named, "")
    if (/\bexport\b/.test(unread)) throw new Error(`The kit's entry exports in a form this test does not read: ${unread.trim()}`)
    return [...entry.matchAll(named)].flatMap(([, list]) => listed(list))
  }

  const shown = kitImports(sources(path.join(FRONTEND, "catalogue")))

  // A catalogue that drew a piece no screen uses would keep it looking alive.
  it("are each imported by a product file outside the kit", () => {
    const product = sources(path.join(FRONTEND, "src")).filter((file) => !file.includes(`${path.sep}haute-ui${path.sep}`))
    const used = kitImports(product)

    expect(shown.size).toBeGreaterThan(0)
    expect([...shown].filter((name) => !used.has(name))).toEqual([])
  })

  // The catalogue is not complete, and says so on its front page. Each piece of the kit is
  // drawn or is on this list, so a new piece is one or the other by a decision.
  const NOT_YET_DRAWN = [
    "PaletteColumn",
    "PaletteHeader",
    "PaletteItem",
    "PaletteItems",
    "PaletteRevealStrip",
    "SaveCommit",
    "SidePanel",
    "SidePanelClose",
    "SidePanelHeader",
    "ToolbarBrand",
    "UndoRedo",
    "ZoomInOut",
    "halfWindowWidth",
  ]

  it("are every piece of the kit but those listed as not yet drawn", () => {
    const exported = kitExports()

    expect(exported).toEqual(expect.arrayContaining(["Button", "SidePanel", "ToolbarButton"]))
    expect(exported.filter((name) => !shown.has(name)).sort()).toEqual([...NOT_YET_DRAWN].sort())
  })
})
