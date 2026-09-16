import { describe, expect, it } from "vitest"

import {
  addInteraction,
  addSlot,
  addTerm,
  anchorColumn,
  dtypeDefaultSpec,
  duplicateInteractionIndexes,
  effectiveSlotSpec,
  expressionIdentifiers,
  fitAllWithDefaults,
  modelMembership,
  pickSlotColumn,
  removeSlot,
  removeTerm,
  renameExpression,
  setExpression,
  setIncludeMain,
  setSlotOverride,
  setTermField,
  slotColumnBlockedReason,
  slotFitOptions,
  slotNeedsExplicitFit,
  switchNativeType,
  termsByColumn,
  uniqueExpressionName,
  type InteractionSpec,
  type Terms,
} from "../glmTerms"

const eligible = new Set(["age", "income", "region", "mileage"])
const columns = [
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "mileage", dtype: "Float64" },
]

describe("expression grammar", () => {
  it.each<[string, string[]]>([
    ["age", ["age"]],
    ["age ** 2", ["age"]],
    ["age * income", ["age", "income"]],
    ["age / 12", ["age"]],
    ["  age - income ", ["age", "income"]],
  ])("accepts %s", (expr, ids) => {
    expect(expressionIdentifiers(expr)).toEqual(ids)
  })

  it.each(["", "log(age)", "age ** 2 + 1", "age > 65", "2 * age"])("rejects %s", (expr) => {
    expect(expressionIdentifiers(expr)).toBeNull()
  })

  it("anchors an expression under the first eligible column it names", () => {
    expect(anchorColumn({ type: "expression", expr: "income * age" }, eligible)).toBe("income")
    expect(anchorColumn({ type: "expression", expr: "height ** 2" }, eligible)).toBeNull()
    expect(anchorColumn({ type: "linear" }, eligible)).toBeNull()
  })
})

describe("terms by column", () => {
  it("lists the native term first, expressions after, and unresolved last", () => {
    const terms: Terms = {
      age_sq: { type: "expression", expr: "age ** 2" },
      age: { type: "bs", df: 4 },
      h2: { type: "expression", expr: "height ** 2" },
      region: { type: "categorical" },
    }
    const { byColumn, unresolved } = termsByColumn(terms, eligible)
    expect(byColumn.get("age")?.map((entry) => entry.key)).toEqual(["age", "age_sq"])
    expect(byColumn.get("region")?.map((entry) => entry.key)).toEqual(["region"])
    expect(unresolved.map((entry) => entry.key)).toEqual(["h2"])
  })
})

