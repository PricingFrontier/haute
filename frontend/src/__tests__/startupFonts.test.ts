import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"

const GOOGLE_FONT_PATTERNS = [
  /fonts\.googleapis\.com/i,
  /fonts\.gstatic\.com/i,
  /fonts\.googleapis\.com\/css2/i,
]

describe("startup font loading", () => {
  it("does not reference Google Fonts from startup HTML or global CSS", () => {
    const startupSources = [
      readFileSync(path.resolve(__dirname, "..", "..", "index.html"), "utf8"),
      readFileSync(path.resolve(__dirname, "..", "index.css"), "utf8"),
    ].join("\n")

    for (const pattern of GOOGLE_FONT_PATTERNS) {
      expect(startupSources).not.toMatch(pattern)
    }
  })

  it("uses an explicit system sans stack instead of Inter as an external font dependency", () => {
    const css = readFileSync(path.resolve(__dirname, "..", "index.css"), "utf8")
    const fontFamily = css.match(/html,\s*body,\s*#root\s*\{[\s\S]*?font-family:\s*([^;]+);/)

    expect(fontFamily?.[1]).toContain("system-ui")
    expect(fontFamily?.[1]).toContain("-apple-system")
    expect(fontFamily?.[1]).toContain("Segoe UI")
    expect(fontFamily?.[1]).toContain("sans-serif")
    expect(fontFamily?.[1]).not.toMatch(/\bInter\b/)
  })
})
