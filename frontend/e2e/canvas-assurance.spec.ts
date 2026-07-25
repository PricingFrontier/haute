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
const optimiserArtifactPath = resolve(
  ratingDir,
  "output",
  "optimiser_browser_optimiser_browser_optimiser.json",
)
const desktopViewport = { width: 1440, height: 900 }
const narrowViewport = { width: 1024, height: 768 }
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
  await expect(locator).toHaveScreenshot(name, {
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

test.describe.configure({ mode: "serial" })

test.describe("frontend canvas assurance", () => {
  test.beforeEach(() => {
    resetE2eProject()
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
    const bandingTabs = bandingPanel.getByRole("tablist", {
      name: "Banding columns",
    })
    const ageTab = bandingTabs.getByRole("tab", { name: /proposer_age_band/i })
    const channelTab = bandingTabs.getByRole("tab", { name: /channel_band/i })
    const vehicleTab = bandingTabs.getByRole("tab", { name: /vehicle_age_band/i })
    await expect(ageTab).toBeVisible()
    await expect(channelTab).toBeVisible()
    await expect(vehicleTab).toBeVisible()

    await channelTab.click()
    await expect(
      bandingPanel.getByRole("radio", { name: "Categorical", exact: true }),
    ).toBeChecked()
    await vehicleTab.click()
    await expect(
      bandingPanel.getByRole("radio", { name: "Breakpoints", exact: true }),
    ).toBeChecked()
    await expect(bandingPanel.getByLabel("Output Column")).toHaveValue(
      "vehicle_age_band",
    )
    await expect(bandingPanel.getByTestId("banding-summary")).toBeVisible()
    await stabiliseCanvasScreenshot(page)

    await expectCanvasScreenshot(
      bandingPanel,
      "mixed-banding-desktop-1440x900.png",
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      bandingPanel,
      "mixed-banding-narrow-1024x768.png",
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
      "rebuilt-three-factor-rating-desktop-1440x900.png",
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      ratingPanel,
      "rebuilt-three-factor-rating-narrow-1024x768.png",
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
    await page.route("**/api/modelling/mlflow/check", async (route) => {
      await route.fulfill({
        json: {
          mlflow_installed: true,
          mlflow_importable: true,
          tracking_configured: true,
          backend: "browser-contract",
          databricks_host: "",
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
    await optimiserPanel.getByRole("button", {
      name: "Individual point",
      exact: true,
    }).click()
    const constraintValue = optimiserPanel.getByRole("spinbutton", {
      name: "volume constraint value",
    })
    await constraintValue.fill("0.91")
    await expect(constraintValue).toHaveValue("0.91")
    await optimiserPanel.getByRole("button", {
      name: "Efficient frontier",
      exact: true,
    }).click()
    const minRange = optimiserPanel.getByLabel("volume min value")
    const maxRange = optimiserPanel.getByLabel("volume max value")
    await minRange.fill("0.88")
    await maxRange.fill("0.97")
    await expect(minRange).toHaveValue("0.88")
    await expect(maxRange).toHaveValue("0.97")

    await page.keyboard.press(saveShortcut)
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect.poll(() => {
      const config = readJson(optimiserConfigPath)
      return {
        constraint: (config.constraints as JsonObject).volume,
        range: (config.frontier_ranges as JsonObject).volume,
      }
    }).toEqual({
      constraint: { min: 0.91 },
      range: { min: 0.88, max: 0.97 },
    })

    await page.reload()
    optimiserPanel = await openNodeProperties(
      page,
      "Optimisation node: browser_optimiser",
    )
    await optimiserPanel.getByRole("button", {
      name: "Individual point",
      exact: true,
    }).click()
    await expect(optimiserPanel.getByRole("spinbutton", {
      name: "volume constraint value",
    })).toHaveValue("0.91")
    await optimiserPanel.getByRole("button", {
      name: "Efficient frontier",
      exact: true,
    }).click()
    await expect(optimiserPanel.getByLabel("volume min value")).toHaveValue("0.88")
    await expect(optimiserPanel.getByLabel("volume max value")).toHaveValue("0.97")

    const solveResponsePromise = page.waitForResponse(response => (
      response.url().endsWith("/api/optimiser/solve")
      && response.request().method() === "POST"
    ))
    await optimiserPanel.getByRole("button", {
      name: "Optimise",
      exact: true,
    }).click()
    const solveResponse = await solveResponsePromise
    const solveBody = await solveResponse.json() as JsonObject
    expect(typeof solveBody.job_id).toBe("string")
    const resultTabs = page.getByRole("tablist", {
      name: "Optimiser result panes",
    })
    await expect(
      resultTabs.getByRole("tab", { name: "Frontier", exact: true }),
    ).toBeVisible({ timeout: 120_000 })

    const frontierPoint = page.getByRole("button", {
      name: /Select frontier point 2/i,
    })
    await frontierPoint.click()
    await expect(page.getByText(/Point 2 of/i)).toBeVisible()
    await expect(
      page.getByRole("alert").filter({ hasText: /Failed to select frontier point/i }),
    ).toHaveCount(0)
    await stabiliseCanvasScreenshot(page)

    const optimiserPreview = page.getByTestId("optimiser-preview-frame")
    await expectCanvasScreenshot(
      optimiserPreview,
      "selected-optimiser-point-desktop-1440x900.png",
    )
    await page.setViewportSize(narrowViewport)
    await expectCanvasScreenshot(
      optimiserPreview,
      "selected-optimiser-point-narrow-1024x768.png",
    )
    await page.setViewportSize(desktopViewport)

    await resultTabs.getByRole("tab", { name: "Export", exact: true }).click()
    await page.getByRole("button", {
      name: "Log to MLflow",
      exact: true,
    }).click()
    await expect(
      page.getByText(
        "Logged to canvas-e2e: https://mlflow.invalid/runs/run-frontier-point-2",
        { exact: true },
      ),
    ).toBeVisible()
    expect(mlflowLogRequest).toMatchObject({
      job_id: solveBody.job_id,
      point_index: 1,
    })

    await page.getByRole("button", { name: "Save result", exact: true }).click()
    await expect(
      page.getByText(/optimiser_browser_optimiser_browser_optimiser\.json/i),
    ).toBeVisible({ timeout: 120_000 })
    await expect.poll(() => existsSync(optimiserArtifactPath)).toBe(true)
    expect(readJson(optimiserArtifactPath)).toMatchObject({
      frontier_selection: {
        selected_from_frontier: true,
        point_index: 1,
      },
    })

    const applyPanel = await openNodeProperties(
      page,
      "Apply Optimisation node: browser_apply",
    )
    await expect(
      applyPanel.getByPlaceholder("artifacts/optimiser_v1.json"),
    ).toHaveValue(
      "rating/output/optimiser_browser_optimiser_browser_optimiser.json",
    )
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
})
