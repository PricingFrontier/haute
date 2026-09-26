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
  const collapsePalette = page.getByTitle("Collapse palette")
  if (await collapsePalette.isVisible()) await collapsePalette.click()
  await page.getByTestId("toolbar-centre").click()
  const zoomIn = page.getByRole("button", { name: "Zoom in", exact: true })
  for (let step = 0; step < 4; step += 1) await zoomIn.click()
  await expect(source).toBeVisible()
  await expect(target).toBeVisible()
  // Locator actions wait for fit-view animation to settle before resolving
  // the live handle position. Cached bounding boxes can become stale here;
  // collapsing the palette keeps both endpoints in view after zooming.
  await source.hover()
  await page.mouse.down()
  try {
    await target.hover()
    // Releasing before React Flow processes the move makes the connection a
    // no-op, even when the browser has geometrically reached the target.
    await expect(source).toHaveClass(/(^|\s)connectingfrom(\s|$)/)
    await expect(target).toHaveClass(/(^|\s)connectingto(\s|$)/)
    await expect(target).toHaveClass(/(^|\s)valid(\s|$)/)
  } finally {
    await page.mouse.up()
  }
}

// Adds a modelling node over raw_rows to the project for one scenario only;
// resetE2eProject restores main.py and removes the sidecar before the next.
function addModellingNode(name: string, algorithm: string, params: Record<string, unknown>): void {
  writeFileSync(
    resolve(ratingDir, "config", "model_training", name + ".json"),
    JSON.stringify({
      name,
      target: "value",
      algorithm,
      task: "regression",
      loss_function: "RMSE",
      params,
      evaluation: { schema_version: 1, strategy: "random", seed: 42, validation: { method: "single", size: 0.2 } },
      metrics: ["rmse"],
      row_limit: 30,
      output_dir: ".haute_cache/browser_training",
    }, null, 2),
    "utf8",
  )
  const block = [
    "",
    "",
    '@pipeline.modelling(config="config/model_training/' + name + '.json")',
    "def " + name + "(raw_rows): ...",
    "",
  ].join("\n")
  writeFileSync(gitMainPath, readFileSync(gitMainPath, "utf8").trimEnd() + "\n" + block, "utf8")
}

