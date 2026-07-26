import { readFileSync } from "node:fs"
import path from "node:path"

import { describe, expect, it } from "vitest"

const appSource = readFileSync(path.resolve(__dirname, "..", "App.tsx"), "utf8")
const bundleCheckSource = readFileSync(
  path.resolve(__dirname, "..", "..", "scripts", "check-bundle-size.mjs"),
  "utf8",
)

describe("Utility panel lazy-loading guard", () => {
  it("loads UtilityPanel only through React.lazy and keeps its chunk lazy-only", () => {
    expect(appSource).not.toMatch(
      /^import\s+UtilityPanel\s+from\s+["']\.\/panels\/UtilityPanel["']/m,
    )
    expect(appSource).toContain('lazy(() => import("./panels/UtilityPanel"))')
    expect(bundleCheckSource).toMatch(
      /LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES\s*=\s*\[[\s\S]*?"UtilityPanel"/,
    )
  })
})
