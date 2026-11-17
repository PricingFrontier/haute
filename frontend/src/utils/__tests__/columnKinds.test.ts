/**
 * The editor's type rules answer every Polars type as the server's do.
 *
 * `fixtures/columnKindCases.json` is written by `scripts/regen_column_kind_cases.py` from
 * the server's own rules, a case for every type class Polars defines; the server's
 * `tests/test_column_kinds.py` holds it equal to those rules.
 */

import { describe, expect, it } from "vitest"
import { COLUMN_KINDS } from "../../generated/api-contracts.constants.generated"
import { glmDtypeClass } from "../../panels/modelling/glmTerms"
import { dtypeFits } from "../workbenchTables"
import {
  castFamily,
  columnKind,
  isNumericDtype,
  isStringOrCategoricalDtype,
  isTemporalDtype,
} from "../polarsDtypes"
import fixture from "./fixtures/columnKindCases.json"

const WORKBENCH_COLUMN_TYPES = ["int", "float", "str", "bool", "date"] as const

describe("the shared column-kind cases", () => {
  it.each(fixture.cases)("$dtype answers as the server's rules do", (entry) => {
    expect({
      dtype: entry.dtype,
      kind: columnKind(entry.dtype),
      cast_family: castFamily(entry.dtype),
      number: isNumericDtype(entry.dtype),
      date: isTemporalDtype(entry.dtype),
      string_or_categorical: isStringOrCategoricalDtype(entry.dtype),
      glm_class: glmDtypeClass(entry.dtype),
      fills: WORKBENCH_COLUMN_TYPES.filter((declared) => dtypeFits(declared, entry.dtype)),
    }).toEqual(entry)
  })

  it("the editor's table knows every type the cases name, and the cases every type it knows", () => {
    const named = new Set(fixture.cases.map((entry) => entry.dtype.split("(", 1)[0]))
    const known = Object.keys(COLUMN_KINDS)
    // Utf8 is String's other name, and every extension type is a BaseExtension.
    expect(known.filter((name) => !named.has(name)).sort()).toEqual(["BaseExtension", "Utf8"])
    expect([...named].filter((name) => !known.includes(name))).toEqual([])
  })

  it("a Float16 column is a float and a number, as the server says", () => {
    expect(castFamily("Float16")).toBe("float")
    expect(isNumericDtype("Float16")).toBe(true)
  })

  it("a name Polars does not define has no kind", () => {
    expect(columnKind("Float8")).toBeNull()
    expect(columnKind("")).toBeNull()
    expect(columnKind(null)).toBeNull()
    expect(isNumericDtype("f64")).toBe(false)
  })
})
