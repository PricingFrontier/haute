/**
 * Lazy-loading enforcement for the assistant panel (mirror of the
 * NodePanel.lazyEditors guard): App.tsx must never statically import the
 * panel, and the markdown renderer must stay inside the lazy panel chunk —
 * keeping both out of the initial bundle within the bundle-size gate.
 */

import { readFileSync, readdirSync } from "node:fs"
import path from "node:path"

import { describe, expect, it } from "vitest"

const SRC = path.resolve(__dirname, "..")
const appSource = readFileSync(path.join(SRC, "App.tsx"), "utf8")

function staticImportsOf(source: string): string[] {
  return [...source.matchAll(/^import\s+(?:type\s+)?[^"']*["']([^"']+)["']/gms)].map(
    (match) => match[1],
  )
}

describe("assistant panel lazy-loading guard", () => {
  it("App.tsx loads AssistantPanel only through React.lazy(import())", () => {
    const staticImports = staticImportsOf(appSource).filter((spec) =>
      spec.includes("assistant"),
    )
    expect(staticImports).toEqual([])
    expect(appSource).toContain('import("./panels/assistant/AssistantPanel")')
  })

  it("App.tsx never references the markdown renderer", () => {
    expect(appSource).not.toMatch(/react-markdown|remark-gfm/)
  })

  it("the markdown renderer is imported only inside the lazy panel chunk", () => {
    const offenders: string[] = []
    const walk = (dir: string) => {
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
          if (entry.name === "__tests__" || entry.name === "node_modules") continue
          walk(full)
          continue
        }
        if (!/\.(ts|tsx)$/.test(entry.name)) continue
        const source = readFileSync(full, "utf8")
        if (/from\s+["'](react-markdown|remark-gfm)["']/.test(source)) {
          const relative = path.relative(SRC, full).replaceAll("\\", "/")
          if (!relative.startsWith("panels/assistant/")) offenders.push(relative)
        }
      }
    }
    walk(SRC)
    expect(offenders).toEqual([])
  })
})
