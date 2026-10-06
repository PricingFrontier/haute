import { expect, type Page } from "@playwright/test"

export async function dispatchAppShortcut(page: Page, key: string): Promise<void> {
  await page.evaluate(
    ({ key, useMeta }) => {
      window.dispatchEvent(new KeyboardEvent("keydown", {
        key,
        bubbles: true,
        cancelable: true,
        ctrlKey: !useMeta,
        metaKey: useMeta,
      }))
    },
    { key, useMeta: process.platform === "darwin" },
  )
}

export async function dispatchNodeDoubleClick(page: Page, label: string): Promise<void> {
  await page
    .locator(`[aria-label^="Submodel node: ${label}"]`)
    .dispatchEvent("dblclick", { bubbles: true, cancelable: true, composed: true })
}

/** Resolves once the canvas viewport stops moving, after React Flow's initial fit or an inspector reveal glide. */
export async function waitForSettledViewport(page: Page): Promise<void> {
  const viewport = page.locator(".react-flow__viewport")
  let previous: string | null = null
  await expect.poll(async () => {
    const current = await viewport.evaluate((element) => (element as HTMLElement).style.transform)
    const settled = current === previous
    previous = current
    return settled
  }, { intervals: [300] }).toBe(true)
}
