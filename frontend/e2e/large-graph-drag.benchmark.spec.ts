import { writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const LARGE_GRAPH_NODE_COUNT = 1000
const DRAG_STEPS = 80
const FRAME_P95_BUDGET_MS = 250
const INPUT_LATENCY_P95_BUDGET_MS = 40

type DragBenchmarkResult = {
  frameCount: number
  pointerMoveCount: number
  frameP95Ms: number
  frameMaxMs: number
  frameOverBudgetCount: number
  inputLatencyP95Ms: number
  inputLatencyMaxMs: number
}

declare global {
  interface Window {
    __hauteDragBenchmark?: {
      stop: () => DragBenchmarkResult
    }
  }
}

function buildLargeGraphSource(nodeCount: number): string {
  const lines = [
    '"""Large graph used by Playwright drag performance benchmarks."""',
    "",
    "from pathlib import Path",
    "",
    "import polars as pl",
    "",
    "import haute",
    "",
    'pipeline = haute.Pipeline("large_drag_benchmark")',
    "",
    "",
    '@pipeline.data_input(config="config/data_input/raw_rows.json")',
    "def raw_rows() -> pl.LazyFrame:",
    '    return pl.scan_parquet(Path(__file__).parent.parent / "data" / "sample.parquet")',
    "",
  ]

  for (let index = 0; index < nodeCount; index += 1) {
    const name = `bench_${index.toString().padStart(3, "0")}`
    lines.push(
      "",
      "@pipeline.polars",
      `def ${name}(raw_rows: pl.LazyFrame) -> pl.LazyFrame:`,
      "    return raw_rows",
    )
  }

  return `${lines.join("\n")}\n`
}

function buildLargeGraphSidecar(nodeCount: number): string {
  const positions: Record<string, { x: number; y: number }> = {
    raw_rows: { x: 40, y: 140 },
  }

  const columns = 18
  for (let index = 0; index < nodeCount; index += 1) {
    const row = Math.floor(index / columns)
    const column = index % columns
    positions[`bench_${index.toString().padStart(3, "0")}`] = {
      x: 340 + column * 280,
      y: 120 + row * 150,
    }
  }

  return `${JSON.stringify({ positions }, null, 2)}\n`
}

function writeLargeGraphPipeline(): void {
  const ratingDir = resolve(e2eProjectRoot, "rating")
  writeFileSync(
    resolve(ratingDir, "main.py"),
    buildLargeGraphSource(LARGE_GRAPH_NODE_COUNT),
    "utf8",
  )
  writeFileSync(
    resolve(ratingDir, "main.haute.json"),
    buildLargeGraphSidecar(LARGE_GRAPH_NODE_COUNT),
    "utf8",
  )
}

async function installDragBenchmark(page: Page, frameBudgetMs: number): Promise<void> {
  await page.evaluate((budgetMs) => {
    const frameDeltas: number[] = []
    const inputLatencies: number[] = []
    let running = true
    let lastFrame: number | null = null

    function tick(now: number): void {
      if (!running) return
      if (lastFrame !== null) {
        frameDeltas.push(now - lastFrame)
      }
      lastFrame = now
      requestAnimationFrame(tick)
    }

    function onPointerMove(event: PointerEvent): void {
      inputLatencies.push(Math.max(0, performance.now() - event.timeStamp))
    }

    window.addEventListener("pointermove", onPointerMove, { capture: true })
    requestAnimationFrame(tick)

    window.__hauteDragBenchmark = {
      stop: () => {
        running = false
        window.removeEventListener("pointermove", onPointerMove, { capture: true })
        const frames = frameDeltas
        const sortedFrames = [...frames].sort((a, b) => a - b)
        const sortedLatencies = [...inputLatencies].sort((a, b) => a - b)
        const pick = (values: number[], p: number): number => {
          if (values.length === 0) return 0
          const index = Math.min(values.length - 1, Math.ceil((p / 100) * values.length) - 1)
          return values[index]
        }
        return {
          frameCount: frames.length,
          pointerMoveCount: inputLatencies.length,
          frameP95Ms: pick(sortedFrames, 95),
          frameMaxMs: sortedFrames[sortedFrames.length - 1] ?? 0,
          frameOverBudgetCount: frames.filter((value) => value > budgetMs).length,
          inputLatencyP95Ms: pick(sortedLatencies, 95),
          inputLatencyMaxMs: sortedLatencies[sortedLatencies.length - 1] ?? 0,
        }
      },
    }
  }, frameBudgetMs)
}

async function waitForAnimationFrames(page: Page, count: number): Promise<void> {
  await page.evaluate(
    (frameCount) =>
      new Promise<void>((resolve) => {
        let remaining = frameCount
        function tick(): void {
          remaining -= 1
          if (remaining <= 0) {
            resolve()
            return
          }
          requestAnimationFrame(tick)
        }
        requestAnimationFrame(tick)
      }),
    count,
  )
}

async function collectDragBenchmark(page: Page): Promise<DragBenchmarkResult> {
  return page.evaluate(() => {
    const benchmark = window.__hauteDragBenchmark
    if (!benchmark) throw new Error("Drag benchmark instrumentation was not installed")
    return benchmark.stop()
  })
}

async function visibleBenchmarkNodeTestId(page: Page): Promise<string> {
  return page.locator('[data-testid^="rf__node-bench_"]').evaluateAll((elements) => {
    const minTop = 72
    const minLeft = 210
    const maxRight = window.innerWidth - 48
    const maxBottom = window.innerHeight - 64
    const center = { x: (minLeft + maxRight) / 2, y: (minTop + maxBottom) / 2 }

    const candidates = elements
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          testId: element.getAttribute("data-testid"),
          rect,
          distance: Math.hypot(
            rect.left + rect.width / 2 - center.x,
            rect.top + rect.height / 2 - center.y,
          ),
        }
      })
      .filter(({ testId, rect }) =>
        testId &&
        rect.width >= 20 &&
        rect.height >= 6 &&
        rect.left >= minLeft &&
        rect.top >= minTop &&
        rect.right <= maxRight &&
        rect.bottom <= maxBottom,
      )
      .sort((a, b) => a.distance - b.distance)

    const best = candidates[0]?.testId
    if (!best) {
      throw new Error("No benchmark node was fully visible after zooming")
    }
    return best
  })
}

