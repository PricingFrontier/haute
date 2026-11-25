/**
 * The checks a quote must pass to be marked final (specs/workbench), held to the cases the
 * server's checks are held to (`tests/test_workbench_checks.py`), and how the list says one.
 */
import { describe, expect, it } from "vitest"
import type { FormSpec } from "../../api/types"
import { checkLine, quoteChecks, type QuoteCheck } from "../quoteChecks"
import type { PricedRows, RowsByTable } from "../sheetValues"
import fixture from "./fixtures/quoteChecks.json"

const form = fixture.form as unknown as FormSpec

interface Case {
  name: string
  values: RowsByTable
  tables: Record<string, PricedRows> | null
  expected: QuoteCheck[]
}

describe("quoteChecks", () => {
  it.each(fixture.cases as unknown as Case[])("$name", ({ values, tables, expected }) => {
    expect(quoteChecks(form, values, tables)).toEqual(expected)
  })

  it("checks a typed index's rows by the value typed, read as the index's type", () => {
    const typed = structuredClone(form)
    const drivers = typed.schema.tables.find((table) => table.name === "drivers")!
    drivers.inputs.find((column) => column.name === "row_number")!.index = "typed"
    const checks = quoteChecks(typed, { drivers: [{ row_number: "07", name: "Ana" }] }, { drivers: [{ row_number: 7, loading: 1, licence_ok: false }] })
    expect(checks.find((check) => check.column === "licence_ok")).toEqual({ kind: "validation", table: "drivers", column: "licence_ok", passed: false, rows: ["7"] })
    expect(checks.find((check) => check.column === "loading")?.passed).toBe(true)
  })
})

describe("quoteChecks on a row the store keeps for columns the form no longer holds", () => {
  it("holds nothing in a tick box, where a row with anything typed holds it unticked", () => {
    const ticked = structuredClone(form)
    const drivers = ticked.schema.tables.find((table) => table.name === "drivers")!
    const name = drivers.inputs.find((column) => column.name === "name")!
    drivers.inputs.push({ ...name, name: "declared", label: "Declared", type: "bool" })
    const required = (rows: RowsByTable) => quoteChecks(ticked, rows, null).find((check) => check.column === "declared")

    expect(required({ drivers: [{ name: "Ana" }, { $kept: '{"licence":"B"}' }] })).toEqual({ kind: "required", table: "drivers", column: "declared", passed: false, rows: ["2"] })
    expect(required({ drivers: [{ name: "Ana" }, {}] })?.passed).toBe(true)
  })
})

describe("checkLine", () => {
  const line = (check: Partial<QuoteCheck>) => checkLine(form, { kind: "required", table: "policy", column: "age", passed: false, rows: [], ...check })

  it("names a check by its table and its column's label and says what it asks", () => {
    expect(line({})).toEqual({ name: "policy · Age", rule: "Needs a value", rows: "" })
    expect(line({ kind: "range" }).rule).toBe("17 to 99")
    expect(line({ kind: "range", column: "excess" }).rule).toBe("At least 0")
    expect(line({ kind: "allowed", column: "region" }).rule).toBe("One of its allowed values")
  })

  it("says a validation's message, and the rows failing a many-row table's check", () => {
    expect(line({ kind: "validation", column: "premium_ok" }).rule).toBe("The premium is below the minimum")
    expect(line({ kind: "validation", table: "drivers", column: "licence_ok", rows: ["2", "3"] })).toEqual({ name: "drivers · Licence check", rule: "", rows: "Rows 2, 3" })
    expect(line({ table: "drivers", column: "name", rows: ["2"] }).rows).toBe("Row 2")
  })
})
