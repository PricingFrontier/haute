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
    // Firefox's pointer/selection timing doesn't reliably translate a
    // single click into a ReactFlow node selection — even a forced center
    // click can land between renders and never register, in which case
    // `panelNode` stays undefined and NodePanel renders null (observed
    // firefox-only CI flake, ~every other run). Retry the click+mount
    // check as ONE unit until the selection actually lands: a missed
    // click leaves no state behind, so re-clicking is idempotent.
    //
    // Assert on the populated panel itself (PanelShell, data-testid
    // "node-panel") rather than the zero-box aria wrapper: it only mounts
    // once a node is selected, so a visible "node-panel" proves the
    // selection landed, and a failure reads as "panel didn't populate"
    // instead of the misleading "wrapper is hidden".
    await expect(async () => {
      await rawRowsNode.click({ force: true })
      await expect(page.getByTestId("node-panel")).toBeVisible({ timeout: 2_000 })
    }).toPass({ timeout: 15_000 })
    await expect(page.getByTestId("node-panel-label-input")).toHaveValue("raw_rows")
  })
})
