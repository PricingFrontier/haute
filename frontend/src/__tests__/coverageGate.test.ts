import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises"
import os from "node:os"
import path from "node:path"
import { afterEach, describe, expect, it } from "vitest"

// @ts-expect-error The checker is a Node CLI script; this test exercises its public exports.
import { checkCriticalCoverage } from "../../scripts/check-critical-coverage.mjs"

type CoverageCheckResult = {
  checkedFiles: Array<{ relativePath: string }>
}

const tempRoots: string[] = []

async function makeTempProject() {
  const root = await mkdtemp(path.join(os.tmpdir(), "haute-coverage-gate-"))
  tempRoots.push(root)
  return root
}

async function writeSource(root: string, relativePath: string) {
  const filePath = path.join(root, relativePath)
  await mkdir(path.dirname(filePath), { recursive: true })
  await writeFile(filePath, "export const covered = true\n")
  return filePath
}

async function writeCoverageSummary(
  root: string,
  entries: Record<string, { statements: number; branches: number; functions: number; lines: number }>,
) {
  const artifactPath = path.join(root, "coverage", "coverage-summary.json")
  await mkdir(path.dirname(artifactPath), { recursive: true })
  const summary = Object.fromEntries(
    Object.entries(entries).map(([filePath, metrics]) => [
      filePath,
      {
        statements: { total: 10, covered: metrics.statements, skipped: 0, pct: metrics.statements },
        branches: { total: 10, covered: metrics.branches, skipped: 0, pct: metrics.branches },
        functions: { total: 10, covered: metrics.functions, skipped: 0, pct: metrics.functions },
        lines: { total: 10, covered: metrics.lines, skipped: 0, pct: metrics.lines },
      },
    ]),
  )
  await writeFile(artifactPath, JSON.stringify({ total: {}, ...summary }, null, 2))
}

const thresholds = {
  statements: 80,
  branches: 70,
  functions: 75,
  lines: 85,
}

describe("critical frontend coverage gate", () => {
  afterEach(async () => {
    await Promise.all(tempRoots.map((root) => rm(root, { recursive: true, force: true })))
    tempRoots.length = 0
  })

  it("passes when every configured file and glob meets explicit thresholds", async () => {
    const root = await makeTempProject()
    const apiFile = await writeSource(root, "src/api/client.ts")
    const storeFile = await writeSource(root, "src/stores/useGraphStore.ts")
    await writeCoverageSummary(root, {
      [apiFile]: { statements: 81, branches: 71, functions: 76, lines: 86 },
      [storeFile]: { statements: 100, branches: 95, functions: 100, lines: 100 },
    })

    const result = (await checkCriticalCoverage(root, {
      artifact: "coverage/coverage-summary.json",
      entries: [
        { pattern: "src/api/client.ts", thresholds },
        { pattern: "src/stores/*.ts", thresholds },
      ],
    })) as CoverageCheckResult

    expect(result.checkedFiles.map((file) => file.relativePath)).toEqual([
      "src/api/client.ts",
      "src/stores/useGraphStore.ts",
    ])
  })

  it("fails with an actionable message when a metric is below threshold", async () => {
    const root = await makeTempProject()
    const apiFile = await writeSource(root, "src/api/client.ts")
    await writeCoverageSummary(root, {
      [apiFile]: { statements: 79, branches: 71, functions: 76, lines: 86 },
    })

    await expect(
      checkCriticalCoverage(root, {
        artifact: "coverage/coverage-summary.json",
        entries: [{ pattern: "src/api/client.ts", thresholds }],
      }),
    ).rejects.toThrow(
      "src/api/client.ts statements coverage is 79.00%, below required 80.00%",
    )
  })

  it("fails loudly when the coverage artifact is missing", async () => {
    const root = await makeTempProject()
    await writeSource(root, "src/api/client.ts")

    await expect(
      checkCriticalCoverage(root, {
        artifact: "coverage/coverage-summary.json",
        entries: [{ pattern: "src/api/client.ts", thresholds }],
      }),
    ).rejects.toThrow(
      "Coverage summary not found at coverage/coverage-summary.json. Run `npm run test:coverage` first.",
    )
  })

  it("fails when a configured file exists but is absent from the coverage artifact", async () => {
    const root = await makeTempProject()
    await writeSource(root, "src/api/client.ts")
    await writeCoverageSummary(root, {})

    await expect(
      checkCriticalCoverage(root, {
        artifact: "coverage/coverage-summary.json",
        entries: [{ pattern: "src/api/client.ts", thresholds }],
      }),
    ).rejects.toThrow(
      "src/api/client.ts exists on disk but is missing from coverage-summary.json",
    )
  })

  it("fails when a configured critical file does not exist on disk", async () => {
    const root = await makeTempProject()
    await writeCoverageSummary(root, {})

    await expect(
      checkCriticalCoverage(root, {
        artifact: "coverage/coverage-summary.json",
        entries: [{ pattern: "src/api/client.ts", thresholds }],
      }),
    ).rejects.toThrow("Critical coverage file src/api/client.ts does not exist.")
  })

  it("fails when a configured glob matches no files", async () => {
    const root = await makeTempProject()
    await writeCoverageSummary(root, {})

    await expect(
      checkCriticalCoverage(root, {
        artifact: "coverage/coverage-summary.json",
        entries: [{ pattern: "src/critical/**/*.ts", thresholds }],
      }),
    ).rejects.toThrow("Critical coverage pattern src/critical/**/*.ts matched no files.")
  })
})
