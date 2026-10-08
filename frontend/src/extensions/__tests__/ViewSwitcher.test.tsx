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

  it("offers Haute and each extension, pressing the view that shows, and switches on click", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    render(<ViewSwitcher compact={false} />)
    const group = screen.getByRole("group", { name: "Views" })
    const haute = within(group).getByRole("button", { name: "Haute" })
    const forms = within(group).getByRole("button", { name: "Obverse" })

    expect(haute).toHaveAttribute("aria-pressed", "true")
    expect(forms).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(forms)

    expect(useExtensionsStore.getState().activeView).toBe("obverse")
    expect(forms).toHaveAttribute("aria-pressed", "true")
    expect(haute).toHaveAttribute("aria-pressed", "false")

    fireEvent.click(haute)
    expect(useExtensionsStore.getState().activeView).toBe(PIPELINE_VIEW)
  })

  it("is a column of icon buttons named by their labels beside the collapsed palette", () => {
    useExtensionsStore.setState({ extensions: [obverse] })
    render(<ViewSwitcher compact />)
    const group = screen.getByRole("group", { name: "Views" })

    expect(group).toHaveClass("flex-col")
    expect(within(group).getByRole("button", { name: "Haute" })).not.toHaveTextContent("Haute")
    fireEvent.click(within(group).getByRole("button", { name: "Obverse" }))
    expect(useExtensionsStore.getState().activeView).toBe("obverse")
  })
})