describe("editor transitions", () => {
  it("dtype defaults are categorical for strings and linear otherwise", () => {
    expect(dtypeDefaultSpec("String")).toEqual({ type: "categorical" })
    expect(dtypeDefaultSpec("Utf8")).toEqual({ type: "categorical" })
    expect(dtypeDefaultSpec("Float64")).toEqual({ type: "linear" })
  })

  it("Add term on an empty row writes the dtype default", () => {
    expect(addTerm({}, "region", "String", eligible)).toEqual({ region: { type: "categorical" } })
    expect(addTerm({}, "age", "Int64", eligible)).toEqual({ age: { type: "linear" } })
  })

  it("Add term on a row with a native term writes a uniquely named ** 2 expression", () => {
    const once = addTerm({ age: { type: "linear" } }, "age", "Int64", eligible)
    expect(once).toEqual({
      age: { type: "linear" },
      age_sq: { type: "expression", expr: "age ** 2" },
    })
    const twice = addTerm(once, "age", "Int64", eligible)
    expect(Object.keys(twice)).toEqual(["age", "age_sq", "age_sq2"])
    expect(uniqueExpressionName("age", { age_sq: { type: "linear" }, age_sq2: { type: "linear" } }, eligible)).toBe("age_sq3")
  })

  it("expression names skip eligible column names", () => {
    expect(uniqueExpressionName("age", {}, new Set(["age", "age_sq"]))).toBe("age_sq2")
  })

  it("type switch keeps only the subset for the new type and ms defaults to increasing", () => {
    const terms: Terms = { age: { type: "bs", df: 5, degree: 2, monotonicity: "increasing", knots: [1, 2] } }
    expect(switchNativeType(terms, "age", "ns")).toEqual({ age: { type: "ns", df: 5 } })
    expect(switchNativeType(terms, "age", "linear")).toEqual({ age: { type: "linear", monotonicity: "increasing" } })
    expect(switchNativeType({ age: { type: "linear" } }, "age", "ms")).toEqual({
      age: { type: "ms", monotonicity: "increasing" },
    })
    expect(switchNativeType(terms, "age", "categorical")).toEqual({ age: { type: "categorical" } })
  })

  it("field edits write the key and empty values delete it, leaving unknown keys alone", () => {
    const terms: Terms = { age: { type: "bs", df: 4, knots: [1] } }
    expect(setTermField(terms, "age", "degree", 2)).toEqual({ age: { type: "bs", df: 4, knots: [1], degree: 2 } })
    expect(setTermField(terms, "age", "df", undefined)).toEqual({ age: { type: "bs", knots: [1] } })
    expect(setTermField(terms, "age", "monotonicity", "")).toEqual({ age: { type: "bs", df: 4, knots: [1] } })
  })

  it("renames an expression unless the name is empty, a column, or another key", () => {
    const terms: Terms = { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } }
    expect(renameExpression(terms, "age_sq", "age_squared", eligible)).toEqual({
      ok: true,
      terms: { age: { type: "linear" }, age_squared: { type: "expression", expr: "age ** 2" } },
    })
    expect(renameExpression(terms, "age_sq", "", eligible).ok).toBe(false)
    expect(renameExpression(terms, "age_sq", "income", eligible)).toMatchObject({ ok: false, reason: expect.stringMatching(/column/) })
    expect(renameExpression(terms, "age_sq", "age", eligible)).toMatchObject({ ok: false, reason: expect.stringMatching(/already/) })
  })

  it("sets an expression only when it matches the grammar over eligible columns", () => {
    const terms: Terms = { age_sq: { type: "expression", expr: "age ** 2" } }
    expect(setExpression(terms, "age_sq", "age * income", eligible)).toEqual({
      ok: true,
      terms: { age_sq: { type: "expression", expr: "age * income" } },
    })
    expect(setExpression(terms, "age_sq", "log(age)", eligible)).toMatchObject({ ok: false, reason: expect.stringContaining("Supported forms") })
    expect(setExpression(terms, "age_sq", "height ** 2", eligible)).toMatchObject({ ok: false, reason: expect.stringContaining("height") })
    expect(setExpression(terms, "age_sq", "age_sq ** 2", eligible).ok).toBe(false)
  })

  it("removes a term and nothing else", () => {
    expect(removeTerm({ age: { type: "linear" }, region: { type: "categorical" } }, "age")).toEqual({
      region: { type: "categorical" },
    })
  })

  it("Fit all with defaults adds only missing native terms", () => {
    const terms: Terms = { age: { type: "bs", df: 4 }, age_sq: { type: "expression", expr: "age ** 2" } }
    expect(fitAllWithDefaults(terms, columns)).toEqual({
      age: { type: "bs", df: 4 },
      age_sq: { type: "expression", expr: "age ** 2" },
      income: { type: "linear" },
      region: { type: "categorical" },
      mileage: { type: "linear" },
    })
  })

  it("membership counts terms, expression identifiers, and filled interaction factors", () => {
    const terms: Terms = { age: { type: "linear" }, inc2: { type: "expression", expr: "income ** 2" } }
    const interactions: InteractionSpec[] = [
      { factors: ["region", "mileage"], include_main: true },
      { factors: ["", ""], include_main: true },
    ]
    expect(modelMembership(terms, interactions, eligible)).toEqual({
      inModel: new Set(["age", "income", "region", "mileage"]),
      interactionOnly: new Set(["region", "mileage"]),
    })
  })
})

