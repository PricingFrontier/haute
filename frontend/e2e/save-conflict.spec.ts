import { expect, test, type Page, type Route } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

/** Rename the node now labelled `from` to `to`, on the canvas. */
async function rename(page: Page, from: string, to: string): Promise<void> {
  await page.getByTestId(`node-${from}`).click()
  const label = page.locator("input.node-label-input")
  await expect(label).toHaveValue(from)
  await label.fill(to)
  await label.press("Enter")
  await expect(page.getByTestId(`node-${to}`)).toBeVisible()
}

/**
 * Let the server write each save, and answer the browser as `lose` says for the first
 * `times`; the operation key of each save sent, in order.
 */
async function loseSaveAnswers(page: Page, times: number, lose: (route: Route) => Promise<void>): Promise<string[]> {
  const operations: string[] = []
  let left = times
  await page.route("**/api/pipeline/save", async (route) => {
    operations.push((route.request().postDataJSON() as { operation: string }).operation)
    if (left === 0) {
      await route.continue()
      return
    }
    left -= 1
    // The save reaches the server and is written: only its answer is lost.
    await route.fetch()
    await lose(route)
  })
  return operations
}

const STALE_BANNER = "Pipeline changed on disk while you have unsaved changes. Reload the file or discard local edits first."

test.describe("save conflict", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  for (const [name, times, lose] of [
    ["dropped twice", 2, (route: Route) => route.abort("failed")],
    ["answered by a gateway's 504 page", 1, (route: Route) => route.fulfill({ status: 504, contentType: "text/html", body: "<html><body><h1>504 Gateway Timeout</h1></body></html>" })],
    ["answered 500", 1, (route: Route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "Operation failed. Check the server logs for details." }) })],
  ] as const) {
    test(`a save whose answer was ${name} lands at the next Save with the work typed since`, async ({ page }) => {
      await page.goto("/")
      await expect(page.getByRole("button", { name: /raw_rows/i })).toBeVisible()
      const sent = await loseSaveAnswers(page, times, lose)
      const save = page.getByRole("button", { name: "Save", exact: true })

      await rename(page, "raw_rows", "raw_rows_lost")
      await save.click()
      await expect(page.getByRole("alert").filter({ hasText: /Failed to save pipeline/ }).first()).toBeVisible()

      await rename(page, "enriched", "enriched_since")
      for (let lost = 1; lost < times; lost += 1) {
        // The held save is sent again first, and its answer is lost again: nothing newer goes.
        await save.click()
        await expect.poll(() => sent.length).toBe(lost + 1)
      }
      await save.click()
      await expect(page.getByRole("alert").filter({ hasText: /Saved/ }).first()).toBeVisible()
      // Each Save sent the held save first, under its key, and then the work typed since.
      expect(sent).toEqual([...Array<string>(times + 1).fill(sent[0]), expect.not.stringMatching(sent[0])])
      await expect(page.getByText(STALE_BANNER, { exact: true })).toHaveCount(0)
      await expect(page.getByText("READ-ONLY")).toHaveCount(0)
      await expect(save).toBeEnabled()

      await page.unroute("**/api/pipeline/save")
      await page.reload()
      await expect(page.getByTestId("node-raw_rows_lost")).toBeVisible()
      await expect(page.getByTestId("node-enriched_since")).toBeVisible()
    })
  }

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
    // The rename has landed on page A's canvas, so page A now holds unsaved work.
    await expect(pageA.getByTestId("node-raw_rows_a")).toBeVisible()

    // 2. On page B: rename label to raw_rows_b, save, wait for "Saved" alert
    await rawRowsNodeB.click()
    const labelInputB = pageB.locator("input.node-label-input")
    await expect(labelInputB).toHaveValue("raw_rows")
    await labelInputB.fill("raw_rows_b")
    await labelInputB.press("Enter")
    await expect(pageB.getByTestId("node-raw_rows_b")).toBeVisible()
    await pageB.getByRole("button", { name: "Save", exact: true }).click()
    await expect(pageB.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    // 4. On page A: click Save. Reject toast / sync banner appears, local edit survives
    await pageA.getByRole("button", { name: "Save", exact: true }).click()
    await expect(pageA.getByRole("alert").filter({ hasText: /Save rejected/ })).toBeVisible()
    // The banner, whose Reload file sits beside it; the node panel gives the same reason.
    const banner = pageA.getByRole("button", { name: "Reload file" }).locator("xpath=..")
    await expect(banner.getByText(STALE_BANNER, { exact: true })).toBeVisible()
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
