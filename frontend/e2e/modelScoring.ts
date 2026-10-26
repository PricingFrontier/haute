import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const mainPath = resolve(ratingDir, "main.py")
const scoringConfigDir = resolve(ratingDir, "config", "model_scoring")

// Every third row holds a channel no model was trained on, and the next a missing one.
const novelRowsBlock = `
@pipeline.polars
def novel_rows(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows.with_columns(
        channel=pl.when(pl.col("id") % 3 == 0)
        .then(pl.lit("carrier_pigeon"))
        .when(pl.col("id") % 3 == 1)
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(pl.col("channel"))
    )
`

/** Append *block* to the pipeline file; resetE2eProject restores it before the next test. */
export function appendToPipeline(block: string): void {
  writeFileSync(mainPath, readFileSync(mainPath, "utf8").trimEnd() + "\n\n\n" + block.trim() + "\n", "utf8")
}

/** Add `novel_rows`: raw_rows with channels the models never saw and missing ones. */
export function addNovelRows(): void {
  appendToPipeline(novelRowsBlock)
}

/** Add a Model Scoring node reading *modelPath* (relative to `rating/`) over *source*. */
export function addScoreNode(
  name: string,
  source: string,
  modelPath: string,
  task: "regression" | "classification",
): void {
  mkdirSync(scoringConfigDir, { recursive: true })
  writeFileSync(
    resolve(scoringConfigDir, name + ".json"),
    JSON.stringify({ sourceType: "file", model_path: modelPath, task, output_column: "prediction" }, null, 2),
    "utf8",
  )
  appendToPipeline(`@pipeline.model_score(config="config/model_scoring/${name}.json")\ndef ${name}(${source}): ...`)
}

export async function openNode(page: Page, name: string): Promise<void> {
  const node = page.getByRole("button", { name: new RegExp(name, "i") })
  await expect(node).toBeVisible()
  await node.click()
}

export async function refreshPreview(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Refresh", exact: true }).click()
  const table = page.getByTestId("data-preview-table")
  await expect(table).toBeVisible({ timeout: 60_000 })
  return table
}

/** The text of every rendered row's cell in *column* (the first cell is the row number). */
export async function columnValues(table: Locator, column: string): Promise<string[]> {
  const headers = (await table.locator("thead th > div:first-child").allInnerTexts()).map((text) => text.trim())
  const index = headers.indexOf(column)
  expect(index, `${column} in ${headers.join(", ")}`).toBeGreaterThanOrEqual(0)
  return table.locator("tbody tr").evaluateAll(
    (rows, cell) => rows.map((row) => (row.children[cell] as HTMLElement).innerText.trim()),
    index + 1,
  )
}

export function asNumber(text: string): number {
  return Number.parseFloat(text.replace(/,/g, ""))
}

/** Preview *name* and expect a finite prediction on every row, novel channels included. */
export async function expectScoredNovelRows(page: Page, name: string): Promise<void> {
  await openNode(page, name)
  const table = await refreshPreview(page)
  const channels = await columnValues(table, "channel")
  expect(channels, name).toContain("carrier_pigeon")
  const predictions = await columnValues(table, "prediction")
  expect(predictions.length, name).toBe(channels.length)
  for (const prediction of predictions) {
    expect(Number.isFinite(asNumber(prediction)), `${name}: ${prediction}`).toBe(true)
  }
}

/**
 * Score the model file a modelling journey just saved to `rating/files/models`: save
 * the pipeline, add a Model Scoring node over `novel_rows` reading that file, reload,
 * and expect a finite prediction on every row.
 */
export async function scoreSavedModel(page: Page, modelFile: string): Promise<void> {
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(page.getByRole("alert").filter({ hasText: /Saved →/ })).toBeVisible()
  const name = "score_" + modelFile.slice(0, modelFile.lastIndexOf("."))
  addNovelRows()
  addScoreNode(name, "novel_rows", "files/models/" + modelFile, "regression")
  await page.goto("/")
  await expectScoredNovelRows(page, name)
}
