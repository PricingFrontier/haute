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
import { execFileSync } from "node:child_process"
import { resolve } from "node:path"
import { expect, test, type Locator } from "@playwright/test"

import { e2eProjectRoot, repoRoot, resetE2eProject } from "./projectIsolation"
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

  test("opens schema SQL beside Refresh and copies it without leaving the workbench", async ({ page, context, browserName }, testInfo) => {
    appendFileSync(resolve(e2eProjectRoot, "haute.toml"), '\n[workbench.store.dev]\nprovider = "sqlite"\n')
    writeFileSync(resolve(e2eProjectRoot, "connections.toml"), '[dev]\nprovider = "sqlite"\ndatabase = "dev.sqlite"\n')
    writeFormFiles({ "schema/policy.json": {
      rows: "many", inputs: [
        { name: "limit", type: "int", index: "numbered" }, { name: "region", type: "str" },
        { name: "territory", type: "str" }, { name: "flood_zone", type: "str" },
      ],
    } })
    // policy as a store keyed by its row number made it: keyed by quote_row, the index limit
    // a column beside it. The real comparison lists the missing columns and tables and the
    // key's move to limit, and exports their SQL without migration commentary.
    execFileSync("uv", ["run", "python", "-c", [
      "import sqlite3, sys",
      "with sqlite3.connect(sys.argv[1]) as db:",
      "    db.execute('CREATE TABLE policy (quote_reference TEXT NOT NULL, quote_option INTEGER NOT NULL, quote_row INTEGER NOT NULL, [limit] INTEGER NOT NULL, region TEXT, PRIMARY KEY (quote_reference, quote_option, quote_row), FOREIGN KEY (quote_reference, quote_option) REFERENCES quote_details (quote_reference, quote_option))')",
    ].join("\n"), resolve(e2eProjectRoot, "dev.sqlite")], { cwd: repoRoot, stdio: "pipe" })
    let finishCatalogue!: () => void
    const catalogueReady = new Promise<void>((resolve) => { finishCatalogue = resolve })
    await page.route("**/api/workbench/store-catalogue", async (route) => {
      await catalogueReady
      await route.continue()
    })
    if (browserName === "chromium") await context.grantPermissions(["clipboard-read", "clipboard-write"])
    await page.goto("/")
    await page.getByTestId("view-switcher-workbench").click()
    await page.getByRole("button", { name: "Schema", exact: true }).click()
    const pane = page.getByTestId("schema-pane")
    await pane.getByRole("combobox", { name: "Database" }).selectOption("dev")
    const refreshButton = pane.getByRole("button", { name: "Refresh", exact: true })
    await expect(refreshButton).toHaveAttribute("aria-busy", "true")
    await expect(refreshButton).toBeDisabled()
    await expect(refreshButton.locator("svg")).toHaveCSS("animation-name", "spin")
    await pane.screenshot({ path: testInfo.outputPath("schema-refresh-busy.png") })
    finishCatalogue()
    await expect(pane.getByRole("status")).toHaveText("Needs attention")
    await expect(refreshButton).toHaveAttribute("aria-busy", "false")
    await expect(refreshButton).toBeEnabled()
    await expect(refreshButton).toHaveCSS("border-top-style", "solid")
    await expect(refreshButton).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)")
    const exportButton = pane.getByRole("button", { name: "Export SQL", exact: true })
    await expect(exportButton).toHaveCSS("border-top-style", "solid")
    await expect(exportButton).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)")
    await exportButton.press("Enter")
    const dialog = page.getByRole("dialog", { name: "Export database changes" })
    await expect(dialog).toBeVisible()
    const code = await dialog.getByTestId("store-sql").innerText()
    expect(code).toContain("CREATE TABLE")
    expect(code).not.toContain("policy.quote_row, renamed limit")
    expect(code).not.toContain("Not changed here")
    await expect(dialog.getByText(/Changes needing manual work are explained in the SQL comments/)).toHaveCount(0)
    await expect(dialog.getByRole("alert")).toHaveCount(0)
    // Let transient activation expire during verification. Copy must reserve its
    // clipboard operation within the click. WebKit runs without a permission grant.
    await page.route("**/api/workbench/store-compare", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 6000))
      await route.continue()
    }, { times: 1 })
    // WebKit's test browser does not grant clipboard reads. Inspect the native
    // item's contents after the real write succeeds; do not replace the write.
    if (browserName === "webkit") {
      await page.evaluate(() => {
        const write = navigator.clipboard.write.bind(navigator.clipboard)
        navigator.clipboard.write = async (items) => {
          const [, text] = await Promise.all([write(items), items[0].getType("text/plain")])
          document.documentElement.dataset.copiedSql = await text.text()
        }
      })
    }
    await dialog.getByRole("button", { name: "Copy SQL", exact: true }).click()
    await expect(dialog.getByRole("status")).toHaveText("SQL copied.")
    const verifiedCode = await dialog.getByTestId("store-sql").innerText()
    if (browserName === "chromium") {
      const copied = await page.evaluate(() => navigator.clipboard.readText())
      expect(copied.replace(/\r\n/g, "\n")).toBe(verifiedCode.replace(/\r\n/g, "\n"))
    } else {
      await expect(page.locator("html")).toHaveAttribute("data-copied-sql", verifiedCode)
    }
    await dialog.screenshot({ path: testInfo.outputPath("schema-sql-code.png") })
    await dialog.getByRole("button", { name: "Close", exact: true }).click()
    await expect(dialog).toHaveCount(0)
    await expect(pane).toBeVisible()
    await expect(page.getByRole("toolbar", { name: "Workbench toolbar" })).toBeVisible()
    await expect(exportButton).toBeFocused()
    await pane.getByText(/^Database changes/).click()
    const additions = pane.getByTestId("store-plan-review")
    // Keyed by quote_row beside its index's own column, policy's key moves to limit: on SQLite
    // a manual change, its note saying so, then the missing columns.
    await expect(additions.getByRole("region", { name: "Changes for policy" }).getByRole("listitem")).toHaveText([/^limitKey change/, "territory", "flood_zone"])
    await expect(additions.getByRole("region", { name: "Changes for quote_details" })).toContainText("New table")
    await expect(additions).not.toContainText(/Needs attention|rename|Saved revision|quotes with values/)
    await expect(additions.locator("details")).toHaveCount(0)
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

  test("writes an Export's cells in an order dragged or moved with Alt+arrows, each opened for its settings, its label placed beside it, its field searched for, a tick's written as X, one removed once confirmed, and saves the order", async ({ page }) => {
    const cell = (field: string, row: number) => ({ field, sheet: "Slip", cell: `B${row}`, label_cell: `A${row}` })
    writeFormFiles({
      "schema/policy.json": {
        rows: "one",
        inputs: [
          { name: "limit", type: "int", required: true },
          { name: "region", type: "str", options: ["north", "south"] },
          { name: "sprinklered", type: "bool" },
        ],
      },
      "sheets/sheet_1.json": {
        title: "Sheet 1",
        width: 1280,
        sections: [{ title: "Section 1", height: 560, components: [{ component: "export_1", x: 16, y: 16, w: 160, h: 40 }] }],
      },
      "components/export_1.json": {
        type: "export",
        label: "Slip",
        target: { format: "excel_cells", file_name: "slip", cells: [cell("policy.limit", 1), cell("policy.region", 2), cell("pricing.premium", 5)], blocks: [] },
        tables: [],
      },
    })
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await buildWorkbenchPipeline(page)
    await page.reload()
    await page.getByTestId("view-switcher-workbench").click()
    await page.getByRole("group", { name: 'Export "Slip"' }).focus()

    // A row for each cell: its field's label, where it writes beside it, its settings shut.
    const rows = page.getByRole("list", { name: "Cells" }).getByRole("listitem")
    const names = async () => rows.evaluateAll((held) => held.map((row) => row.getAttribute("aria-label")))
    await expect(rows).toHaveCount(3)
    expect(await names()).toEqual(["Cell 1 Limit", "Cell 2 Region", "Cell 3 Premium"])
    await expect(page.getByTestId("export-cell-1-summary")).toHaveText("Slip!B1 · label A1")
    await expect(page.getByRole("textbox", { name: "Cell 1 sheet" })).toHaveCount(0)

    // Dragged by its grip onto the first, the last cell takes its place.
    await rows.nth(2).hover()
    await rows.nth(2).getByTitle("Drag to move").dragTo(rows.nth(0))
    await expect.poll(names).toEqual(["Cell 1 Premium", "Cell 2 Limit", "Cell 3 Region"])

    // Its settings open under it and stay open as Alt+Down moves it, the focus with it.
    await page.getByRole("button", { name: "Cell 1 settings" }).click()
    await expect(page.getByRole("textbox", { name: "Cell 1 address" })).toHaveValue("B5")
    await page.getByRole("listitem", { name: "Cell 1 Premium" }).focus()
    await page.keyboard.press("Alt+ArrowDown")
    await expect.poll(names).toEqual(["Cell 1 Limit", "Cell 2 Premium", "Cell 3 Region"])
    await expect(page.getByRole("listitem", { name: "Cell 2 Premium" })).toBeFocused()
    await expect(page.getByRole("textbox", { name: "Cell 2 address" })).toHaveValue("B5")

    // Its label, saved left of it, reads as Left; put Right, it goes with the cell to a new address.
    const label = page.getByRole("combobox", { name: "Cell 2 label" })
    await expect(label).toHaveValue("left")
    await expect(page.getByRole("textbox", { name: "Cell 2 label address" })).toHaveCount(0)
    await label.selectOption("right")
    await page.getByRole("textbox", { name: "Cell 2 address" }).fill("b6")
    await page.keyboard.press("Enter")
    await expect(page.getByTestId("export-cell-2-summary")).toHaveText("Slip!B6 · label C6")
    // Custom shows its Label cell.
    await label.selectOption("custom")
    await expect(page.getByRole("textbox", { name: "Cell 2 label address" })).toHaveValue("C6")

    // Its field searched for and chosen with the keyboard.
    await page.getByRole("button", { name: "Cell 2 field" }).click()
    await page.getByRole("combobox", { name: "Search Cell 2 field" }).fill("regi")
    await page.keyboard.press("Enter")
    await expect.poll(names).toEqual(["Cell 1 Limit", "Cell 2 Region", "Cell 3 Region"])

    // Add a cell searched the same way, the new cell under the last.
    await page.getByRole("button", { name: "Add a cell" }).click()
    await page.getByRole("combobox", { name: "Search Add a cell" }).fill("prem")
    await page.keyboard.press("Enter")
    await expect.poll(names).toEqual(["Cell 1 Limit", "Cell 2 Region", "Cell 3 Region", "Cell 4 Premium"])
    await expect(page.getByTestId("export-cell-4-summary")).toHaveText("Slip!B3 · label A3")

    // A tick's settings hold its True/False, Excel's own to start, written here as X or blank.
    await page.getByRole("button", { name: "Add a cell" }).click()
    await page.getByRole("combobox", { name: "Search Add a cell" }).fill("sprink")
    await page.keyboard.press("Enter")
    await page.getByRole("button", { name: "Cell 5 settings" }).click()
    const ticks = page.getByRole("combobox", { name: "Cell 5 True/False" })
    await expect(ticks).toHaveValue("excel")
    await ticks.selectOption("custom")
    await ticks.selectOption("x")
    await expect(page.getByTestId("export-cell-5-summary")).toHaveText("Slip!B4 · label A4 · X / blank")

    // A cell is removed from its settings once the question is answered Remove.
    await page.getByRole("button", { name: "Cell 3 settings" }).click()
    await page.getByRole("button", { name: "Remove cell 3" }).click()
    const question = page.getByRole("dialog", { name: "Remove cell 3, Region?" })
    await expect(question).toContainText("It stops writing to Slip!B2, and its label to A2.")
    await question.getByRole("button", { name: "Remove", exact: true }).click()
    await expect.poll(names).toEqual(["Cell 1 Limit", "Cell 2 Region", "Cell 3 Premium", "Cell 4 Sprinklered"])

    // Saved, the cells in their new order.
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByTestId("toast-notification").filter({ hasText: "Saved → rating/workbench" })).toBeVisible()
    expect(readFormFile("components/export_1.json").target.cells).toEqual([
      cell("policy.limit", 1),
      { field: "policy.region", sheet: "Slip", cell: "B6", label_cell: "C6" },
      cell("pricing.premium", 3),
      { ...cell("policy.sprinklered", 4), true_false: { true: "X", false: "" } },
    ])
  })
})
