import { readFileSync } from "node:fs"
import path from "node:path"

import { describe, expect, it } from "vitest"

const appSource = readFileSync(path.resolve(__dirname, "..", "App.tsx"), "utf8")
const bundleCheckSource = readFileSync(
  path.resolve(__dirname, "..", "..", "scripts", "check-bundle-size.mjs"),
  "utf8",
)

describe("Trace panel lazy-loading guard", () => {
  it("loads TracePanel only through React.lazy, keeps its chunk lazy-only, and its request surface eager", () => {
    expect(appSource).not.toMatch(/^import\s+TracePanel\b.*from\s+["']\.\/panels\/TracePanel["']/m)
    expect(appSource).toContain('const loadTracePanel = () => import("./panels/TracePanel")')
    expect(appSource).toContain("const TracePanel = lazy(loadTracePanel)")
    expect(appSource).toMatch(/^import\s+\{\s*TraceStatePanel\s*\}\s+from\s+["']\.\/panels\/TraceStatePanel["']/m)
    expect(bundleCheckSource).toMatch(
      /LAZY_ONLY_MODULEPRELOAD_CHUNK_PREFIXES\s*=\s*\[[\s\S]*?"TracePanel"/,
    )
  })
})
