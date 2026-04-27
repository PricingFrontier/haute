import { expect, test, type Page } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

const PREVIEW_ROW_COUNT = 10_000
const PREVIEW_COLUMN_COUNT = 1_000
const TARGET_ROW_INDEX = 7_500
const TARGET_COLUMN_INDEX = 900
const ROW_HEIGHT = 28
const COLUMN_WIDTH = 160
const ROW_NUMBER_WIDTH = 48
const SCROLL_STEPS = 90
const FRAME_P95_BUDGET_MS = 80
const SCROLL_STEP_P95_BUDGET_MS = 120
const OVER_BUDGET_FRAME_SHARE = 0.05
const MAX_RENDERED_HEADER_COUNT = 30
const MAX_RENDERED_BODY_CELL_COUNT = 900

const SPARSE_VALUE_COLUMN_INDEXES = [
  0,
  1,
  250,
  500,
  750,
  TARGET_COLUMN_INDEX,
  PREVIEW_COLUMN_COUNT - 1,
]

type PreviewRequestBody = {
  node_id?: unknown
  row_limit?: unknown
  requested_preview_columns?: unknown
}

type ScrollBenchmarkResult = {
  frameCount: number
  scrollEventCount: number
  frameP95Ms: number
  frameMaxMs: number
  frameOverBudgetCount: number
  scrollStepP95Ms: number
  scrollStepMaxMs: number
  initialScrollTop: number
  initialScrollLeft: number
  scrollTop: number
  scrollLeft: number
  targetTop: number
  targetLeft: number
  maxScrollTop: number
  maxScrollLeft: number
  renderedHeaderCount: number
  renderedBodyCellCount: number
  visibleColumnNames: string[]
  minVisibleRowIndex: number | null
  maxVisibleRowIndex: number | null
}

function columnName(index: number): string {
  return `col_${index.toString().padStart(4, "0")}`
}

const PREVIEW_COLUMNS = Array.from({ length: PREVIEW_COLUMN_COUNT }, (_, index) => ({
  name: columnName(index),
  dtype: index % 5 === 0 ? "Utf8" : index % 3 === 0 ? "Float64" : "Int64",
}))

const PREVIEW_ROWS = Array.from({ length: PREVIEW_ROW_COUNT }, (_, rowIndex) => {
  const row: Record<string, number | string> = {}
  for (const columnIndex of SPARSE_VALUE_COLUMN_INDEXES) {
    const name = columnName(columnIndex)
    row[name] = columnIndex % 5 === 0 ? `r${rowIndex}_c${columnIndex}` : rowIndex * 10_000 + columnIndex
  }
  return row
})

function buildPreviewResponseBody(nodeId: string): string {
  return JSON.stringify({
    status: "ok",
    node_id: nodeId,
    row_count: PREVIEW_ROW_COUNT,
    column_count: PREVIEW_COLUMN_COUNT,
    columns: PREVIEW_COLUMNS,
    available_columns: PREVIEW_COLUMNS,
    preview: PREVIEW_ROWS,
    preview_row_count: PREVIEW_ROW_COUNT,
    preview_row_limit: PREVIEW_ROW_COUNT,
    preview_truncated: false,
    error: null,
    timings: [],
    memory: [],
    schema_warnings: [],
    node_statuses: { [nodeId]: "ok" },
  })
}

async function installSparsePreviewRoute(page: Page): Promise<{ requestCount: () => number }> {
  let previewRequestCount = 0

  await page.route("**/api/pipeline/preview", async (route) => {
    previewRequestCount += 1
    const body = route.request().postDataJSON() as PreviewRequestBody
    if (typeof body.node_id !== "string" || body.node_id.length === 0) {
      throw new Error(`Preview benchmark expected a string node_id, received ${String(body.node_id)}`)
    }

    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: buildPreviewResponseBody(body.node_id),
    })
  })

  return {
    requestCount: () => previewRequestCount,
  }
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

