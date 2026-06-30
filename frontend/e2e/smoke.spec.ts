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
    // default click into a ReactFlow node selection. Without a selection
    // `panelNode` stays undefined, NodePanel renders null, and the
    // classless <aside aria-label="Node properties"> wrapper collapses to a
    // zero-box element that reports `visible: hidden` — the firefox-only
    // flake this guards. Force a center click so selection fires
    // deterministically across browsers.
    await rawRowsNode.click({ force: true })

    // Assert on the populated panel itself (PanelShell, data-testid
    // "node-panel") rather than the zero-box aria wrapper: it only mounts
    // once a node is selected, so a visible "node-panel" proves the
    // selection landed, and a failure reads as "panel didn't populate"
    // instead of the misleading "wrapper is hidden".
    await expect(page.getByTestId("node-panel")).toBeVisible()
    await expect(page.getByTestId("node-panel-label-input")).toHaveValue("raw_rows")
  })
})
