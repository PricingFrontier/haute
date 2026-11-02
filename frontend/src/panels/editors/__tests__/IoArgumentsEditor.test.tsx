import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen } from "@testing-library/react"

import { IoArgumentsEditor } from "../_IoFormatEditor"

afterEach(cleanup)

function renderArguments(value: Record<string, unknown>) {
  const onCommit = vi.fn()
  render(
    <IoArgumentsEditor value={value} argumentNames={["separator", "batch_size"]} context="CSV" onCommit={onCommit} />,
  )
  return onCommit
}

function type(label: string, text: string) {
  const input = screen.getByLabelText(label)
  fireEvent.change(input, { target: { value: text } })
  fireEvent.blur(input)
}

describe("IoArgumentsEditor", () => {
  it("says a named argument without a value is not saved", () => {
    const onCommit = renderArguments({})
    fireEvent.click(screen.getByRole("button", { name: "Add argument" }))
    type("Argument 1 name", "separator")

    expect(onCommit).toHaveBeenLastCalledWith({})
    expect(screen.getByText("separator has no value, so it is not saved. Enter a JSON value such as \",\".")).toBeInTheDocument()
  })

  it("does not claim a previous value is kept for a new argument with invalid JSON", () => {
    renderArguments({})
    fireEvent.click(screen.getByRole("button", { name: "Add argument" }))
    type("Argument 1 name", "separator")
    type("Argument 1 value", "{bad")

    expect(screen.getByText("Invalid JSON. separator is not saved until this parses.")).toBeInTheDocument()
    expect(screen.queryByText(/previous value is kept/)).not.toBeInTheDocument()
  })

  it("keeps a saved argument's value while its edit does not parse, and says so", () => {
    const onCommit = renderArguments({ separator: "," })
    type("Argument 1 value", "{bad")

    expect(onCommit).toHaveBeenLastCalledWith({ separator: "," })
    expect(screen.getByText("Invalid JSON. The previous value is kept until this parses.")).toBeInTheDocument()
  })
})
