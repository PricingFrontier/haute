import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

test.describe("save conflict", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a stale save is rejected, keeps the local edit, and succeeds after reload", async ({
    context,
  }) => {
    const pageA = await context.newPage()
    await pageA.routeWebSocket(/\/ws\/sync/, () => {})
    const pageB = await context.newPage()

    await pageA.goto("/")
    await pageB.goto("/")

    const rawRowsNodeA = pageA.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNodeA).toBeVisible()
    const rawRowsNodeB = pageB.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNodeB).toBeVisible()

    // 3. Edit page A first so it becomes dirty before page B saves
    await rawRowsNodeA.click()
    const labelInputA = pageA.locator("input.node-label-input")
    await expect(labelInputA).toHaveValue("raw_rows")
    await labelInputA.fill("raw_rows_a")
    await labelInputA.press("Enter")
    await expect(pageA.getByTitle("Unsaved changes")).toBeVisible()

    // 2. On page B: rename label to raw_rows_b, save, wait for "Saved" alert
    await rawRowsNodeB.click()
    const labelInputB = pageB.locator("input.node-label-input")
    await expect(labelInputB).toHaveValue("raw_rows")
    await labelInputB.fill("raw_rows_b")
    await labelInputB.press("Enter")
    await expect(pageB.getByTitle("Unsaved changes")).toBeVisible()
    await pageB.getByRole("button", { name: "Save", exact: true }).click()
    await expect(pageB.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    // 4. On page A: click Save. Reject toast / sync banner appears, local edit survives
    await pageA.getByRole("button", { name: "Save", exact: true }).click()
    await expect(pageA.getByRole("alert").filter({ hasText: /Save rejected/ })).toBeVisible()
    await expect(
      pageA.getByText(
        "Pipeline changed on disk while you have unsaved changes. Reload the file or discard local edits first.",
        { exact: true },
      ),
    ).toBeVisible()
    await expect(labelInputA).toHaveValue("raw_rows_a")

    // 5. On page A: reload. Assert B's saved label is shown
    await pageA.reload()
    const rawRowsNodeBOnA = pageA.getByRole("button", { name: /raw_rows_b/i })
    await expect(rawRowsNodeBOnA).toBeVisible()

    // 6. On page A: rename to raw_rows_a2, save, reload and assert visible
    await rawRowsNodeBOnA.click()
    const labelInputA2 = pageA.locator("input.node-label-input")
    await expect(labelInputA2).toHaveValue("raw_rows_b")
    await labelInputA2.fill("raw_rows_a2")
    await pageA.getByRole("button", { name: "Save", exact: true }).click()
    await expect(pageA.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await pageA.reload()
    await expect(pageA.getByRole("button", { name: /raw_rows_a2/i })).toBeVisible()
  })
})
