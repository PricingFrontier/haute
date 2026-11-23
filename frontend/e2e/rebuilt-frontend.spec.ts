import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

// The lazily loaded Utility panel module, as the page requests it.
const UTILITY_PANEL_CHUNK = /\/src\/panels\/UtilityPanel\.tsx/

test.describe("a lazily loaded chunk that could not be loaded", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  for (const { why, answer, title } of [
    // The page still names the chunk it was served with; the server no longer has it.
    { why: "is gone from a rebuilt server", answer: "gone", title: "Haute has been updated" },
    // The connection is away as the panel is first opened.
    { why: "got no answer", answer: "nothing", title: "Haute's server did not answer" },
  ] as const) {
    test(`offers a reload, not a dead retry, when the chunk ${why}`, async ({ page }) => {
      await page.goto("/")
      await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()

      await page.route(UTILITY_PANEL_CHUNK, (route) => (
        answer === "gone" ? route.fulfill({ status: 404, body: "Not Found" }) : route.abort("connectionrefused")
      ))
      await page.getByRole("button", { name: /^Utility$/i }).click()

      const panel = page.getByRole("complementary", { name: "Node properties" })
      await expect(panel.getByText(title)).toBeVisible()
      await expect(panel.getByRole("button", { name: "Try again" })).toHaveCount(0)

      // Reload fetches the current build from a server that answers, which has the chunk.
      await page.unroute(UTILITY_PANEL_CHUNK)
      await Promise.all([
        page.waitForEvent("load"),
        panel.getByRole("button", { name: "Reload" }).click(),
      ])
      await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
      await page.getByRole("button", { name: /^Utility$/i }).click()
      await expect(page.getByText("Utility Scripts")).toBeVisible()
    })
  }
})
