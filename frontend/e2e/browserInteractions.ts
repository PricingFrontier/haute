import type { Page } from "@playwright/test"

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