async function trainFamilyAndSaveModel(
  page: Page,
  algorithm: string,
  params: Record<string, unknown>,
  suffix: string,
): Promise<void> {
  const name = "browser_" + algorithm
  addModellingNode(name, algorithm, params)
  await page.goto("/")
  const node = page.getByRole("button", { name: new RegExp(name, "i") })
  await expect(node).toBeVisible()
  await node.click()
  const panes = page.getByRole("tablist", { name: "Modelling panes" })
  await panes.getByRole("tab", { name: "Train", exact: true }).click()
  await expect(page.getByLabel("Training run summary")).toContainText(" · RMSE")
  await expect(page.getByText("Dataset fits in memory")).toBeVisible({ timeout: 60_000 })
  await page.getByRole("button", { name: /Train Model/i }).click()
  await expect(
    page.getByText(/Model trained - results in preview panel below/i),
  ).toBeVisible({ timeout: 120_000 })
  await panes.getByRole("tab", { name: "Export", exact: true }).click()
  const modelFilePath = page.getByLabel("Filename or path *")
  await modelFilePath.fill(name)
  await modelFilePath.press("Enter")
  await expect(page.getByText("Destination: models/" + name + suffix)).toBeVisible()
  await page.getByRole("button", { name: "Save model to file" }).click()
  await expect(page.getByText("Saved model to models/" + name + suffix)).toBeVisible()
  await expect(page.getByText("Feature contract: models/" + name + ".feature_contract.json")).toBeVisible()
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

  test("runs a real modelling training job, keeps results when switching panels, and saves the model to a file", async ({ page }) => {
    test.slow()

    await page.goto("/")

    const modelNode = page.getByRole("button", { name: /browser_model/i })
    await expect(modelNode).toBeVisible()
    await modelNode.click()

    const modellingPanes = page.getByRole("tablist", { name: "Modelling panes" })
    await modellingPanes.getByRole("tab", { name: "Train", exact: true }).click()
    const trainButton = page.getByRole("button", { name: /Train Model/i })
    await expect(trainButton).toBeVisible()
    // The pane's memory estimate reserves the training budget while it runs,
    // so training starts once it has answered, as for a user reading it.
    await expect(page.getByText("Dataset fits in memory")).toBeVisible()
    await trainButton.click()

    await expect(
      page.getByText(/Model trained - results in preview panel below/i),
    ).toBeVisible({ timeout: 120_000 })
    await expect(page.getByText("Model Info")).toBeVisible()
    await expect(
      page.getByRole("term").filter({ hasText: /^Training rows$/ }),
    ).toBeVisible()
    const modelResultTabs = page.getByRole("tablist", { name: "Model result panes" })
    await expect(modelResultTabs.getByRole("tab", { name: "Summary", exact: true })).toBeVisible()

    // Lift: the double lift chart over the validation rows, with its values.
    await modelResultTabs.getByRole("tab", { name: "Lift", exact: true }).click()
    const liftPane = page.getByRole("tabpanel", { name: "Lift" })
    await expect(liftPane.getByRole("heading", { name: "Lift and discrimination" })).toBeVisible()
    await expect(liftPane.getByRole("img", { name: "Double lift chart" })).toBeVisible()
    const liftValues = liftPane.getByRole("table", { name: "Lift values", includeHidden: true })
    await expect(liftValues).toHaveCount(1)
    expect(await liftValues.locator("tbody tr").count()).toBeGreaterThan(0)

    // AvE: actual against expected for a feature's groups, alongside exposure.
    await modelResultTabs.getByRole("tab", { name: "AvE", exact: true }).click()
    const avePane = page.getByRole("tabpanel", { name: "AvE" })
    await expect(avePane.getByRole("heading", { name: "Actual vs expected" })).toBeVisible()
    await expect(avePane.getByRole("img", { name: /^Actual vs expected for / })).toBeVisible()
    await expect(avePane.getByRole("img", { name: /^Exposure for / })).toBeVisible()
    await expect(avePane.getByRole("button", { name: /\. Actual: .+\. Expected: .+\. Exposure: / }).first())
      .toBeVisible()

    await page.getByRole("button", { name: /enriched/i }).click()
    await modelNode.click()

    await expect(
      page.getByText(/Model trained - results in preview panel below/i),
    ).toBeVisible()
    await expect(page.getByText("Model Info")).toBeVisible()
    await expect(page.getByText("Experiment tracking")).toHaveCount(0)

    await modellingPanes.getByRole("tab", { name: "Export", exact: true }).click()
    const modelFilePath = page.getByLabel("Filename or path *")
    await modelFilePath.fill("browser_model")
    await modelFilePath.press("Enter")
    await expect(page.getByText("Destination: models/browser_model.cbm")).toBeVisible()
    await page.getByRole("button", { name: "Save model to file" }).click()
    await expect(page.getByText("Saved model to models/browser_model.cbm")).toBeVisible()
    await expect(page.getByText("Feature contract: models/browser_model.feature_contract.json")).toBeVisible()
  })

  test("configures a GLM through the terms pane and trains it", async ({ page }) => {
    test.slow()
    await page.goto("/")

    const glmNode = page.getByRole("button", { name: /browser_glm/i })
    await expect(glmNode).toBeVisible()
    await glmNode.click()

    const panes = page.getByRole("tablist", { name: "Modelling panes" })
    await panes.getByRole("tab", { name: "Features", exact: true }).click()

    const mileage = page.getByRole("group", { name: "mileage feature" })
    await expect(mileage.getByText("Not in model")).toBeVisible()
    await mileage.getByRole("button", { name: "Add mileage term" }).click()
    await expect(mileage.getByRole("combobox", { name: "mileage term type" })).toHaveValue("linear")
    await mileage.getByRole("button", { name: "Add mileage term" }).click()
    await expect(mileage.getByRole("textbox", { name: "mileage_sq expression" })).toHaveValue("mileage ** 2")

    await page.getByRole("button", { name: "Add interaction" }).click()
    const card = page.getByRole("group", { name: "Interaction 1" })
    await card.getByRole("combobox", { name: "Interaction 1 feature 1" }).selectOption("channel")
    await card.getByRole("combobox", { name: "Interaction 1 feature 2" }).selectOption("mileage")
    // An automatic (penalised) interaction spline: RustyStats chooses its smoothing.
    await card.getByRole("combobox", { name: "mileage fit in interaction" }).selectOption("bs")
    await expect(card.getByRole("combobox", { name: "mileage df mode" })).toHaveValue("auto")
    await expect(card.getByRole("checkbox", { name: "Include main effects" })).toBeChecked()
    await expect(
      page.getByRole("group", { name: "channel feature" }).getByText("Main effect from Interaction 1 (Categorical)"),
    ).toBeVisible()
    // raw_rows exposes id, value, proposer_age, channel, vehicle_age, mileage;
    // the target `value` is the only role column, leaving five eligible features.
    await expect(page.getByText("2 of 5 in model")).toBeVisible()

    await panes.getByRole("tab", { name: "Train", exact: true }).click()
    await page.getByRole("button", { name: /Train Model/i }).click()
    await expect(
      page.getByText(/Model trained - results in preview panel below/i),
    ).toBeVisible({ timeout: 120_000 })
    // GLM fit details are collapsed under the Summary's "Fit details" disclosure.
    await page.getByText("Fit details", { exact: true }).click()
    await expect(page.getByRole("table", { name: "Smooth terms" })).toBeVisible()
    const resultTabs = page.getByRole("tablist", { name: "Model result panes" })
    await resultTabs.getByRole("tab", { name: "Coefficients", exact: true }).click()
    await expect(
      page.getByText("Automatically smoothed splines are penalised, so standard errors and p-values are not valid."),
    ).toBeVisible()
    await expect(page.getByText("I(mileage ** 2)")).toBeVisible()
    await expect(page.getByText("channel[T.direct]:bs(mileage, 1/9, k)")).toBeVisible()
  })

  test("trains an XGBoost node and saves its model file", async ({ page }) => {
    test.slow()
    await trainFamilyAndSaveModel(page, "xgboost", { num_boost_round: 20, eta: 0.3, max_depth: 3 }, ".ubj")
  })

  test("trains a LightGBM node and saves its model file", async ({ page }) => {
    test.slow()
    await trainFamilyAndSaveModel(
      page,
      "lightgbm",
      { num_iterations: 20, learning_rate: 0.3, num_leaves: 7, min_data_in_leaf: 3 },
      ".lgbm",
    )
  })

  test("chooses an EBM interaction, trains it, and reads the interaction surface", async ({ page }) => {
    test.slow()
    addModellingNode("browser_ebm", "ebm", { max_rounds: 40, interactions: [] })
    await page.goto("/")

    const ebmNode = page.getByRole("button", { name: /browser_ebm/i })
    await expect(ebmNode).toBeVisible()
    await ebmNode.click()
    const panes = page.getByRole("tablist", { name: "Modelling panes" })
    await panes.getByRole("tab", { name: "Features", exact: true }).click()
    await expect(page.getByRole("heading", { name: "Pairwise interactions" })).toBeVisible()
    await expect(page.getByRole("radio", { name: "Choose pairs" })).toBeChecked()
    // The node was added to the file just now: wait for its upstream schema.
    await expect(page.getByRole("group", { name: "mileage feature" })).toBeVisible({ timeout: 60_000 })
    await page.getByRole("button", { name: "Add interaction" }).click()
    const pair = page.getByRole("group", { name: "Interaction 1" })
    await pair.getByRole("combobox", { name: "Interaction 1 feature 1" }).selectOption("channel")
    await pair.getByRole("combobox", { name: "Interaction 1 feature 2" }).selectOption("mileage")

    await panes.getByRole("tab", { name: "Train", exact: true }).click()
    await page.getByRole("button", { name: /Train Model/i }).click()
    await expect(
      page.getByText(/Model trained - results in preview panel below/i),
    ).toBeVisible({ timeout: 120_000 })
    const resultTabs = page.getByRole("tablist", { name: "Model result panes" })
    await resultTabs.getByRole("tab", { name: "Terms", exact: true }).click()
    await page.getByRole("button", { name: /channel & mileage/ }).click()
    await expect(page.getByText(/Pairwise interaction/)).toBeVisible()
    await expect(
      page.getByRole("table", { name: "Interaction surface for channel & mileage" }),
    ).toBeVisible()
    await expect(page.getByText("Additive term scores on the model", { exact: false })).toBeVisible()
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

  test("does not re-send the document it just loaded when live sync connects", async ({ page }) => {
    let resyncFingerprint: string | undefined
    const receivedDocumentPreambles: string[] = []
    page.on("websocket", (socket) => {
      if (!new URL(socket.url()).pathname.endsWith("/ws/sync")) return
      socket.on("framesent", ({ payload }) => {
        const message = JSON.parse(String(payload)) as { type?: string; document_fingerprint?: string }
        if (message.type === "resync") resyncFingerprint = message.document_fingerprint
      })
      socket.on("framereceived", ({ payload }) => {
        const message = JSON.parse(String(payload)) as { type?: string; document?: { preamble?: string } }
        if (message.type === "pipeline_document_update") {
          receivedDocumentPreambles.push(message.document?.preamble ?? "")
        }
      })
    })
    const loadResponse = page.waitForResponse((response) =>
      response.request().method() === "GET" && new URL(response.url()).pathname === "/api/pipeline",
    )

    await page.goto("/")
    const loadedFingerprint = (await loadResponse).headers()["x-haute-document-fingerprint"]
    expect(loadedFingerprint).toMatch(/^[0-9a-f]{64}$/)
    await expect.poll(() => resyncFingerprint).toBe(loadedFingerprint)

    // The first document frame must be this external edit, not a copy of the loaded document.
    const original = readFileSync(gitMainPath, "utf8")
    const constructorAnchor = original.match(/^pipeline\s*=\s*haute\.Pipeline\(.+$/m)?.[0]
    if (!constructorAnchor) throw new Error("E2E pipeline fixture has no pipeline constructor")
    try {
      writeFileSync(
        gitMainPath,
        original.replace(constructorAnchor, `import statistics as initial_sync_probe\n\n${constructorAnchor}`),
        "utf8",
      )
      await expect.poll(() => receivedDocumentPreambles.length).toBeGreaterThan(0)
      expect(receivedDocumentPreambles[0]).toContain("initial_sync_probe")
    } finally {
      writeFileSync(gitMainPath, original, "utf8")
    }
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
      .toMatch(/submodel = haute\.Submodel\(\s*"browser_group",/)

    await page.reload()
    await expect(submodelNode).toBeVisible()

    const sourceNode = page.getByTestId("rf__node-browser_optimiser_rows")
    const sourceHandle = sourceNode.getByTestId("output-connector[0]:browser_optimiser_rows")
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
    const frameName = "browser_optimiser_rows"
    await connectHandles(page, sourceHandle, newInputHandle)
    // The new public input is named after the frame it receives (F13): its
    // handle is in__<name>, no longer an opaque in__input_<n>.
    await expect(submodelNode.locator(`[data-handleid="in__${frameName}"]`)).toBeAttached()
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
      .toContain(`"name": "${frameName}"`)
  })

  test("renames a submodel occurrence, updating its alias and downstream bindings across preview and save", async ({
    page,
  }) => {
    await page.goto("/")

    const rawRowsNode = page.getByRole("button", { name: /raw_rows/i })
    const enrichedNode = page.getByRole("button", { name: /enriched/i })
    await expect(rawRowsNode).toBeVisible()
    await expect(enrichedNode).toBeVisible()
    await rawRowsNode.click()
    await enrichedNode.click({
      modifiers: [process.platform === "darwin" ? "Meta" : "Control"],
    })
    await dispatchAppShortcut(page, "g")

    await expect(page.getByText("Create Submodel")).toBeVisible()
    const nameInput = page.getByPlaceholder("e.g. model_scoring")
    await nameInput.fill("browser_group")
    await page.getByRole("button", { name: "Create" }).click()

    const submodelNode = page.getByRole("button", { name: /browser_group/i })
    await expect(submodelNode).toBeVisible()

    // Rename the occurrence using the node panel header input (matching the ordinary rename flow at ~111-124)
    await submodelNode.click()
    const labelInput = page.locator("input.node-label-input")
    await expect(labelInput).toHaveValue("browser_group")
    const renamedOccurrence = "renamed_group"
    await labelInput.fill(renamedOccurrence)
    await labelInput.blur()

    // (a) Canvas reflects the new occurrence name
    const renamedNode = page.getByRole("button", { name: new RegExp(renamedOccurrence, "i") })
    await expect(renamedNode).toBeVisible()

    // (b) Downstream node fed by the occurrence still previews successfully
    const downstreamNode = page.getByRole("button", { name: /browser_mixed_banding/i })
    await expect(downstreamNode).toBeVisible()
    await downstreamNode.click()
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()

    // (c) After Save, pipeline source contains pipeline.submodel("modules/browser_group.py", "<new name>"), connect("<new name>", and no instance_id=, definition_id=, alias=, or label=
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    await expect
      .poll(() => readFileSync(gitMainPath, "utf8"))
      .toContain(`pipeline.submodel("modules/browser_group.py", "${renamedOccurrence}")`)
    await expect
      .poll(() => readFileSync(gitMainPath, "utf8"))
      .toContain(`connect("${renamedOccurrence}"`)
    const savedSource = readFileSync(gitMainPath, "utf8")
    expect(savedSource).not.toMatch(/instance_id\s*=/)
    expect(savedSource).not.toMatch(/definition_id\s*=/)
    expect(savedSource).not.toMatch(/alias\s*=/)
    expect(savedSource).not.toMatch(/label\s*=/)

    // (d) Reload fail-safe / reload persistence: reload page, verify occurrence is visible, drill into it, verify breadcrumbs and boundary cards
    await page.reload()
    const reloadedOccurrenceNode = page.getByRole("button", { name: new RegExp(renamedOccurrence, "i") })
    await expect(reloadedOccurrenceNode).toBeVisible()
    await dispatchNodeDoubleClick(page, renamedOccurrence)
    await expect(page.getByRole("button", { name: renamedOccurrence, exact: true })).toBeVisible()
    await expect(page.getByTestId("submodel-boundary-card").first()).toBeVisible()
  })
})
