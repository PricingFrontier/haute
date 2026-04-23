/**
 * Structural DRY regression guard for Phase 2 Package 3B.
 *
 * After extracting `useStaleConfigEstimate` and centralising the shared
 * polling loop via `useJobPolling` / `useBackgroundJobs`, the two panels
 * — ModellingConfig.tsx and OptimiserConfig.tsx — must not re-inline
 * the patterns that now live in shared hooks.
 *
 * This test reads the panel sources from disk and fails loudly if a
 * future diff smuggles the inline copy back in. It does NOT render
 * anything: rendering behaviour is already covered by
 * ModellingConfig.test.tsx and OptimiserConfig.test.tsx.
 */
import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { fileURLToPath } from "node:url"

const here = path.dirname(fileURLToPath(import.meta.url))
const panelsDir = path.resolve(here, "..")

const modellingSrc = readFileSync(path.join(panelsDir, "ModellingConfig.tsx"), "utf8")
const optimiserSrc = readFileSync(path.join(panelsDir, "OptimiserConfig.tsx"), "utf8")

describe("configPanels DRY guard", () => {
  it("neither panel re-inlines the RAM / estimate useState+abort pattern", () => {
    // The old inline block used `setRamEstimate*` plus an `estimateAbortRef`
    // useRef<AbortController>. That lives inside useStaleConfigEstimate now.
    for (const src of [modellingSrc, optimiserSrc]) {
      expect(src).not.toMatch(/setRamEstimate\s*\(/)
      expect(src).not.toMatch(/setRamEstimateLoading\s*\(/)
      expect(src).not.toMatch(/setRamEstimateError\s*\(/)
      expect(src).not.toMatch(/estimateAbortRef/)
    }
  })

  it("neither panel re-computes its own currentConfigHash via useMemo + hashConfig", () => {
    // The inline pattern:
    //   const currentConfigHash = useMemo(() => hashConfig(config), [config])
    // should now come from the shared hook. Direct hashConfig imports here
    // are also a signal the staleness derivation was re-inlined.
    const usesMemoHash = /currentConfigHash\s*=\s*useMemo\s*\(\s*\(\)\s*=>\s*hashConfig/
    const importsHashConfig = /import\s+[^;]*\bhashConfig\b[^;]*from\s+["'][^"']*useNodeResultsStore["']/
    for (const src of [modellingSrc, optimiserSrc]) {
      expect(src).not.toMatch(usesMemoHash)
      expect(src).not.toMatch(importsHashConfig)
    }
  })

  it("neither panel sets up its own polling loop", () => {
    // Polling (`getOptimiserStatus` / `getTrainStatus` inside a setInterval
    // or recursive setTimeout) is owned by useBackgroundJobs +
    // useJobPolling. A panel that references these APIs directly is
    // re-inventing the loop.
    const badPatterns = [
      /setInterval\s*\(/,
      /getOptimiserStatus/,
      /getTrainStatus/,
    ]
    for (const src of [modellingSrc, optimiserSrc]) {
      for (const p of badPatterns) {
        expect(src).not.toMatch(p)
      }
    }
  })

  it("both panels call the shared useStaleConfigEstimate hook", () => {
    // The reviewer gate for item #67 requires 2+ callers, so both panels
    // must flow through the shared hook (even if the endpoint differs).
    expect(modellingSrc).toMatch(/\buseStaleConfigEstimate\s*\(/)
    expect(optimiserSrc).toMatch(/\buseStaleConfigEstimate\s*\(/)
  })
})
