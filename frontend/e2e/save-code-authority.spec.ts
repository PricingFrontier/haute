import { readFileSync, writeFileSync } from "node:fs"
import { resolve } from "node:path"

import { expect, test, type Page } from "@playwright/test"

import { dispatchAppShortcut } from "./browserInteractions"
import { e2eProjectRoot, resetE2eProject } from "./projectIsolation"

// A save writes a stepped node's code from its steps only when they were edited (CGEN-11):
// a list the browser held has lost what JavaScript cannot hold, `1.0` arriving as `1`.
const mainPath = resolve(e2eProjectRoot, "rating", "main.py")

const HAND_WRITTEN = `

@pipeline.polars
def hand_written(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    df = raw_rows
    df = df.with_columns(pl.lit(1.0).alias("relativity"))
    df = df.filter(pl.col("value") == 9007199254740993)
    return df


@pipeline.polars
def rated_rows(raw_rows: pl.LazyFrame) -> pl.LazyFrame:
    df = raw_rows
    df = df.with_columns(pl.lit(2.5).alias("rate"))
    return df
`

const read = () => readFileSync(mainPath, "utf8")

/** The text of the node function *name* in *source*, up to the next decorator. */
function nodeFunction(source: string, name: string): string {
  const start = source.indexOf(`def ${name}(`)
  expect(start, `${name} is in the file`).toBeGreaterThanOrEqual(0)
  const end = source.indexOf("\n@", start)
  return source.slice(start, end < 0 ? undefined : end)
}

async function openApp(page: Page): Promise<void> {
  const loaded = page.waitForResponse((response) =>
    response.request().method() === "GET" && /\/api\/pipeline(?:\?|$)/.test(response.url()),
  )
  await page.goto("/")
  await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
  expect((await loaded).status()).toBe(200)
}

function saveResponse(page: Page) {
  return page.waitForResponse((response) =>
    response.request().method() === "POST" && response.url().includes("/api/pipeline/save"),
  )
}

async function save(page: Page): Promise<void> {
  const answered = saveResponse(page)
  await page.getByRole("button", { name: "Save", exact: true }).click()
  expect((await answered).status(), "pipeline save succeeds").toBe(200)
}

async function openNode(page: Page, name: string) {
  await page.getByLabel(new RegExp(`Transform node: ${name}\\b`, "i")).click()
  const editor = page.getByTestId("node-panel").getByTestId("polars-steps-editor")
  // The editor is a lazily loaded chunk, which a cold development server builds first.
  await expect(editor).toBeVisible({ timeout: 60_000 })
  return editor
}

async function closePanel(page: Page): Promise<void> {
  await page.getByTestId("node-panel-close").click()
  await expect(page.getByTestId("node-panel")).toHaveCount(0)
}

/** Add a Limit step of *n* rows to the open editor, without waiting for its rendering. */
async function addLimit(page: Page, n: number): Promise<void> {
  const editor = page.getByTestId("node-panel").getByTestId("polars-steps-editor")
  await editor.getByRole("button", { name: "Add step" }).click()
  await editor.getByRole("menu", { name: "Add step" }).getByRole("menuitem", { name: "Limit rows" }).click()
  const rowLimit = editor.getByLabel("Row limit").last()
  await rowLimit.fill(String(n))
  await rowLimit.press("Enter")
}

test.describe.configure({ mode: "serial" })

test.describe("A save writes only the steps that were edited", () => {
  test.beforeEach(() => {
    resetE2eProject()
    writeFileSync(mainPath, read().trimEnd() + "\n" + HAND_WRITTEN, "utf8")
  })

  test("keeps hand-written code nobody edited through saves, Undo, Redo and a late rendering", async ({ page }) => {
    test.slow()
    await openApp(page)

    // Saved untouched, the hand-written numbers stay as written; saved again, nothing moves.
    await save(page)
    const first = read()
    const handWritten = nodeFunction(first, "hand_written")
    expect(handWritten).toContain('pl.lit(1.0).alias("relativity")')
    expect(handWritten).toContain('pl.col("value") == 9007199254740993')
    await save(page)
    expect(read()).toBe(first)

    // Opening a panel on steps nobody edited edits nothing.
    const opened = await openNode(page, "hand_written")
    await expect(opened.getByTestId("polars-generated-code")).toContainText("relativity")
    await closePanel(page)
    await save(page)
    expect(read()).toBe(first)

    // An edited node is written from its steps, and no other node is.
    await openNode(page, "rated_rows")
    await addLimit(page, 5)
    await expect(page.getByTestId("polars-generated-code")).toContainText("df = df.head(5)")
    await closePanel(page)
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // Undo and Redo each save the steps on screen, and only those.
    await page.getByTestId("rf__node-rated_rows").click()
    await dispatchAppShortcut(page, "z")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).not.toContain("df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)
    await page.getByTestId("rf__node-rated_rows").click()
    await dispatchAppShortcut(page, "y")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(5)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // An edit made while a save runs is saved by the next one.
    let release!: () => void
    const held = new Promise<void>((resolve) => { release = resolve })
    await page.route("**/api/pipeline/save", async (route) => {
      await held
      await route.continue()
    })
    const answered = saveResponse(page)
    await page.getByRole("button", { name: "Save", exact: true }).click()
    await openNode(page, "rated_rows")
    await addLimit(page, 4)
    await expect(page.getByTestId("polars-generated-code")).toContainText("df = df.head(4)")
    release()
    expect((await answered).status()).toBe(200)
    await page.unroute("**/api/pipeline/save")
    expect(nodeFunction(read(), "rated_rows")).not.toContain("df.head(4)")
    await save(page)
    expect(nodeFunction(read(), "rated_rows")).toContain("df = df.head(4)")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)

    // With the browser's own rendering held back, the second save leaves the file as the
    // first wrote it: the baseline holds the code the server wrote.
    await page.route("**/api/pipeline/polars-steps/render", () => {})
    await addLimit(page, 3)
    await save(page)
    const rendered = read()
    expect(nodeFunction(rendered, "rated_rows")).toContain("df = df.head(3)")
    await save(page)
    expect(read()).toBe(rendered)
    await page.unroute("**/api/pipeline/polars-steps/render")
    expect(nodeFunction(read(), "hand_written")).toBe(handWritten)
  })
})