async function runDataPreviewScrollBenchmark(page: Page): Promise<ScrollBenchmarkResult> {
  return page.evaluate(
    async ({
      frameBudgetMs,
      requestedTargetTop,
      requestedTargetLeft,
      steps,
    }) => {
      const scrollEl = document.querySelector<HTMLElement>('[data-testid="data-preview-scroll"]')
      if (!scrollEl) {
        throw new Error("Data preview scroll container was not mounted")
      }

      const frameDeltas: number[] = []
      const scrollStepLatencies: number[] = []
      let scrollEventCount = 0
      let running = true
      let lastFrame: number | null = null

      const percentile = (values: number[], p: number): number => {
        if (values.length === 0) return 0
        const sorted = [...values].sort((a, b) => a - b)
        const index = Math.min(sorted.length - 1, Math.ceil((p / 100) * sorted.length) - 1)
        return sorted[index]
      }

      const waitForFrames = (count: number) =>
        new Promise<void>((resolve) => {
          let remaining = count
          function tick(): void {
            remaining -= 1
            if (remaining <= 0) {
              resolve()
              return
            }
            requestAnimationFrame(tick)
          }
          requestAnimationFrame(tick)
        })

      function tick(now: number): void {
        if (!running) return
        if (lastFrame !== null) {
          frameDeltas.push(now - lastFrame)
        }
        lastFrame = now
        requestAnimationFrame(tick)
      }

      const onScroll = () => {
        scrollEventCount += 1
      }

      const initialScrollTop = scrollEl.scrollTop
      const initialScrollLeft = scrollEl.scrollLeft
      scrollEl.addEventListener("scroll", onScroll, { passive: true })
      requestAnimationFrame(tick)

      const targetTop = Math.min(
        Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight),
        requestedTargetTop,
      )
      const targetLeft = Math.min(
        Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth),
        requestedTargetLeft,
      )

      await waitForFrames(2)
      for (let step = 1; step <= steps; step += 1) {
        const startedAt = performance.now()
        scrollEl.scrollTop = Math.round((targetTop * step) / steps)
        scrollEl.scrollLeft = Math.round((targetLeft * step) / steps)
        scrollEl.dispatchEvent(new Event("scroll", { bubbles: true }))
        await waitForFrames(2)
        scrollStepLatencies.push(performance.now() - startedAt)
      }
      await waitForFrames(3)
      running = false
      scrollEl.removeEventListener("scroll", onScroll)

      const headers = Array.from(scrollEl.querySelectorAll("th")).filter(
        (header) => header.getAttribute("aria-hidden") !== "true",
      )
      const cells = Array.from(
        scrollEl.querySelectorAll<HTMLTableCellElement>("td[data-row-index][data-column]"),
      )
      const visibleColumnNames = Array.from(
        new Set(cells.map((cell) => cell.dataset.column).filter((value): value is string => !!value)),
      )
      const visibleRowIndexes = cells
        .map((cell) => Number(cell.dataset.rowIndex))
        .filter((value) => Number.isInteger(value))

      return {
        frameCount: frameDeltas.length,
        scrollEventCount,
        frameP95Ms: percentile(frameDeltas, 95),
        frameMaxMs: frameDeltas.length ? Math.max(...frameDeltas) : 0,
        frameOverBudgetCount: frameDeltas.filter((value) => value > frameBudgetMs).length,
        scrollStepP95Ms: percentile(scrollStepLatencies, 95),
        scrollStepMaxMs: scrollStepLatencies.length ? Math.max(...scrollStepLatencies) : 0,
        initialScrollTop,
        initialScrollLeft,
        scrollTop: scrollEl.scrollTop,
        scrollLeft: scrollEl.scrollLeft,
        targetTop,
        targetLeft,
        maxScrollTop: Math.max(0, scrollEl.scrollHeight - scrollEl.clientHeight),
        maxScrollLeft: Math.max(0, scrollEl.scrollWidth - scrollEl.clientWidth),
        renderedHeaderCount: headers.length,
        renderedBodyCellCount: cells.length,
        visibleColumnNames,
        minVisibleRowIndex: visibleRowIndexes.length ? Math.min(...visibleRowIndexes) : null,
        maxVisibleRowIndex: visibleRowIndexes.length ? Math.max(...visibleRowIndexes) : null,
      }
    },
    {
      frameBudgetMs: FRAME_P95_BUDGET_MS,
      requestedTargetTop: TARGET_ROW_INDEX * ROW_HEIGHT,
      requestedTargetLeft: ROW_NUMBER_WIDTH + TARGET_COLUMN_INDEX * COLUMN_WIDTH,
      steps: SCROLL_STEPS,
    },
  )
}

