import { existsSync, readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const ratingConfigPath = resolve(
  ratingDir,
  "config",
  "rating_step",
  "browser_rating.json",
)
const optimiserConfigPath = resolve(
  ratingDir,
  "config",
  "optimisation",
  "browser_optimiser.json",
)
const optimiserArtifactApplyPath = "output/optimiser_browser_optimiser_browser_optimiser.json"
const optimiserArtifactPath = resolve(e2eProjectRoot, optimiserArtifactApplyPath)
const desktopViewport = { width: 1440, height: 900 }
const narrowViewport = { width: 1024, height: 768 }
const mixedBandingDesktopSnapshot = process.platform === "linux"
  ? "mixed-banding-desktop-1440x900-linux.png"
  : "mixed-banding-desktop-1440x900.png"
const mixedBandingNarrowSnapshot = process.platform === "linux"
  ? "mixed-banding-narrow-1024x768-linux.png"
  : "mixed-banding-narrow-1024x768.png"
const rebuiltRatingNarrowSnapshot = process.platform === "linux"
  ? "rebuilt-three-factor-rating-narrow-1024x768-linux.png"
  : "rebuilt-three-factor-rating-narrow-1024x768.png"
const rebuiltRatingDesktopSnapshot = process.platform === "linux"
  ? "rebuilt-three-factor-rating-desktop-1440x900-linux.png"
  : "rebuilt-three-factor-rating-desktop-1440x900.png"
const selectedOptimiserDesktopSnapshot = process.platform === "linux"
  ? "selected-optimiser-point-desktop-1440x900-linux.png"
  : "selected-optimiser-point-desktop-1440x900.png"
const selectedOptimiserNarrowSnapshot = process.platform === "linux"
  ? "selected-optimiser-point-narrow-1024x768-linux.png"
  : "selected-optimiser-point-narrow-1024x768.png"
const saveShortcut = process.platform === "darwin" ? "Meta+s" : "Control+s"

type JsonObject = Record<string, unknown>

function readJson(path: string): JsonObject {
  return JSON.parse(readFileSync(path, "utf8")) as JsonObject
}

function numericLeaves(value: unknown): number[] {
  if (typeof value === "number") return [value]
  if (Array.isArray(value)) return value.flatMap(numericLeaves)
  if (value && typeof value === "object") {
    return Object.values(value as JsonObject).flatMap(numericLeaves)
  }
  return []
}

async function expectCanvasScreenshot(
  locator: Locator,
  name: string,
): Promise<void> {
  // Keep the journey running so CI captures every viewport mismatch. Each
  // mismatch still fails the test; functional assertions remain immediate.
  await expect.soft(locator).toHaveScreenshot(name, {
    animations: "disabled",
    caret: "hide",
    maxDiffPixelRatio: 0.02,
  })
}

async function stabiliseCanvasScreenshot(page: Page): Promise<void> {
  const dismissButtons = page.getByRole("button", {
    name: "Dismiss notification",
  })
  while (await dismissButtons.count() > 0) {
    await dismissButtons.first().click()
  }
  await expect(dismissButtons).toHaveCount(0)
  await page.evaluate(() => {
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur()
    }
  })
}

async function openNodeProperties(page: Page, accessibleName: string): Promise<Locator> {
  const node = page.getByLabel(accessibleName)
  await expect(node).toBeVisible()
  await node.click()
  const panel = page.getByRole("complementary", { name: /node properties/i })
  await expect(panel).toBeVisible()
  await expect(panel.locator("input.node-label-input")).toHaveValue(
    accessibleName.slice(accessibleName.lastIndexOf(": ") + 2),
  )
  return panel
}

async function openResultPane(page: Page, name: string): Promise<void> {
  const tab = page.getByRole("tablist", { name: "Optimiser result panes" })
    .getByRole("tab", { name, exact: true })
  await tab.click()
  await expect(tab).toHaveAttribute("aria-selected", "true")
}

