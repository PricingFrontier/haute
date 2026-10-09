/**
 * The view switcher (specs/workbench): Pricing and Workbench at the bottom of the left
 * palette while the workbench is enabled, pressing the view that shows.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import ViewSwitcher from "../ViewSwitcher"
import useWorkbenchStore from "../../stores/useWorkbenchStore"

describe("ViewSwitcher", () => {
  beforeEach(() => {
    useWorkbenchStore.setState({ enabled: true, activeView: "pipeline" })
  })

  afterEach(() => {
    cleanup()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
  })

  it("renders nothing while the workbench is not enabled", () => {
    useWorkbenchStore.setState({ enabled: false })
    const { container } = render(<ViewSwitcher compact={false} />)

    expect(container).toBeEmptyDOMElement()
  })

  it("offers Pricing and Workbench, pressing the view that shows, and switches on click", () => {
    render(<ViewSwitcher compact={false} />)
    const group = screen.getByRole("group", { name: "Views" })
    const pricing = within(group).getByRole("button", { name: "Pricing" })
    const workbench = within(group).getByRole("button", { name: "Workbench" })

    expect(pricing).toHaveAttribute("aria-pressed", "true")
    expect(workbench).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(workbench)

    expect(useWorkbenchStore.getState().activeView).toBe("workbench")
    expect(workbench).toHaveAttribute("aria-pressed", "true")
    expect(pricing).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(pricing)
    expect(useWorkbenchStore.getState().activeView).toBe("pipeline")
  })

  it("stacks the views one per row, each as wide as the palette, so a long label fits", () => {
    render(<ViewSwitcher compact={false} />)
    const group = screen.getByRole("group", { name: "Views" })

    expect(group).toHaveClass("flex-col")
    const buttons = within(group).getAllByRole("button")
    expect(buttons.map((button) => button.textContent)).toEqual(["Pricing", "Workbench"])
    for (const button of buttons) expect(button).toHaveClass("w-full")
  })

  it("is a column of icon buttons named by their labels beside the collapsed palette", () => {
    render(<ViewSwitcher compact />)
    const group = screen.getByRole("group", { name: "Views" })

    expect(group).toHaveClass("flex-col")
    expect(within(group).getByRole("button", { name: "Pricing" })).not.toHaveTextContent("Pricing")
    fireEvent.click(within(group).getByRole("button", { name: "Workbench" }))
    expect(useWorkbenchStore.getState().activeView).toBe("workbench")
  })
})
