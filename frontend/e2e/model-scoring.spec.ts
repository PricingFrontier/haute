import { execFileSync } from "node:child_process"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, repoRoot, resetE2eProject } from "./projectIsolation"

const ratingDir = resolve(e2eProjectRoot, "rating")
const mainPath = resolve(ratingDir, "main.py")
const scoringConfigDir = resolve(ratingDir, "config", "model_scoring")
const regressionFamilies = ["catboost", "xgboost", "lightgbm", "ebm", "tboost", "glm"]

/** Train saved models into the project and return each one's pipeline-relative path. */
function seedModels(models: string[]): Record<string, string> {
  const stdout = execFileSync(
    "uv",
    ["run", "python", "scripts/e2e_scoring_models.py", "--seed", e2eProjectRoot, "--models", models.join(",")],
    { cwd: repoRoot, encoding: "utf8" },
  )
  // Training log lines may precede the JSON summary; its root brace is the only line that is exactly "{".
  const lines = stdout.split(/\r?\n/)
  const start = lines.indexOf("{")
  if (start === -1) throw new Error(`e2e_scoring_models printed no JSON summary:\n${stdout}`)
  return JSON.parse(lines.slice(start).join("\n")) as Record<string, string>
}

// resetE2eProject restores main.py and removes these sidecars before the next test.
function appendToPipeline(block: string): void {
  writeFileSync(mainPath, readFileSync(mainPath, "utf8").trimEnd() + "\n\n\n" + block.trim() + "\n", "utf8")
}

function addScoreNode(
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

// Every third row holds a channel no model was trained on, and the next a missing one.
const novelRows = `
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

async function openNode(page: Page, name: string): Promise<void> {
  const node = page.getByRole("button", { name: new RegExp(name, "i") })
  await expect(node).toBeVisible()
  await node.click()
}

async function refreshPreview(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "Refresh", exact: true }).click()
  const table = page.getByTestId("data-preview-table")
  await expect(table).toBeVisible({ timeout: 60_000 })
  return table
}

/** The text of every rendered row's cell in *column* (the first cell is the row number). */
async function columnValues(table: Locator, column: string): Promise<string[]> {
  const headers = (await table.locator("thead th > div:first-child").allInnerTexts()).map((text) => text.trim())
  const index = headers.indexOf(column)
  expect(index, `${column} in ${headers.join(", ")}`).toBeGreaterThanOrEqual(0)
  return table.locator("tbody tr").evaluateAll(
    (rows, cell) => rows.map((row) => (row.children[cell] as HTMLElement).innerText.trim()),
    index + 1,
  )
}

function asNumber(text: string): number {
  return Number.parseFloat(text.replace(/,/g, ""))
}

test.describe.configure({ mode: "serial" })

test.describe("model scoring", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("scores a saved model of every family, including categories it never saw and missing values", async ({ page }) => {
    test.slow()
    const paths = seedModels(regressionFamilies)
    appendToPipeline(novelRows)
    for (const family of regressionFamilies) {
      addScoreNode("score_" + family, "novel_rows", paths["score_" + family], "regression")
    }
    await page.goto("/")

    for (const family of regressionFamilies) {
      await openNode(page, "score_" + family)
      await expect(page.getByText("Task recorded with the model.")).toBeVisible()
      const table = await refreshPreview(page)
      const channels = await columnValues(table, "channel")
      expect(channels, family).toContain("carrier_pigeon")
      const predictions = await columnValues(table, "prediction")
      expect(predictions.length, family).toBe(channels.length)
      for (const prediction of predictions) {
        expect(Number.isFinite(asNumber(prediction)), `${family}: ${prediction}`).toBe(true)
      }
    }
  })

  test("a classifier adds the probability of the positive class", async ({ page }) => {
    test.slow()
    const paths = seedModels(["classifier"])
    appendToPipeline(novelRows)
    addScoreNode("score_classifier", "novel_rows", paths.score_classifier, "classification")
    await page.goto("/")

    await openNode(page, "score_classifier")
    await expect(page.getByTestId("model-score-recorded-task")).toHaveText("Classification")
    const table = await refreshPreview(page)
    const labels = await columnValues(table, "prediction")
    const probabilities = (await columnValues(table, "prediction_proba")).map(asNumber)
    expect(labels.length).toBeGreaterThan(0)
    expect(new Set(labels)).toEqual(new Set(labels.filter((label) => label === "yes" || label === "no")))
    expect(probabilities).toHaveLength(labels.length)
    labels.forEach((label, row) => {
      expect(probabilities[row]).toBeGreaterThan(0)
      expect(probabilities[row]).toBeLessThan(1)
      expect(label).toBe(probabilities[row] > 0.5 ? "yes" : "no")
    })
  })

  test("a node scoring as the wrong task says so and fixes it in one click", async ({ page }) => {
    test.slow()
    const paths = seedModels(["xgboost"])
    addScoreNode("score_xgboost", "raw_rows", paths.score_xgboost, "classification")
    await page.goto("/")

    await openNode(page, "score_xgboost")
    const warning = page.getByRole("alert").filter({ hasText: "This node scores as classification" })
    await expect(warning).toContainText("but the model was trained for regression.")
    await page.getByRole("button", { name: "Refresh", exact: true }).click()
    await expect(
      page.getByText(/model was trained for regression but the node scores it as classification/).first(),
    ).toBeVisible({ timeout: 60_000 })

    await warning.getByRole("button", { name: "Use regression" }).click()
    await expect(warning).toHaveCount(0)
    const table = await refreshPreview(page)
    const predictions = await columnValues(table, "prediction")
    expect(predictions.length).toBeGreaterThan(0)
    expect(predictions.every((prediction) => Number.isFinite(asNumber(prediction)))).toBe(true)
  })

  test("a missing feature fails with an error that names it", async ({ page }) => {
    test.slow()
    const paths = seedModels(["glm"])
    appendToPipeline(`
@pipeline.polars
def no_mileage(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    return raw_rows.drop("mileage")
`)
    addScoreNode("score_glm", "no_mileage", paths.score_glm, "regression")
    await page.goto("/")

    await openNode(page, "score_glm")
    await page.getByRole("button", { name: "Refresh", exact: true }).click()
    // Column planning catches the gap before the model loads, naming the node and the column.
    await expect(
      page.getByText("'score_glm' needs the column 'mileage', which is not in its input.").first(),
    ).toBeVisible({ timeout: 60_000 })
  })
})
