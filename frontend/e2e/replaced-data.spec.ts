import { execFileSync } from "node:child_process"
import { mkdirSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, repoRoot, resetE2eProject } from "./projectIsolation"

// A data file replaced on disk while the editor is open: nothing in the graph changes,
// so only the node's Refresh can find out, and one Refresh must show the file as it is.

const ratingDir = resolve(e2eProjectRoot, "rating")
const parquetPath = resolve(ratingDir, "data", "extract.parquet")
const csvPath = resolve(ratingDir, "data", "extract.csv")

type Columns = Record<string, (number | string)[]>

function rows(count: number, from = 0): number[] {
  return Array.from({ length: count }, (_, index) => from + index)
}

/** The first extract: 30 rows of six columns. */
function firstExtract(): Columns {
  const ids = rows(30, 1)
  return {
    policy_id: ids,
    region: ids.map((id) => (id % 2 === 0 ? "north" : "south")),
    value: ids.map((id) => 7 + id * 4),
    mileage: ids.map((id) => 1000 * id),
    premium: ids.map((id) => 100 + id),
    age: ids.map((id) => 20 + id),
  }
}

function writeParquet(columns: Columns): void {
  execFileSync(
    "uv",
    ["run", "python", "-I", "-", parquetPath, JSON.stringify(columns)],
    {
      cwd: repoRoot,
      input: "import json, sys\nimport polars as pl\npl.DataFrame(json.loads(sys.argv[2])).write_parquet(sys.argv[1])\n",
      encoding: "utf8",
    },
  )
}

function writeCsv(count: number, valueFrom: number): void {
  const lines = rows(count, 1).map((id) => `${id},${valueFrom + id}`)
  writeFileSync(csvPath, `policy_id,value\n${lines.join("\n")}\n`, "utf8")
}

function writeJson(relative: string, value: unknown): void {
  const path = resolve(ratingDir, relative)
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf8")
}

function writeProject(): void {
  writeJson("config/data_input/extract.json", {
    input_type: "file",
    format: "parquet",
    mode: "scan",
    path: "data/extract.parquet",
    arguments: {},
  })
  writeJson("config/data_input/csv_extract.json", {
    input_type: "file",
    format: "csv",
    mode: "scan",
    path: "data/extract.csv",
    arguments: { schema: { policy_id: "int64", value: "int64" } },
  })
  const overview = { overview: { dataset_snapshot: true, schema: true } }
  writeJson("config/explore/extract_explore.json", overview)
  writeJson("config/explore/csv_explore.json", overview)
  writeJson("config/banding/extract_banding.json", {
    factors: [
      {
        banding: "categorical",
        column: "region",
        output_column: "region_band",
        rules: { north: "North", south: "South" },
        default: "Other region",
      },
    ],
  })
  writeParquet(firstExtract())
  writeCsv(30, 10)
  writeFileSync(
    resolve(ratingDir, "main.py"),
    [
      '"""Inputs whose files a browser journey replaces on disk."""',
      "",
      "import polars as pl",
      "",
      "import haute",
      "",
      'pipeline = haute.Pipeline("replaced_data_e2e")',
      "",
      "",
      '@pipeline.data_input(config="config/data_input/extract.json")',
      "def extract(): ...",
      "",
      "",
      "@pipeline.polars",
      "def passed(extract: pl.LazyFrame) -> pl.LazyFrame:",
      "    return extract",
      "",
      "",
      '@pipeline.explore(config="config/explore/extract_explore.json")',
      "def extract_explore(extract): ...",
      "",
      "",
      '@pipeline.banding(config="config/banding/extract_banding.json")',
      "def extract_banding(extract): ...",
      "",
      "",
      '@pipeline.data_input(config="config/data_input/csv_extract.json")',
      "def csv_extract(): ...",
      "",
      "",
      '@pipeline.explore(config="config/explore/csv_explore.json")',
      "def csv_explore(csv_extract): ...",
      "",
    ].join("\n"),
    "utf8",
  )
}

async function openApp(page: Page): Promise<void> {
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
}

async function openNode(page: Page, label: string): Promise<void> {
  // The label gains the node's status (", status: ok") once it has run.
  const node = page.getByLabel(new RegExp(`node: ${label}(,|$)`, "i"))
  await expect(node).toBeVisible()
  await expect(async () => {
    await node.click({ force: true })
    await expect(page.getByTestId("node-panel-label-input")).toHaveValue(label, { timeout: 2_000 })
  }).toPass({ timeout: 15_000 })
}

/** The open data preview's column headers, once it shows a table. */
async function previewHeaders(page: Page): Promise<string[]> {
  const table = page.getByTestId("data-preview-table")
  await expect(table).toBeVisible({ timeout: 60_000 })
  return (await table.locator("thead th > div:first-child").allInnerTexts()).map((text) => text.trim())
}

async function expectPreviewColumns(page: Page, columns: string[]): Promise<void> {
  await expect.poll(() => previewHeaders(page), { timeout: 60_000 }).toEqual(columns)
}

