/**
 * The workbench end to end (specs/workbench): switched on for the project, its schema read
 * into the schema's pane, a Collection dragged onto a sheet and given its fields, drawn
 * while building as a new quote, the sample typed in Preview and priced live on the
 * pipeline open in the editor, with no Price or Clear, a column added to the schema, and
 * the project saved in one Save: the schema, the sheets and the sample to the files of the
 * pipeline folder's workbench/ folder and the pipeline after them, its workbench nodes
 * holding no copy of the tables, both on the project's ledger.
 */
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test, type Locator } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"
import { buildWorkbenchPipeline, enableWorkbench, readFormFile } from "./workbenchProject"

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

  test("lays out a sheet from the schema, drawn as a new quote, prices the sample live in Preview, and saves the sheets", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await buildWorkbenchPipeline(page)
    await page.reload()
    await expect(page.getByRole("button", { name: /Workbench Input node: quote/i })).toBeVisible()

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
    const premium = page.getByTestId("priced-t_pricing:c_premium")
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
    await policy.getByRole("button", { name: "Add column" }).click()
    // After the quote's reference and option, which lead every table, and the policy's two.
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
    expect(readFormFile("schema/policy.json").columns.map((column: { name: string }) => column.name)).toEqual(["limit", "region", "excess"])
    // The column is in the form folder alone: neither workbench node's config file holds a copy.
    expect(readNodeConfig("workbench_input", "quote")).toEqual({})
    expect(readNodeConfig("workbench_output", "response")).toEqual({})
    // The Collection in the sheet's file, its fields by name, and its place in the sheet's layout.
    const savedSheet = readFormFile("sheets/sheet_1.json")
    expect(savedSheet.sections[0].components).toHaveLength(1)
    expect(savedSheet.sections[0].components[0]).toMatchObject({
      type: "collection",
      fields: ["policy.limit", "policy.region", "pricing.premium"],
    })
    const savedLayout = readFormFile("sheets/sheet_1.layout.json")
    expect(Object.keys(savedLayout.sections.section_1.components)).toEqual([savedSheet.sections[0].components[0].id])
    expect(readFormFile("sample/policy.json")).toEqual([{ limit: "1000", region: "north" }])
  })
})
