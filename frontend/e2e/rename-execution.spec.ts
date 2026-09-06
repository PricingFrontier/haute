import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

test.describe.configure({ mode: "serial" })

test.describe("rename execution stability", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("renaming an upstream node keeps downstream Python executable through preview, save and reload", async ({ page }) => {
    // 1. Open / and preview enriched node
    await page.goto("/")

    const enrichedNode = page.getByTestId("node-enriched")
    await expect(enrichedNode).toBeVisible()
    await enrichedNode.click()

    await page.getByRole("button", { name: "Refresh" }).click()

    const previewTable = page.getByRole("table").first()
    await expect(previewTable.getByText("value_doubled", { exact: true })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "22" }).first()).toBeVisible()

    // 2. Click raw_rows and rename label to raw_rows_browser
    const rawRowsNode = page.getByTestId("node-raw_rows")
    await expect(rawRowsNode).toBeVisible()
    await rawRowsNode.click()

    const labelInput = page.locator("input.node-label-input")
    await expect(labelInput).toHaveValue("raw_rows")
    await labelInput.fill("raw_rows_browser")
    await labelInput.blur()

    const renamedButton = page.getByTestId("node-raw_rows_browser")
    await expect(renamedButton).toBeVisible()

    // 3. Click enriched and Refresh again — preview table shows value_doubled and 22
    await page.getByTestId("node-enriched").click()
    await page.getByRole("button", { name: "Refresh" }).click()

    await expect(previewTable.getByText("value_doubled", { exact: true })).toBeVisible()
    await expect(previewTable.getByRole("cell", { name: "22" }).first()).toBeVisible()

    // 4. Save and verify main.py codegen
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()

    const mainPyPath = resolve(e2eProjectRoot, "rating", "main.py")
    const mainPyContent = readFileSync(mainPyPath, "utf-8")
    expect(mainPyContent).toMatch(/def enriched\(raw_rows:\s*pl\.LazyFrame\)/)
    expect(mainPyContent).toMatch(/inputMapping\s*=\s*\{['"]raw_rows['"]:\s*['"]raw_rows_browser['"]\}/)
    expect(mainPyContent).toContain("df = raw_rows.with_columns")

    // 5. Reload the page, click enriched, Refresh, assert value_doubled, 22, and node properties trace text
    await page.reload()

    await expect(page.getByTestId("node-enriched")).toBeVisible()
    await page.getByTestId("node-enriched").click()

    await page.getByRole("button", { name: "Refresh" }).click()

    const reloadedTable = page.getByRole("table").first()
    await expect(reloadedTable.getByText("value_doubled", { exact: true })).toBeVisible()

    const cell22 = reloadedTable.getByRole("cell", { name: "22" }).first()
    await expect(cell22).toBeVisible()
    await cell22.click()

    const nodeProperties = page.getByRole("complementary", { name: /node properties/i })
    await expect(nodeProperties.getByText(/Trace: value_doubled/i)).toBeVisible()
    await expect(nodeProperties.getByText(/\(raw_rows(_browser)?\)/i)).toBeVisible()
  })
})
