/**
 * The underwriter app's quotes end to end through `haute app serve` (specs/workbench, WB-42):
 * the journeys' project made a workbench project whose release builds, served as an analyst
 * serves it; a quote made in the New quote window, typed, priced and saved in the browser, and
 * read from dev's SQLite file with plain SQL, the values typed in the form's own input table
 * and the price beside them in its output table, each row under the quote's reference and
 * option; marked final, amended giving a reason and saved again, read again as a draft with
 * the new value and the activity of each step; and a column added to the form, in its table
 * once the app starts again, the quote opening at its address.
 */
import { writeFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test } from "@playwright/test"

import { buildAppPage, readStore, startAppServe, type AppServe } from "./appServe"
import { e2eGit, e2eProjectRoot, resetE2eProject } from "./projectIsolation"
import { buildWorkbenchPipeline, enableWorkbench, FORM_FILES, readFormFile, writeFormFiles } from "./workbenchProject"

/** The form's sheet: one Collection showing the policy's limit and region, and the premium priced. */
const SHEET_FILES: Record<string, unknown> = {
  "sheets/sheet_1.json": {
    id: "page_1",
    title: "Sheet 1",
    sections: [
      {
        id: "section_1",
        title: "Section 1",
        components: [{ id: "collection_1", type: "collection", fields: ["policy.limit", "policy.region", "pricing.premium"] }],
      },
    ],
  },
  "sheets/sheet_1.layout.json": {
    width: 1280,
    sections: { section_1: { height: 560, components: { collection_1: { x: 184, y: 112, w: 720, h: 120 } } } },
  },
}

/**
 * The project's test quotes, which its release is validated on as `haute deploy` validates
 * the pipeline: the scaffold's example is no quote of the form, and a Workbench Input's
 * request names the quote's reference.
 */
function writeTestQuotes(): void {
  const quotes = [
    {
      input: { quote_details: { quote_reference: "TEST-1", quote_option: 1 }, policy: { limit: 1000, region: "north" } },
      expected: { pricing: { premium: 100.0 } },
    },
  ]
  writeFileSync(resolve(e2eProjectRoot, "tests", "quotes", "example.json"), JSON.stringify(quotes, null, 2) + "\n")
}

const REFERENCE = "E2E-1"
const LABEL = `${REFERENCE}/1`
const AMENDED_BECAUSE = "The broker asked for a higher limit"

/** The quote's rows in each of the form's tables and in its activity, as SQLite holds them. */
function quoteRows() {
  const quote = { params: [REFERENCE, 1] }
  const where = "WHERE quote_reference = ? AND quote_option = ?"
  return readStore({
    details: { ...quote, sql: `SELECT quote_reference, quote_option, stage, saved_by, priced_release FROM quote_details ${where}` },
    policy: { ...quote, sql: `SELECT quote_reference, quote_option, "limit", region FROM policy ${where}` },
    pricing: { ...quote, sql: `SELECT quote_reference, quote_option, premium FROM pricing ${where}` },
    activity: { ...quote, sql: `SELECT action, reason FROM haute_activity ${where} ORDER BY activity_id` },
  })
}

/** Every app started, each stopped once the test ends, however it ends. */
const started: AppServe[] = []

async function serve(staticDir: string): Promise<AppServe> {
  const app = await startAppServe(staticDir)
  started.push(app)
  return app
}