describe("interaction slots", () => {
  it("offers only honoured fits per column state", () => {
    expect(slotFitOptions("Float64", null).map((o) => [o.value, o.disabledReason ?? null])).toEqual([
      ["main", "No main term"],
      ["linear", null],
      ["categorical", null],
      ["bs", null],
      ["ns", null],
    ])
    expect(slotFitOptions("String", null).map((o) => [o.value, Boolean(o.disabledReason)])).toEqual([
      ["main", true],
      ["linear", true],
      ["categorical", false],
      ["bs", true],
      ["ns", true],
    ])
    expect(slotFitOptions("Float64", { type: "linear" }).find((o) => o.value === "categorical")?.disabledReason).toMatch(/re-type/)
    expect(slotFitOptions("String", { type: "categorical" }).find((o) => o.value === "linear")?.disabledReason).toMatch(/string/)
    expect(slotFitOptions("Float64", { type: "ms", df: 4 }).find((o) => o.value === "main")?.disabledReason).toMatch(/Monotone/)
  })

  it("flags monotone main terms and blocks target-encoded columns", () => {
    expect(slotNeedsExplicitFit({ type: "ms", df: 4 })).toBe(true)
    expect(slotNeedsExplicitFit({ type: "bs", df: 4, monotonicity: "decreasing" })).toBe(true)
    expect(slotNeedsExplicitFit({ type: "bs", df: 4 })).toBe(false)
    expect(slotColumnBlockedReason({ type: "target_encoding" })).toMatch(/Target-encoded/)
    expect(slotColumnBlockedReason({ type: "linear" })).toBeNull()
  })

  it("resolves the effective slot spec as override, else main, else dtype default", () => {
    expect(effectiveSlotSpec("Float64", { type: "bs", df: 4 }, { type: "linear" })).toEqual({ type: "linear" })
    expect(effectiveSlotSpec("Float64", { type: "bs", df: 4 }, undefined)).toEqual({ type: "bs", df: 4 })
    expect(effectiveSlotSpec("String", null, undefined)).toEqual({ type: "categorical" })
  })

  it("writes factors and overrides exactly", () => {
    const base = addInteraction([])
    expect(base).toEqual([{ factors: ["", ""], include_main: true }])
    const picked = pickSlotColumn(base, 0, 0, "age")
    expect(picked).toEqual([{ factors: ["age", ""], include_main: true }])
    const overridden = setSlotOverride(picked, 0, "age", { type: "bs", df: 3 })
    expect(overridden).toEqual([{ factors: ["age", ""], specs: { age: { type: "bs", df: 3 } }, include_main: true }])
    expect(setSlotOverride(overridden, 0, "age", null)).toEqual([{ factors: ["age", ""], include_main: true }])
    expect(pickSlotColumn(overridden, 0, 0, "income")).toEqual([{ factors: ["income", ""], include_main: true }])
    expect(addSlot(picked, 0)).toEqual([{ factors: ["age", "", ""], include_main: true }])
    expect(removeSlot(addSlot(overridden, 0), 0, 0)).toEqual([{ factors: ["", ""], include_main: true }])
    expect(removeSlot(picked, 0, 0)).toEqual(picked)
    expect(setIncludeMain(picked, 0, false)).toEqual([{ factors: ["age", ""], include_main: false }])
  })

  it("marks duplicate factor sets in any order", () => {
    const interactions: InteractionSpec[] = [
      { factors: ["age", "region"], include_main: true },
      { factors: ["region", "age"], include_main: true },
      { factors: ["age", ""], include_main: true },
      { factors: ["age", "", ""], include_main: true },
    ]
    expect(duplicateInteractionIndexes(interactions)).toEqual(new Set([1]))
  })
})
