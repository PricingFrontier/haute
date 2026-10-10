/**
 * The sheets' tabs (specs/workbench): one per sheet, the showing one marked; a sheet
 * added, renamed by double-click, and deleted with a confirmation when components are on
 * it; and, read only in Preview, only switching sheets.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchViewStore from "../../stores/useWorkbenchViewStore"
import PageTabs from "../PageTabs"
import { currentForm, loadForm } from "./fixtures"

const tabs = () => screen.getAllByRole("tab").map((tab) => [tab.textContent, tab.getAttribute("aria-selected")])

describe("PageTabs", () => {
  beforeEach(() => loadForm())

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("lists the sheets, marking the one showing, and shows another on click", () => {
    render(<PageTabs />)

    expect(tabs()).toEqual([["Sheet 1", "true"], ["Sheet 2", "false"]])
    fireEvent.click(screen.getByRole("tab", { name: "Sheet 2" }))
    expect(useWorkbenchViewStore.getState().pageId).toBe("p2")
    expect(tabs()).toEqual([["Sheet 1", "false"], ["Sheet 2", "true"]])
  })

  it("adds a sheet, named in turn, and shows it", () => {
    render(<PageTabs />)

    fireEvent.click(screen.getByRole("button", { name: "Add a sheet" }))

    expect(currentForm().pages.map((page) => page.title)).toEqual(["Sheet 1", "Sheet 2", "Sheet 3"])
    expect(useWorkbenchViewStore.getState().pageId).toBe(currentForm().pages[2].id)
    expect(useWorkbenchFormStore.getState().undoStack).toHaveLength(1)
  })

  it("renames a sheet on double-click, committing on Enter and keeping a blank name out", () => {
    render(<PageTabs />)

    fireEvent.doubleClick(screen.getByRole("tab", { name: "Sheet 1" }))
    const field = screen.getByRole("textbox", { name: "Sheet name" })
    expect(field).toBe(document.activeElement)
    fireEvent.change(field, { target: { value: "  Policy  " } })
    fireEvent.keyDown(field, { key: "Enter" })
    expect(currentForm().pages[0].title).toBe("Policy")
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()

    fireEvent.doubleClick(screen.getByRole("tab", { name: "Policy" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Sheet name" }), { target: { value: "   " } })
    fireEvent.blur(screen.getByRole("textbox", { name: "Sheet name" }))
    expect(currentForm().pages[0].title).toBe("Policy")

    // By the keyboard: F2 on the tab.
    fireEvent.keyDown(screen.getByRole("tab", { name: "Policy" }), { key: "F2" })
    fireEvent.change(screen.getByRole("textbox", { name: "Sheet name" }), { target: { value: "Cover" } })
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Sheet name" }), { key: "Enter" })
    expect(currentForm().pages[0].title).toBe("Cover")
  })

  it("deletes the showing sheet, asking first when components are on it, and never the last one", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false)
    render(<PageTabs />)

    fireEvent.click(screen.getByRole("button", { name: "Delete sheet Sheet 1" }))
    expect(confirm).toHaveBeenCalledWith('Delete "Sheet 1" and the 2 components on it? The schema and the sample stay.')
    expect(currentForm().pages).toHaveLength(2)

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole("button", { name: "Delete sheet Sheet 1" }))
    expect(currentForm().pages.map((page) => page.id)).toEqual(["p2"])
    expect(currentForm().schema.tables).toHaveLength(3)
    expect(useWorkbenchViewStore.getState().pageId).toBe("p2")
    expect(screen.queryByRole("button", { name: /Delete sheet/ })).not.toBeInTheDocument()
  })

  it("read only, in Preview, only switches sheets, staying in Preview", () => {
    useWorkbenchViewStore.setState({ section: "preview" })
    render(<PageTabs readOnly />)

    expect(screen.queryByRole("button", { name: "Add a sheet" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Delete sheet/ })).not.toBeInTheDocument()
    fireEvent.doubleClick(screen.getByRole("tab", { name: "Sheet 1" }))
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole("tab", { name: "Sheet 2" }))
    expect(useWorkbenchViewStore.getState()).toMatchObject({ pageId: "p2", section: "preview" })
  })
})
