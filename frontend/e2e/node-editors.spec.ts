import { readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { openApp, seedGraph } from "./pipelineSeed"
import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

// What a node editor's field accepts, saves and shows (specs/frontend-node-editors
// high-level): the fields the 11 November sweep found taking a value the run refuses,
// or dropping one in silence.

const mainPath = resolve(e2eProjectRoot, "rating", "main.py")
const bandingConfigPath = resolve(e2eProjectRoot, "rating", "config", "banding", "browser_mixed_banding.json")
const constantConfigPath = resolve(e2eProjectRoot, "rating", "config", "constant", "rates.json")

async function save(page: Page): Promise<void> {
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await responsePromise).status(), "pipeline save succeeds").toBe(200)
}

/** Seed a Data Input over the 30 sample rows (`proposer_age` whole numbers, 24 in 15 rows;
 * `channel` direct or broker) whose post-load steps are `steps`. */
async function seedSteppedInput(page: Page, label: string, steps: unknown[]): Promise<void> {
  await seedGraph(
    page,
    new Function(
      "nodes",
      `nodes.push(${JSON.stringify({
        id: label,
        type: "custom",
        position: { x: 60, y: 520 },
        data: {
          label,
          nodeType: "dataInput",
          config: { input_type: "file", format: "parquet", mode: "scan", path: "data/sample.parquet", arguments: {}, steps },
        },
      })})`,
    ) as (nodes: unknown[]) => void,
  )
}

/** The open node's steps editor, on its Transform tab. */
async function openSteps(page: Page, label: string): Promise<Locator> {
  await page.getByRole("button", { name: new RegExp(`Data Input node: ${label}`, "i") }).click()
  const panel = page.getByTestId("node-panel")
  await expect(panel).toBeVisible()
  await panel.getByRole("button", { name: /^transform$/i }).click()
  const editor = panel.getByTestId("polars-steps-editor")
  await expect(editor).toBeVisible()
  return editor
}

/** Paste `text` into `box` as a spreadsheet's copied cells arrive. */
async function paste(box: Locator, text: string): Promise<void> {
  await box.focus()
  await box.evaluate((element, pasted) => {
    const data = new DataTransfer()
    data.setData("text/plain", pasted)
    element.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }))
  }, text)
}

test.describe.configure({ mode: "serial" })

