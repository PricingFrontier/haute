/**
 * The workbench end to end (specs/workbench): switched on for the project, its schema read
 * into the schema's pane, a Collection dragged onto a sheet and given its fields, drawn
 * while building as a new quote, the sample typed in Preview and priced live on the
 * pipeline open in the editor, with no Price or Clear, a column added to the schema, and
 * the project saved in one Save: the schema, the sheets and the sample to the files of the
 * pipeline folder's workbench/ folder and the pipeline after them, its workbench nodes
 * holding no copy of the tables, both on the project's ledger.
 */
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

/** The form's folder: the pipeline folder's workbench/. */
const FORM_FOLDER = resolve(e2eProjectRoot, "rating", "workbench")

/**
 * The form as a pricing team would start it, file by file, each saying only what is set: a
 * policy table in, a pricing table out, and one blank sheet.
 */
const FORM_FILES: Record<string, unknown> = {
  "form.json": { version: 1, name: "haute-e2e", schema: ["quote_details", "policy", "pricing"], sheets: ["sheet_1"] },
  "schema/quote_details.json": { columns: [] },
  "schema/policy.json": {
    id: "t_policy",
    role: "input",
    rows: "one",
    columns: [
      { id: "c_limit", name: "limit", type: "int", required: true },
      { id: "c_region", name: "region", type: "str", options: ["north", "south"] },
    ],
  },
  "schema/pricing.json": { id: "t_pricing", role: "output", rows: "one", columns: [{ id: "c_premium", name: "premium", type: "float" }] },
  "sheets/sheet_1.json": { id: "page_1", title: "Sheet 1", sections: [{ id: "section_1", title: "Section 1", components: [] }] },
  "sheets/sheet_1.layout.json": { width: 1280, sections: { section_1: { height: 560, components: {} } } },
}

const readFormFile = (path: string) => JSON.parse(readFileSync(resolve(FORM_FOLDER, path), "utf8"))

/** A node's config file in the pipeline folder's config/. */
const readNodeConfig = (folder: string, node: string) =>
  JSON.parse(readFileSync(resolve(e2eProjectRoot, "rating", "config", folder, `${node}.json`), "utf8"))

/** Switch the project's workbench on and write its form's files, as `haute init --workbench` and a save would. */
function enableWorkbench(): void {
  appendFileSync(resolve(e2eProjectRoot, "haute.toml"), "\n[workbench]\nenabled = true\n")
  for (const [path, content] of Object.entries(FORM_FILES)) {
    const file = resolve(FORM_FOLDER, path)
    mkdirSync(dirname(file), { recursive: true })
    writeFileSync(file, JSON.stringify(content, null, 2) + "\n")
  }
}

/**
 * Replace the scaffold's Quote Input and Quote Response with a workbench pipeline: a
 * Workbench Input whose policy table a Transform prices, filling a Workbench Output's
 * pricing table. Saved through the API, as a developer's own edits would be. Neither
 * workbench node's config holds anything: both read their tables from the form folder.
 */
async function buildWorkbenchPipeline(page: Page): Promise<void> {
  const status = await page.evaluate(
    async () => {
      const headers: Record<string, string> = { "Content-Type": "application/json" }
      const read = await fetch("/api/pipeline", { headers })
      if (!read.ok) throw new Error(`GET /api/pipeline ${read.status}`)
      const document = await read.json()
      type DocumentNode = {
        recovery_id: string
        label: string
        decorator_name: string
        node_type: string | null
        description: string
        display_position: { x: number; y: number }
        config: Record<string, unknown> | null
      }
      type DocumentEdge = {
        recovery_id: string
        source_recovery_id: string
        target_recovery_id: string
        source_handle: string | null
        target_handle: string | null
      }
      const replaced = new Set(["quotes", "priced"])
      const nodes = (document.nodes as DocumentNode[])
        .filter((node) => !replaced.has(node.label))
        .map((node) => ({
          id: node.recovery_id,
          type: node.node_type ?? node.decorator_name,
          position: node.display_position,
          data: {
            label: node.label,
            description: node.description,
            nodeType: node.node_type ?? node.decorator_name,
            ...(node.config === null ? {} : { config: node.config }),
          },
        }))
      const kept = new Set(nodes.map((node) => node.id))
      const edges = (document.edges as DocumentEdge[])
        .filter((edge) => kept.has(edge.source_recovery_id) && kept.has(edge.target_recovery_id))
        .map((edge) => ({
          id: edge.recovery_id,
          source: edge.source_recovery_id,
          target: edge.target_recovery_id,
          sourceHandle: edge.source_handle,
          targetHandle: edge.target_handle,
        }))
      nodes.push(
        {
          id: "quote",
          type: "custom",
          position: { x: 60, y: 720 },
          data: { label: "quote", description: "", nodeType: "workbenchInput", config: {} },
        },
        {
          id: "premium",
          type: "custom",
          position: { x: 380, y: 720 },
          data: {
            label: "premium",
            description: "",
            nodeType: "polars",
            config: { code: "df = policy.with_columns(premium=pl.col('limit') * 0.1)" },
          },
        },
        {
          id: "response",
          type: "custom",
          position: { x: 700, y: 720 },
          data: { label: "response", description: "", nodeType: "workbenchOutput", config: {} },
        },
      )
      edges.push(
        { id: "e_quote_premium", source: "quote", target: "premium", sourceHandle: "policy", targetHandle: null },
        { id: "e_premium_response", source: "premium", target: "response", sourceHandle: null, targetHandle: "pricing" },
      )
      const saved = await fetch("/api/pipeline/save", {
        method: "POST",
        headers,
        body: JSON.stringify({
          name: document.pipeline_name ?? "main",
          description: document.pipeline_description ?? "",
          source_file: document.source_file,
          base_revision: document.source_revision,
          preamble: document.preamble ?? "",
          preserved_blocks: document.preserved_blocks,
          sources: document.sources,
          active_source: document.active_source ?? "live",
          graph: { nodes, edges },
        }),
      })
      if (!saved.ok) throw new Error(`POST /api/pipeline/save ${saved.status}: ${await saved.text()}`)
      return (await saved.json()).status as string
    },
  )
  expect(status).toBe("saved")
}

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
