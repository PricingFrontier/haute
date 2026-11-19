import { existsSync, readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"
import { buildWorkbenchPipeline, enableWorkbench, readFormFile } from "./workbenchProject"

// The same keystrokes give the same saved number in every number field, in any browser
// language (specs/frontend-shared high-level, "Typed numbers"). Each field is typed into a
// key at a time, as the analyst types, under English and under German: the browser's own
// number input read `1,5` as 15 in the one and 1.5 in the other. A case the grammar refuses
// (`null` below) is refused at the field in words and never saved; every other is saved as
// the number the case file gives it.

type Case = readonly [typed: string, saved: number | null]

/** What the cases of `frontend/src/utils/__tests__/fixtures/typedText.json` save as, within each field's own range. */
const ANY_NUMBER: readonly Case[] = [
  ["1,5", null],
  ["1 000,50", null],
  ["1_000", null],
  ["nan", null],
  ["1,000", 1000],
  ["0123", 123],
  ["1E5", 100000],
  ["2.5", 2.5],
]

const configFile = (folder: string, node: string) => resolve(e2eProjectRoot, "rating", "config", folder, `${node}.json`)
const readConfig = (folder: string, node: string) => JSON.parse(readFileSync(configFile(folder, node), "utf8"))

async function openNode(page: Page, accessibleName: string): Promise<Locator> {
  await page.getByLabel(accessibleName).click()
  const panel = page.getByRole("complementary", { name: /node properties/i })
  await expect(panel).toBeVisible()
  return panel
}

/** Replace a field's text a key at a time, as the analyst types it, and commit it with Enter. */
async function retype(field: Locator, text: string): Promise<void> {
  await field.click()
  await field.press("ControlOrMeta+a")
  await field.pressSequentially(text)
  await field.press("Enter")
}

/**
 * Type each case into *field* and require what is saved: a refused case keeps its text in the
 * field, marked, and the file what it held; any other is saved as its number, read by *saved*.
 */
async function typeEachCase(page: Page, field: Locator, cases: readonly Case[], saved: () => unknown, toStored: (typed: number) => number = (n) => n): Promise<void> {
  for (const [typed, number] of cases) {
    const before = saved()
    await retype(field, typed)
    if (number === null) {
      await expect(field, `${typed} is refused`).toHaveAttribute("aria-invalid", "true")
      await expect(field).toHaveValue(typed)
      expect(saved(), `${typed} is never saved`).toEqual(before)
      continue
    }
    await expect(field, `${typed} is taken`).not.toHaveAttribute("aria-invalid", "true")
    // The save lands once its answer, which follows the save's commit, is back.
    const landed = page.waitForResponse((response) => response.url().endsWith("/api/pipeline/save") && response.request().method() === "POST")
    await page.getByRole("button", { name: "Save", exact: true }).click()
    expect((await landed).ok(), `${typed} is saved`).toBe(true)
    await expect.poll(saved, { message: `${typed} is saved as ${number}` }).toBe(toStored(number))
  }
}

for (const locale of ["en-GB", "de-DE"]) {
  test.describe(`typed numbers in a browser set to ${locale}`, () => {
    test.use({ locale })
    test.describe.configure({ mode: "serial" })
    test.beforeEach(() => {
      resetE2eProject()
    })
    // A test ends with no commit of the project still holding git's lock, so the next one's
    // reset finds the repository free.
    test.afterEach(async () => {
      await expect.poll(() => existsSync(resolve(e2eProjectRoot, ".git", "index.lock")), { timeout: 30_000 }).toBe(false)
    })

    test(`a Rating Step's Default (${locale})`, async ({ page }) => {
      await page.goto("/")
      const panel = await openNode(page, "Rating Step node: browser_rating")
      await typeEachCase(page, panel.getByLabel("Default", { exact: true }), ANY_NUMBER, () => readConfig("rating_step", "browser_rating").tables[0].default_value)
    })

    test(`a Banding breakpoint (${locale})`, async ({ page }) => {
      await page.goto("/")
      const panel = await openNode(page, "Banding node: browser_mixed_banding")
      await typeEachCase(page, panel.getByLabel("Breakpoint 1 boundary"), [["40,5", null], ["4E1", 40], ["045", 45], ["40.5", 40.5], ["1,000", 1000]], () => readConfig("banding", "browser_mixed_banding").factors[0].rules[0].upper)
    })

    test(`a model's validation share (${locale})`, async ({ page }) => {
      await page.goto("/")
      await page.getByRole("button", { name: /browser_model/i }).click()
      await page.getByRole("tablist", { name: "Modelling panes" }).getByRole("tab", { name: "Split", exact: true }).click()
      await typeEachCase(
        page,
        page.getByLabel("Validation set (%)"),
        [["12,5", null], ["0,5", null], ["nan", null], ["025", 25], ["1E1", 10], ["12.5", 12.5]],
        () => readConfig("model_training", "browser_model").evaluation.validation.size,
        (percent) => percent / 100,
      )
    })

    test(`an optimiser's swept bound and tolerance (${locale})`, async ({ page }) => {
      await page.goto("/")
      let panel = await openNode(page, "Optimisation node: browser_optimiser")
      await panel.getByRole("tab", { name: "Constraints", exact: true }).click()
      await typeEachCase(page, panel.getByLabel("volume sweep from"), [["7,5", null], ["nan", null], ["07.5", 7.5], ["8E0", 8]], () => readConfig("optimisation", "browser_optimiser").frontier_ranges.volume.min)
      panel = page.getByRole("complementary", { name: /node properties/i })
      await panel.getByRole("tab", { name: "Solve", exact: true }).click()
      await typeEachCase(page, panel.getByLabel("Tolerance", { exact: true }), [["0,0001", null], ["1,5", null], ["1E-5", 0.00001], ["0.001", 0.001], ["1,000", 1000]], () => readConfig("optimisation", "browser_optimiser").tolerance)
    })

    test(`a Workbench sheet's width (${locale})`, async ({ page }) => {
      enableWorkbench()
      await page.goto("/")
      await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
      await buildWorkbenchPipeline(page)
      await page.reload()
      await page.getByTestId("view-switcher-workbench").click()
      await page.getByRole("button", { name: "Settings of sheet Sheet 1" }).click()
      await typeEachCase(page, page.getByTestId("sheet-width"), [["1500,5", null], ["1,5", null], ["1,500", 1500], ["1 600", 1600], ["1E3", 1000]], () => readFormFile("sheets/sheet_1.json").width)
    })
  })
}
