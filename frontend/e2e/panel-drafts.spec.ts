import { readFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Locator, type Page } from "@playwright/test"

import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

// What a panel shows after an Undo is what the store holds, and one edit is one undo step
// (specs/frontend-shared high-level, "Panel values and Undo").

const bandingConfigPath = resolve(e2eProjectRoot, "rating", "config", "banding", "browser_mixed_banding.json")

async function openNodeProperties(page: Page, accessibleName: string): Promise<Locator> {
  const node = page.getByLabel(accessibleName)
  await expect(node).toBeVisible()
  await node.click()
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

async function toolbarUndo(page: Page): Promise<void> {
  await page.getByTestId("toolbar-undo").click()
}

async function save(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Save", exact: true }).click()
  await expect(page.getByRole("alert").filter({ hasText: /Saved/ })).toBeVisible()
}

test.describe.configure({ mode: "serial" })

test.describe("panel values follow Undo", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("a Banding cell's edit is one undo step, in the panel and in the saved file", async ({ page }) => {
    await page.goto("/")
    const panel = await openNodeProperties(page, "Banding node: browser_mixed_banding")
    const columns = panel.getByRole("group", { name: "Banding columns" })

    const label = panel.getByLabel("Breakpoint 1 label")
    await expect(label).toHaveValue("Age 40 or below")
    await retype(label, "Young")
    await expect(label).toHaveValue("Young")
    await toolbarUndo(page)
    await expect(panel.getByLabel("Breakpoint 1 label")).toHaveValue("Age 40 or below")

    const boundary = panel.getByLabel("Breakpoint 1 boundary")
    await retype(boundary, "45")
    await toolbarUndo(page)
    await expect(panel.getByLabel("Breakpoint 1 boundary")).toHaveValue("40")

    await columns.getByRole("button", { name: /^channel_band (complete|incomplete)$/ }).click()
    await retype(panel.getByLabel("Rule 1 match value"), "online")
    await toolbarUndo(page)
    await expect(panel.getByLabel("Rule 1 match value")).toHaveValue("direct")
    await retype(panel.getByLabel("Rule 1 group name"), "Web")
    await toolbarUndo(page)
    await expect(panel.getByLabel("Rule 1 group name")).toHaveValue("Direct")

    await save(page)
    const saved = JSON.parse(readFileSync(bandingConfigPath, "utf8"))
    expect(saved.factors[0].rules[0]).toMatchObject({ upper: 40, label: "Age 40 or below" })
    expect(JSON.stringify(saved.factors[1].rules)).toContain('"direct"')
    expect(JSON.stringify(saved.factors[1].rules)).toContain('"Direct"')
    expect(JSON.stringify(saved)).not.toMatch(/Young|Web|online|45/)
  })

  test("a Banding factor's remembered rules stay with it through an Undo", async ({ page }) => {
    await page.goto("/")
    const panel = await openNodeProperties(page, "Banding node: browser_mixed_banding")
    const columns = panel.getByRole("group", { name: "Banding columns" })
    const row = (name: string) => columns.getByRole("button", { name: new RegExp(`^${name} (complete|incomplete)$`) })
    const rows = columns.getByRole("button", { name: /_band (complete|incomplete)$/ })

    // The categorical factor moves first and is switched to Numeric; two Undos put back its
    // place and its type.
    await row("channel_band").click()
    await row("channel_band").press("Alt+ArrowUp")
    await expect(rows.first()).toHaveAccessibleName(/^channel_band /)
    await panel.getByRole("radio", { name: "Numeric", exact: true }).click()
    await toolbarUndo(page)
    await toolbarUndo(page)
    await expect(rows.first()).toHaveAccessibleName(/^proposer_age_band /)

    // The factor now first switched to Categorical starts with no rules of another's.
    await row("proposer_age_band").click()
    await panel.getByRole("radio", { name: "Categorical", exact: true }).click()
    await expect(panel.getByText("No rules yet")).toBeVisible()
    await row("channel_band").click()
    await expect(panel.getByLabel("Rule 1 match value")).toHaveValue("direct")
    await expect(panel.getByLabel("Rule 2 match value")).toHaveValue("broker")
  })

  test("after Undo the search space box shows what training will use", async ({ page }) => {
    await page.goto("/")
    await page.getByRole("button", { name: /browser_model/i }).click()
    const panes = page.getByRole("tablist", { name: "Modelling panes" })
    await panes.getByRole("tab", { name: "Parameters", exact: true }).click()
    await page.getByRole("radio", { name: "Tune parameters" }).click()

    const box = page.getByLabel("CatBoost search space JSON")
    for (const space of ['{"depth":[4,6]}', '{"depth":[8,10]}']) {
      await box.fill(space)
      await box.blur()
    }
    await expect.poll(async () => JSON.parse(await box.inputValue())).toEqual({ depth: [8, 10] })
    await toolbarUndo(page)
    await expect.poll(async () => JSON.parse(await box.inputValue())).toEqual({ depth: [4, 6] })

    // The training request carries the range on screen.
    let sent: { graph: { nodes: { id: string; data: { config: { tuning: { search_space: unknown } } } }[] } } | null = null
    await page.route("**/api/modelling/train", async (route) => {
      sent = route.request().postDataJSON()
      await route.abort()
    })
    await panes.getByRole("tab", { name: "Train", exact: true }).click()
    await expect(page.getByText("Dataset fits in memory")).toBeVisible()
    await page.getByRole("button", { name: /Tune & Train/i }).click()
    await expect.poll(() => sent).not.toBeNull()
    const model = sent!.graph.nodes.find((node) => node.id === "browser_model")
    expect(model?.data.config.tuning.search_space).toEqual({ depth: [4, 6] })
  })
})