function refreshButton(page: Page, frame: "preview-panel-frame-header" | "explore-preview-frame-header"): Locator {
  return page.getByTestId(frame).getByRole("button", { name: "Refresh", exact: true })
}

/** The preview frame's Overview tab; the node's editor has one of its own. */
function overviewTab(page: Page): Locator {
  return page.getByTestId("explore-preview-frame").getByRole("tab", { name: "Overview", exact: true })
}

function datasetRows(page: Page): Locator {
  return page.getByTestId("explore-dataset-snapshot-card")
}

test.describe.configure({ mode: "serial" })

test.describe("a data file replaced under an open editor", () => {
  test.beforeEach(() => {
    resetE2eProject()
    writeProject()
  })

  test("one Refresh previews a Parquet file that lost, gained or renamed a column, on the input and below it", async ({ page }) => {
    test.slow()
    const first = ["policy_id", "region", "value", "mileage", "premium", "age"]
    await openApp(page)
    await openNode(page, "extract")
    await expectPreviewColumns(page, first)
    await openNode(page, "passed")
    await expectPreviewColumns(page, first)

    const lost = firstExtract()
    delete lost.mileage
    const gained = { ...lost, channel: lost.policy_id.map((id) => (Number(id) % 3 === 0 ? "broker" : "direct")) }
    const { premium, ...withoutPremium } = gained
    const renamed = { ...withoutPremium, gross_premium: premium }
    const changes: Array<[string, Columns, string[]]> = [
      ["loses mileage", lost, ["policy_id", "region", "value", "premium", "age"]],
      ["gains channel", gained, ["policy_id", "region", "value", "premium", "age", "channel"]],
      ["renames premium", renamed, ["policy_id", "region", "value", "age", "channel", "gross_premium"]],
    ]
    for (const [change, columns, expected] of changes) {
      await test.step(change, async () => {
        writeParquet(columns)
        await openNode(page, "extract")
        await refreshButton(page, "preview-panel-frame-header").click()
        await expectPreviewColumns(page, expected)

        await openNode(page, "passed")
        await refreshButton(page, "preview-panel-frame-header").click()
        await expectPreviewColumns(page, expected)

        // Coming back shows the table the Refresh showed.
        await openNode(page, "extract")
        await expectPreviewColumns(page, expected)
        await expect(page.getByText(/not found on target|Eager projection/)).toHaveCount(0)
      })
    }
  })

  test("one Refresh brings Explore's Overview to a Parquet file replaced by a new extract", async ({ page }) => {
    test.slow()
    await openApp(page)
    await openNode(page, "extract_explore")
    await overviewTab(page).click()
    await expect(datasetRows(page)).toContainText(/Rows\s*30(?!\d)/, { timeout: 60_000 })
    await expect(page.getByTestId("explore-schema-row-mileage")).toBeVisible()

    const replaced = firstExtract()
    for (const name of Object.keys(replaced)) replaced[name] = replaced[name].slice(0, 12)
    delete replaced.mileage
    writeParquet(replaced)
    await refreshButton(page, "explore-preview-frame-header").click()

    await expect(datasetRows(page)).toContainText(/Rows\s*12(?!\d)/, { timeout: 60_000 })
    await expect(page.getByTestId("explore-schema-row-mileage")).toHaveCount(0)
    await expect(page.getByTestId("explore-schema-row-premium")).toBeVisible()
  })

  test("one Refresh brings Explore's Overview to a CSV file Haute snapshots, replaced by a new extract", async ({ page }) => {
    test.slow()
    await openApp(page)
    await openNode(page, "csv_explore")
    await overviewTab(page).click()
    // Nothing is cached yet: Refresh snapshots the file.
    await refreshButton(page, "explore-preview-frame-header").click()
    await expect(datasetRows(page)).toContainText(/Rows\s*30(?!\d)/, { timeout: 60_000 })

    writeCsv(12, 500)
    await refreshButton(page, "explore-preview-frame-header").click()

    await expect(datasetRows(page)).toContainText(/Rows\s*12(?!\d)/, { timeout: 60_000 })
  })

  test("one Refresh brings the Banding editor's count and values to a replaced file", async ({ page }) => {
    test.slow()
    await openApp(page)
    await openNode(page, "extract_banding")
    const basis = page.getByTestId("banding-data-basis")
    await expect(basis).toHaveText("All rows · 30", { timeout: 60_000 })

    const replaced = firstExtract()
    for (const name of Object.keys(replaced)) replaced[name] = replaced[name].slice(0, 12)
    replaced.region = replaced.region.map((region, index) => (index < 4 ? "east" : region))
    writeParquet(replaced)
    await refreshButton(page, "preview-panel-frame-header").click()

    await expect(basis).toHaveText("All rows · 12", { timeout: 60_000 })
    // Counted over the whole new file, not the preview's rows.
    await expect(page.getByRole("button", { name: "east (4)", exact: true })).toBeVisible()
  })
})
