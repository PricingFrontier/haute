import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"

import type { TBoostTables } from "../../../api/types"
import { makeTrainResult } from "../../../test-utils/factories"
import { TBoostTablesTab } from "../TBoostTablesTab"

const exp = (rows: number[][]) => rows.map((row) => row.map(Math.exp))

// The backend has already dropped t-boost's empty categorical cells, so the
// region axis lists only the cells that hold levels, in cell order.
const TABLES: TBoostTables = {
  link: "log",
  base_value: Math.log(0.12),
  tables: [
    {
      term: "region",
      features: ["region"],
      order: 1,
      importance: 0.5,
      axes: [{ feature: "region", type: "nominal", labels: ["east", "south, west", "Missing", "north"] }],
      scores: [-0.3, -0.1, 0, 0.4],
      relativities: [-0.3, -0.1, 0, 0.4].map(Math.exp),
      support: [455, 440, 12, 433],
    },
    {
      term: "age",
      features: ["age"],
      order: 1,
      importance: 0.3,
      axes: [
        {
          feature: "age",
          type: "continuous",
          labels: ["Missing", "<= 25", "> 25"],
          cuts: [25],
        },
      ],
      scores: [0.05, 0.6, -0.1],
      relativities: [0.05, 0.6, -0.1].map(Math.exp),
      support: [3, 120, 1200],
    },
    {
      term: "region × age",
      features: ["region", "age"],
      order: 2,
      importance: 0.15,
      axes: [
        { feature: "region", type: "nominal", labels: ["east", "north"] },
        { feature: "age", type: "continuous", labels: ["Missing", "<= 25", "> 25"], cuts: [25] },
      ],
      scores: [[0, 0.2, -0.02], [0, -0.1, 0.01]],
      relativities: exp([[0, 0.2, -0.02], [0, -0.1, 0.01]]),
      support: [[1, 60, 600], [2, 60, 600]],
    },
    {
      term: "region × age × cover",
      features: ["region", "age", "cover"],
      order: 3,
      importance: 0.05,
      axes: [
        { feature: "region", type: "nominal", labels: ["east", "north"] },
        { feature: "age", type: "continuous", labels: ["Missing", "<= 25", "> 25"], cuts: [25] },
        { feature: "cover", type: "nominal", labels: ["basic", "full"] },
      ],
      // scores[r][a][c] = 0.1 * r + 0.01 * a + 0.001 * c, so each cell names its index.
      scores: [0, 1].map((r) => [0, 1, 2].map((a) => [0, 1].map((c) => 0.1 * r + 0.01 * a + 0.001 * c))),
      relativities: [0, 1].map((r) => [0, 1, 2].map((a) => [0, 1].map((c) => Math.exp(0.1 * r + 0.01 * a + 0.001 * c)))),
      support: [0, 1].map(() => [0, 1, 2].map(() => [0, 1].map(() => 10))),
    },
  ],
  factored: [{ term: "age × cover × vehicle", features: ["age", "cover", "vehicle"], importance: 0.01 }],
}

function select(table: string) {
  fireEvent.click(screen.getByText(table, { selector: "button *, button" }))
}

function cellTexts(table: HTMLElement): string[][] {
  return within(table)
    .getAllByRole("row")
    .slice(1)
    .map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent ?? ""))
}

