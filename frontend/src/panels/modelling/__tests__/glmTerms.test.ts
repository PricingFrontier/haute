import { describe, expect, it } from "vitest"

import {
  addInteraction,
  addSlot,
  addTerm,
  additionalTypeOptions,
  anchorColumn,
  dtypeDefaultSpec,
  duplicateInteractionIndexes,
  effectiveSlotSpec,
  expressionIdentifiers,
  fitAllWithDefaults,
  interactionEncodingOptions,
  isExpressionSpec,
  modelMembership,
  nativeTermOf,
  nativeTypeOptions,
  pickSlotColumn,
  removeSlot,
  removeTerm,
  renameExpression,
  setExpression,
  setIncludeMain,
  setInteractionEncoding,
  setInteractionField,
  setSlotOverride,
  setTermField,
  slotFitOptions,
  slotNeedsExplicitFit,
  switchNativeType,
  switchAdditionalType,
  termsByColumn,
  uniqueExpressionName,
  type InteractionSpec,
  type Terms,
  type TermSpec,
} from "../glmTerms"

const eligible = new Set(["age", "income", "region", "mileage"])
const columns = [
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "mileage", dtype: "Float64" },
]

describe("target-encoding product fits", () => {
  it("allows one categorical target encoding alongside linear numeric partners", () => {
    expect(slotFitOptions("String", { type: "categorical" }, [{ type: "linear" }]).map((option) => option.value))
      .toEqual(["main", "categorical", "target_encoding"])
    expect(slotFitOptions("Float64", { type: "linear" }, [{ type: "target_encoding" }]).map((option) => option.value))
      .toEqual(["main", "linear"])
  })

  it("rejects target encoding with categorical, spline, or a second encoded partner", () => {
    for (const partner of [{ type: "categorical" }, { type: "bs" }, { type: "target_encoding" }]) {
      expect(slotFitOptions("String", { type: "categorical" }, [partner]).map((option) => option.value))
        .not.toContain("target_encoding")
    }
    expect(slotFitOptions("Float64", { type: "bs" }, [{ type: "target_encoding" }]).map((option) => option.value))
      .toEqual(["linear"])
  })

  it("allows a linear partner only when it is paired with exactly one target encoding", () => {
    expect(slotFitOptions("Float64", { type: "linear" }, [{ type: "target_encoding" }, { type: "linear" }]).map((option) => option.value))
      .toEqual(["main", "linear"])
    expect(slotFitOptions("Float64", { type: "linear" }, [{ type: "target_encoding" }, { type: "categorical" }])).toEqual([])
    expect(slotFitOptions("Float64", { type: "linear" }, [{ type: "target_encoding" }, { type: "target_encoding" }])).toEqual([])
  })

  it("permits native target encoding inheritance but frequency encoding only as an explicit target encoding", () => {
    expect(slotFitOptions("String", { type: "target_encoding" }, [{ type: "linear" }]).map((option) => option.value))
      .toEqual(["main", "target_encoding"])
    expect(slotFitOptions("String", { type: "frequency_encoding" }, [{ type: "linear" }]).map((option) => option.value))
      .toEqual(["target_encoding"])
  })
})

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
  it("preserves a column-key encoding with an explicit source when adding another term", () => {
    const spec = { type: "target_encoding", variable: "region" }
    const terms = { region: spec }
    expect(nativeTermOf(terms, "region")).toEqual(spec)
    expect(addTerm(terms, "region", "String", eligible)).toEqual({
      region: spec,
      region_fe: { type: "frequency_encoding", variable: "region" },
    })
  })

  it("adds each encoding once for a categorical feature and anchors aliases by variable", () => {
    const native: Terms = { region: { type: "categorical" } }
    const target = addTerm(native, "region", "String", eligible)
    const frequency = addTerm(target, "region", "String", eligible)
    expect(target).toEqual({ region: { type: "categorical" }, region_te: { type: "target_encoding", variable: "region" } })
    expect(frequency).toEqual({ ...target, region_fe: { type: "frequency_encoding", variable: "region" } })
    expect(addTerm(frequency, "region", "String", eligible)).toBe(frequency)
    expect(termsByColumn(removeTerm(frequency, "region"), eligible).byColumn.get("region")?.map((entry) => entry.key)).toEqual(["region_te", "region_fe"])
    expect(modelMembership(removeTerm(frequency, "region"), [], eligible).inModel).toEqual(new Set(["region"]))
  })

  it("uses free encoding aliases and excludes duplicate encodings from selectors", () => {
    const terms: Terms = { region: { type: "categorical" }, region_te: { type: "target_encoding", variable: "region" }, region_fe: { type: "frequency_encoding", variable: "region" } }
    expect(addTerm({ region: { type: "categorical" }, region_te: { type: "expression", expr: "region" } }, "region", "String", eligible)).toHaveProperty("region_te2")
    expect(additionalTypeOptions("String", terms, "region", "region_te").map((o) => o.value)).toEqual(["target_encoding"])
    expect(nativeTypeOptions(terms, "region", "String").map((o) => o.value)).toEqual(["categorical"])
    expect(nativeTypeOptions({}, "region", "String").map((o) => o.value))
      .toEqual(["categorical", "target_encoding", "frequency_encoding"])
  })

  it.each(["Int64", "Float64", "Decimal(12, 2)"])("does not offer encodings for %s", (dtype) => {
    const terms: Terms = { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 3", monotonicity: "increasing" } }
    expect(additionalTypeOptions(dtype, terms, "age", "age_sq").map((option) => option.value)).toEqual(["expression"])
    const nativeTypes = nativeTypeOptions(terms, "age", dtype).map((option) => option.value)
    expect(nativeTypes).toEqual(["linear", "bs", "ns", "ms"])
    for (const encoding of ["target_encoding", "frequency_encoding"] as const) {
      expect(switchAdditionalType(terms, "age_sq", "age", dtype, encoding)).toEqual({ ok: false, reason: "Encodings are available only for categorical features." })
    }
  })

  it("switches categorical encoding types without retaining target-only settings", () => {
    const terms: Terms = { region_te: { type: "target_encoding", variable: "region", prior_weight: 10, n_permutations: 3 } }
    expect(switchAdditionalType(terms, "region_te", "region", "String", "frequency_encoding"))
      .toEqual({ ok: true, terms: { region_te: { type: "frequency_encoding", variable: "region" } } })
  })
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
    expect(switchNativeType(terms, "age", "ns")).toEqual({ age: { type: "ns", df: 5, knots: [1, 2] } })
    expect(switchNativeType(terms, "age", "linear")).toEqual({ age: { type: "linear", monotonicity: "increasing" } })
    expect(switchNativeType({ age: { type: "linear" } }, "age", "ms")).toEqual({
      age: { type: "ms", monotonicity: "increasing" },
    })
    expect(switchNativeType(terms, "age", "categorical")).toEqual({ age: { type: "categorical" } })
  })

  it("field edits keep spline mode fields mutually exclusive and preserve unrelated keys", () => {
    const terms: Terms = { age: { type: "bs", df: 4, knots: [1] } }
    expect(setTermField(terms, "age", "degree", 2)).toEqual({ age: { type: "bs", df: 4, knots: [1], degree: 2 } })
    expect(setTermField(terms, "age", "df", undefined)).toEqual({ age: { type: "bs" } })
    expect(setTermField(terms, "age", "monotonicity", "")).toEqual({ age: { type: "bs", df: 4, knots: [1] } })
  })

  it("clears stale spline settings atomically", () => {
    const terms: Terms = { age: { type: "bs", df: 6, k: 10, knots: [1, 2], boundary_knots: [0, 3], degree: 2 } }
    expect(setTermField(terms, "age", "df", undefined)).toEqual({ age: { type: "bs", boundary_knots: [0, 3], degree: 2 } })
    expect(setTermField(terms, "age", "k", 12)).toEqual({ age: { type: "bs", k: 12, boundary_knots: [0, 3], degree: 2 } })
    expect(setTermField(terms, "age", "knots", [1, 2])).toEqual({ age: { type: "bs", knots: [1, 2], boundary_knots: [0, 3], degree: 2 } })
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

  it("treats a malformed term entry as a native term of unknown type instead of throwing", () => {
    const malformed = { bad: null, age: { df: 3 } } as unknown as Terms
    const { byColumn, unresolved } = termsByColumn(malformed, eligible)
    expect(unresolved).toEqual([])
    expect(byColumn.get("bad")).toEqual([{ key: "bad", spec: null }])
    expect(byColumn.get("age")).toEqual([{ key: "age", spec: { df: 3 } }])
    expect(modelMembership(malformed, [], eligible)).toEqual({
      inModel: new Set(["age"]),
      interactionOnly: new Set(),
    })
    expect(nativeTermOf(malformed, "age")).toEqual({ df: 3 })
    expect(nativeTermOf(malformed, "bad")).toBeNull()
    expect(isExpressionSpec(null as unknown as TermSpec)).toBe(false)
    expect(isExpressionSpec([] as unknown as TermSpec)).toBe(false)
  })
})

