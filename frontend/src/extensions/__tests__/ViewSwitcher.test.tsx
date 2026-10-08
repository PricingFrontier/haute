import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import ViewSwitcher from "../ViewSwitcher"
import useExtensionsStore, { PIPELINE_VIEW } from "../../stores/useExtensionsStore"

const obverse = {
  name: "obverse",
  label: "Obverse",
  api_base: "/api/extensions/obverse",
  entry_url: "/extensions/obverse/obverse-embed.js",
  ready: true,
  detail: null,
}

describe("ViewSwitcher", () => {
  beforeEach(() => {
    useExtensionsStore.setState({ extensions: [], activeView: PIPELINE_VIEW, toolbarSlot: null })
  })

  afterEach(cleanup)

  it("renders nothing while no extension is installed", () => {
    const { container } = render(<ViewSwitcher compact={false} />)

    expect(container).toBeEmptyDOMElement()
  })

  it("offers Pricing and each extension, pressing the view that shows, and switches on click", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    render(<ViewSwitcher compact={false} />)
    const group = screen.getByRole("group", { name: "Views" })
    const pricing = within(group).getByRole("button", { name: "Pricing" })
    const forms = within(group).getByRole("button", { name: "Obverse" })

    expect(pricing).toHaveTextContent("Pricing")
    expect(pricing).toHaveAttribute("aria-pressed", "true")
    expect(forms).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(forms)

    expect(useExtensionsStore.getState().activeView).toBe("obverse")
    expect(forms).toHaveAttribute("aria-pressed", "true")
    expect(pricing).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(pricing)
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)
  })

  it("stacks the views one per row, each as wide as the palette, so a long label fits", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    render(<ViewSwitcher compact={false} />)
    const group = screen.getByRole("group", { name: "Views" })

    expect(group).toHaveClass("flex-col")
    const buttons = within(group).getAllByRole("button")
    expect(buttons.map((button) => button.textContent)).toEqual(["Pricing", "Obverse"])
    for (const button of buttons) expect(button).toHaveClass("w-full")
  })

  it("is a column of icon buttons named by their labels beside the collapsed palette", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    render(<ViewSwitcher compact />)
    const group = screen.getByRole("group", { name: "Views" })

    expect(group).toHaveClass("flex-col")
    expect(within(group).getByRole("button", { name: "Pricing" })).not.toHaveTextContent("Pricing")
    fireEvent.click(within(group).getByRole("button", { name: "Obverse" }))
    expect(useExtensionsStore.getState().activeView).toBe("obverse")
  })
})