describe("TBoostTablesTab", () => {
  afterEach(cleanup)

  it("shows the most important table as relativities with its training mass and the base", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: TABLES })} />)
    expect(screen.getByRole("heading", { name: "region" })).toBeInTheDocument()
    expect(screen.getByText(/Main effect · importance 0.5 · base 0.12/)).toBeInTheDocument()
    const shape = screen.getByRole("img", { name: "Table for region" })
    expect(within(shape).getByText("south, west")).toBeInTheDocument()
    expect(within(shape).getByText("north").closest("[title]")).toHaveAttribute(
      "title",
      `north: Relativity: ${Math.exp(0.4)}; Training mass (weight × exposure): 433`,
    )
  })

  it("switches a log-link table to link-scale values", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: TABLES })} />)
    fireEvent.click(screen.getByRole("button", { name: "Link scale" }))
    expect(screen.getByRole("button", { name: "Link scale" })).toHaveAttribute("aria-pressed", "true")
    expect(within(screen.getByRole("img", { name: "Table for region" })).getByText("north").closest("[title]"))
      .toHaveAttribute("title", "north: Score: 0.4; Training mass (weight × exposure): 433")
    select("age")
    expect(screen.getByText("Missing values score 0.05 · Training mass (weight × exposure) 3")).toBeInTheDocument()
  })

  it("draws a two-way table over its two axes", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: TABLES })} />)
    select("region × age")
    expect(screen.getByText(/2-way table/)).toBeInTheDocument()
    const table = screen.getByRole("table", { name: "Table for region × age" })
    // The table's first axis is on the rows, as chosen, though age has more cells.
    expect(within(table).getAllByRole("columnheader")[0]).toHaveTextContent(String.raw`region \ age`)
    expect(within(table).getAllByRole("rowheader").map((cell) => cell.textContent)).toEqual([
      "east",
      "north",
    ])
    expect(cellTexts(table)[0]).toEqual(["1", "1.22", "0.98"])
  })

  it("slices a three-way table by the selector of its remaining axis", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: TABLES })} />)
    select("region × age × cover")
    fireEvent.click(screen.getByRole("button", { name: "Link scale" }))
    const table = () => screen.getByRole("table", { name: "Table for region × age × cover" })
    // Rows are region and columns age, as the selectors show; cover is fixed at basic.
    expect(screen.getByRole("combobox", { name: "Rows" })).toHaveValue("0")
    expect(screen.getByRole("combobox", { name: "Columns" })).toHaveValue("1")
    expect(cellTexts(table())).toEqual([["0", "0.01", "0.02"], ["0.1", "0.11", "0.12"]])
    fireEvent.change(screen.getByRole("combobox", { name: "cover" }), { target: { value: "1" } })
    expect(cellTexts(table())).toEqual([["0.001", "0.011", "0.021"], ["0.101", "0.111", "0.121"]])
    // Swapping the axes puts age on the rows.
    fireEvent.change(screen.getByRole("combobox", { name: "Rows" }), { target: { value: "1" } })
    expect(screen.getByRole("combobox", { name: "Columns" })).toHaveValue("0")
    expect(cellTexts(table())).toEqual([["0.001", "0.101"], ["0.011", "0.111"], ["0.021", "0.121"]])
    // Putting cover on the columns (rows stay age) leaves region to be sliced.
    fireEvent.change(screen.getByRole("combobox", { name: "Columns" }), { target: { value: "2" } })
    expect(screen.queryByRole("combobox", { name: "cover" })).toBeNull()
    expect(cellTexts(table())).toEqual([["0", "0.001"], ["0.01", "0.011"], ["0.02", "0.021"]])
    fireEvent.change(screen.getByRole("combobox", { name: "region" }), { target: { value: "1" } })
    expect(cellTexts(table())).toEqual([["0.1", "0.101"], ["0.11", "0.111"], ["0.12", "0.121"]])
  })

  it("lists a factored effect without a table", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: TABLES })} />)
    select("age × cover × vehicle")
    expect(screen.getByText(/Factored effect, no dense table/)).toBeInTheDocument()
    expect(screen.queryByRole("table")).toBeNull()
  })

  it("shows link-scale values only for a non-log link", () => {
    const identity: TBoostTables = {
      ...TABLES,
      link: "identity",
      base_value: 100,
      tables: TABLES.tables.map((table) => ({ ...table, relativities: null })),
    }
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: identity })} />)
    expect(screen.queryByRole("button", { name: "Relativity" })).toBeNull()
    expect(screen.getByText(/base 100/)).toBeInTheDocument()
  })

  it("states plainly when a result has no tables", () => {
    render(<TBoostTablesTab result={makeTrainResult({ tboost_tables: null })} />)
    expect(screen.getByText("No t-boost tables available")).toBeInTheDocument()
  })
})
