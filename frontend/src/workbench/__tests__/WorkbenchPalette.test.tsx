/**
 * The workbench's palette (specs/workbench): Table and Collection in Haute's palette
 * shell with the switcher under them, collapsing with the node palette.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useUIStore from "../../stores/useUIStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import WorkbenchPalette from "../WorkbenchPalette"

describe("WorkbenchPalette", () => {
  beforeEach(() => {
    useUIStore.setState({ paletteOpen: true })
    useWorkbenchStore.setState({ enabled: true, activeView: "workbench" })
  })

  afterEach(() => {
    cleanup()
    useUIStore.setState({ paletteOpen: true })
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
  })

  it("offers a Table and a Collection, with the switcher at the bottom", () => {
    render(<WorkbenchPalette />)

    expect(screen.getByRole("heading", { name: "Components" })).toBeInTheDocument()
    expect(screen.getByTestId("palette-item-tableInput")).toHaveTextContent("Table")
    expect(screen.getByTestId("palette-item-collection")).toHaveTextContent("Collection")
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Workbench" })).toHaveAttribute("aria-pressed", "true")
  })

  it("collapses with the node palette, to the reveal strip with the compact switcher", () => {
    render(<WorkbenchPalette />)

    fireEvent.click(screen.getByTitle("Collapse palette"))
    expect(useUIStore.getState().paletteOpen).toBe(false)
    expect(screen.queryByTestId("palette-item-tableInput")).not.toBeInTheDocument()
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Pricing" })).not.toHaveTextContent("Pricing")

    fireEvent.click(screen.getByRole("button", { name: "Show component palette" }))
    expect(useUIStore.getState().paletteOpen).toBe(true)
    expect(screen.getByTestId("palette-item-tableInput")).toBeInTheDocument()
  })
})
