/**
 * The workbench's view (specs/workbench): the form read when it first shows, its states,
 * the way out of a refused save, the view's keyboard shortcuts, and the project's panels
 * beside it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import useUIStore from "../../stores/useUIStore"
import useWorkbenchFormStore from "../../stores/useWorkbenchFormStore"
import useWorkbenchStore from "../../stores/useWorkbenchStore"
import WorkbenchView from "../WorkbenchView"

vi.mock("../SchemaEditor", () => ({
  default: () => (
    <div data-testid="schema-editor">
      <input aria-label="Column name" />
    </div>
  ),
}))
vi.mock("../../panels/GitPanel", () => ({ default: () => <div data-testid="git-panel" /> }))
vi.mock("../../panels/assistant/AssistantPanel", () => ({ default: () => <div data-testid="assistant-panel" /> }))

const onSave = vi.fn(async () => true)

function renderView() {
  return render(<WorkbenchView onSave={onSave} isInsideSubmodel={false} readOnly={false} />)
}

const key = (target: EventTarget, init: KeyboardEventInit) =>
  target.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }))

describe("WorkbenchView", () => {
  const actions = { load: vi.fn(async () => {}), reload: vi.fn(async () => {}), save: vi.fn(async () => true), undo: vi.fn(), redo: vi.fn() }

  beforeEach(() => {
    useWorkbenchFormStore.setState({ status: "ready", loadError: null, stale: false, ...actions })
    useWorkbenchStore.setState({ enabled: true, activeView: "workbench" })
    useUIStore.setState({ gitOpen: false, assistantOpen: false })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
    useWorkbenchStore.setState({ enabled: false, activeView: "pipeline" })
  })

  it("reads the form when it first shows, and shows the schema editor once it is read", () => {
    renderView()

    expect(actions.load).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("schema-editor")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Schema" })).toHaveAttribute("aria-current", "page")
    expect(within(screen.getByRole("group", { name: "Views" })).getByRole("button", { name: "Workbench" })).toHaveAttribute("aria-pressed", "true")
  })

  it("says the form is loading, or why it could not be read with a way to try again", () => {
    useWorkbenchFormStore.setState({ status: "loading" })
    const { rerender } = renderView()
    expect(screen.getByRole("status")).toHaveTextContent("Loading the form…")

    act(() => {
      useWorkbenchFormStore.setState({ status: "failed", loadError: "forms/form.json is not JSON: bad" })
    })
    rerender(<WorkbenchView onSave={onSave} isInsideSubmodel={false} readOnly={false} />)
    expect(screen.getByRole("alert")).toHaveTextContent("The form could not be read: forms/form.json is not JSON: bad")
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(actions.reload).toHaveBeenCalledTimes(1)
  })

  it("reports a save refused because the form changed on disk, with a reload", () => {
    useWorkbenchFormStore.setState({ stale: true })
    renderView()

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The form changed on disk since the workbench read it. Reload it to go on; unsaved edits will be lost.",
    )
    fireEvent.click(screen.getByRole("button", { name: "Reload" }))
    expect(actions.reload).toHaveBeenCalledTimes(1)
  })

  it("saves on Ctrl+S, with a focused field's edit included", async () => {
    vi.useFakeTimers()
    try {
      renderView()
      const field = screen.getByRole("textbox", { name: "Column name" })
      field.focus()

      key(field, { key: "s", ctrlKey: true })
      expect(document.activeElement).not.toBe(field)
      expect(actions.save).not.toHaveBeenCalled()
      await vi.runAllTimersAsync()
      expect(actions.save).toHaveBeenCalledTimes(1)

      key(window, { key: "s", metaKey: true })
      expect(actions.save).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it("undoes and redoes with Ctrl+Z, Ctrl+Shift+Z and Ctrl+Y, not while typing or in a dialog", () => {
    renderView()
    key(window, { key: "z", ctrlKey: true })
    key(window, { key: "Z", ctrlKey: true, shiftKey: true })
    key(window, { key: "y", ctrlKey: true })
    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(actions.redo).toHaveBeenCalledTimes(2)

    key(screen.getByRole("textbox", { name: "Column name" }), { key: "z", ctrlKey: true })
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    dialog.setAttribute("aria-modal", "true")
    document.body.appendChild(dialog)
    key(dialog, { key: "z", ctrlKey: true })
    key(dialog, { key: "s", ctrlKey: true })
    expect(actions.undo).toHaveBeenCalledTimes(1)
    expect(actions.save).not.toHaveBeenCalled()
  })

  it("opens the Git and Assistant panels beside the view", async () => {
    useUIStore.setState({ gitOpen: true })
    renderView()
    // The panels are lazy chunks, mounted once their import settles.
    expect(await within(screen.getByRole("complementary", { name: "Version control" })).findByTestId("git-panel")).toBeInTheDocument()

    act(() => {
      useUIStore.setState({ gitOpen: false, assistantOpen: true })
    })
    expect(await within(screen.getByRole("complementary", { name: "Assistant" })).findByTestId("assistant-panel")).toBeInTheDocument()
  })
})