type AttainmentRow = Record<string, string>

/** The one "Constraint attainment" table inside *scope*, keyed by its headers. */
async function attainmentRows(scope: Locator): Promise<{ headers: string[]; rows: AttainmentRow[] }> {
  const table = scope.getByRole("table", { name: "Constraint attainment" })
  await expect(table).toBeVisible()
  // Text content, not inner text: the headers are upper-cased by CSS only.
  const headers = (await table.locator("thead th").allTextContents()).map((text) => text.trim())
  const rows = await table.locator("tbody tr").evaluateAll((elements) => elements.map((row) => (
    Array.from(row.children).map((cell) => (cell.textContent ?? "").trim())
  )))
  return {
    headers,
    rows: rows.map((cells) => Object.fromEntries(headers.map((header, index) => [header, cells[index]]))),
  }
}

function parseDisplayed(text: string): number {
  const value = Number(text.replace(/,/g, ""))
  expect(Number.isFinite(value), `"${text}" is a displayed number`).toBe(true)
  return value
}

/** Bound, achieved, signed slack and status of one row tell one story. */
function expectAttainmentRowConsistent(row: AttainmentRow): void {
  const bound = parseDisplayed(row.Bound)
  const achieved = parseDisplayed(row.Achieved)
  const slackMatch = /^([+-]?[\d,.]+) \(([+-]?[\d.]+)%\)$/.exec(row.Slack)
  expect(slackMatch, `slack "${row.Slack}" reads "value (pct%)"`).not.toBeNull()
  const slack = parseDisplayed(slackMatch![1])
  // Values are shown to four decimals, so the difference carries their rounding.
  expect(Math.abs(slack - (achieved - bound))).toBeLessThanOrEqual(2e-4)
  expect(row.Status).toBe(slackMatch![1].startsWith("-") ? "Breached" : "Met")
  parseDisplayed(row["λ (multiplier)"])
}

test.describe.configure({ mode: "serial" })