test.describe("data preview scroll benchmark", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@benchmark keeps render latency within budget while scrolling a sparse 10k x 1000 preview", async ({
    page,
  }) => {
    test.slow()

    const previewRoute = await installSparsePreviewRoute(page)

    await page.goto("/")

    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const rowLimitInput = page
      .getByTitle("Row limit for preview (0 = no limit)")
      .locator('input[type="number"]')
    await rowLimitInput.fill(String(PREVIEW_ROW_COUNT))
    await expect(rowLimitInput).toHaveValue(String(PREVIEW_ROW_COUNT))

    const sourceNode = page.getByRole("button", { name: /raw_rows/i })
    await expect(sourceNode).toBeVisible()
    await sourceNode.click()
    await expect(page.locator("input.node-label-input")).toHaveValue("raw_rows")

    const scrollContainer = page.getByTestId("data-preview-scroll")
    await expect(scrollContainer.getByText(columnName(0), { exact: true })).toBeVisible({
      timeout: 120_000,
    })
    await waitForAnimationFrames(page, 5)

    const result = await runDataPreviewScrollBenchmark(page)
    const targetColumnName = columnName(TARGET_COLUMN_INDEX)
    const summary = JSON.stringify(
      {
        rows: PREVIEW_ROW_COUNT,
        columns: PREVIEW_COLUMN_COUNT,
        sparseValueColumnIndexes: SPARSE_VALUE_COLUMN_INDEXES,
        previewRequests: previewRoute.requestCount(),
        scrollSteps: SCROLL_STEPS,
        target: {
          rowIndex: TARGET_ROW_INDEX,
          column: targetColumnName,
        },
        budgets: {
          frameP95Ms: FRAME_P95_BUDGET_MS,
          overBudgetFrameShare: `${OVER_BUDGET_FRAME_SHARE * 100}%`,
          scrollStepP95Ms: SCROLL_STEP_P95_BUDGET_MS,
        },
        result,
      },
      null,
      2,
    )
    await test.info().attach("data-preview-scroll-metrics.json", {
      body: summary,
      contentType: "application/json",
    })
    console.info(`data preview scroll benchmark metrics:\n${summary}`)

    expect(previewRoute.requestCount(), `preview route was not exercised:\n${summary}`).toBeGreaterThanOrEqual(
      1,
    )
    expect(result.maxScrollTop, `preview did not expose enough vertical scroll range:\n${summary}`).toBeGreaterThan(
      result.targetTop,
    )
    expect(result.maxScrollLeft, `preview did not expose enough horizontal scroll range:\n${summary}`).toBeGreaterThan(
      result.targetLeft,
    )
    expect(result.scrollEventCount, `benchmark scroll samples:\n${summary}`).toBeGreaterThanOrEqual(
      SCROLL_STEPS,
    )
    expect(result.frameCount, `benchmark frame samples:\n${summary}`).toBeGreaterThanOrEqual(
      SCROLL_STEPS,
    )
    expect(result.scrollTop, `vertical scroll did not move:\n${summary}`).toBeGreaterThan(
      result.initialScrollTop,
    )
    expect(result.scrollLeft, `horizontal scroll did not move:\n${summary}`).toBeGreaterThan(
      result.initialScrollLeft,
    )
    expect(result.scrollTop, `vertical scroll did not reach target:\n${summary}`).toBeGreaterThanOrEqual(
      Math.floor(result.targetTop * 0.95),
    )
    expect(
      result.scrollLeft,
      `horizontal scroll did not reach target:\n${summary}`,
    ).toBeGreaterThanOrEqual(Math.floor(result.targetLeft * 0.95))
    expect(result.visibleColumnNames, `target column was not rendered after scrolling:\n${summary}`).toContain(
      targetColumnName,
    )
    expect(
      result.minVisibleRowIndex,
      `target row was above the rendered row window:\n${summary}`,
    ).not.toBeNull()
    expect(
      result.maxVisibleRowIndex,
      `target row was below the rendered row window:\n${summary}`,
    ).not.toBeNull()
    expect(result.minVisibleRowIndex ?? Number.POSITIVE_INFINITY).toBeLessThanOrEqual(TARGET_ROW_INDEX)
    expect(result.maxVisibleRowIndex ?? Number.NEGATIVE_INFINITY).toBeGreaterThanOrEqual(
      TARGET_ROW_INDEX,
    )
    expect(
      result.renderedHeaderCount,
      `column virtualization rendered too many headers:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_RENDERED_HEADER_COUNT)
    expect(
      result.renderedBodyCellCount,
      `row/column virtualization rendered too many cells:\n${summary}`,
    ).toBeLessThanOrEqual(MAX_RENDERED_BODY_CELL_COUNT)
    expect(result.frameP95Ms, `data preview scroll frame budget exceeded:\n${summary}`).toBeLessThanOrEqual(
      FRAME_P95_BUDGET_MS,
    )
    const allowedOverBudgetFrames = Math.ceil(result.frameCount * OVER_BUDGET_FRAME_SHARE)
    expect(
      result.frameOverBudgetCount,
      `data preview scroll had too many over-budget frames:\n${summary}`,
    ).toBeLessThanOrEqual(allowedOverBudgetFrames)
    expect(
      result.scrollStepP95Ms,
      `data preview scroll render-step budget exceeded:\n${summary}`,
    ).toBeLessThanOrEqual(SCROLL_STEP_P95_BUDGET_MS)
  })
})