test.describe("the underwriter app's quotes", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test.afterEach(async () => {
    const testInfo = test.info()
    for (const [index, app] of started.splice(0).entries()) {
      await app.stop()
      // A journey that failed shows what each app it started printed, in its report.
      if (testInfo.status !== testInfo.expectedStatus) {
        await testInfo.attach(`haute app serve ${index + 1}`, { body: app.output(), contentType: "text/plain" })
      }
    }
  })

  test("a quote saved in haute app serve is read from dev's SQLite file, marked final, amended and saved again, and a form change reaches dev's tables", async ({ page }, testInfo) => {
    // The app's page is built, and the app builds the project's release twice.
    test.setTimeout(5 * 60_000)
    const staticDir = testInfo.outputPath("app-page")
    // Signed in as the analyst's git identity, which the journeys' project sets.
    const principal = e2eGit(["config", "user.email"]).trim()

    const app = await test.step("the journeys' project made a workbench project, whose release haute app serve builds and serves", async () => {
      enableWorkbench({ ...FORM_FILES, ...SHEET_FILES })
      writeTestQuotes()
      await Promise.all([
        buildAppPage(staticDir),
        (async () => {
          await page.goto("/")
          await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
          await buildWorkbenchPipeline(page)
        })(),
      ])
      return serve(staticDir)
    })

    const limit = page.getByRole("textbox", { name: "Limit" })
    const premium = page.getByTestId("priced-t_pricing:c_premium")
    const unsaved = page.getByTestId("quote-unsaved")
    const question = page.getByTestId("question")

    await test.step("a quote made in the New quote window, typed, priced and saved", async () => {
      await page.goto(app.url)
      await expect(page.getByTestId("quotes-empty")).toBeVisible()
      await page.getByTestId("new-quote").click()
      const newQuote = page.getByRole("dialog", { name: "New quote" })
      await newQuote.getByRole("textbox", { name: "Quote reference" }).fill(REFERENCE)
      await newQuote.getByRole("button", { name: "Create" }).click()
      await expect(page).toHaveURL(`${app.url}/quotes/${REFERENCE}/1`)
      await expect(page.getByTestId("quote-reference")).toHaveText(LABEL)
      await expect(page.getByTestId("stage-draft")).toBeVisible()

      await limit.fill("1000")
      await limit.press("Enter")
      await page.getByRole("combobox", { name: "Region" }).selectOption("north")
      await expect(premium).toHaveText(/^100(\.0+)?$/, { timeout: 60_000 })
      await expect(unsaved).toBeVisible()
      await page.getByTestId("save-quote").click()
      await expect(unsaved).toHaveCount(0)
      await expect(page.getByTestId("quote-line")).toContainText("Priced on this release")
    })

    await test.step("its rows read from dev's SQLite file: the values typed and the price, under its reference and option", async () => {
      const saved = quoteRows()
      expect(saved.details).toEqual([
        { quote_reference: REFERENCE, quote_option: 1, stage: "draft", saved_by: principal, priced_release: app.release },
      ])
      expect(saved.policy).toEqual([{ quote_reference: REFERENCE, quote_option: 1, limit: 1000, region: "north" }])
      expect(saved.pricing).toEqual([{ quote_reference: REFERENCE, quote_option: 1, premium: 100 }])
    })

    await test.step("marked final", async () => {
      await page.getByRole("button", { name: "Mark final" }).click()
      await expect(question).toContainText(`Mark ${LABEL} final?`)
      await question.getByRole("button", { name: "Mark final" }).click()
      await expect(page.getByTestId("stage-final")).toBeVisible()
      expect(quoteRows().details).toMatchObject([{ stage: "final" }])
    })

    await test.step("amended giving a reason, changed and saved again", async () => {
      await page.getByRole("button", { name: "Amend" }).click()
      await question.getByLabel("Why it is being amended").fill(AMENDED_BECAUSE)
      await question.getByRole("button", { name: "Amend" }).click()
      await expect(page.getByTestId("stage-draft")).toBeVisible()

      await limit.fill("2500")
      await limit.press("Enter")
      await expect(premium).toHaveText(/^250(\.0+)?$/, { timeout: 60_000 })
      await expect(unsaved).toBeVisible()
      await page.getByTestId("save-quote").click()
      await expect(unsaved).toHaveCount(0)

      const again = quoteRows()
      expect(again.details).toEqual([
        { quote_reference: REFERENCE, quote_option: 1, stage: "draft", saved_by: principal, priced_release: app.release },
      ])
      expect(again.policy).toEqual([{ quote_reference: REFERENCE, quote_option: 1, limit: 2500, region: "north" }])
      expect(again.pricing).toEqual([{ quote_reference: REFERENCE, quote_option: 1, premium: 250 }])
      expect(again.activity).toEqual([
        { action: "created", reason: null },
        { action: "saved", reason: null },
        { action: "finalised", reason: null },
        { action: "amended", reason: AMENDED_BECAUSE },
        { action: "saved", reason: null },
      ])
    })

    await test.step("a column added to the form reaches dev's table as the app starts again, and the quote opens", async () => {
      const policy = readFormFile("schema/policy.json")
      writeFormFiles({ "schema/policy.json": { ...policy, columns: [...policy.columns, { id: "c_excess", name: "excess", type: "int" }] } })
      await app.stop()
      const restarted = await serve(staticDir)

      const { columns } = readStore({ columns: { sql: "SELECT name FROM pragma_table_info('policy')" } })
      expect(columns.map((column) => column.name)).toContain("excess")
      await page.goto(`${restarted.url}/quotes/${REFERENCE}/1`)
      await expect(page.getByTestId("quote-reference")).toHaveText(LABEL)
      await expect(page.getByRole("main", { name: "Sheets" })).toBeVisible()
    })
  })
})