test.describe("frontend canvas assurance", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("keeps the toolbar and canvas within the viewport", async ({ page }) => {
    await page.goto("/")
    // The MLflow destination is a per-node choice, so the toolbar carries no
    // MLflow control; its settings modal opens from each node's gear.
    await expect(page.getByTestId("toolbar-centre")).toBeVisible()
    await expect(page.getByTestId("toolbar-mlflow-chip")).toHaveCount(0)
    for (const width of [1440, 1280, 1024]) {
      await page.setViewportSize({ width, height: 900 })
      await expect.poll(() => page.evaluate(() => (
        document.documentElement.scrollWidth <= document.documentElement.clientWidth
      ))).toBe(true)
      await expect(page.getByTestId("toolbar-centre")).toBeInViewport()
      await expect(page.getByRole("button", { name: "Save", exact: true })).toBeInViewport()
      expect(await page.evaluate(() => window.scrollX)).toBe(0)
    }
  })

  test("fits every node of the loaded pipeline inside the canvas", async ({ page }) => {
    await page.goto("/")
    const canvas = page.locator(".react-flow")
    const nodes = page.locator(".react-flow__node")
    await expect(nodes.first()).toBeVisible()
    expect(await nodes.count()).toBeGreaterThan(1)

    await expect.poll(async () => {
      const canvasBox = await canvas.boundingBox()
      if (!canvasBox) return false
      const nodeBoxes = await nodes.evaluateAll((elements) => elements.map((element) => {
        const box = element.getBoundingClientRect()
        return { x: box.x, y: box.y, right: box.right, bottom: box.bottom }
      }))
      return nodeBoxes.every((box) => box.x >= canvasBox.x
        && box.y >= canvasBox.y
        && box.right <= canvasBox.x + canvasBox.width
        && box.bottom <= canvasBox.y + canvasBox.height)
    }).toBe(true)
  })

  test("discovers mixed Banding factors and rebuilds, edits, and reloads a three-factor Rating table by keyboard", async ({
    page,
  }) => {
    await page.setViewportSize(desktopViewport)
    await page.goto("/")

    const bandingPanel = await openNodeProperties(
      page,
      "Banding node: browser_mixed_banding",
    )
    const bandingColumns = bandingPanel.getByRole("group", {
      name: "Banding columns",
    })
    // A row's name is its output column then its state; anchored, so a
    // row's own "Remove … column" button does not match too.
    const ageRow = bandingColumns.getByRole("button", { name: /^proposer_age_band (complete|incomplete)$/ })
    const channelRow = bandingColumns.getByRole("button", { name: /^channel_band (complete|incomplete)$/ })
    const vehicleRow = bandingColumns.getByRole("button", { name: /^vehicle_age_band (complete|incomplete)$/ })
    await expect(ageRow).toBeVisible()
    await expect(channelRow).toBeVisible()
    await expect(vehicleRow).toBeVisible()

    await channelRow.click()
    await expect(
      bandingPanel.getByRole("radio", { name: "Categorical", exact: true }),
    ).toBeChecked()
    await vehicleRow.click()
    await expect(
      bandingPanel.getByRole("radio", { name: "Numeric", exact: true }),
    ).toBeChecked()
    await expect(bandingPanel.getByLabel("Output Column")).toHaveValue(
      "vehicle_age_band",
    )
    await expect(page.getByTitle("Unsaved changes", { exact: true })).toHaveCount(0)
    // Banding shows numbers only for the whole dataset, so its data is cached
    // first; until then it says so rather than counting the preview.
    await expect(bandingPanel.getByText("Not cached · Refresh this node to count all rows")).toBeVisible()
    await page.getByRole("button", { name: "Refresh", exact: true }).click()
    await expect(bandingPanel.getByText(/^All rows · /)).toBeVisible({ timeout: 60_000 })
    await expect(bandingPanel.getByRole("img", { name: "Distribution histogram" })).toBeVisible()
    await expect(bandingPanel.getByRole("combobox", { name: "Input Column", exact: true })).toHaveValue("vehicle_age")
    await stabiliseCanvasScreenshot(page)

    await expectCanvasScreenshot(
      bandingPanel,
      mixedBandingDesktopSnapshot,
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      bandingPanel,
      mixedBandingNarrowSnapshot,
    )
    await page.setViewportSize(desktopViewport)

    const ratingPanel = await openNodeProperties(
      page,
      "Rating Step node: browser_rating",
    )
    await expect(
      ratingPanel.getByRole("combobox", { name: "Factor 1", exact: true }),
    ).toHaveValue("proposer_age_band")
    await expect(
      ratingPanel.getByRole("combobox", { name: "Factor 2", exact: true }),
    ).toHaveValue("channel_band")
    await expect(
      ratingPanel.getByRole("combobox", { name: "Factor 3", exact: true }),
    ).toHaveValue("vehicle_age_band")

    const rebuildButton = ratingPanel.getByRole("button", {
      name: /Rebuild from factor levels/i,
    })
    await rebuildButton.focus()
    await expect(rebuildButton).toBeFocused()
    await rebuildButton.press("Enter")
    await expect(ratingPanel.getByText(/8 entries/)).toBeVisible()

    const relativity = ratingPanel.getByLabel(
      "Relativity for proposer_age_band Age 40 or below and channel_band Direct",
    )
    await relativity.fill("1.23")
    await relativity.press("Tab")
    await expect(relativity).toHaveValue("1.23")
    await stabiliseCanvasScreenshot(page)

    await expectCanvasScreenshot(
      ratingPanel,
      rebuiltRatingDesktopSnapshot,
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      ratingPanel,
      rebuiltRatingNarrowSnapshot,
    )
    await page.setViewportSize(desktopViewport)

    await page.keyboard.press(saveShortcut)
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect.poll(() => {
      const config = readJson(ratingConfigPath)
      const tables = config.tables as JsonObject[]
      return numericLeaves(tables[0]?.entries)
    }).toEqual(expect.arrayContaining([1.23]))
    expect(numericLeaves((readJson(ratingConfigPath).tables as JsonObject[])[0].entries))
      .toHaveLength(8)

    await page.reload()
    const reloadedRatingPanel = await openNodeProperties(
      page,
      "Rating Step node: browser_rating",
    )
    await expect(
      reloadedRatingPanel.getByLabel(
        "Relativity for proposer_age_band Age 40 or below and channel_band Direct",
      ),
    ).toHaveValue("1.23")
    await expect(reloadedRatingPanel.getByText(/8 entries/)).toBeVisible()
  })

  test("persists optimiser ranges and preserves selected-point identity across local and intercepted MLflow exports", async ({
    page,
  }) => {
    test.slow()
    await page.setViewportSize(desktopViewport)

    let mlflowLogRequest: JsonObject | null = null
    await page.route("**/api/mlflow/status", async (route) => {
      await route.fulfill({
        json: {
          mlflow_installed: true,
          mlflow_importable: true,
          configured: true,
          mode: "local",
          destination: "mlruns",
          config_source: "default",
          detail: "Deterministic Playwright boundary",
        },
      })
    })
    await page.route("**/api/optimiser/mlflow/log", async (route) => {
      mlflowLogRequest = route.request().postDataJSON() as JsonObject
      await route.fulfill({
        json: {
          status: "ok",
          backend: "browser-contract",
          experiment_name: "canvas-e2e",
          run_id: "run-frontier-point-2",
          run_url: "https://mlflow.invalid/runs/run-frontier-point-2",
          tracking_uri: "intercepted://playwright",
          error: null,
        },
      })
    })

    await page.goto("/")
    let optimiserPanel = await openNodeProperties(
      page,
      "Optimisation node: browser_optimiser",
    )
    await optimiserPanel.getByRole("tab", { name: "Constraints", exact: true }).click()
    await optimiserPanel.getByRole("button", {
      name: "Individual point",
      exact: true,
    }).click()
    const constraintValue = optimiserPanel.getByRole("spinbutton", {
      name: "volume constraint value",
    })
    await constraintValue.fill("8.4")
    await expect(constraintValue).toHaveValue("8.4")
    await optimiserPanel.getByRole("button", {
      name: "Efficient frontier",
      exact: true,
    }).click()
    const minRange = optimiserPanel.getByLabel("volume min value")
    const maxRange = optimiserPanel.getByLabel("volume max value")
    await minRange.fill("7.8")
    await maxRange.fill("9.2")
    await expect(minRange).toHaveValue("7.8")
    await expect(maxRange).toHaveValue("9.2")

    await page.keyboard.press(saveShortcut)
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect.poll(() => {
      const config = readJson(optimiserConfigPath)
      return {
        constraint: (config.constraints as JsonObject).volume,
        range: (config.frontier_ranges as JsonObject).volume,
      }
    }).toEqual({
      constraint: { min: 8.4 },
      range: { min: 7.8, max: 9.2 },
    })

    await page.reload()
    optimiserPanel = await openNodeProperties(
      page,
      "Optimisation node: browser_optimiser",
    )
    await optimiserPanel.getByRole("tab", { name: "Constraints", exact: true }).click()
    await optimiserPanel.getByRole("button", {
      name: "Individual point",
      exact: true,
    }).click()
    await expect(optimiserPanel.getByRole("spinbutton", {
      name: "volume constraint value",
    })).toHaveValue("8.4")
    await optimiserPanel.getByRole("button", {
      name: "Efficient frontier",
      exact: true,
    }).click()
    await expect(optimiserPanel.getByLabel("volume min value")).toHaveValue("7.8")
    await expect(optimiserPanel.getByLabel("volume max value")).toHaveValue("9.2")

    const solveResponsePromise = page.waitForResponse(response => (
      response.url().endsWith("/api/optimiser/solve")
      && response.request().method() === "POST"
    ))
    await optimiserPanel.getByRole("tab", { name: "Solve", exact: true }).click()
    await optimiserPanel.getByRole("button", {
      name: "Optimise",
      exact: true,
    }).click()
    const solveResponse = await solveResponsePromise
    const solveBody = await solveResponse.json() as JsonObject
    expect(typeof solveBody.job_id).toBe("string")
    const completedStatus = await page.waitForResponse(async (response) => {
      if (!response.url().endsWith(`/api/optimiser/solve/status/${solveBody.job_id as string}`)) {
        return false
      }
      return ((await response.json()) as JsonObject).status === "completed"
    }, { timeout: 120_000 })
    const solved = ((await completedStatus.json()) as JsonObject).result as JsonObject
    const resultTabs = page.getByRole("tablist", {
      name: "Optimiser result panes",
    })
    await expect(
      resultTabs.getByRole("tab", { name: "Frontier", exact: true }),
    ).toBeVisible({ timeout: 120_000 })
    // The Solve pane reports the solve itself, not the frontier point the
    // preview opens on.
    await expect(optimiserPanel.getByText(
      `${solved.converged ? "Converged" : "Did not converge"} in ${solved.iterations as number} iterations `
      + "(8 quotes, 5 steps)",
      { exact: true },
    )).toBeVisible()

    // The workspace offers every online pane (no Rates: that is ratebook's),
    // and the provenance strip says what the figures are: a frontier solve
    // opens on its first point.
    const optimiserPreview = page.getByTestId("optimiser-preview-frame")
    await expect(resultTabs.getByRole("tab")).toHaveText([
      "Frontier",
      "Summary",
      "Adjustments",
      "Segments",
      "Quotes",
      "Convergence",
    ])
    await expect(resultTabs.getByRole("tab", { name: "Frontier", exact: true }))
      .toHaveAttribute("aria-selected", "true")
    const provenance = optimiserPreview.getByTestId("optimiser-provenance")
    await expect(provenance).toHaveText(new RegExp(
      "^Online · 8 quotes × 5 scenario steps · Data: batch scenario of rating/main\\.py · "
      + "Frontier point 1 of 5 · "
      + "Expected values from the scoring models on the solve quotes; not observed outcomes\\.$",
    ))

    // Summary states each constraint's attainment in words.
    await openResultPane(page, "Summary")
    const solvedAttainment = await attainmentRows(optimiserPreview)
    expect(solvedAttainment.headers).toEqual([
      "Constraint", "Kind", "Bound", "Achieved", "Slack", "Status", "λ (multiplier)",
    ])
    expect(solvedAttainment.rows).toHaveLength(1)
    expect(solvedAttainment.rows[0]).toMatchObject({
      Constraint: "volume",
      Kind: "min",
      Status: expect.stringMatching(/^(Met|Breached)$/),
    })
    expectAttainmentRowConsistent(solvedAttainment.rows[0])

    await openResultPane(page, "Frontier")
    const frontierPoint = page.getByRole("button", {
      name: /Select frontier point 2/i,
    })
    await frontierPoint.click()
    await expect(
      page.getByTestId("optimiser-preview-frame-header").getByText("Point 2 of 5", { exact: true }),
    ).toBeVisible()
    await expect(provenance).toContainText("Frontier point 2 of 5")
    await expect(
      page.getByRole("alert").filter({ hasText: /Failed to select frontier point/i }),
    ).toHaveCount(0)
    await stabiliseCanvasScreenshot(page)

    await expectCanvasScreenshot(
      optimiserPreview,
      selectedOptimiserDesktopSnapshot,
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      optimiserPreview,
      selectedOptimiserNarrowSnapshot,
    )
    await page.setViewportSize(desktopViewport)

    // The frontier detail card and Summary judge the selected point alike.
    const detailCard = optimiserPreview.locator(".optimiser-frontier-detail")
    await expect(detailCard.getByText("Point details", { exact: true })).toBeVisible()
    const detailAttainment = await attainmentRows(detailCard)
    expect(detailAttainment.rows).toHaveLength(1)
    expectAttainmentRowConsistent(detailAttainment.rows[0])
    await openResultPane(page, "Summary")
    await expect(
      optimiserPreview.getByText("Frontier point 2's adjustments load in the Adjustments tab."),
    ).toBeVisible()
    const summaryAttainment = await attainmentRows(optimiserPreview)
    expect(summaryAttainment.rows).toEqual(detailAttainment.rows)

    // Adjustments: one bar per grid value and the base-price line at 1.0.
    await openResultPane(page, "Adjustments")
    const adjustmentsChart = optimiserPreview.getByRole("img", {
      name: /Chosen scenario values histogram/,
    })
    await expect(adjustmentsChart).toBeVisible({ timeout: 60_000 })
    await expect(adjustmentsChart.getByTestId("adjustment-bar")).toHaveCount(5)
    for (const value of ["0.8", "0.9", "1", "1.1", "1.2"]) {
      await expect(
        adjustmentsChart.getByRole("button", { name: new RegExp(`^Scenario value ${value.replace(".", "\\.")}: `) }),
      ).toHaveCount(1)
    }
    // The dashed line is vertical, so it has no width to be "visible" by;
    // it must sit over the 1.0 bar instead.
    const basePriceLine = adjustmentsChart.getByTestId("base-price-line")
    await expect(basePriceLine).toBeAttached()
    const lineX = await basePriceLine.evaluate((line) => line.getBoundingClientRect().x)
    const unadjustedBar = await adjustmentsChart
      .getByRole("button", { name: /^Scenario value 1: / })
      .evaluate((bar) => {
        const box = bar.getBoundingClientRect()
        return { left: box.left, right: box.right }
      })
    expect(lineX).toBeGreaterThanOrEqual(unadjustedBar.left)
    expect(lineX).toBeLessThanOrEqual(unadjustedBar.right)
    await expect(
      optimiserPreview.getByText("1.0 = base price (no adjustment)", { exact: true }),
    ).toBeVisible()
    await expect(
      adjustmentsChart.getByText("Scenario value (1.0 = base price)", { exact: true }),
    ).toBeVisible()

    // Segments: the analysis column's levels around the 1.0 base line.
    await openResultPane(page, "Segments")
    await expect(
      optimiserPreview.getByRole("group", { name: "Keys ranked by adjustment spread" })
        .getByRole("button", { name: "region" }),
    ).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 })
    const regionSegments = optimiserPreview.getByRole("group", {
      name: "Mean chosen scenario value for region",
    })
    await expect(regionSegments).toBeVisible({ timeout: 60_000 })
    const segmentLevels = regionSegments.getByTestId("relativity-row")
    await expect(segmentLevels).toHaveCount(3)
    expect((await segmentLevels.getByTestId("relativity-label").allTextContents()).sort())
      .toEqual(["East", "North", "South"])
    // Quotes 3 and 6 are the fixture's North quotes.
    await regionSegments.getByRole("button", { name: /^North\. / }).focus()
    await expect(
      optimiserPreview.getByRole("status").filter({ hasText: /^North/ }),
    ).toContainText("Quotes: 2 (25.0%)")

    // Quotes: the "Highest adjustment" preset sorts every quote by scenario value, descending.
    await openResultPane(page, "Quotes")
    const presets = optimiserPreview.getByRole("group", { name: "Presets" })
    const highest = presets.getByRole("button", { name: "Highest adjustment", exact: true })
    await expect(highest).toHaveAttribute("aria-pressed", "false", { timeout: 60_000 })
    await highest.click()
    await expect(highest).toHaveAttribute("aria-pressed", "true")
    const quotesTable = optimiserPreview.getByRole("table", { name: "Per-quote detail" })
    await expect(
      quotesTable.getByRole("columnheader", { name: /Scenario value/ }),
    ).toHaveAttribute("aria-sort", "descending")
    await expect(optimiserPreview.getByText("Showing 1–8 of 8 matching (of 8)", { exact: true }))
      .toBeVisible()
    const scenarioColumn = await quotesTable.getByRole("columnheader").allInnerTexts()
    const scenarioIndex = scenarioColumn.findIndex((text) => text.startsWith("Scenario value"))
    expect(scenarioIndex).toBeGreaterThan(0)
    const chosenValues = await quotesTable.locator("tbody tr").evaluateAll(
      (rows, index) => rows.map((row) => Number.parseFloat(
        (row.children[index] as HTMLElement).innerText.replace(/[^0-9.]/g, ""),
      )),
      scenarioIndex,
    )
    expect(chosenValues).toHaveLength(8)
    expect(chosenValues).toEqual([...chosenValues].sort((a, b) => b - a))

    // Convergence: every online solve records its history, drawn on real axes.
    await openResultPane(page, "Convergence")
    const objectiveHistory = optimiserPreview.getByRole("img", { name: "Objective by iteration" })
    await expect(objectiveHistory).toBeVisible()
    await expect(objectiveHistory.getByText("Iteration", { exact: true })).toBeVisible()
    await expect(objectiveHistory.getByText("Objective", { exact: true })).toBeVisible()
    expect(await objectiveHistory.getByTestId("chart-x-tick").count()).toBeGreaterThan(0)
    expect(await objectiveHistory.getByTestId("chart-value-tick").count()).toBeGreaterThan(1)
    await expect(
      optimiserPreview.getByRole("img", { name: "volume total by iteration" }),
    ).toBeVisible()
    await expect(optimiserPreview.getByText(
      /^History is recorded for the solved result; frontier point 2: /,
    )).toBeVisible()

    // Focus view keeps the pane and closes on Escape.
    await optimiserPreview.getByRole("button", { name: "Focus view", exact: true }).click()
    const focusDialog = page.getByRole("dialog", { name: "Optimiser validation" })
    await expect(focusDialog).toBeVisible()
    await expect(focusDialog.getByRole("tab", { name: "Convergence", exact: true }))
      .toHaveAttribute("aria-selected", "true")
    await page.keyboard.press("Escape")
    await expect(focusDialog).toHaveCount(0)
    await expect(optimiserPreview.getByRole("button", { name: "Focus view", exact: true }))
      .toBeFocused()
    await expect(resultTabs.getByRole("tab", { name: "Convergence", exact: true }))
      .toHaveAttribute("aria-selected", "true")

    // Publishing lives in the node's Export pane; its target is the point the
    // preview selected.
    await optimiserPanel.getByRole("tab", { name: "Export", exact: true }).click()
    await expect(
      optimiserPanel.getByRole("combobox", { name: "Result to publish" }),
    ).toHaveValue("1")
    await optimiserPanel.getByRole("button", {
      name: "Log to MLflow",
      exact: true,
    }).click()
    await expect(optimiserPanel.getByTestId("optimiser-log-receipt")).toContainText(
      "Logged frontier point 2 to canvas-e2e: run run-frontier-point-2",
    )
    expect(mlflowLogRequest).toMatchObject({
      job_id: solveBody.job_id,
      point_index: 1,
    })

    // Logging never consumes the result, so the save still works afterwards.
    await optimiserPanel.getByRole("button", { name: "Save to file", exact: true }).click()
    const saveReceipt = optimiserPanel.getByTestId("optimiser-save-receipt")
    await expect(saveReceipt).toContainText(optimiserArtifactApplyPath, { timeout: 120_000 })
    await expect.poll(() => existsSync(optimiserArtifactPath)).toBe(true)
    expect(readJson(optimiserArtifactPath)).toMatchObject({
      frontier_selection: {
        selected_from_frontier: true,
        point_index: 1,
      },
    })
    await saveReceipt.getByRole("combobox", { name: "Apply Optimisation node" }).selectOption({ label: "browser_apply" })
    await saveReceipt.getByRole("button", { name: "Use in Apply node" }).click()
    await expect(saveReceipt).toContainText(`browser_apply now loads ${optimiserArtifactApplyPath}.`)

    const applyPanel = await openNodeProperties(
      page,
      "Apply Optimisation node: browser_apply",
    )
    await expect(
      applyPanel.getByPlaceholder("artifacts/optimiser_v1.json"),
    ).toHaveValue(optimiserArtifactApplyPath)
    await expect(applyPanel.getByText("Loaded Artifact")).toBeVisible()
    await expect(applyPanel.getByText("online", { exact: true })).toBeVisible()
    const previewTable = page.getByRole("table").first()
    await expect(
      previewTable.getByText("optimal_scenario_value", { exact: true }),
    ).toBeVisible({ timeout: 120_000 })
    await expect(
      previewTable.getByText("__optimiser_version__", { exact: true }),
    ).toBeVisible()
  })

  test("solves a ratebook and draws each rating factor's rates", async ({ page }) => {
    test.slow()
    await page.setViewportSize(desktopViewport)
    await page.goto("/")
    const ratebookPanel = await openNodeProperties(
      page,
      "Optimisation node: browser_ratebook",
    )
    const solveResponsePromise = page.waitForResponse(response => (
      response.url().endsWith("/api/optimiser/solve")
      && response.request().method() === "POST"
    ))
    await ratebookPanel.getByRole("tab", { name: "Solve", exact: true }).click()
    await ratebookPanel.getByRole("button", { name: "Optimise", exact: true }).click()
    expect((await solveResponsePromise).ok()).toBe(true)

    const resultTabs = page.getByRole("tablist", { name: "Optimiser result panes" })
    // No frontier, so the workspace opens on Summary; a ratebook result offers Rates.
    await expect(resultTabs.getByRole("tab", { name: "Summary", exact: true }))
      .toHaveAttribute("aria-selected", "true", { timeout: 120_000 })
    await expect(resultTabs.getByRole("tab")).toHaveText([
      "Summary",
      "Rates",
      "Adjustments",
      "Segments",
      "Quotes",
      "Convergence",
    ])
    const ratebookPreview = page.getByTestId("optimiser-preview-frame")
    await expect(ratebookPreview.getByTestId("optimiser-provenance")).toHaveText(new RegExp(
      "^Ratebook · 8 quotes × 5 scenario steps · Data: .+ · As solved · ",
    ))

    await openResultPane(page, "Rates")
    await expect(ratebookPreview.getByRole("heading", { name: "region_band", exact: true })).toBeVisible()
    await expect(
      ratebookPreview.getByRole("button", { name: "region_band", pressed: true }),
    ).toBeVisible()
    // Quotes 3 and 6 are North; 1, 4 and 7 South; 2, 5 and 8 East.
    await expect(ratebookPreview.getByText(/^3 levels · 8 quotes · rates \d\.\d{4} to \d\.\d{4}$/))
      .toBeVisible()
    const rates = ratebookPreview.getByRole("group", { name: "Rates for region_band" })
    const levels = rates.getByTestId("relativity-row")
    await expect(levels).toHaveCount(3)
    expect((await levels.getByTestId("relativity-label").allTextContents()).sort())
      .toEqual(["East", "North", "South"])
    await expect(levels.getByTestId("quote-strip-bar")).toHaveCount(3)
    await rates.getByRole("button", { name: /^North\. Rate / }).focus()
    await expect(ratebookPreview.getByRole("status").filter({ hasText: /^North/ }))
      .toContainText("Quotes: 2")
  })
})
