import { expect, test, type Locator, type Page } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

const PREVIEW_ROW_COUNT = 30

function columnName(index: number): string {
  return `col_${index}`
}

/**
 * Answers every preview with `columnCount()` columns, as a Polars node does
 * when each Refresh follows an edit that adds a column.
 */
async function routePreviewColumns(page: Page, columnCount: () => number): Promise<void> {
  await page.route("**/api/pipeline/preview", async (route) => {
    const body = route.request().postDataJSON() as { node_id?: unknown }
    if (typeof body.node_id !== "string" || body.node_id.length === 0) {
      throw new Error(`Preview route expected a string node_id, received ${String(body.node_id)}`)
    }
    const columns = Array.from({ length: columnCount() }, (_, index) => ({
      name: columnName(index),
      dtype: "Int64",
    }))
    const preview = Array.from({ length: PREVIEW_ROW_COUNT }, (_, row) =>
      Object.fromEntries(columns.map((column, index) => [column.name, row * 1_000 + index])),
    )
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        status: "ok",
        node_id: body.node_id,
        row_count: PREVIEW_ROW_COUNT,
        column_count: columns.length,
        columns,
        available_columns: columns,
        preview,
        preview_row_count: PREVIEW_ROW_COUNT,
        preview_row_limit: PREVIEW_ROW_COUNT,
        preview_truncated: false,
        error: null,
        timings: [],
        memory: [],
        schema_warnings: [],
        node_statuses: { [body.node_id]: "ok" },
      }),
    })
  })
}

function distanceFromRightEdge(scroll: Locator): Promise<number> {
  return scroll.evaluate((el) => el.scrollWidth - el.clientWidth - el.scrollLeft)
}

test.describe("data preview scroll place", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a preview scrolled fully right stays fully right as each Refresh adds a column", async ({ page }) => {
    let columnCount = 40
    await routePreviewColumns(page, () => columnCount)
    await page.goto("/")
    await page.getByRole("button", { name: /enriched/i }).click()
    const scroll = page.getByTestId("data-preview-scroll")
    await expect(scroll.getByText(columnName(0), { exact: true })).toBeVisible()

    await scroll.hover()
    await page.mouse.wheel(100_000, 0)
    await expect.poll(() => distanceFromRightEdge(scroll)).toBeLessThan(1)
    await expect(scroll.getByText(columnName(39), { exact: true })).toBeInViewport()

    // A second Refresh shows that putting the table back did not count as
    // the user moving it.
    for (const added of [40, 41]) {
      columnCount = added + 1
      await page.getByRole("button", { name: "Refresh", exact: true }).click()
      await expect(scroll.getByText(columnName(added), { exact: true })).toBeInViewport()
      expect(await distanceFromRightEdge(scroll)).toBeLessThan(1)
    }
  })
})