test.describe("node editor fields", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("an \"is one of\" list takes its column's type and pasted cells", async ({ page }) => {
    test.slow()
    await openApp(page)
    await seedSteppedInput(page, "member_filter", [
      {
        id: "f",
        kind: "filter",
        match: "all",
        conditions: [{ column: "proposer_age", operator: "eq", value: { kind: "literal", type: "number", value: 0 } }],
      },
    ])
    const editor = await openSteps(page, "member_filter")
    // The node's preview tells the steps their columns' types.
    await expect(page.getByText("0 rows · 6 cols")).toBeVisible()
    await editor.getByRole("button", { name: /^Step 1:/ }).click()
    const code = editor.getByTestId("polars-generated-code")
    const rows = page.getByRole("table").first().locator("tbody tr")

    // A whole-number column's list holds whole numbers: 24 is added as a number.
    await editor.getByLabel("Filter condition 1 operator").selectOption("is_in")
    await expect(editor.getByLabel("Filter condition 1 values type")).toHaveCount(0)
    const age = editor.getByLabel("Filter condition 1 values new value")
    await age.fill("24")
    await age.press("Enter")
    await editor.getByRole("button", { name: "Filter condition 1 values: add value" }).click()
    await expect(code).toContainText('pl.col("proposer_age").is_in([24])')
    await page.getByRole("button", { name: "Refresh" }).click()
    await expect(rows).toHaveCount(15)

    // Two cells of a spreadsheet row, then three of a column, become that many values.
    await editor.getByRole("button", { name: "Add condition" }).click()
    const channel = editor.getByLabel("Filter condition 2 column")
    await channel.fill("channel")
    await channel.press("Enter")
    await editor.getByLabel("Filter condition 2 operator").selectOption("is_in")
    const channels = editor.getByLabel("Filter condition 2 values new value")
    await paste(channels, "direct\tbroker")
    await expect(code).toContainText('pl.col("channel").is_in(["direct", "broker"])')
    await paste(channels, "web\r\nphone\r\npost\r\n")
    await expect(code).toContainText('pl.col("channel").is_in(["direct", "broker", "web", "phone", "post"])')
    await page.getByRole("button", { name: "Refresh" }).click()
    await expect(rows).toHaveCount(15)
  })

  test("a Rename to a name the data already has is refused at the field and not saved", async ({ page }) => {
    await openApp(page)
    await seedSteppedInput(page, "renamed_input", [{ id: "r", kind: "rename", renames: [{ from: "channel", to: "sales_channel" }] }])
    const editor = await openSteps(page, "renamed_input")
    await editor.getByRole("button", { name: /^Step 1:/ }).click()

    const to = editor.getByLabel("Rename 1 to")
    await to.fill("value")
    await to.press("Enter")
    await expect(editor.getByText("The data already has a column named value.")).toBeVisible()
    await expect(editor.getByTestId("polars-generated-code")).toContainText('"channel": "sales_channel"')

    await save(page)
    const main = readFileSync(mainPath, "utf8")
    expect(main).toContain('"channel": "sales_channel"')
    expect(main).not.toContain('"channel": "value"')
  })

  test("a saved Group by with a row count opens as a step that can be edited", async ({ page }) => {
    await openApp(page)
    // As the panel builds it: the row count keeps the empty column it started with.
    await seedSteppedInput(page, "grouped_input", [
      { id: "g", kind: "group_by", keys: ["channel"], aggregations: [{ agg: "len", column: "", name: "policy_count" }] },
    ])
    expect(readFileSync(mainPath, "utf8")).toContain('.agg(pl.len().alias("policy_count"))')

    // Read back from its code, the row count has no column, and the card still opens.
    const editor = await openSteps(page, "grouped_input")
    await expect(editor.getByText("Invalid step")).toHaveCount(0)
    const card = editor.getByRole("button", { name: "Step 1: Group and aggregate", exact: true })
    await card.click()
    await expect(card).toHaveAttribute("aria-expanded", "true")
  })

  test("a Constant value whose name is cleared keeps its last name in the file", async ({ page }) => {
    await openApp(page)
    await seedGraph(page, (nodes) => {
      nodes.push({
        id: "rates",
        type: "custom",
        position: { x: 60, y: 520 },
        data: {
          label: "rates",
          nodeType: "constant",
          config: { values: [{ name: "brand", value: "x" }, { name: "base_rate", value: "100" }] },
        },
      })
    })
    await page.getByLabel("Constant node: rates").click()
    const name = page.getByTestId("constant-name-1")
    await expect(name).toHaveValue("base_rate")
    await name.fill("")
    await name.press("Tab")
    await expect(page.getByTestId("constant-name-1-error")).toHaveText("Enter a name")

    // A save while the name is cleared writes the value under its last name.
    await save(page)
    expect(JSON.parse(readFileSync(constantConfigPath, "utf8")).values).toEqual({ brand: "x", base_rate: 100 })
    await page.reload()
    await page.getByLabel("Constant node: rates").click()
    await expect(page.getByTestId("constant-name-1")).toHaveValue("base_rate")
  })

  test("a Banding factor whose input column is gone is marked as the editor opens", async ({ page }) => {
    // The factor names a column its input no longer has, as after an upstream rename.
    const config = readFileSync(bandingConfigPath, "utf8")
    expect(config).toContain('"column": "proposer_age"')
    writeFileSync(bandingConfigPath, config.replace('"column": "proposer_age"', '"column": "proposer_agee"'))
    await openApp(page)

    await page.getByLabel("Banding node: browser_mixed_banding").click()
    const columns = page.getByRole("group", { name: "Banding columns" })
    await expect(columns.getByRole("button", { name: "proposer_age_band input column" })).toHaveAttribute(
      "title",
      "proposer_agee is not in the input's columns",
    )
    // Counted among the Issues, which list it alone.
    await page.getByRole("button", { name: "Issues 1", exact: true }).click()
    await expect(columns.getByRole("button", { name: "proposer_age_band incomplete" })).toHaveAttribute(
      "title",
      "proposer_agee is not in the input",
    )
    await expect(columns.getByRole("button", { name: /^channel_band / })).toHaveCount(0)
  })
})
