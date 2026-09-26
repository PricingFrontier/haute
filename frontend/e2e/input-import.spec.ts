import { mkdirSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const pipelinePath = resolve(ratingDir, "main.py")
const configPath = resolve(ratingDir, "config", "data_input", "import_rows.json")
const dataPath = resolve(ratingDir, "data", "import_rows.csv")

function writeRows(count: number): void {
  const rows = Array.from({ length: count }, (_, index) => `${index + 1},row-${index + 1}`)
  writeFileSync(dataPath, `id,label\n${rows.join("\n")}\n`, "utf8")
}

function writeProject(): void {
  mkdirSync(dirname(configPath), { recursive: true })
  mkdirSync(dirname(dataPath), { recursive: true })
  writeFileSync(
    configPath,
    `${JSON.stringify({
      inputType: "file",
      format: "csv",
      mode: "scan",
      path: "data/import_rows.csv",
      arguments: { schema: { id: "int64", label: "str" } },
    }, null, 2)}\n`,
    "utf8",
  )
  writeRows(2)
  writeFileSync(
    pipelinePath,
    [
      '"""A snapshot-backed input for the Import browser journey."""',
      "",
      "import polars as pl",
      "",
      "import haute",
      "",
      'pipeline = haute.Pipeline("input_import_e2e")',
      "",
      '@pipeline.data_input(config="config/data_input/import_rows.json")',
      "def import_rows(): ...",
      "",
    ].join("\n"),
    "utf8",
  )
}

async function openInput(page: Page): Promise<void> {
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  const node = page.getByLabel(/Data Input node: import_rows/i)
  await expect(node).toBeVisible()
  await expect(async () => {
    await node.click({ force: true })
    await expect(page.getByTestId("node-panel")).toBeVisible({ timeout: 2_000 })
  }).toPass({ timeout: 15_000 })
}

test.describe("Import", () => {
  test.beforeEach(() => {
    resetE2eProject()
    writeProject()
  })

  test("re-reads a snapshot-backed input and shows the new rows", async ({ page }) => {
    await openInput(page)
    const header = page.getByTestId("preview-panel-frame-header")
    const importButton = header.getByTestId("input-import")
    await expect(importButton).toBeVisible()
    await expect(page.getByText("2 rows", { exact: false }).first()).toBeVisible({ timeout: 60_000 })

    writeRows(5)
    await importButton.click()

    // The import re-reads the source, then previews the input again.
    await expect(page.getByText("5 rows", { exact: false }).first()).toBeVisible({ timeout: 60_000 })
    await expect(importButton).toHaveText("Import")
    await expect(importButton).toHaveAttribute("title", /^Imported just now\./)
  })
})
