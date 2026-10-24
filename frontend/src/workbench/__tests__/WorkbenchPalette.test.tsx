/**
 * The workbench's palette (specs/workbench): a Section, then Heading, Collection, Table and
 * Chart, in Haute's palette shell with the switcher under them, collapsing with the node
 * palette, and disabled in Preview.
 */
import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useUIStore from "../../stores/useUIStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
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
    useWorkbenchViewStore.setState({ mode: "build", schemaOpen: false, creating: null })
  })

  it("offers a Section, then a Heading, a Collection, a Table and a Chart, with the switcher at the bottom", () => {
    render(<WorkbenchPalette />)

    expect(screen.getByRole("heading", { name: "Components" })).toBeInTheDocument()
    expect(screen.getAllByTestId(/^palette-item-/).map((item) => item.dataset.testid)).toEqual([
      "palette-item-section",
      "palette-item-heading",
      "palette-item-collection",
      "palette-item-tableInput",
      "palette-item-chart",
    ])
    expect(screen.getByTestId("palette-item-tableInput")).toHaveTextContent("Table")
    expect(screen.getByTestId("palette-item-collection")).toHaveTextContent("Collection")
    expect(screen.getByTestId("palette-item-chart")).toHaveTextContent("Chart")
    expect(screen.getByTestId("palette-item-heading")).toHaveTextContent("Heading")
    expect(screen.getByTestId("palette-item-heading")).toHaveAttribute("title", "Text naming a section of the sheet: a title, a heading or a label")
    expect(screen.getByTestId("palette-item-section")).toHaveTextContent("Section")
    expect(screen.getByTestId("palette-item-section")).toHaveAttribute("title", "A strip across the sheet that hides and shows as one; drop it where a section should start")
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

  it("disables the components and the Section in Preview, saying where to lay out the sheets", () => {
    useWorkbenchViewStore.setState({ mode: "preview" })
    render(<WorkbenchPalette />)

    for (const id of ["palette-item-tableInput", "palette-item-section"]) {
      const item = screen.getByTestId(id)
      expect(item).toHaveAttribute("aria-disabled", "true")
      expect(item).toHaveAttribute("title", "Build to lay out the sheets")
      fireEvent.pointerDown(item, { button: 0, clientX: 10, clientY: 10 })
      expect(useWorkbenchViewStore.getState().creating).toBeNull()
    }
  })
})
