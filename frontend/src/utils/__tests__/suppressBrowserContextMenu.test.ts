import { afterEach, describe, expect, it } from "vitest"

import { suppressBrowserContextMenu } from "../suppressBrowserContextMenu"

/** Right-clicks `element`; true when something cancelled the browser's menu. */
function rightClickCancelled(element: Element): boolean {
  return !element.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true }))
}

describe("suppressBrowserContextMenu", () => {
  let restore: (() => void) | undefined

  afterEach(() => {
    restore?.()
    restore = undefined
    document.body.replaceChildren()
  })

  it("cancels the browser's menu on any element, a text field and a portalled one included", () => {
    const plain = document.createElement("div")
    const field = document.createElement("input")
    const nested = document.createElement("span")
    plain.append(nested)
    document.body.append(plain, field)
    expect(rightClickCancelled(nested)).toBe(false)

    restore = suppressBrowserContextMenu()
    for (const element of [plain, nested, field, document.body]) {
      expect(rightClickCancelled(element)).toBe(true)
    }
  })

  it("stops cancelling once restored", () => {
    suppressBrowserContextMenu()()
    expect(rightClickCancelled(document.body)).toBe(false)
  })
})
