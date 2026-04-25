import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

test.describe("cross-browser smoke", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@smoke app shell and node inspector stay reachable", async ({ page }) => {
    await page.goto("/")

    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await expect(page.getByTitle("Data source")).toBeVisible()
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible()

    const rawRowsNode = page.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNode).toBeVisible()
    await rawRowsNode.click()

    await expect(page.getByLabel("Node properties")).toBeVisible()
    await expect(page.locator("input.node-label-input")).toHaveValue("raw_rows")
  })
})
