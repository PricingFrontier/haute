/**
 * Toolbar add-source form: a rejected name must surface inline feedback and
 * keep the form open, not close silently.
 *
 * addSource returns a discriminated AddSourceResult; before this the toolbar
 * treated the bare `null` as success and just closed the form, so a name that
 * collided with an existing source (under the blessed sanitizeName identity)
 * or a blank name vanished with no explanation. These tests pin that both
 * reject reasons produce a distinguishable message and leave the form open,
 * while a valid name still adds the source and closes the form.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import Toolbar from "../Toolbar"
import useSettingsStore from "../../stores/useSettingsStore"

function makeProps(overrides: Partial<Parameters<typeof Toolbar>[0]> = {}) {
  return {
    nodeCount: 5,
    dirty: false,
    canUndo: true,
    canRedo: false,
    onUndo: vi.fn(),
    onRedo: vi.fn(),
    onZoomIn: vi.fn(),
    onZoomOut: vi.fn(),
    onOpenUtility: vi.fn(),
    onOpenImports: vi.fn(),
    onCentre: vi.fn(),
    onAutoLayout: vi.fn(),
    isAutoLayouting: false,
    onSave: vi.fn(),
    onSaveCommit: vi.fn(),
    wsStatus: "connected" as const,
    ...overrides,
  }
}

/** Render the toolbar, open the dropdown, and click "Add source" so the inline form is showing. */
function openAddSourceForm() {
  render(<Toolbar {...makeProps()} />)
  fireEvent.click(screen.getByTitle("Data source"))
  fireEvent.click(screen.getByText("Add source"))
  return screen.getByPlaceholderText("name") as HTMLInputElement
}

function submitForm(input: HTMLInputElement) {
  fireEvent.submit(input.closest("form")!)
}

describe("Toolbar add-source rejection feedback", () => {
  beforeEach(() => {
    useSettingsStore.setState({ sources: ["live"], activeSource: "live" })
  })
  afterEach(cleanup)

  it("shows a collision message naming the existing key, and keeps the form open", () => {
    // "My_Src" already exists; "My Src" sanitises onto the same key.
    useSettingsStore.setState({ sources: ["live", "My_Src"], activeSource: "live" })
    const input = openAddSourceForm()
    fireEvent.change(input, { target: { value: "My Src" } })
    submitForm(input)

    const err = screen.getByTestId("source-add-error")
    expect(err).toBeInTheDocument()
    expect(err).toHaveTextContent('Matches existing source "My_Src"')
    // Form stays open for correction; no phantom source added.
    expect(screen.getByPlaceholderText("name")).toBeInTheDocument()
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src"])
  })

  it("shows an empty-name message on a blank submit, distinct from the collision message", () => {
    const input = openAddSourceForm()
    fireEvent.change(input, { target: { value: "   " } })
    submitForm(input)

    const err = screen.getByTestId("source-add-error")
    expect(err).toHaveTextContent("Enter a name for the source")
    expect(err.textContent).not.toContain("Matches existing source")
    expect(screen.getByPlaceholderText("name")).toBeInTheDocument()
    expect(useSettingsStore.getState().sources).toEqual(["live"])
  })

  it("clears the error and closes the form once a valid name is submitted", () => {
    useSettingsStore.setState({ sources: ["live", "My_Src"], activeSource: "live" })
    const input = openAddSourceForm()

    // First a rejected attempt surfaces the error…
    fireEvent.change(input, { target: { value: "My Src" } })
    submitForm(input)
    expect(screen.getByTestId("source-add-error")).toBeInTheDocument()

    // …then a valid name adds the source, closes the form, and clears the error.
    fireEvent.change(input, { target: { value: "Other Src" } })
    submitForm(input)
    expect(screen.queryByTestId("source-add-error")).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText("name")).not.toBeInTheDocument()
    expect(useSettingsStore.getState().sources).toEqual(["live", "My_Src", "Other_Src"])
    expect(useSettingsStore.getState().activeSource).toBe("Other_Src")
  })

  it("clears the error as soon as the user edits the name", () => {
    const input = openAddSourceForm()
    fireEvent.change(input, { target: { value: "" } })
    submitForm(input)
    expect(screen.getByTestId("source-add-error")).toBeInTheDocument()

    fireEvent.change(input, { target: { value: "a" } })
    expect(screen.queryByTestId("source-add-error")).not.toBeInTheDocument()
  })
})
