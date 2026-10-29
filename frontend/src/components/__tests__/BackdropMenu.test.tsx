/**
 * The git surfaces' right-click menu (specs/frontend-git-ui): the menu at the pointer over
 * a backdrop that closes it on a click and on a right-click, which never opens the
 * browser's menu.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { Eye, Trash2 } from "lucide-react"

import BackdropMenu, { MenuBackdrop, MenuItem } from "../BackdropMenu"

afterEach(cleanup)

function renderMenu(onClose = vi.fn(), onView = vi.fn()) {
  render(
    <BackdropMenu testId="probe-menu" x={40} y={60} title="pricing/nick/spur" onClose={onClose}>
      <MenuItem testId="probe-view" icon={Eye} onClick={onView}>
        View this branch
      </MenuItem>
      <MenuItem testId="probe-delete" icon={Trash2} danger disabled onClick={vi.fn()}>
        Delete
      </MenuItem>
    </BackdropMenu>,
  )
  return { onClose, onView, menu: screen.getByTestId("probe-menu") }
}

describe("BackdropMenu", () => {
  it("opens at the pointer with its title line and items", () => {
    const { menu } = renderMenu()
    expect(menu).toHaveStyle({ left: "40px", top: "60px" })
    expect(menu).toHaveTextContent("pricing/nick/spur")
    expect(screen.getByTestId("probe-view")).toHaveTextContent("View this branch")
  })

  it("runs an item's action, and a disabled item does nothing", () => {
    const { onView } = renderMenu()
    fireEvent.click(screen.getByTestId("probe-view"))
    expect(onView).toHaveBeenCalledOnce()
    expect(screen.getByTestId("probe-delete")).toBeDisabled()
  })

  it("closes on a click on its backdrop, the menu's previous sibling", () => {
    const { onClose, menu } = renderMenu()
    fireEvent.click(menu.previousSibling as Element)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("closes on a right-click on its backdrop, never showing the browser's menu", () => {
    const { onClose, menu } = renderMenu()
    // fireEvent answers false when the handler called preventDefault.
    expect(fireEvent.contextMenu(menu.previousSibling as Element)).toBe(false)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it("does not close on a click inside the menu", () => {
    const { onClose, menu } = renderMenu()
    fireEvent.click(menu)
    expect(onClose).not.toHaveBeenCalled()
  })
})

describe("MenuBackdrop", () => {
  it("dismisses on a click and on a right-click it keeps from the browser", () => {
    const onDismiss = vi.fn()
    const { container } = render(<MenuBackdrop onDismiss={onDismiss} />)
    const backdrop = container.firstChild as Element
    fireEvent.click(backdrop)
    expect(fireEvent.contextMenu(backdrop)).toBe(false)
    expect(onDismiss).toHaveBeenCalledTimes(2)
  })
})
