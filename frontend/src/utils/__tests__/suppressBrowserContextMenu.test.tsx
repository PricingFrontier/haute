import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { type ReactElement, useState } from "react"
import { createPortal } from "react-dom"
import { afterEach, describe, expect, it } from "vitest"

import { suppressBrowserContextMenu } from "../suppressBrowserContextMenu"

/** Right-clicks `element`; true when something cancelled the browser's menu. */
function rightClickCancelled(element: Element): boolean {
  return !fireEvent.contextMenu(element)
}

/** A surface with a menu of its own beside a plain portalled one. */
function Surfaces() {
  const [menuAt, setMenuAt] = useState<string | null>(null)
  return (
    <>
      <div
        onContextMenu={(event) => {
          event.preventDefault()
          setMenuAt(`${event.clientX},${event.clientY}`)
        }}
      >
        <span data-testid="inside-menu-surface">row</span>
      </div>
      {menuAt !== null && <div role="menu" aria-label={`Menu at ${menuAt}`} />}
      {createPortal(<p data-testid="portalled">panel text</p>, document.body)}
    </>
  )
}

describe("suppressBrowserContextMenu", () => {
  let restore: (() => void) | undefined

  afterEach(() => {
    cleanup()
    restore?.()
    restore = undefined
  })

  it("cancels the browser's menu on a plain surface and in a portal, once installed and until restored", () => {
    render(<Surfaces />)
    const portalled = screen.getByTestId("portalled")
    expect(rightClickCancelled(portalled)).toBe(false)

    restore = suppressBrowserContextMenu()
    expect(rightClickCancelled(portalled)).toBe(true)
    expect(rightClickCancelled(document.body)).toBe(true)

    restore()
    restore = undefined
    expect(rightClickCancelled(portalled)).toBe(false)
  })

  it("lets the editor's own menu handler run first and open its menu", () => {
    render(<Surfaces />)
    restore = suppressBrowserContextMenu()

    expect(rightClickCancelled(screen.getByTestId("inside-menu-surface"))).toBe(true)
    expect(screen.getByRole("menu", { name: "Menu at 0,0" })).toBeInTheDocument()
  })

  const target = <span data-testid="target" />
  it.each<[string, ReactElement]>([
    ["a text field", <input type="text" data-testid="target" />],
    ["a number field", <input type="number" data-testid="target" />],
    ["a text area", <textarea data-testid="target" />],
    ["a code editor's content", <div className="cm-editor"><div className="cm-content">{target}</div></div>],
    ["the text of a link", <a href="/files/rates.csv">{target}</a>],
  ])("keeps the browser's menu in %s", (_name, surface) => {
    render(surface)
    restore = suppressBrowserContextMenu()

    expect(rightClickCancelled(screen.getByTestId("target"))).toBe(false)
  })

  it.each<[string, ReactElement]>([
    ["a checkbox", <input type="checkbox" data-testid="target" />],
    ["an anchor with no address", <a>{target}</a>],
  ])("cancels the browser's menu on %s, which takes no typing and opens nothing", (_name, surface) => {
    render(surface)
    restore = suppressBrowserContextMenu()

    expect(rightClickCancelled(screen.getByTestId("target"))).toBe(true)
  })
})
