import { expect, test } from "@playwright/test"

import { resetE2eProject } from "./projectIsolation"

test.describe("cross-browser smoke", () => {
  test.beforeEach(() => {
    resetE2eProject()
  })

  test("@smoke app shell and node inspector stay reachable", async ({ page }) => {
    await page.goto("/")

    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    await expect(page.getByTitle("Data source")).toBeVisible()
    await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible()

    const rawRowsNode = page.getByRole("button", { name: /raw_rows/i })
    await expect(rawRowsNode).toBeVisible()
    await rawRowsNode.click()

    await expect(page.getByLabel("Node properties")).toBeVisible()
    await expect(page.locator("input.node-label-input")).toHaveValue("raw_rows")
  })

  test("OUTPUT node card is flush, not vendor-centred @smoke", async ({ page }) => {
    // Bug class: our OUTPUT type key "output" collides with React Flow's
    // built-in node type; vendor CSS centres it at a fixed 150px.
    await page.goto("/")
    // App-ready gate, mirroring the existing smoke test:
    await expect(page.getByRole("toolbar", { name: /pipeline toolbar/i })).toBeVisible()
    const wrapper = page.locator(".react-flow__node-output") // OUTPUT is a singleton
    await expect(wrapper).toHaveCount(1) // render gate: the node exists
    const styles = await wrapper.evaluate((el) => {
      const cs = getComputedStyle(el)
      const card = el.firstElementChild as HTMLElement
      return {
        textAlign: cs.textAlign,
        padding: cs.padding,
        wrapperW: el.getBoundingClientRect().width,
        cardW: card.getBoundingClientRect().width,
      }
    })
    expect(["start", "left"]).toContain(styles.textAlign)
    expect(styles.padding).toBe("0px")
    expect(styles.wrapperW).toBeCloseTo(styles.cardW, 0) // flush: wrapper shrink-wraps card (not 150px)
  })
})