describe("interaction slots", () => {
  it("offers only honoured fits per column state", () => {
    expect(slotFitOptions("Float64", null).map((o) => o.value)).toEqual(["linear", "bs", "ns"])
    expect(slotFitOptions("String", null).map((o) => o.value)).toEqual(["categorical", "target_encoding"])
    expect(slotFitOptions("Float64", { type: "linear" }).map((o) => o.value)).toEqual(["main", "linear", "bs", "ns"])
    expect(slotFitOptions("String", { type: "categorical" }).map((o) => o.value)).toEqual(["main", "categorical", "target_encoding"])
    expect(slotFitOptions("Float64", { type: "categorical" }).map((o) => o.value)).toEqual(["main", "categorical"])
    expect(slotFitOptions("Float64", { type: "ms", df: 4 }).map((o) => o.value)).toEqual(["linear", "bs", "ns"])
    expect(slotFitOptions("String", { type: "target_encoding" }).map((o) => o.value)).toEqual(["main", "target_encoding"])
    expect(slotFitOptions("String", { type: "frequency_encoding" }).map((o) => o.value)).toEqual(["target_encoding"])
  })

  it("filters interaction modes by dtype and native encodings while allowing mode-first creation", () => {
    const available = (factors: string[], terms: Terms = {}) => interactionEncodingOptions(
      [{ factors, include_main: true }], 0, terms, columns,
    ).map((option) => option.value)
    expect(available(["", ""])).toEqual(["product", "target_encoding", "frequency_encoding"])
    expect(available(["region", ""])).toEqual(["product", "target_encoding", "frequency_encoding"])
    expect(available(["age", "region"])).toEqual(["product"])
    expect(interactionEncodingOptions([
      { factors: ["region", ""], encoding: "target_encoding", include_main: false },
    ], 0, { region: { type: "frequency_encoding" } }, columns).map((option) => option.value)).toEqual(["product", "target_encoding", "frequency_encoding"])
  })

  it("ignores hidden joint overrides when deciding whether Product is available", () => {
    expect(interactionEncodingOptions([
      { factors: ["age", "region"], encoding: "target_encoding", specs: { age: { type: "linear" } }, include_main: true },
    ], 0, { age: { type: "bs" }, region: { type: "target_encoding" } }, columns).map((option) => option.value))
      .toEqual(["target_encoding"])
  })

  it("omits modes used by another interaction with the same factors regardless of order", () => {
    const categoryColumns = [...columns, { name: "brand", dtype: "String" }]
    const interactions: InteractionSpec[] = [
      { factors: ["brand", "region"], include_main: false },
      { factors: ["region", "brand"], encoding: "target_encoding", include_main: false },
      { factors: ["region", "brand"], encoding: "frequency_encoding", include_main: false },
    ]
    expect(interactionEncodingOptions(interactions, 0, {}, categoryColumns).map((option) => option.value)).toEqual(["product"])
    expect(interactionEncodingOptions(interactions, 1, {}, categoryColumns).map((option) => option.value)).toEqual(["target_encoding"])
    expect(interactionEncodingOptions([...interactions, interactions[0]], 3, {}, categoryColumns).map((option) => option.value)).toEqual(["product"])
  })

  it("retains a saved numeric joint mode without offering other encodings", () => {
    expect(interactionEncodingOptions([
      { factors: ["age", "region"], encoding: "target_encoding", include_main: false },
    ], 0, {}, columns).map((option) => option.value)).toEqual(["product", "target_encoding"])
  })

  it("flags monotone main terms", () => {
    expect(slotNeedsExplicitFit({ type: "ms", df: 4 })).toBe(true)
    expect(slotNeedsExplicitFit({ type: "bs", df: 4, monotonicity: "decreasing" })).toBe(true)
    expect(slotNeedsExplicitFit({ type: "bs", df: 4 })).toBe(false)
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

  it("keys duplicate interactions by encoding mode and clears hidden mode fields", () => {
    const interactions: InteractionSpec[] = [
      { factors: ["age", "region"], include_main: true },
      { factors: ["region", "age"], include_main: true, encoding: "target_encoding" },
      { factors: ["region", "age"], include_main: true, encoding: "target_encoding" },
    ]
    expect(duplicateInteractionIndexes(interactions)).toEqual(new Set([2]))
    const encoded = setInteractionEncoding([{ factors: ["age", "region"], specs: { age: { type: "bs" } }, prior_weight: 2, n_permutations: 5, include_main: true }], 0, "target_encoding")
    expect(encoded).toEqual([{ factors: ["age", "region"], encoding: "target_encoding", include_main: true }])
    expect(setInteractionField(encoded, 0, "prior_weight", 0)).toEqual([{ factors: ["age", "region"], encoding: "target_encoding", prior_weight: 0, include_main: true }])
    expect(setInteractionField(encoded, 0, "prior_weight", undefined)).toEqual(encoded)
  })

  it("removes every mode-specific field when returning to Product", () => {
    const initial: InteractionSpec[] = [{
      factors: ["age", "region"],
      encoding: "target_encoding",
      prior_weight: 0,
      n_permutations: 4,
      include_main: true,
    }]
    const frequency = setInteractionEncoding(initial, 0, "frequency_encoding")
    expect(frequency).toEqual([{ factors: ["age", "region"], encoding: "frequency_encoding", include_main: true }])
    expect(setInteractionEncoding(frequency, 0, "product")).toEqual([{ factors: ["age", "region"], include_main: true }])
    expect(setInteractionEncoding(initial, 0, "target_encoding")).toEqual(initial)
  })
})
