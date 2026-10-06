import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

// The lazily loaded Utility panel module, as the page requests it.
const UTILITY_PANEL_CHUNK = /\/src\/panels\/UtilityPanel\.tsx/

test.describe("a page served before a rebuild", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("offers a reload, not a dead retry, when a lazily loaded chunk is gone", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

    // The page still names the chunk it was served with; the server no longer has it.
    await page.route(UTILITY_PANEL_CHUNK, (route) => route.abort())
    await page.getByRole("button", { name: /^Utility$/i }).click()

    const panel = page.getByRole("complementary", { name: "Node properties" })
    await expect(panel.getByText("Haute has been updated")).toBeVisible()
    await expect(panel.getByRole("button", { name: "Try again" })).toHaveCount(0)

    // Reload fetches the current build, which has the chunk.
    await page.unroute(UTILITY_PANEL_CHUNK)
    await Promise.all([
      page.waitForEvent("load"),
      panel.getByRole("button", { name: "Reload" }).click(),
    ])
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByRole("button", { name: /^Utility$/i }).click()
    await expect(page.getByText("Utility Scripts")).toBeVisible()
  })
})
