import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const sidecarPath = resolve(ratingDir, "main.haute.json")
const utilityModulePath = resolve(ratingDir, "utility", "browser_helpers.py")
const gitMainPath = resolve(ratingDir, "main.py")
const browserSubmodelPath = resolve(e2eProjectRoot, "modules", "browser_group.py")
const optimiserArtifactPath = resolve(
  e2eProjectRoot,
  "rating",
  "output",
  "optimiser_browser_optimiser.json",
)
const selectAll = process.platform === "darwin" ? "Meta+A" : "Control+A"

async function dispatchAppShortcut(page: Page, key: string): Promise<void> {
  await page.evaluate(
    ({ key, useMeta }) => {
      window.dispatchEvent(
        new KeyboardEvent("keydown", {
          key,
          bubbles: true,
          cancelable: true,
          ctrlKey: !useMeta,
          metaKey: useMeta,
        }),
      )
    },
    { key, useMeta: process.platform === "darwin" },
  )
}

async function dispatchNodeDoubleClick(page: Page, label: string): Promise<void> {
  await page
    .locator(`[aria-label^="Submodel node: ${label}"]`)
    .dispatchEvent("dblclick", { bubbles: true, cancelable: true, composed: true })
}

test.describe.configure({ mode: "serial" })

test.describe("core browser flows", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("loads the starter pipeline and supports preview plus trace", async ({ page }) => {
    await page.goto("/")

    await expect(
      page.getByRole("toolbar", { name: /pipeline toolbar/i }),
    ).toBeVisible()

    const enrichedNode = page.getByRole("button", { name: /enriched/i })
    await expect(enrichedNode).toBeVisible()
    await enrichedNode.click()

    const nodeLabelInput = page.locator("input.node-label-input")
    await expect(nodeLabelInput).toHaveValue("enriched")
    await page.getByRole("button", { name: "Refresh" }).click()

    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("value_doubled", { exact: true })).toBeVisible()

    await previewTable.getByRole("cell", { name: "22" }).first().click()

    const nodeProperties = page.getByRole("complementary", { name: /node properties/i })
    await expect(nodeProperties.getByText(/Trace: value_doubled/i)).toBeVisible()
    await expect(nodeProperties.getByText(/\(raw_rows\)/i)).toBeVisible()
  })

  test("runs a real modelling training job and keeps results when switching panels", async ({ page }) => {
    test.slow()

    await page.goto("/")

    const modelNode = page.getByRole("button", { name: /browser_model/i })
    await expect(modelNode).toBeVisible()
    await modelNode.click()

    const trainButton = page.getByRole("button", { name: /Train Model/i })
    await expect(trainButton).toBeVisible()
    await trainButton.click()

    await expect(
      page.getByText(/Model trained — results in preview panel below/i),
    ).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText("Model Info")).toBeVisible()
    await expect(page.getByText(/Train rows/i)).toBeVisible()
    const modelResultTabs = page.getByRole("tablist", { name: "Model result panes" })
    await expect(modelResultTabs.getByRole("tab", { name: "Summary", exact: true })).toBeVisible()

    await page.getByRole("button", { name: /enriched/i }).click()
    await modelNode.click()

    await expect(
      page.getByText(/Model trained — results in preview panel below/i),
    ).toBeVisible()
    await expect(page.getByText("Model Info")).toBeVisible()
  })

  test("runs a real optimiser flow, selects a frontier point, and opens an apply node wired to the saved artifact", async ({ page }) => {
    test.slow()

    await page.goto("/")

    const optimiserNode = page.getByLabel("Optimisation node: browser_optimiser")
    await expect(optimiserNode).toBeVisible()
    await optimiserNode.click()

    const optimiseButton = page.getByRole("button", { name: "Optimise", exact: true })
    await expect(optimiseButton).toBeVisible()
    await optimiseButton.click()

    const optimiserResultTabs = page.getByRole("tablist", { name: "Optimiser result panes" })
    await expect(optimiserResultTabs.getByRole("tab", { name: "Frontier", exact: true })).toBeVisible({
      timeout: 120_000,
    })
    await expect(optimiserResultTabs.getByRole("tab", { name: "Summary", exact: true })).toBeVisible()

    const frontierPoint = page.getByRole("button", { name: /Select frontier point 2/i })
    await expect(frontierPoint).toBeVisible()
    await frontierPoint.click()

    await expect(page.getByText(/Point 2 of/i)).toBeVisible()
    await expect(
      page.getByRole("alert").filter({ hasText: /Failed to select frontier point/i }),
    ).toHaveCount(0)

    await optimiserResultTabs.getByRole("tab", { name: "Export", exact: true }).click()
    await page.getByRole("button", { name: "Save result", exact: true }).click()

    await expect(page.getByText(/optimiser_browser_optimiser\.json/i)).toBeVisible({
      timeout: 120_000,
    })
    await expect.poll(() => existsSync(optimiserArtifactPath)).toBe(true)

    const applyNode = page.getByLabel("Apply Optimisation node: browser_apply")
    await expect(applyNode).toBeVisible()
    await applyNode.click()
    await expect(page.locator("input.node-label-input")).toHaveValue("browser_apply")
    await expect(
      page.getByPlaceholder("artifacts/optimiser_v1.json"),
    ).toHaveValue("rating/output/optimiser_browser_optimiser.json")
    await expect(page.getByPlaceholder("__optimiser_version__")).toHaveValue(
      "__optimiser_version__",
    )
    await expect(page.getByText("Loaded Artifact")).toBeVisible()
    await expect(page.getByText("online", { exact: true })).toBeVisible()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("optimal_scenario_value", { exact: true })).toBeVisible({
      timeout: 120_000,
    })
    await expect(previewTable.getByText("__optimiser_version__", { exact: true })).toBeVisible()
  })

  test("persists node edits through save and reload", async ({ page }) => {
    await page.goto("/")

    const renamedNode = "raw_rows_browser"
    const rawRowsNode = page.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNode).toBeVisible()
    await rawRowsNode.click()

    const labelInput = page.locator("input.node-label-input")
    await expect(labelInput).toHaveValue("raw_rows")
    await labelInput.fill(renamedNode)

    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await page.reload()
    await expect(page.getByRole("button", { name: new RegExp(renamedNode, "i") })).toBeVisible()
  })

  test("manages sources and persists the active source through save", async ({ page }) => {
    await page.goto("/")

    await page.getByTitle("Data source").click()
    await page.getByRole("button", { name: /Add source/i }).click()

    const sourceInput = page.getByPlaceholder("name")
    await sourceInput.fill("Batch Smoke")
    await sourceInput.press("Enter")

    await expect(page.getByTitle("Data source")).toContainText("batch_smoke")

    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await expect
      .poll(() => JSON.parse(readFileSync(sidecarPath, "utf8")).active_source)
      .toBe("batch_smoke")
    await expect
      .poll(() => JSON.parse(readFileSync(sidecarPath, "utf8")).sources)
      .toEqual(["live", "batch_smoke"])

    await page.reload()
    await expect(page.getByTitle("Data source")).toContainText("batch_smoke")
  })

  test("creates and persists utility scripts from the browser", async ({ page }) => {
    await page.goto("/")

    await page.getByRole("button", { name: /^Utility$/i }).click()
    await expect(page.getByText("Utility Scripts")).toBeVisible()
    await page.getByTitle("New utility file").click()

    const moduleInput = page.getByPlaceholder("module_name")
    await moduleInput.fill("browser_helpers")
    await moduleInput.press("Enter")

    await expect(page.getByRole("button", { name: "browser_helpers", exact: true })).toBeVisible()

    const editor = page.locator(".cm-content").first()
    await editor.click()
    await page.keyboard.press(selectAll)
    await page.keyboard.insertText(
      [
        "from __future__ import annotations",
        "",
        "def browser_helper(value: int) -> int:",
        "    return value * 3",
      ].join("\n"),
    )

    await expect
      .poll(() => readFileSync(utilityModulePath, "utf8"))
      .toContain("def browser_helper(value: int) -> int:")

    await page.reload()
    await page.getByRole("button", { name: /^Utility$/i }).click()
    await expect(page.getByRole("button", { name: "browser_helpers", exact: true })).toBeVisible()
  })

  test("persists pipeline imports through save and reload", async ({ page }) => {
    await page.goto("/")

    await page.getByRole("button", { name: /^Imports$/i }).click()
    await expect(page.getByText("Pipeline Imports")).toBeVisible()

    const editor = page.getByTestId("code-editor-wrapper").locator(".cm-content")
    await editor.click()
    await page.keyboard.press(selectAll)
    await page.keyboard.insertText("import math")
    await expect(page.getByTitle("Unsaved changes")).toBeVisible()

    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect.poll(() => readFileSync(gitMainPath, "utf8")).toContain("import math")

    await page.reload()
    await page.getByRole("button", { name: /^Imports$/i }).click()
    await expect(page.getByText("Pipeline Imports")).toBeVisible()
    await expect(page.getByTestId("code-editor-wrapper").locator(".cm-content")).toContainText(
      "import math",
    )
  })

  test("refreshes the imports panel from websocket file sync", async ({ page }) => {
    await page.goto("/")

    await page.getByRole("button", { name: /^Imports$/i }).click()
    await expect(page.getByText("Pipeline Imports")).toBeVisible()

    const original = readFileSync(gitMainPath, "utf8")
    const syncProbeImport = "import statistics as websocket_sync_probe"
    const editor = page.getByTestId("code-editor-wrapper").locator(".cm-content")
    const updated = original.includes("import math")
      ? original.replace("import math", `import math\n${syncProbeImport}`)
      : original.replace("import haute", `import haute\n\n${syncProbeImport}`)

    try {
      writeFileSync(gitMainPath, updated, "utf8")

      await expect(editor).toContainText("websocket_sync_probe")
      await expect(page.getByText(/Pipeline updated from file/i)).toBeVisible()
    } finally {
      writeFileSync(gitMainPath, original, "utf8")
    }

    await expect(editor).not.toContainText("websocket_sync_probe")
  })

  test("creates a git branch and records saved progress in history", async ({ page }) => {
    await page.goto("/")

    await page.getByRole("button", { name: /^Git$/i }).click()
    await expect(page.getByLabel("Node properties").getByText("Git")).toBeVisible()
    await page.getByRole("button", { name: /Start editing \(create branch\)/i }).click()

    const branchInput = page.getByPlaceholder("Update area factors")
    await branchInput.fill("Browser e2e flow")
    await page.getByRole("button", { name: /Create branch/i }).click()

    await expect
      .poll(() =>
        execFileSync("git", ["branch", "--show-current"], {
          cwd: e2eProjectRoot,
          encoding: "utf8",
        }).trim(),
      )
      .toMatch(/pricing\/.+\/browser-e2e-flow$/)

    await page.getByTitle("Close").click()

    const pricedNode = page.getByRole("button", { name: /priced/i })
    await expect(pricedNode).toBeVisible()
    await pricedNode.click()

    const labelInput = page.locator("input.node-label-input")
    await expect(labelInput).toHaveValue("priced")
    await labelInput.fill("priced_browser")
    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await expect.poll(() => readFileSync(gitMainPath, "utf8")).toContain("def priced_browser")

    await page.getByRole("button", { name: /^Git$/i }).click()
    await expect(page.getByRole("button", { name: /Save progress/i })).toBeVisible()
    await page.getByRole("button", { name: /Save progress/i }).click()
    await expect
      .poll(() =>
        execFileSync("git", ["log", "-1", "--format=%s"], {
          cwd: e2eProjectRoot,
          encoding: "utf8",
        }).trim(),
      )
      .toMatch(/^Updated /)

    await page.getByRole("button", { name: /Version history/i }).click()
    await expect(page.getByText(/Updated/i).first()).toBeVisible()
  })

  test("creates a submodel, reloads it, and drills in through breadcrumbs", async ({ page }) => {
    await page.goto("/")

    const rawRowsNode = page.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNode).toBeVisible()
    await rawRowsNode.click()
    await dispatchAppShortcut(page, "a")
    await dispatchAppShortcut(page, "g")

    await expect(page.getByText("Create Submodel")).toBeVisible()
    const nameInput = page.getByPlaceholder("e.g. model_scoring")
    await nameInput.fill("browser_group")
    await page.getByRole("button", { name: "Create" }).click()

    // Scope to the submodel node's own accessible name. A bare /browser_group/i
    // now also matches the node-explosion peek trigger ("Peek inside
    // browser_group"); "Submodel node:" disambiguates to the node body.
    const submodelNode = page.getByRole("button", { name: /Submodel node: browser_group/i })
    await expect(submodelNode).toBeVisible()
    await expect
      .poll(() => readFileSync(browserSubmodelPath, "utf8"))
      .toContain('submodel = haute.Submodel("browser_group"')

    await page.getByRole("button", { name: "Save" }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await page.reload()
    await expect(submodelNode).toBeVisible()

    await dispatchNodeDoubleClick(page, "browser_group")
    await expect(page.getByRole("button", { name: "main", exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "browser_group", exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: /raw_rows/i })).toBeVisible()

    await page.getByRole("button", { name: "main", exact: true }).click()
    await expect(submodelNode).toBeVisible()
  })
})
