import { expect, test, type Locator, type Page, type Route } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

async function openApp(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  const response = await responsePromise
  expect(response.status(), "initial pipeline request succeeds").toBe(200)
}

async function connect(page: Page, source: Locator, target: Locator): Promise<void> {
  await page.getByRole("button", { name: "Layout", exact: true }).click()
  await page.getByTestId("toolbar-centre").click()
  await expect(source).toBeVisible()
  await expect(target).toBeVisible()
  await source.hover({ timeout: 10_000 })
  await page.mouse.down()
  await target.hover({ timeout: 10_000 })
  await page.mouse.up()
}

async function save(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await responsePromise).status(), "pipeline save succeeds").toBe(200)
}

test.describe.configure({ mode: "serial" })

test.describe("Explore cached field pivot journey", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("uses cached Explore schema and formats a Pivot before its ordinary preview resolves", async ({ page }) => {
    test.slow()
    await openApp(page)

    const canvas = page.locator(".react-flow")
    await expect(canvas).toBeVisible()
    // The canvas centre can be occupied after fit-to-view, so drop into a
    // deterministic open corner rather than relying on dragTo's centre point.
    await page.getByTestId("node-palette-item-explore").dragTo(canvas, {
      targetPosition: { x: 250, y: 150 },
    })
    const exploreNode = page.getByLabel(/Explore node: Explore/i)
    await expect(exploreNode).toBeVisible()
    await exploreNode.click()

    const label = page.getByTestId("node-panel-label-input")
    await label.fill("browser_explore")
    await label.press("Enter")
    await expect(label).toHaveValue("browser_explore")

    const namedExploreNode = page.getByLabel(/Explore node: browser_explore/i)
    await expect(namedExploreNode).toBeVisible()
    await page.getByTestId("node-panel-close").click()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      namedExploreNode.getByTestId("input-connector[0]:browser_explore"),
    )

    await namedExploreNode.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    await page.getByRole("tab", { name: "Polars Code", exact: true }).click()
    const editor = page.locator(".cm-content")
    await expect(editor).toBeVisible()
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type('df = df.with_columns((pl.col("value") * 2000).alias("derived_value"))')
    await expect(editor).toContainText("derived_value")

    await save(page)
    // Saving normalises newly-authored function ids from their committed
    // labels. Reload once before caching so the durable family is keyed by the
    // same stable node id that subsequent application loads will request.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const persistedExplore = page.getByLabel(/Explore node: browser_explore/i)
    await persistedExplore.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    const cacheButton = page.getByTestId("explore-preview-frame").getByRole("button", {
      name: /Needs caching|Re-cache/,
    })
    await expect(cacheButton).toBeEnabled()
    await cacheButton.click()
    await expect(page.getByTestId("explore-preview-frame")).toContainText("Cached", { timeout: 60_000 })

    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const reloadedExplore = page.getByLabel(/Explore node: browser_explore/i)
    const reloadedExploreId = await page.locator(".react-flow__node").filter({ has: reloadedExplore }).getAttribute("data-id")
    if (!reloadedExploreId) throw new Error("Reloaded Explore node did not expose its ReactFlow id")

    let releaseHeldPreview!: () => void
    const previewReleaseGate = new Promise<void>((resolve) => {
      releaseHeldPreview = resolve
    })
    let heldPreview!: () => void
    const previewHeld = new Promise<void>((resolve) => {
      heldPreview = resolve
    })
    let heldRoute: Route | null = null
    await page.route("**/api/pipeline/preview", async (route) => {
      const body = route.request().postDataJSON() as { node_id?: string } | null
      if (body?.node_id !== reloadedExploreId || heldRoute) {
        await route.continue()
        return
      }
      heldRoute = route
      heldPreview()
      await previewReleaseGate
      try {
        await route.continue()
      } catch (error) {
        if (!(error instanceof Error) || !error.message.includes("already handled")) throw error
      }
    })

    try {
      await reloadedExplore.click()
      await previewHeld
      await page.getByTestId("node-panel").getByRole("tab", { name: "Pivots", exact: true }).click()
      await page.getByRole("button", { name: "Add Pivot", exact: true }).click()
      await page.getByRole("button", { name: "Configure Pivot 1", exact: true }).click()

      const derivedActions = page.getByRole("group", { name: "derived_value field actions" })
      await expect(derivedActions, "cached Explore schema supplies post-code fields before preview completes").toBeVisible()
      await expect.poll(() => heldRoute !== null, { message: "ordinary Explore preview remains held" }).toBe(true)

      await derivedActions.getByRole("button", { name: "Add derived_value to Columns" }).click()
      await page.getByRole("group", { name: "id field actions" }).getByRole("button", {
        name: "Add id to Rows",
      }).click()
      await page.getByRole("group", { name: "value field actions" }).getByRole("button", {
        name: "Add value to Values",
      }).click()
      await expect(page.getByRole("group", { name: "derived_value in Columns" })).toBeVisible()
      await expect(page.getByRole("group", { name: "id in Rows" })).toBeVisible()
      await expect(page.getByRole("group", { name: "value in Values" })).toBeVisible()

      const formatting = page.getByTestId("pivot-formatting-section")
      await formatting.getByRole("combobox", {
        name: "Number format for Column 1 — derived_value",
      }).selectOption("number")
      await formatting.getByRole("combobox", {
        name: "Decimal places for Column 1 — derived_value",
      }).selectOption("2")
      await formatting.getByRole("combobox", {
        name: "Number format for Row 1 — id",
      }).selectOption("percent")
      await formatting.getByRole("combobox", {
        name: "Decimal places for Row 1 — id",
      }).selectOption("0")
      await formatting.getByRole("combobox", {
        name: "Number format for Value 1 — value",
      }).selectOption("currency_gbp")
      await formatting.getByRole("combobox", {
        name: "Decimal places for Value 1 — value",
      }).selectOption("2")

      await save(page)
      await page.getByTestId("explore-preview-frame").getByRole("tab", {
        name: "Pivots",
        exact: true,
      }).click()
      const pivot = page.getByRole("region", { name: "Pivot 1" })
      await expect(pivot).toBeVisible()
      const table = pivot.getByRole("table")
      await expect(table, "real cached-data pivot calculation renders a table").toBeVisible({ timeout: 60_000 })
      await expect(table.getByRole("columnheader", { name: "22,000.00", exact: true })).toBeVisible()
      await expect(table.getByRole("rowheader", { name: "100%", exact: true })).toBeVisible()
      await expect(table.getByRole("cell", { name: "£11.00", exact: true }).first()).toBeVisible()
    } finally {
      releaseHeldPreview()
      if (heldRoute) await page.unroute("**/api/pipeline/preview")
    }
  })

  test("a chart bound to a pivot renders from real pivot results and goes stale with its pivot", async ({
    page,
  }) => {
    test.slow()
    await openApp(page)

    const canvas = page.locator(".react-flow")
    await expect(canvas).toBeVisible()
    await page.getByTestId("node-palette-item-explore").dragTo(canvas, {
      targetPosition: { x: 250, y: 150 },
    })
    const exploreNode = page.getByLabel(/Explore node: Explore/i)
    await expect(exploreNode).toBeVisible()
    await exploreNode.click()

    const label = page.getByTestId("node-panel-label-input")
    await label.fill("browser_explore")
    await label.press("Enter")
    await expect(label).toHaveValue("browser_explore")

    const namedExploreNode = page.getByLabel(/Explore node: browser_explore/i)
    await expect(namedExploreNode).toBeVisible()
    await page.getByTestId("node-panel-close").click()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      namedExploreNode.getByTestId("input-connector[0]:browser_explore"),
    )

    await namedExploreNode.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    await page.getByRole("tab", { name: "Polars Code", exact: true }).click()
    const editor = page.locator(".cm-content")
    await expect(editor).toBeVisible()
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type('df = df.with_columns((pl.col("value") * 2000).alias("derived_value"))')
    await expect(editor).toContainText("derived_value")

    await save(page)
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const persistedExplore = page.getByLabel(/Explore node: browser_explore/i)
    await persistedExplore.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    const cacheButton = page.getByTestId("explore-preview-frame").getByRole("button", {
      name: /Needs caching|Re-cache/,
    })
    await expect(cacheButton).toBeEnabled()
    await cacheButton.click()
    await expect(page.getByTestId("explore-preview-frame")).toContainText("Cached", { timeout: 60_000 })

    await page.getByTestId("node-panel").getByRole("tab", { name: "Pivots", exact: true }).click()
    await page.getByRole("button", { name: "Add Pivot", exact: true }).click()
    await page.getByRole("button", { name: "Configure Pivot 1", exact: true }).click()

    const derivedActions = page.getByRole("group", { name: "derived_value field actions" })
    await expect(derivedActions).toBeVisible()
    await derivedActions.getByRole("button", { name: "Add derived_value to Columns" }).click()
    await page.getByRole("group", { name: "id field actions" }).getByRole("button", {
      name: "Add id to Rows",
    }).click()
    await page.getByRole("group", { name: "value field actions" }).getByRole("button", {
      name: "Add value to Values",
    }).click()

    await save(page)
    await page.getByTestId("explore-preview-frame").getByRole("tab", {
      name: "Pivots",
      exact: true,
    }).click()
    const pivot = page.getByRole("region", { name: "Pivot 1" })
    await expect(pivot).toBeVisible()
    const table = pivot.getByRole("table")
    await expect(table, "real cached-data pivot calculation renders a table").toBeVisible({ timeout: 60_000 })

    // Open Charts tab in editor and add a chart bound to Pivot 1 with one series
    await page.getByTestId("node-panel").getByRole("tab", { name: "Charts", exact: true }).click()
    await page.getByRole("button", { name: "Add Chart", exact: true }).click()
    await page.getByRole("button", { name: "Configure Chart 1", exact: true }).click()

    const sourceSelect = page.getByRole("combobox", { name: "Source pivot" })
    await sourceSelect.selectOption({ label: "Pivot 1" })
    await save(page)

    // Open Charts tab in preview frame; assert chart element renders (not a placeholder)
    await page.getByTestId("explore-preview-frame").getByRole("tab", {
      name: "Charts",
      exact: true,
    }).click()
    const chartCard = page.getByTestId("explore-chart-visualisation")
    await expect(chartCard).toBeVisible()
    const chartElement = chartCard.locator("canvas, svg")
    await expect(chartElement.first()).toBeVisible({ timeout: 60_000 })
    await expect(chartCard.getByText(/Select a source Pivot/i)).toHaveCount(0)

    // Change the pivot (remove a row field)
    await page.getByTestId("node-panel").getByRole("tab", { name: "Pivots", exact: true }).click()
    const configPivotButton = page.getByRole("button", { name: "Configure Pivot 1", exact: true })
    if (await configPivotButton.isVisible()) await configPivotButton.click()
    await page.getByRole("button", { name: "Remove id from Rows" }).click()

    // Assert the chart card shows the stale indicator/text
    await expect(chartCard.getByText(/Updating source Pivot automatically|Calculating Pivot/i)).toBeVisible()

    // After the pivot recalculates, the chart re-renders (no stale text)
    await expect(chartCard.getByText(/Updating source Pivot automatically|Calculating Pivot/i)).toHaveCount(0, {
      timeout: 60_000,
    })
    await expect(chartElement.first()).toBeVisible()
  })

  test("a failed re-cache keeps the previous report visible as stale and reports the failure", async ({
    page,
  }) => {
    test.slow()
    await openApp(page)

    const canvas = page.locator(".react-flow")
    await expect(canvas).toBeVisible()
    await page.getByTestId("node-palette-item-explore").dragTo(canvas, {
      targetPosition: { x: 250, y: 150 },
    })
    const exploreNode = page.getByLabel(/Explore node: Explore/i)
    await expect(exploreNode).toBeVisible()
    await exploreNode.click()

    const label = page.getByTestId("node-panel-label-input")
    await label.fill("browser_explore")
    await label.press("Enter")
    await expect(label).toHaveValue("browser_explore")

    const namedExploreNode = page.getByLabel(/Explore node: browser_explore/i)
    await expect(namedExploreNode).toBeVisible()
    await page.getByTestId("node-panel-close").click()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      namedExploreNode.getByTestId("input-connector[0]:browser_explore"),
    )

    await namedExploreNode.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    await page.getByRole("tab", { name: "Polars Code", exact: true }).click()
    const editor = page.locator(".cm-content")
    await expect(editor).toBeVisible()
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type('df = df.with_columns((pl.col("value") * 2000).alias("derived_value"))')
    await expect(editor).toContainText("derived_value")

    await save(page)
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const persistedExplore = page.getByLabel(/Explore node: browser_explore/i)
    await persistedExplore.click()
    await expect(page.getByTestId("node-panel")).toBeVisible()
    const cacheButton = page.getByTestId("explore-preview-frame").getByRole("button", {
      name: /Needs caching|Re-cache/,
    })
    await expect(cacheButton).toBeEnabled()
    await cacheButton.click()
    await expect
      .poll(() => cacheButton.evaluate((el) => el.style.background))
      .toContain("var(--success-fill)")

    // Edit Explore Polars code to raise
    await page.getByTestId("node-panel").getByRole("tab", { name: "Polars Code", exact: true }).click()
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type('raise ValueError("boom")')
    await expect(editor).toContainText("raise ValueError")

    // Trigger Re-cache
    const reCacheButton = page.getByTestId("explore-preview-frame").getByRole("button", {
      name: /Re-cache|Needs caching/,
    })
    await expect(reCacheButton).toBeEnabled()
    await reCacheButton.click()

    // Assert: job ends in error state visible in the panel, no traceback or file path in message
    const failureAlert = page.getByRole("alert").filter({ hasText: /Explore failed/i })
    await expect(failureAlert).toBeVisible({ timeout: 60_000 })
    const failureMessage = (await failureAlert.textContent()) ?? ""
    expect(failureMessage).not.toMatch(/traceback/i)
    expect(failureMessage).not.toMatch(/\.py/i)
    expect(failureMessage).not.toMatch(/File\s+"/i)

    // Previous report marked stale (yellow/stale indicator)
    await expect
      .poll(() => reCacheButton.evaluate((el) => el.style.background))
      .toContain("var(--warning-strong)")
    await expect(page.getByTestId("explore-preview-frame")).toContainText(/cache stale/i)

    // Restore code and Re-cache: report is fresh again
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.type('df = df.with_columns((pl.col("value") * 2000).alias("derived_value"))')
    await expect(editor).toContainText("derived_value")

    await reCacheButton.click()
    await expect(page.getByTestId("explore-preview-frame")).toContainText("Cached", { timeout: 60_000 })
    const freshButton = page.getByTestId("explore-preview-frame").getByRole("button", {
      name: /Re-cache|Needs caching/,
    })
    await expect
      .poll(() => freshButton.evaluate((el) => el.style.background))
      .toContain("var(--success-fill)")
  })
})
