import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

test.describe("pipeline settings pane", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("scrolls down to the last cached-data row in a short window", async ({ page }) => {
    // Short enough that the settings above the inventory fill the pane: the
    // inventory must scroll into view, never be squeezed and cut short.
    await page.setViewportSize({ width: 1280, height: 640 })
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByTestId("toolbar-pipeline-settings").click()

    const list = page.getByTestId("cache-node-list")
    const rows = list.locator('div[data-testid^="cache-node-"]')
    await expect(rows.first()).toBeAttached()
    expect(await rows.count()).toBeGreaterThan(3)

    // The list is as tall as its rows: nothing in it is clipped.
    expect(await list.evaluate((element) => element.scrollHeight - element.clientHeight)).toBe(0)

    const last = rows.last()
    await last.scrollIntoViewIfNeeded()
    await expect(last).toBeInViewport({ ratio: 1 })
  })
})