test.describe("large graph drag benchmark", () => {
  test.beforeEach(() => {
    resetE2eProject()
    writeLargeGraphPipeline()
  })

  test("@benchmark keeps input and frame latency within budget while dragging in a large graph", async ({
    page,
  }) => {
    await page.goto("/")

    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await expect(page.getByTestId("rf__node-bench_000")).toBeVisible()
    for (let index = 0; index < 4; index += 1) {
      await page.getByRole("button", { name: "Zoom in" }).click()
    }
    await waitForAnimationFrames(page, 5)
    const node = page.getByTestId(await visibleBenchmarkNodeTestId(page))
    await expect(node).toBeVisible()

    const box = await node.boundingBox()
    expect(box, "benchmark target node should have a stable bounding box").not.toBeNull()
    if (!box) return

    await installDragBenchmark(page, FRAME_P95_BUDGET_MS)

    const startX = box.x + box.width / 2
    const startY = box.y + box.height / 2
    await page.mouse.move(startX, startY)
    await page.mouse.down()
    await waitForAnimationFrames(page, 1)
    await page.mouse.move(startX + DRAG_STEPS * 3, startY + DRAG_STEPS, { steps: DRAG_STEPS })
    await page.mouse.up()
    await waitForAnimationFrames(page, 2)

    const movedBox = await node.boundingBox()
    expect(movedBox, "benchmark target node should still have a bounding box after drag").not.toBeNull()
    if (!movedBox) return
    const movedDistance = Math.hypot(movedBox.x - box.x, movedBox.y - box.y)

    const result = await collectDragBenchmark(page)
    const summary = JSON.stringify(
      {
        graphNodeCount: LARGE_GRAPH_NODE_COUNT + 1,
        dragSteps: DRAG_STEPS,
        budgets: {
          frameP95Ms: FRAME_P95_BUDGET_MS,
          overBudgetFrameShare: "5%",
          inputLatencyP95Ms: INPUT_LATENCY_P95_BUDGET_MS,
        },
        startBox: box,
        movedBox,
        movedDistance,
        result,
      },
      null,
      2,
    )
    await test.info().attach("large-graph-drag-metrics.json", {
      body: summary,
      contentType: "application/json",
    })
    console.info(`large graph drag benchmark metrics:\n${summary}`)

    expect(result.pointerMoveCount, `benchmark pointer samples:\n${summary}`).toBeGreaterThanOrEqual(
      Math.floor(DRAG_STEPS * 0.8),
    )
    expect(result.frameCount, `benchmark frame samples:\n${summary}`).toBeGreaterThanOrEqual(3)
    expect(movedDistance, `benchmark target node did not move enough:\n${summary}`).toBeGreaterThanOrEqual(40)
    expect(result.frameP95Ms, `large graph drag frame budget exceeded:\n${summary}`).toBeLessThanOrEqual(
      FRAME_P95_BUDGET_MS,
    )
    const allowedOverBudgetFrames = Math.ceil(result.frameCount * 0.05)
    expect(
      result.frameOverBudgetCount,
      `large graph drag had too many over-budget frames:\n${summary}`,
    ).toBeLessThanOrEqual(
      allowedOverBudgetFrames,
    )
    expect(
      result.inputLatencyP95Ms,
      `large graph drag input latency budget exceeded:\n${summary}`,
    ).toBeLessThanOrEqual(INPUT_LATENCY_P95_BUDGET_MS)
  })
})
