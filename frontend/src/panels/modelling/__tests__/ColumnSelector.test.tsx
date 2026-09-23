import { afterEach, describe, expect, it, vi } from "vitest"
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { ColumnSelector } from "../ColumnSelector"

const columns = [
  { name: "claim", dtype: "Int64" },
  { name: "region", dtype: "Utf8" },
  { name: "weight", dtype: "Float64" },
]

describe("ColumnSelector", () => {
  it("searches and selects only known numeric columns with the keyboard", () => {
    const onChange = vi.fn()
    render(<ColumnSelector label="Weight column" value="" columns={columns.filter((column) => column.dtype !== "Utf8")} onChange={onChange} optional />)

    fireEvent.click(screen.getByRole("button", { name: "Weight column" }))
    const search = screen.getByRole("combobox", { name: "Search Weight column" })
    fireEvent.change(search, { target: { value: "wei" } })
    expect(screen.queryByRole("option", { name: /region/i })).toBeNull()
    fireEvent.keyDown(search, { key: "ArrowDown" })
    fireEvent.keyDown(search, { key: "Enter" })

    expect(onChange).toHaveBeenCalledWith("weight")
  })

  it("keeps a saved unavailable value visible without making it selectable", () => {
    render(<ColumnSelector label="Offset column" value="removed_column" columns={columns} onChange={vi.fn()} optional />)
    expect(screen.getByRole("button", { name: "Offset column" })).toHaveTextContent("removed_column")
    expect(screen.getByRole("alert")).toHaveTextContent("removed_column is unavailable")
    fireEvent.click(screen.getByRole("button", { name: "Offset column" }))
    expect(screen.queryByRole("option", { name: /removed_column/i })).toBeNull()
  })

  it("announces the highlighted option and returns focus after Escape or selection", async () => {
    const onChange = vi.fn()
    render(<ColumnSelector label="Target column" value="" columns={columns} onChange={onChange} />)
    const trigger = screen.getByRole("button", { name: "Target column" })
    fireEvent.click(trigger)
    const search = screen.getByRole("combobox", { name: "Search Target column" })
    await waitFor(() => expect(search).toHaveFocus())
    const activeId = search.getAttribute("aria-activedescendant")
    expect(activeId).toBeTruthy()
    expect(document.getElementById(activeId!)).toHaveAttribute("role", "option")
    fireEvent.keyDown(search, { key: "ArrowDown" })
    expect(search.getAttribute("aria-activedescendant")).not.toBe(activeId)
    fireEvent.keyDown(search, { key: "Escape" })
    await waitFor(() => expect(trigger).toHaveFocus())
    fireEvent.click(trigger)
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Search Target column" }), { key: "Enter" })
    expect(onChange).toHaveBeenCalledWith("claim")
    await waitFor(() => expect(trigger).toHaveFocus())
  })
})

afterEach(cleanup)
