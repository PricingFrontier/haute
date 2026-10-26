import { execFileSync } from "node:child_process"

import { expect, test } from "@playwright/test"

import {
  addNovelRows,
  addScoreNode,
  appendToPipeline,
  asNumber,
  columnValues,
  openNode,
  refreshPreview,
} from "./modelScoring"
import { e2eProjectRoot, repoRoot, resetE2eProject } from "./projectIsolation"

// Every family trained by the modelling journeys (core-flows) is scored there, over
// novel_rows. These journeys cover the rest of Model Scoring with models trained
// beforehand by scripts/e2e_scoring_models.py, so none trains in the browser.

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

test.describe.configure({ mode: "serial" })

test.describe("model scoring", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a classifier adds the probability of the positive class", async ({ page }) => {
    test.slow()
    const paths = seedModels(["classifier"])
    addNovelRows()
    addScoreNode("score_classifier", "novel_rows", paths.score_classifier, "classification")
    await page.goto("/")

    await openNode(page, "score_classifier")
    await expect(page.getByTestId("model-score-recorded-task")).toHaveText("Classification")
    const table = await refreshPreview(page)
    const labels = await columnValues(table, "prediction")
    const probabilities = (await columnValues(table, "prediction_proba")).map(asNumber)
    expect(labels.length).toBeGreaterThan(0)
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
