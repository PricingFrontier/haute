import { existsSync, readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const sidecarPath = resolve(e2eProjectRoot, "rating", "config", "polars", "browser_steps.json")
const mainPath = resolve(e2eProjectRoot, "rating", "main.py")

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

test.describe("Transform step builder journey", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("builds a stepped transform, previews it, saves its sidecar and reopens it", async ({ page }) => {
    test.slow()
    await openApp(page)

    // A new Transform starts in step mode with no input: it asks for one.
    const canvas = page.locator(".react-flow")
    await expect(canvas).toBeVisible()
    await page.getByTestId("node-palette-item-polars").dragTo(canvas, {
      targetPosition: { x: 250, y: 150 },
    })
    const newNode = page.getByLabel(/Polars node: Polars/i)
    await expect(newNode).toBeVisible()
    await newNode.click()
    const panel = page.getByTestId("node-panel")
    await expect(panel).toBeVisible()
    await expect(panel.getByText(/Connect an input to start building steps/)).toBeVisible()
    const label = page.getByTestId("node-panel-label-input")
    await label.fill("browser_steps")
    await label.press("Enter")
    await expect(label).toHaveValue("browser_steps")
    await page.getByTestId("node-panel-close").click()

    // Connecting the only input seeds the start step and renders `df = raw_rows`.
    const stepsNode = page.getByLabel(/Polars node: browser_steps/i)
    await expect(stepsNode).toBeVisible()
    await connect(
      page,
      page.getByTestId("rf__node-raw_rows").getByTestId("output-connector[0]:raw_rows"),
      stepsNode.getByTestId("input-connector[0]:browser_steps"),
    )
    await stepsNode.click()
    await expect(panel).toBeVisible()
    const editor = panel.getByTestId("polars-steps-editor")
    await expect(editor).toBeVisible()
    await expect(editor.getByLabel("Start from input")).toHaveValue("raw_rows")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = raw_rows")

    // Adding a step from the chooser opens its card; editing it re-renders the code.
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Limit rows" }).click()
    await expect(editor.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toHaveAttribute("aria-expanded", "true")
    const rowLimit = editor.getByLabel("Row limit")
    await rowLimit.fill("3")
    await rowLimit.press("Enter")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = df.head(3)")

    // Free code uses the current frame, and later low-code steps keep working.
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Free code" }).click()
    const snippet = 'df = df.with_columns(\n    pl.lit("custom").alias("step_marker")\n)'
    await editor.locator(".cm-content").fill(snippet)
    await expect(editor.getByText("df holds the current frame.", { exact: false })).toHaveCount(0)
    const codeBox = editor.getByTestId("free-code-box")
    await codeBox.scrollIntoViewIfNeeded()
    const initialBox = await codeBox.boundingBox()
    expect(initialBox).not.toBeNull()
    expect(initialBox!.height).toBeLessThanOrEqual(122)
    // Drag the native resize handle; CodeMirror fills the expanded box.
    await page.mouse.move(initialBox!.x + initialBox!.width - 4, initialBox!.y + initialBox!.height - 4)
    await page.mouse.down()
    await page.mouse.move(initialBox!.x + initialBox!.width - 4, initialBox!.y + initialBox!.height + 96, { steps: 8 })
    await page.mouse.up()
    await expect.poll(async () => (await codeBox.boundingBox())!.height).toBeGreaterThan(200)
    const expandedBox = (await codeBox.boundingBox())!
    const editorBox = (await codeBox.locator(".cm-editor").boundingBox())!
    expect(Math.abs(expandedBox.height - editorBox.height)).toBeLessThanOrEqual(2)
    await expect(editor.getByTestId("polars-generated-code")).toContainText('pl.lit("custom").alias("step_marker")')
    await editor.getByRole("button", { name: "Add step" }).click()
    await editor.getByRole("menuitem", { name: "Limit rows" }).click()
    await editor.getByLabel("Row limit").fill("2")
    await editor.getByLabel("Row limit").press("Enter")
    await expect(editor.getByTestId("polars-generated-code")).toContainText("df = df.head(2)")

    // The preview runs the rendered program.
    await page.getByRole("button", { name: "Refresh" }).click()
    const previewTable = page.getByRole("table").first()
    await expect(previewTable).toBeVisible()
    await expect(previewTable.getByRole("cell").first()).toBeVisible()
    await expect(previewTable.getByRole("columnheader", { name: /step_marker/ })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "custom", exact: true })).toHaveCount(2)

    // Saving writes the steps to the node's sidecar and the rendered body to the module.
    await save(page)
    await expect.poll(() => existsSync(sidecarPath)).toBe(true)
    const sidecar = JSON.parse(readFileSync(sidecarPath, "utf8")) as { steps: Array<{ kind: string }> }
    expect(sidecar.steps.map((step) => step.kind)).toEqual(["source", "limit", "free_code", "limit"])
    expect(sidecar.steps[2]).toMatchObject({ code: snippet })
    const main = readFileSync(mainPath, "utf8")
    expect(main).toContain('config="config/polars/browser_steps.json"')
    expect(main).toContain("def browser_steps(raw_rows: pl.LazyFrame)")
    expect(main).toContain("df = df.head(3)")

    // Reopening parses the sidecar back into the same step cards.
    await page.reload()
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await page.getByLabel(/Polars node: browser_steps/i).click()
    await expect(panel).toBeVisible()
    await expect(panel.getByRole("button", { name: "Step 1: Limit rows", exact: true })).toBeVisible()
    await expect(panel.getByTestId("polars-generated-code")).toContainText("df = df.head(3)")
    await expect(panel.getByRole("button", { name: "Step 3: Limit rows", exact: true })).toBeVisible()
    await panel.getByRole("button", { name: "Step 2: Free code", exact: true }).click()
    await expect(panel.locator(".cm-content .cm-line")).toHaveText(snippet.split("\n"))
  })
})
