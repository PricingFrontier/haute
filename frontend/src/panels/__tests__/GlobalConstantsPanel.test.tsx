import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { Node } from "@xyflow/react"
import GlobalConstantsPanel from "../GlobalConstantsPanel"
import useGraphStore from "../../stores/useGraphStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { GlobalConstantDraft } from "../../utils/globalConstants"

const uniform = (name: string, value: string): GlobalConstantDraft =>
  ({ name, type: "float", split: false, value, bySource: {} })

function reader(id: string, label: string, code: string): Node {
  return { id, type: "polars", position: { x: 0, y: 0 }, data: { label, nodeType: "polars", config: { code } } }
}

function load(constants: GlobalConstantDraft[], options: { error?: string | null; nodes?: Node[] } = {}) {
  useGraphStore.getState().loadGraphSnapshot({
    nodes: options.nodes ?? [],
    edges: [],
    preamble: "",
    submodels: {},
    globalConstants: constants,
    globalConstantsError: options.error ?? null,
  })
}

const constants = () => useGraphStore.getState().globalConstants

describe("GlobalConstantsPanel", () => {
  beforeEach(() => {
    useGraphStore.getState().resetForTests()
    useSettingsStore.getState().setSources(["live", "nb_batch"])
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("adds an empty uniform float constant with a free name, marked as needing a value", () => {
    load([uniform("constant_1", "1")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "Add constant" }))

    // The new constant is selected for editing.
    expect(constants()[1]).toEqual(uniform("constant_2", ""))
    expect(screen.getByTestId("constant-name-1")).toHaveValue("constant_2")
    expect(screen.getByTestId("constant-value-issue-1")).toHaveTextContent("Enter a value.")
    expect(useGraphStore.getState().dirty).toBe(true)
    expect(useGraphStore.getState().undoStack).toEqual([])
  })

  it("edits a value and marks an invalid one with its reason", () => {
    load([uniform("rate", "1")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    fireEvent.change(screen.getByTestId("constant-value-0"), { target: { value: "abc" } })

    expect(constants()[0].value).toBe("abc")
    expect(screen.getByTestId("constant-value-issue-0")).toHaveTextContent("Enter a number.")
  })

  it("splits by source from the uniform value and marks an emptied source as missing", () => {
    load([uniform("rate", "1.5")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    fireEvent.click(screen.getByTestId("constant-split-0"))
    expect(constants()[0]).toMatchObject({ split: true, bySource: { live: "1.5", nb_batch: "1.5" } })

    fireEvent.change(screen.getByTestId("constant-value-0-nb_batch"), { target: { value: "" } })
    expect(screen.getByTestId("constant-value-issue-0-nb_batch")).toHaveTextContent("Missing")
  })

  it("asks before joining discards a differing source value, and keeps live", () => {
    load([{ name: "rate", type: "float", split: true, value: "", bySource: { live: "1", nb_batch: "2" } }])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true)

    fireEvent.click(screen.getByTestId("constant-split-0"))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("discards nb_batch: 2"))
    expect(constants()[0].split).toBe(true)

    fireEvent.click(screen.getByTestId("constant-split-0"))
    expect(constants()[0]).toEqual(uniform("rate", "1"))
  })

  it("keeps the values that convert exactly when the type changes", () => {
    load([{ name: "rate", type: "float", split: true, value: "", bySource: { live: "2.0", nb_batch: "2.5" } }])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    fireEvent.click(screen.getByRole("radio", { name: "Integer" }))

    expect(constants()[0]).toMatchObject({ type: "integer", bySource: { live: "2", nb_batch: "" } })
    expect(screen.getByTestId("constant-value-issue-0-nb_batch")).toHaveTextContent("Missing")
  })

  it("lists the constants with their type and value, and shows the selected one's readers", () => {
    load([uniform("rate", "1.5"), { name: "cap", type: "integer", split: true, value: "", bySource: { live: "3" } }], {
      nodes: [reader("n1", "Pricing", "df = df.with_columns(r=pl.lit(global_constants.rate))")],
    })
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    const rate = screen.getByRole("button", { name: "rate complete" })
    expect(rate).toHaveTextContent("Decimal")
    expect(rate).toHaveTextContent("1.5")
    const cap = screen.getByRole("button", { name: "cap needs attention" })
    expect(cap).toHaveTextContent("1/2 sources")
    expect(cap).toHaveAttribute("title", "No nb_batch value")
    expect(screen.getByTestId("constant-readers-0")).toHaveTextContent("Pricing")

    fireEvent.click(cap)
    expect(screen.getByTestId("constant-readers-1")).toHaveTextContent("No node reads it")
  })

  it("moves a constant within the list", () => {
    load([uniform("first", "1"), uniform("second", "2")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    fireEvent.keyDown(screen.getByRole("button", { name: "first complete" }), { key: "ArrowDown", altKey: true })

    expect(constants().map((draft) => draft.name)).toEqual(["second", "first"])
  })

  it("asks before deleting a constant that is read, naming its readers", () => {
    load([uniform("rate", "1")], { nodes: [reader("n1", "Pricing", "x = global_constants.rate")] })
    render(<GlobalConstantsPanel onClose={vi.fn()} />)
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true)

    fireEvent.click(screen.getByRole("button", { name: "Remove rate" }))
    expect(confirm).toHaveBeenLastCalledWith("Delete rate? Pricing read it.")
    expect(constants()).toHaveLength(1)

    fireEvent.click(screen.getByRole("button", { name: "Remove rate" }))
    expect(constants()).toEqual([])
  })

  it("deletes an unread constant without asking", () => {
    load([uniform("rate", "1")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)
    const confirm = vi.spyOn(window, "confirm")

    fireEvent.click(screen.getByRole("button", { name: "Remove rate" }))

    expect(confirm).not.toHaveBeenCalled()
    expect(constants()).toEqual([])
  })

  it("asks before renaming a constant that is read, and restores the name when declined", () => {
    load([uniform("rate", "1")], { nodes: [reader("n1", "Pricing", "x = global_constants.rate")] })
    render(<GlobalConstantsPanel onClose={vi.fn()} />)
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true)
    const name = screen.getByTestId("constant-name-0")

    fireEvent.change(name, { target: { value: "base_rate" } })
    fireEvent.blur(name)
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("Pricing read rate"))
    expect(constants()[0].name).toBe("rate")
    expect(name).toHaveValue("rate")

    fireEvent.change(name, { target: { value: "base_rate" } })
    fireEvent.blur(name)
    expect(constants()[0].name).toBe("base_rate")
  })

  it("marks an invalid or duplicate name", () => {
    load([uniform("rate", "1"), uniform("other", "2")])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "other complete" }))

    const name = screen.getByTestId("constant-name-1")
    fireEvent.change(name, { target: { value: "rate" } })
    fireEvent.blur(name)

    expect(screen.getByTestId("constant-name-issue-1")).toHaveTextContent("rate is defined more than once.")
  })

  it("shows a constants file that failed to load, read-only", () => {
    load([], { error: "bad JSON" })
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    expect(screen.getByTestId("constants-load-error")).toHaveTextContent("bad JSON")
    expect(screen.queryByRole("button", { name: "Add constant" })).not.toBeInTheDocument()
  })

  it("lists values for sources the pipeline lacks and removes them", () => {
    load([{ name: "rate", type: "float", split: true, value: "", bySource: { live: "1", old: "3" } }])
    render(<GlobalConstantsPanel onClose={vi.fn()} />)

    expect(screen.getByTestId("constants-unknown-sources")).toHaveTextContent("old")
    fireEvent.click(screen.getByTestId("constants-remove-unknown"))

    expect(constants()[0].bySource).toEqual({ live: "1" })
  })
})
