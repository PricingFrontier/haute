import { execFileSync } from "node:child_process"
import { existsSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { dispatchAppShortcut, dispatchNodeDoubleClick } from "./browserInteractions"
import { e2eProjectRoot, resetE2eProject, unsetWorkingBranch } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const sidecarPath = resolve(ratingDir, "main.haute.json")
const utilityModulePath = resolve(ratingDir, "utility", "browser_helpers.py")
const gitMainPath = resolve(ratingDir, "main.py")
const browserSubmodelPath = resolve(e2eProjectRoot, "rating", "modules", "browser_group.py")
const selectAll = process.platform === "darwin" ? "Meta+A" : "Control+A"

async function connectHandles(page: Page, source: Locator, target: Locator): Promise<void> {
  await page.getByTestId("toolbar-centre").click()
  const zoomIn = page.getByRole("button", { name: "Zoom in", exact: true })
  for (let step = 0; step < 4; step += 1) await zoomIn.click()
  await expect(source).toBeVisible()
  await expect(target).toBeVisible()
  const [sourceBox, targetBox] = await Promise.all([
    source.boundingBox(),
    target.boundingBox(),
  ])
  if (sourceBox === null || targetBox === null) {
    throw new Error("Could not measure the handles for a graph connection")
  }
  await page.mouse.move(
    sourceBox.x + sourceBox.width / 2,
    sourceBox.y + sourceBox.height / 2,
  )
  await page.mouse.down()
  await page.mouse.move(
    targetBox.x + targetBox.width / 2,
    targetBox.y + targetBox.height / 2,
    { steps: 12 },
  )
  const expectedTargetHandle = await target.getAttribute("data-handleid")
  const hitTargetHandle = await page.evaluate(({ x, y }) => (
    document.elementFromPoint(x, y)
      ?.closest(".react-flow__handle")
      ?.getAttribute("data-handleid") ?? null
  ), {
    x: targetBox.x + targetBox.width / 2,
    y: targetBox.y + targetBox.height / 2,
  })
  if (hitTargetHandle !== expectedTargetHandle) {
    throw new Error(
      `Connection drop missed handle ${String(expectedTargetHandle)}; hit ${String(hitTargetHandle)}`,
    )
  }
  await page.mouse.up()
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

    const modellingPanes = page.getByRole("tablist", { name: "Modelling panes" })
    await modellingPanes.getByRole("tab", { name: "Train", exact: true }).click()
    const trainButton = page.getByRole("button", { name: /Train Model/i })
    await expect(trainButton).toBeVisible()
    await trainButton.click()

    await expect(
      page.getByText(/Model trained — results in preview panel below/i),
    ).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText("Model Info")).toBeVisible()
    await expect(
      page.getByRole("columnheader", { name: "Development rows", exact: true }),
    ).toBeVisible()
    const modelResultTabs = page.getByRole("tablist", { name: "Model result panes" })
    await expect(modelResultTabs.getByRole("tab", { name: "Summary", exact: true })).toBeVisible()

    await page.getByRole("button", { name: /enriched/i }).click()
    await modelNode.click()

    await expect(
      page.getByText(/Model trained — results in preview panel below/i),
    ).toBeVisible()
    await expect(page.getByText("Model Info")).toBeVisible()
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

    await page.getByRole("button", { name: "Save", exact: true }).click()
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

    // Browser-owned source keys use portableKey: case is preserved (the old
    // ad-hoc fold lowercased, silently merging case-distinct labels).
    await expect(page.getByTitle("Data source")).toContainText("Batch_Smoke")

    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await expect
      .poll(() => JSON.parse(readFileSync(sidecarPath, "utf8")).active_source)
      .toBe("Batch_Smoke")
    await expect
      .poll(() => JSON.parse(readFileSync(sidecarPath, "utf8")).sources)
      .toEqual(["live", "Batch_Smoke"])

    await page.reload()
    await expect(page.getByTitle("Data source")).toContainText("Batch_Smoke")
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

    await page.getByRole("button", { name: "Save", exact: true }).click()
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
    const constructorAnchor = original.match(/^pipeline\s*=\s*haute\.Pipeline\(.+$/m)?.[0]
    if (!constructorAnchor) throw new Error("E2E pipeline fixture has no pipeline constructor")
    const updated = original.replace(
      constructorAnchor,
      `${syncProbeImport}\n\n${constructorAnchor}`,
    )

    try {
      writeFileSync(gitMainPath, updated, "utf8")

      await expect(editor).toContainText("websocket_sync_probe")
      await expect(page.getByText(/Pipeline updated from file/i)).toBeVisible()
    } finally {
      writeFileSync(gitMainPath, original, "utf8")
    }

    await expect(editor).not.toContainText("websocket_sync_probe")
  })

  test("first-run chooser creates a working branch and saves land on its ledger", async ({
    page,
  }) => {
    // Model a never-configured clone: the S27 startup readiness check must
    // open the working-branch chooser over the canvas (state "unset").
    unsetWorkingBranch()
    await page.goto("/")

    const branchSelect = page.getByTestId("working-branch-select")
    await expect(branchSelect).toBeVisible()

    // Create a new working branch through the chooser. Confirm spawns the
    // "-save" ledger and moves HEAD onto it (HEAD-on-ledger posture, S10).
    await branchSelect.selectOption("__create__")
    await page.getByTestId("working-branch-new").fill("browser-e2e-flow")
    await page.getByTestId("working-branch-confirm").click()

    await expect
      .poll(() =>
        execFileSync("git", ["branch", "--show-current"], {
          cwd: e2eProjectRoot,
          encoding: "utf8",
        }).trim(),
      )
      .toBe("browser-e2e-flow-save")

    // The modal is gone; the branch indicator now names the working branch.
    await expect(branchSelect).not.toBeVisible()
    await expect(page.getByTestId("branch-indicator-name")).toContainText("browser-e2e-flow")

    // Edit a node and save — the save must write the file AND record a
    // ledger auto-commit (one commit per save, P1).
    const pricedNode = page.getByRole("button", { name: /priced/i })
    await expect(pricedNode).toBeVisible()
    await pricedNode.click()

    const labelInput = page.locator("input.node-label-input")
    await expect(labelInput).toHaveValue("priced")
    await labelInput.fill("priced_browser")
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await expect.poll(() => readFileSync(gitMainPath, "utf8")).toContain("def priced_browser")

    await expect
      .poll(() =>
        execFileSync("git", ["log", "-1", "--format=%s"], {
          cwd: e2eProjectRoot,
          encoding: "utf8",
        }).trim(),
      )
      .toMatch(/^Updated /)

    // The version-control panel surfaces the save as pending (not yet
    // folded into a milestone).
    await page.getByTestId("branch-indicator-name").click()
    await expect(page.getByTestId("git-panel-pending")).toBeVisible()
  })

  test("creates a submodel, adds a parent-side input, and persists it", async ({ page }) => {
    await page.goto("/")

    const bandingNode = page.getByRole("button", { name: /browser_mixed_banding/i })
    const ratingNode = page.getByRole("button", { name: /browser_rating/i })
    await expect(bandingNode).toBeVisible()
    await expect(ratingNode).toBeVisible()
    await bandingNode.click()
    await ratingNode.click({
      modifiers: [process.platform === "darwin" ? "Meta" : "Control"],
    })
    await dispatchAppShortcut(page, "g")

    await expect(page.getByText("Create Submodel")).toBeVisible()
    const nameInput = page.getByPlaceholder("e.g. model_scoring")
    await nameInput.fill("browser_group")
    await page.getByRole("button", { name: "Create" }).click()

    const submodelNode = page.getByRole("button", { name: /browser_group/i })
    await expect(submodelNode).toBeVisible()
    expect(existsSync(browserSubmodelPath)).toBe(false)

    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect
      .poll(() => readFileSync(browserSubmodelPath, "utf8"))
      .toContain('submodel = haute.Submodel("browser_group"')

    await page.reload()
    await expect(submodelNode).toBeVisible()

    const sourceNode = page.getByTestId("rf__node-raw_rows")
    const sourceHandle = sourceNode.getByTestId("output-connector[0]:raw_rows")
    const newInputHandle = submodelNode.getByTestId("submodel-input-handle")
    await expect(sourceHandle).toBeVisible()
    await expect(newInputHandle).toBeVisible()
    await expect(newInputHandle).toHaveClass(/connectableend/)
    const collapsedTargets = submodelNode.locator(".react-flow__handle.target")
    await expect(collapsedTargets).toHaveCount(2)
    await expect(collapsedTargets.first()).toHaveAttribute(
      "data-handleid",
      "__submodel_inputs__",
    )
    await expect(submodelNode.getByTestId(/^submodel-input-frame-row-/)).toHaveCount(0)
    await expect(submodelNode.getByText("enriched", { exact: true })).toHaveCount(0)
    const frameName = "raw_rows"
    await connectHandles(page, sourceHandle, newInputHandle)
    await expect(submodelNode.locator('[data-handleid="in__input_2"]')).toBeAttached()
    await expect(collapsedTargets).toHaveCount(3)
    await expect(newInputHandle).toBeVisible()
    await expect(submodelNode.getByTestId(/^submodel-input-frame-row-/)).toHaveCount(0)
    await expect(submodelNode.getByText(frameName, { exact: true })).toHaveCount(0)

    await dispatchNodeDoubleClick(page, "browser_group")
    await expect(page.getByRole("button", { name: "main", exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "browser_group", exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: /browser_mixed_banding/i })).toBeVisible()

    const inputRows = page.getByTestId(/^submodel-input-frame-row-/)
    await expect(inputRows).toHaveCount(2)
    await expect(inputRows.filter({ hasText: "enriched" })).toBeVisible()
    const newInputRow = inputRows.filter({ hasText: frameName })
    await expect(newInputRow).toBeVisible()
    await expect(page.getByText("new input", { exact: true })).toHaveCount(0)
    const drilledInputHandle = newInputRow.locator(".react-flow__handle-right")
    const childInputHandle = page.getByTestId("input-connector[0]:browser_mixed_banding")
    await expect(drilledInputHandle).toBeVisible()
    await expect(childInputHandle).toBeVisible()
    await connectHandles(page, drilledInputHandle, childInputHandle)

    await page.getByRole("button", { name: "main", exact: true }).click()
    await expect(submodelNode).toBeVisible()
    await expect(submodelNode.getByTestId("submodel-input-handle")).toBeVisible()
    await expect(submodelNode.getByText(frameName, { exact: true })).toHaveCount(0)
    await expect(submodelNode.getByText("enriched", { exact: true })).toHaveCount(0)

    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
    await expect.poll(() => readFileSync(browserSubmodelPath, "utf8"))
      .toContain(`'label': '${frameName}'`)
  })
})
