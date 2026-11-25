import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { EbmTermAxis, NestedNumbers } from "../../../api/types"
import { TermTableView, type TermDisplay } from "../TermTableView"

// A three-way term whose value names its cell: 100 * proposer + 10 * vehicle + channel.
function term(sizes: [number, number, number]): { axes: EbmTermAxis[]; values: NestedNumbers[] } {
  const labels = (prefix: string, size: number) => Array.from({ length: size }, (_, i) => `${prefix}${i}`)
  const [proposer, vehicle, channel] = sizes
  return {
    axes: [
      { feature: "proposer_age", type: "nominal", labels: labels("p", proposer) },
      { feature: "vehicle_age", type: "nominal", labels: labels("v", vehicle) },
      { feature: "channel", type: "nominal", labels: labels("c", channel) },
    ],
    values: Array.from({ length: proposer }, (_, p) =>
      Array.from({ length: vehicle }, (_, v) => Array.from({ length: channel }, (_, c) => 100 * p + 10 * v + c)),
    ),
  }
}

function view(sizes: [number, number, number], display: TermDisplay = "table") {
  return (
    <TermTableView
      title="Table for proposer_age × vehicle_age × channel"
      {...term(sizes)}
      reference={0}
      valueLabel="Score"
      display={display}
    />
  )
}

function cellTexts(): string[][] {
  const table = screen.getByRole("table", { name: "Table for proposer_age × vehicle_age × channel" })
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent ?? ""))
}

describe("TermTableView", () => {
  afterEach(cleanup)

  it("shows a retrained term with fewer cells afresh, with the last fixed cell picked before", () => {
    const { rerender } = render(view([9, 7, 3]))
    // proposer_age (9) runs down the rows and channel (3) across; vehicle_age is fixed.
    expect(screen.getByRole("combobox", { name: "Rows" })).toHaveValue("0")
    expect(screen.getByRole("combobox", { name: "Columns" })).toHaveValue("2")
    fireEvent.change(screen.getByRole("combobox", { name: "vehicle_age" }), { target: { value: "6" } })
    expect(cellTexts()[0]).toEqual(["60", "61", "62"])

    rerender(view([5, 4, 2]))

    expect(screen.getByRole("combobox", { name: "vehicle_age" })).toHaveValue("0")
    expect(cellTexts()).toEqual([
      ["0", "1"],
      ["100", "101"],
      ["200", "201"],
      ["300", "301"],
      ["400", "401"],
    ])
  })

  it("keeps the picks when a retrain gives the term the same cells", () => {
    const { rerender } = render(view([9, 7, 3]))
    fireEvent.change(screen.getByRole("combobox", { name: "vehicle_age" }), { target: { value: "6" } })
    rerender(view([9, 7, 3]))
    expect(screen.getByRole("combobox", { name: "vehicle_age" })).toHaveValue("6")
  })

  it("forgets a chart's pointed cell that the retrained term no longer has", () => {
    const { rerender } = render(view([9, 7, 3], "chart"))
    const chart = screen.getByRole("img", { name: "Table for proposer_age × vehicle_age × channel" })
    fireEvent.mouseEnter(within(chart).getByText("p8", { selector: "title" }).parentElement!)
    expect(screen.getByRole("status")).toHaveTextContent("proposer_age p8")

    rerender(view([5, 4, 2], "chart"))

    expect(screen.getByRole("status")).toBeEmptyDOMElement()
    expect(screen.getByRole("img", { name: "Table for proposer_age × vehicle_age × channel" }).querySelectorAll("path"))
      .toHaveLength(2)
  })
})
