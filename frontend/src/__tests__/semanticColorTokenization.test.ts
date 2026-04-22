import { describe, it, expect } from "vitest"
import { readFileSync, readdirSync, statSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const HERE = path.dirname(fileURLToPath(import.meta.url))
const SRC_ROOT = path.resolve(HERE, "..")

const TARGET_PATTERNS = [
  /#(?:ef4444|22c55e|60a5fa|dc2626|b91c1c|4ade80|f59e0b|eab308|fca5a5|f87171)\b/i,
  /rgba?\(\s*239\s*,\s*68\s*,\s*68\s*,/i,
  /rgba?\(\s*34\s*,\s*197\s*,\s*94\s*,/i,
  /rgba?\(\s*245\s*,\s*158\s*,\s*11\s*,/i,
  /rgba?\(\s*251\s*,\s*191\s*,\s*36\s*,/i,
  /rgba?\(\s*234\s*,\s*179\s*,\s*8\s*,/i,
  /rgba?\(\s*59\s*,\s*130\s*,\s*246\s*,/i,
] as const
const CHART_ALLOWLIST = new Set([
  // Data-visualisation palettes deliberately keep literal series colors so
  // charts remain stable even if app chrome tokens are rethemed.
  path.normalize("panels/OptimiserDataPreview.tsx"),
  path.normalize("panels/modelling/AveTab.tsx"),
  path.normalize("panels/modelling/LiftTab.tsx"),
  path.normalize("panels/modelling/LossTab.tsx"),
  path.normalize("panels/modelling/LossChart.tsx"),
  path.normalize("panels/modelling/ResidualsTab.tsx"),
  path.normalize("panels/optimiser/ConvergenceChart.tsx"),
  path.normalize("panels/optimiser/FrontierChart.tsx"),
  path.normalize("panels/editors/rating/OneWayEditor.tsx"),
  path.normalize("trace/WaterfallChart.tsx"),
])

function collectSourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    const info = statSync(full)
    if (info.isDirectory()) {
      if (entry === "__tests__") continue
      out.push(...collectSourceFiles(full))
      continue
    }
    if (!/\.(ts|tsx)$/.test(entry)) continue
    out.push(full)
  }
  return out
}

function isAllowedPath(rel: string): boolean {
  const normalized = rel.split(path.sep).join(path.posix.sep)
  if (normalized.includes("/__tests__/")) return true
  if (normalized.endsWith(".test.ts") || normalized.endsWith(".test.tsx")) return true
  if (normalized.endsWith(".spec.ts") || normalized.endsWith(".spec.tsx")) return true
  return CHART_ALLOWLIST.has(path.normalize(normalized))
}

describe("semantic color tokenization", () => {
  it("keeps semantic UI color literals out of live frontend files", () => {
    const offenders: string[] = []
    for (const file of collectSourceFiles(SRC_ROOT)) {
      const rel = path.relative(SRC_ROOT, file)
      if (isAllowedPath(rel)) continue
      const text = readFileSync(file, "utf8")
      for (const pattern of TARGET_PATTERNS) {
        const match = text.match(pattern)
        if (match) {
          offenders.push(`${rel} -> ${match[0]}`)
        }
      }
    }

    expect(offenders, offenders.join("\n")).toEqual([])
  })
})
