/**
 * UI tests for the shared KeyPickerModal: collapsible grouped candidate list,
 * already-present (by path) candidates disabled, and confirm returning the
 * selected paths.
 */
import type { ComponentProps } from "react"
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import KeyPickerModal from "../KeyPickerModal"
import type { InheritGroup } from "../../panels/editors/apiInputInherit"

afterEach(cleanup)

const groups: InheritGroup[] = [
  {
    ancestorPath: "$[:]",
    ancestorLabel: "root",
    candidates: [
      { path: "$[:].quote_id", name: "quote_id", type: "str", levels: null },
      { path: "$[:].customer.id", name: "customer_id", type: "str", levels: null },
    ],
  },
  {
    ancestorPath: "$[:].orders[:]",
    ancestorLabel: "orders",
    candidates: [{ path: "$[:].orders[:].order_date", name: "order_date", type: "date", levels: null }],
  },
]

function renderModal(over: Partial<ComponentProps<typeof KeyPickerModal>> = {}) {
  const onConfirm = vi.fn()
  const onClose = vi.fn()
  render(
    <KeyPickerModal
      title="Inherit keys"
      targetLabel="$[:].orders[:].items[:]"
      accentColor="#3b82f6"
      groups={groups}
      existingPaths={new Set()}
      onConfirm={onConfirm}
      onClose={onClose}
      {...over}
    />,
  )
  return { onConfirm, onClose }
}

describe("KeyPickerModal", () => {
  it("renders one collapsible group per ancestor level with its candidates", () => {
    renderModal()
    expect(screen.getByTestId("key-picker-group-$[:]")).toBeTruthy()
    expect(screen.getByTestId("key-picker-group-$[:].orders[:]")).toBeTruthy()
    expect(screen.getByTestId("key-picker-candidate-$[:].quote_id")).toBeTruthy()
    expect(screen.getByTestId("key-picker-candidate-$[:].orders[:].order_date")).toBeTruthy()
  })

  it("collapsing a group hides its candidates", () => {
    renderModal()
    expect(screen.queryByTestId("key-picker-candidate-$[:].quote_id")).toBeTruthy()
    fireEvent.click(screen.getByTestId("key-picker-group-$[:]").querySelector("button")!)
    expect(screen.queryByTestId("key-picker-candidate-$[:].quote_id")).toBeNull()
  })

  it("renders an already-present candidate checked and disabled", () => {
    renderModal({ existingPaths: new Set(["$[:].quote_id"]) })
    const cb = screen
      .getByTestId("key-picker-candidate-$[:].quote_id")
      .querySelector("input") as HTMLInputElement
    expect(cb.checked).toBe(true)
    expect(cb.disabled).toBe(true)
  })

  it("confirm returns the selected paths; the button counts and disables at zero", () => {
    const { onConfirm } = renderModal()
    const confirm = screen.getByTestId("key-picker-confirm") as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    fireEvent.click(
      screen.getByTestId("key-picker-candidate-$[:].quote_id").querySelector("input")!,
    )
    fireEvent.click(
      screen
        .getByTestId("key-picker-candidate-$[:].orders[:].order_date")
        .querySelector("input")!,
    )
    expect(confirm.textContent).toContain("2")
    fireEvent.click(confirm)
    expect(onConfirm).toHaveBeenCalledWith(["$[:].quote_id", "$[:].orders[:].order_date"])
  })

  it("cancel closes without confirming", () => {
    const { onConfirm, onClose } = renderModal()
    fireEvent.click(screen.getByTestId("key-picker-cancel"))
    expect(onClose).toHaveBeenCalled()
    expect(onConfirm).not.toHaveBeenCalled()
  })
})
