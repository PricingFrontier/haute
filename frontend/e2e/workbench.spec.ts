/**
 * The workbench end to end (specs/workbench): switched on for the project, its schema read
 * into the schema's pane, a Collection dragged onto a sheet and given its fields, drawn
 * while building as a new quote, the sample typed in Preview and priced live on the
 * pipeline open in the editor, with no Price or Clear, a column added to the schema, and
 * the project saved in one Save: the schema, the sheets and the sample to the files of the
 * pipeline folder's workbench/ folder and the pipeline after them, its workbench nodes
 * holding no copy of the tables, both on the project's ledger.
 */
import { appendFileSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test, type Locator } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"
import { buildWorkbenchPipeline, enableWorkbench, readFormFile, writeFormFiles } from "./workbenchProject"

/** A node's config file in the pipeline folder's config/. */
const readNodeConfig = (folder: string, node: string) =>
  JSON.parse(readFileSync(resolve(e2eProjectRoot, "rating", "config", folder, `${node}.json`), "utf8"))

async function centre(locator: Locator): Promise<{ x: number; y: number }> {
  const box = await locator.boundingBox()
  if (box === null) throw new Error("the element has no box")
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test.describe("workbench", () => {
  test.beforeEach(() => {
    resetE2eProject()
    enableWorkbench()
  })

  test("opens schema SQL beside Refresh and copies it without leaving the workbench", async ({ page, context }, testInfo) => {
    appendFileSync(resolve(e2eProjectRoot, "haute.toml"), '\n[workbench.store.dev]\nprovider = "sqlite"\n')
    writeFileSync(resolve(e2eProjectRoot, "connections.toml"), '[dev]\nprovider = "sqlite"\ndatabase = "dev.sqlite"\n')
    writeFormFiles({ "schema/policy.json": {
      rows: "many", inputs: [{ name: "limit", type: "int", index: "numbered" }, { name: "region", type: "str" }],
    } })
    // An existing index collides with the desired name. The real comparison must
    // explain that omission while still allowing the other tables' SQL to be copied.
    await page.route("**/api/workbench/store-catalogue", (route) => route.fulfill({
      json: { environment: "dev", checked_at: 1700000000000, tables: [{
        name: "policy", primary_key: ["quote_reference", "quote_option", "quote_row"], refers: true, quotes: 1,
        columns: [
          { name: "quote_reference", kind: "str", type: "TEXT", nullable: false, collation: null, quotes: 1 },
          ...["quote_option", "quote_row", "limit"].map((name) => ({ name, kind: "int", type: "INTEGER", nullable: false, collation: null, quotes: 1 })),
          { name: "region", kind: "str", type: "TEXT", nullable: true, collation: null, quotes: 1 },
        ],
      }] },
    }))
    await context.grantPermissions(["clipboard-read", "clipboard-write"])
    await page.goto("/")
    await page.getByTestId("view-switcher-workbench").click()
    await page.getByRole("button", { name: "Schema", exact: true }).click()
    const pane = page.getByTestId("schema-pane")
    await pane.getByRole("combobox", { name: "Database" }).selectOption("dev")
    await expect(pane.getByRole("status")).toHaveText("Needs attention")
    const exportButton = pane.getByRole("button", { name: "Export SQL", exact: true })
    await exportButton.click()
    const dialog = page.getByRole("dialog", { name: "Export database changes" })
    await expect(dialog).toBeVisible()
    const code = await dialog.getByTestId("store-sql").innerText()
    expect(code).toContain("CREATE TABLE")
    expect(code).toContain("policy.quote_row, renamed limit")
    await expect(dialog.getByText(/Changes needing manual work are explained in the SQL comments/)).toBeVisible()
    await expect(dialog.getByRole("alert")).toHaveCount(0)
    await dialog.getByRole("button", { name: "Copy SQL", exact: true }).click()
    await expect(dialog.getByRole("status")).toHaveText("SQL copied.")
    const copied = await page.evaluate(() => navigator.clipboard.readText())
    expect(copied.replace(/\r\n/g, "\n")).toBe(code.replace(/\r\n/g, "\n"))
    await dialog.screenshot({ path: testInfo.outputPath("schema-sql-code.png") })
    await dialog.getByRole("button", { name: "Close", exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(pane).toBeVisible()
    await expect(page.getByRole("toolbar", { name: "Workbench toolbar" })).toBeVisible()
    await expect(exportButton).toBeFocused()
    const search = await pane.getByRole("textbox", { name: "Search fields" }).boundingBox()
    const refresh = await pane.getByRole("button", { name: "Refresh", exact: true }).boundingBox()
    const exported = await exportButton.boundingBox()
    expect(search).not.toBeNull()
    expect(refresh).not.toBeNull()
    expect(exported).not.toBeNull()
    expect(exported!.x).toBeGreaterThanOrEqual(refresh!.x + refresh!.width)
    expect(Math.abs(exported!.y - refresh!.y)).toBeLessThan(4)
    expect(search!.width).toBeLessThanOrEqual(400)
    await pane.screenshot({ path: testInfo.outputPath("schema-export-toolbar.png") })
    const handle = await centre(pane.getByTestId("panel-resize-handle"))
    await page.mouse.move(handle.x, handle.y)
    await page.mouse.down()
    await page.mouse.move(handle.x - 550, handle.y, { steps: 8 })
    await page.mouse.up()
    const narrowPane = await pane.boundingBox()
    const narrowRefresh = await pane.getByRole("button", { name: "Refresh", exact: true }).boundingBox()
    const narrowExport = await exportButton.boundingBox()
    expect(narrowPane!.width).toBeLessThan(400)
    expect(narrowExport!.x).toBeGreaterThanOrEqual(narrowRefresh!.x + narrowRefresh!.width)
    expect(narrowExport!.x + narrowExport!.width).toBeLessThanOrEqual(narrowPane!.x + narrowPane!.width)
    await exportButton.click()
    await expect(dialog.getByRole("button", { name: "Copy SQL", exact: true })).toBeVisible()
    await dialog.getByRole("button", { name: "Close", exact: true }).click()
    await pane.screenshot({ path: testInfo.outputPath("schema-export-toolbar-narrow.png") })
  })

  test("lays out a sheet from the schema, drawn as a new quote, prices the sample live in Preview, and saves the sheets", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await buildWorkbenchPipeline(page)
    await page.reload()
    await expect(page.getByRole("button", { name: /Workbench Input node: workbench_input/i })).toBeVisible()

    // The view, with the schema the sheets work with in its pane over the sheet.
    await page.getByTestId("view-switcher-workbench").click()
    await expect(page.getByRole("toolbar", { name: "Workbench toolbar" })).toBeVisible()
    await page.getByRole("button", { name: "Schema" }).click()
    await expect(page.getByTestId("schema-pane")).toBeVisible()
    // Haute's quote_details table first, as every form holds it, then the project's.
    await expect(page.getByLabel("Table name").nth(0)).toHaveValue("quote_details")
    await expect(page.getByLabel("Table name").nth(1)).toHaveValue("policy")
    await expect(page.getByLabel("Table name").nth(2)).toHaveValue("pricing")
    await page.getByRole("button", { name: "Schema" }).click()
    await expect(page.getByTestId("schema-pane")).toHaveCount(0)

    // A Collection dragged out of the palette onto the sheet, then given its fields.
    const item = await centre(page.getByTestId("palette-item-collection"))
    const sheet = await page.getByTestId("sheet").boundingBox()
    if (sheet === null) throw new Error("the sheet has no box")
    await page.mouse.move(item.x, item.y)
    await page.mouse.down()
    await page.mouse.move(sheet.x + 160, sheet.y + 120, { steps: 12 })
    await page.mouse.up()
    const collection = page.getByRole("group", { name: "A Collection" })
    await expect(collection).toBeVisible()
    await expect(collection).toHaveText(/Choose its fields/)
    await page.getByRole("checkbox", { name: "Show policy limit" }).check()
    await page.getByRole("checkbox", { name: "Show policy region" }).check()
    await page.getByRole("checkbox", { name: "Show pricing premium" }).check()

    // Building, the sheet is a new quote's: nothing typed, nothing priced, its cells inert.
    const premium = page.getByTestId("priced-pricing:premium")
    const builtLimit = collection.getByRole("textbox", { name: "Limit", includeHidden: true })
    await expect(premium).toHaveText("—")
    await expect(builtLimit).toHaveValue("")

    // Preview: the sample typed and priced live on the pipeline, with nothing to press.
    await page.getByRole("button", { name: "Preview" }).click()
    await expect(page.getByRole("button", { name: "Price" })).toHaveCount(0)
    await expect(page.getByRole("button", { name: "Clear" })).toHaveCount(0)
    await page.getByRole("textbox", { name: "Limit" }).fill("1000")
    await page.getByRole("textbox", { name: "Limit" }).press("Enter")
    await page.getByRole("combobox", { name: "Region" }).selectOption("north")
    await expect(premium).toHaveText(/^100(\.0+)?$/, { timeout: 60_000 })
    // Back to building: a new quote's sheet again, the sample kept for Preview.
    await page.getByRole("button", { name: "Build" }).click()
    await expect(builtLimit).toHaveValue("")
    await expect(premium).toHaveText("—")

    // A column added to the schema, unsaved: on the sheets alone until the save.
    await page.getByRole("button", { name: "Schema" }).click()
    const policy = page.getByRole("region", { name: "Table policy" })
    await policy.getByRole("group", { name: "policy Inputs" }).getByRole("button", { name: "Add column" }).click()
    // After the quote's reference and option, which lead every table's inputs, and the policy's two.
    const added = policy.getByLabel("Column name").nth(4)
    await expect(added).toBeFocused()
    await added.fill("excess")
    await added.press("Tab")

    // Saved, the project in one Save: the schema, the sheet and the sample in the files of
    // the folder the workbench reads, then the pipeline.
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByTestId("toast-notification").filter({ hasText: "Saved → rating/workbench" })).toBeVisible()
    await expect(page.getByTestId("toast-notification").filter({ hasText: /Saved → rating[\\/]main\.py/ })).toBeVisible()
    expect(readFormFile("form.json").schema).toEqual(["quote_details", "policy", "pricing"])
    // The quote's reference and option lead the table without being written in its file.
    expect(readFormFile("schema/policy.json").inputs.map((column: { name: string }) => column.name)).toEqual(["limit", "region", "excess"])
    // The column is in the form folder alone: neither workbench node's config file holds a copy.
    expect(readNodeConfig("workbench_input", "workbench_input")).toEqual({})
    expect(readNodeConfig("workbench_output", "workbench_output")).toEqual({})
    // The Collection placed by its name in the sheet's file, and in its own file its fields by name.
    const savedSheet = readFormFile("sheets/sheet_1.json")
    expect(savedSheet.sections[0].components).toEqual([expect.objectContaining({ component: "collection_1" })])
    expect(readFormFile("components/collection_1.json")).toMatchObject({
      type: "collection",
      fields: ["policy.limit", "policy.region", "pricing.premium"],
    })
    expect(readFormFile("sample/policy.json")).toEqual([{ limit: "1000", region: "north" }])
  })
})
