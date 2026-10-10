/**
 * The workbench end to end (specs/workbench): switched on for the project, its schema read
 * into the Schema section, a Collection dragged onto a sheet and given its fields, the
 * sample typed and priced live on the pipeline open in the editor, an underwriter's quote
 * priced in Preview apart from the sample, a column added to the schema, and the project
 * saved in one Save: the sheets to forms/form.json and the pipeline after them, its
 * Workbench Input's copy carrying the column, both on the project's ledger.
 */
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"
import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

const column = (id: string, name: string, type: string, overrides: Record<string, unknown> = {}) => ({
  id,
  name,
  type,
  label: "",
  key: false,
  required: false,
  min: null,
  max: null,
  options: [],
  index: false,
  ...overrides,
})

/** The sheets as a pricing team would start them: a policy table in, a pricing table out. */
const FORM = {
  version: 1,
  name: "haute-e2e",
  schema: {
    tables: [
      {
        id: "t_policy",
        name: "policy",
        role: "input",
        rows: "one",
        columns: [
          column("c_limit", "limit", "int", { required: true }),
          column("c_region", "region", "str", { options: ["north", "south"] }),
        ],
      },
      {
        id: "t_pricing",
        name: "pricing",
        role: "output",
        rows: "one",
        columns: [column("c_premium", "premium", "float")],
      },
    ],
  },
  pages: [{ id: "page_1", title: "Sheet 1", widgets: [] }],
  sample: {},
}

/** The pipeline's copies of the schema's tables, as the editor keeps them. */
const INPUT_TABLES = [{ name: "policy", rows: "one", columns: [{ name: "limit", type: "int" }, { name: "region", type: "str" }] }]
const OUTPUT_TABLES = [{ name: "pricing", rows: "one", columns: [{ name: "premium", type: "float" }] }]

/** Switch the project's workbench on and write the sheets, as `haute init --workbench` and a save would. */
function enableWorkbench(): void {
  appendFileSync(resolve(e2eProjectRoot, "haute.toml"), '\n[workbench]\nenabled = true\nform = "forms/form.json"\n')
  mkdirSync(resolve(e2eProjectRoot, "forms"), { recursive: true })
  writeFileSync(resolve(e2eProjectRoot, "forms", "form.json"), JSON.stringify(FORM, null, 2) + "\n")
}

/**
 * Replace the scaffold's Quote Input and Quote Response with a workbench pipeline: a
 * Workbench Input whose policy table a Transform prices, filling a Workbench Output's
 * pricing table. Saved through the API, as a developer's own edits would be.
 */
async function buildWorkbenchPipeline(page: Page): Promise<void> {
  const status = await page.evaluate(
    async ({ inputTables, outputTables }) => {
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
          data: { label: "quote", description: "", nodeType: "workbenchInput", config: { tables: inputTables, sample: {} } },
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
          data: { label: "response", description: "", nodeType: "workbenchOutput", config: { tables: outputTables } },
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
    { inputTables: INPUT_TABLES, outputTables: OUTPUT_TABLES },
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

  test("lays out a sheet from the schema, prices the sample live and a quote in Preview, and saves the sheets", async ({ page }) => {
    await page.goto("/")
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await buildWorkbenchPipeline(page)
    await page.reload()
    await expect(page.getByRole("button", { name: /Workbench Input node: quote/i })).toBeVisible()

    // The view, with the schema the sheets work with.
    await page.getByTestId("view-switcher-workbench").click()
    await expect(page.getByRole("toolbar", { name: "Workbench toolbar" })).toBeVisible()
    await page.getByRole("button", { name: "Schema" }).click()
    await expect(page.getByLabel("Table name").nth(0)).toHaveValue("policy")
    await expect(page.getByLabel("Table name").nth(1)).toHaveValue("pricing")
    await page.getByRole("button", { name: "Sheets" }).click()

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

    // The sample, priced live on the pipeline as it is typed.
    const premium = page.getByTestId("priced-t_pricing:c_premium")
    await expect(premium).toHaveText("—")
    await page.getByRole("textbox", { name: "Limit" }).fill("1000")
    await page.getByRole("textbox", { name: "Limit" }).press("Enter")
    await page.getByRole("combobox", { name: "Region" }).selectOption("north")
    await expect(premium).toHaveText(/^100(\.0+)?$/, { timeout: 60_000 })

    // Preview: an underwriter's quote, apart from the sample, priced on Price.
    await page.getByRole("button", { name: "Preview" }).click()
    await expect(page.getByRole("button", { name: "Price" })).toBeDisabled()
    await expect(page.getByRole("textbox", { name: "Limit" })).toHaveValue("")
    await page.getByRole("textbox", { name: "Limit" }).fill("2000")
    await page.getByRole("textbox", { name: "Limit" }).press("Enter")
    await page.getByRole("button", { name: "Price" }).click()
    await expect(premium).toHaveText(/^200(\.0+)?$/, { timeout: 60_000 })
    await page.getByRole("button", { name: "Build" }).click()
    await expect(page.getByRole("textbox", { name: "Limit" })).toHaveValue("1000")

    // A column added to the schema, unsaved: on the sheets alone until the save.
    await page.getByRole("button", { name: "Schema" }).click()
    await page.getByRole("button", { name: "Add column" }).first().click()
    const added = page.getByLabel("Column name").nth(2)
    await expect(added).toBeFocused()
    await added.fill("excess")
    await added.press("Tab")

    // Saved, the project in one Save: the sheet, the sample and the schema in the file the
    // workbench reads, then the pipeline, its Workbench Input's copy carrying the column.
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await expect(page.getByTestId("toast-notification").filter({ hasText: "Saved → forms/form.json" })).toBeVisible()
    await expect(page.getByTestId("toast-notification").filter({ hasText: /Saved → rating[\\/]main\.py/ })).toBeVisible()
    const saved = JSON.parse(readFileSync(resolve(e2eProjectRoot, "forms", "form.json"), "utf8"))
    expect(saved.schema.tables[0].columns.map((column: { name: string }) => column.name)).toEqual(["limit", "region", "excess"])
    const copy = JSON.parse(readFileSync(resolve(e2eProjectRoot, "rating", "config", "workbench_input", "quote.json"), "utf8"))
    expect(copy.tables).toEqual([
      { name: "policy", rows: "one", columns: [{ name: "limit", type: "int" }, { name: "region", type: "str" }, { name: "excess", type: "str" }] },
    ])
    expect(saved.pages[0].widgets).toHaveLength(1)
    expect(saved.pages[0].widgets[0]).toMatchObject({
      type: "collection",
      fields: [
        { table: "t_policy", column: "c_limit" },
        { table: "t_policy", column: "c_region" },
        { table: "t_pricing", column: "c_premium" },
      ],
    })
    expect(saved.sample).toEqual({ t_policy: [{ c_limit: "1000", c_region: "north" }] })
  })
})
