import { describe, expect, it } from "vitest"

import dtypeFixture from "./fixtures/glmDtypeClasses.json"
import grammarFixture from "./fixtures/glmExpressionGrammar.json"
import type { ModellingColumn } from "../featureSelection"
import {
  EXPRESSION_GRAMMAR,
  addInteraction,
  addSlot,
  addTerm,
  addTermAvailability,
  additionalTypeOptions,
  columnContext,
  columnIssue,
  duplicateInteractionIndexes,
  expressionIdentifiers,
  featureTag,
  fitAllWithDefaults,
  glmDtypeClass,
  interactionEncodingOptions,
  interactionEntryIssue,
  modelMembership,
  monotoneConstraintTerms,
  nativeTermOf,
  nativeTypeOptions,
  penalisedSmoothTerms,
  pickSlotColumn,
  removeInteraction,
  removeSlot,
  removeTerm,
  renameTerm,
  repairMalformedTerm,
  resolveSlot,
  resolvedSlotSpec,
  setExpression,
  setIncludeMain,
  setInteractionEncoding,
  setInteractionField,
  setSlotOverride,
  setSplineMode,
  setTermField,
  simulateInteractionDesign,
  slotFitOptions,
  slotOverride,
  splineMode,
  switchAdditionalType,
  switchNativeType,
  termSpecIssues,
  termsByColumn,
  typeLabel,
  type InteractionSpec,
  type TermSpec,
  type Terms,
} from "../glmTerms"

const columns: ModellingColumn[] = [
  { name: "age", dtype: "Int64" },
  { name: "income", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "flag", dtype: "Boolean" },
  { name: "start", dtype: "Date" },
  { name: "annual mileage", dtype: "Float64" },
  { name: "constructor", dtype: "Float64" },
  { name: "y", dtype: "Float64" },
]
const context = columnContext(columns, new Map([["y", "target"]]))
const dtypeOf = (name: string) => context.byName.get(name)?.dtype ?? ""
const values = (options: readonly { value: string }[]) => options.map((option) => option.value)

describe("shared contract fixtures", () => {
  it("classifies every Polars dtype name exactly like the backend", () => {
    const cases: Record<string, string> = { ...dtypeFixture.polars, ...dtypeFixture.aliases }
    expect(Object.fromEntries(Object.keys(cases).map((name) => [name, glmDtypeClass(name)]))).toEqual(cases)
  })

  it("the expression grammar fixture passes", () => {
    for (const { expr, identifiers } of grammarFixture.accepted) {
      expect(expressionIdentifiers(expr), expr).toEqual(identifiers)
    }
    for (const expr of grammarFixture.rejected) {
      expect(expressionIdentifiers(expr), expr).toBeNull()
    }
  })
})

describe("column context", () => {
  it("separates eligible, role, and unsupported columns and explains each refusal", () => {
    expect(context.eligible.map((column) => column.name)).toEqual(["age", "income", "region", "flag", "annual mileage", "constructor"])
    expect(context.unsupported.map((column) => column.name)).toEqual(["start"])
    expect(columnIssue("y", context)).toBe("y is the target column")
    expect(columnIssue("ghost", context)).toBe("ghost is not in the upstream data")
    expect(columnIssue("start", context)).toBe("start has dtype Date, which GLM fits do not support")
    expect(columnIssue("age", context)).toBeNull()
  })
})

describe("term placement", () => {
  it("lists unresolved role, missing, unsupported, and malformed terms with reasons", () => {
    const terms = {
      y: { type: "linear" },
      ghost: { type: "linear" },
      start: { type: "categorical" },
      bad: null,
      odd: { type: "poly" },
      h2: { type: "expression", expr: "height ** 2" },
      r2: { type: "expression", expr: "region * 2" },
      income: { type: "expression", expr: "income ** 2" },
      age_sq: { type: "expression", expr: "age ** 2" },
      age: { type: "bs", df: 5 },
      region_te: { type: "target_encoding", variable: "region" },
    } as unknown as Terms
    const { byColumn, unresolved } = termsByColumn(terms, context)
    expect(unresolved.map(({ key, reason, malformed }) => ({ key, reason, malformed }))).toEqual([
      { key: "y", reason: "y is the target column", malformed: false },
      { key: "ghost", reason: "ghost is not in the upstream data", malformed: false },
      { key: "start", reason: "start has dtype Date, which GLM fits do not support", malformed: false },
      { key: "bad", reason: "This entry has no fit type", malformed: true },
      { key: "odd", reason: "Fit type poly is not supported", malformed: true },
      { key: "h2", reason: "height is not in the upstream data", malformed: false },
      { key: "r2", reason: "region is a categorical column; expressions need numbers", malformed: false },
      { key: "income", reason: "The name income is an upstream column; rename the expression", malformed: false },
    ])
    // The native term comes first even when an additional term was added before it.
    expect(byColumn.get("age")?.map((entry) => entry.key)).toEqual(["age", "age_sq"])
    expect(byColumn.get("region")?.map((entry) => entry.key)).toEqual(["region_te"])
  })

  it("a constructor column behaves as an ordinary column", () => {
    expect(nativeTermOf({}, "constructor")).toBeNull()
    expect(nativeTermOf({}, "toString")).toBeNull()
    expect(addTerm({}, "constructor", "Float64", context.upstreamNames)).toEqual({ constructor: { type: "linear" } })
    expect(modelMembership({}, [], context).inModel).toEqual(new Set())
    const terms: Terms = { constructor: { type: "linear" } }
    expect(termsByColumn(terms, context).byColumn.get("constructor")).toEqual([{ key: "constructor", spec: { type: "linear" } }])
    expect(values(nativeTypeOptions({}, "constructor", "Float64"))).toEqual(["linear", "bs", "ns", "ms"])
    expect(typeLabel("constructor")).toBe("constructor")
    const interaction: InteractionSpec = { factors: ["constructor", "income"], specs: { income: { type: "bs", df: 4 } }, include_main: true }
    expect(slotOverride(interaction, "constructor")).toBeUndefined()
    expect(resolvedSlotSpec({}, "constructor", "Float64", slotOverride(interaction, "constructor"))).toEqual({ type: "linear" })
    expect(simulateInteractionDesign({}, [interaction], dtypeOf).materialised.get("constructor")).toEqual({ cards: [0], spec: { type: "linear" } })
    expect(values(interactionEncodingOptions([interaction], 0, {}, dtypeOf))).toEqual(["product"])
  })

  it("membership counts terms, expression identifiers, and filled interaction factors", () => {
    const terms: Terms = { age: { type: "linear" }, inc2: { type: "expression", expr: "income ** 2" } }
    const interactions: InteractionSpec[] = [
      { factors: ["region", "flag"], include_main: true },
      { factors: ["", ""], include_main: true },
    ]
    expect(modelMembership(terms, interactions, context)).toEqual({
      inModel: new Set(["age", "income", "region", "flag"]),
      interactionOnly: new Set(["region", "flag"]),
    })
  })
})

describe("fit menus by dtype class", () => {
  it("integer columns offer categorical and encoding fits", () => {
    expect(values(nativeTypeOptions({}, "age", "Int64")))
      .toEqual(["linear", "categorical", "bs", "ns", "ms", "target_encoding", "frequency_encoding"])
    expect(values(nativeTypeOptions({}, "income", "Float64"))).toEqual(["linear", "bs", "ns", "ms"])
    expect(values(nativeTypeOptions({}, "flag", "Boolean"))).toEqual(["categorical", "target_encoding", "frequency_encoding"])
    expect(values(nativeTypeOptions({}, "region", "String"))).toEqual(["categorical", "target_encoding", "frequency_encoding"])
    const terms: Terms = { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } }
    expect(values(additionalTypeOptions(terms, "age_sq", "age", "Int64"))).toEqual(["expression", "target_encoding", "frequency_encoding"])
  })

  it("an existing encoding is not offered twice for one column", () => {
    const terms: Terms = { region: { type: "categorical" }, region_te: { type: "target_encoding", variable: "region" } }
    expect(values(nativeTypeOptions(terms, "region", "String"))).toEqual(["categorical", "frequency_encoding"])
    expect(values(additionalTypeOptions(terms, "region_te", "region", "String"))).toEqual(["target_encoding", "frequency_encoding"])
  })

  it("non-identifier columns offer no expression", () => {
    const terms: Terms = { "annual mileage": { type: "linear" } }
    expect(addTermAvailability({}, "annual mileage", "Float64")).toEqual({ ok: true })
    expect(addTermAvailability(terms, "annual mileage", "Float64")).toEqual({
      ok: false,
      reason: "annual mileage cannot appear in an expression: rename it upstream to letters, digits, and underscores.",
    })
    expect(addTerm(terms, "annual mileage", "Float64", context.upstreamNames)).toBe(terms)
    expect(switchAdditionalType({ x: { type: "target_encoding", variable: "annual mileage" } }, "x", "annual mileage", "Float64", "expression"))
      .toEqual({ ok: false, reason: "Expressions need a numeric column named with letters, digits, and underscores." })
  })

  it("explains when a categorical column has every additional fit", () => {
    const terms: Terms = {
      region: { type: "categorical" },
      region_te: { type: "target_encoding", variable: "region" },
      region_fe: { type: "frequency_encoding", variable: "region" },
    }
    expect(addTermAvailability(terms, "region", "String")).toEqual({ ok: false, reason: "region already has every additional fit it supports." })
  })
})

describe("term transitions", () => {
  it("Add term writes the dtype default, then an expression or each encoding once", () => {
    expect(addTerm({}, "flag", "Boolean", context.upstreamNames)).toEqual({ flag: { type: "categorical" } })
    expect(addTerm({}, "age", "Int64", context.upstreamNames)).toEqual({ age: { type: "linear" } })
    const withExpression = addTerm({ age: { type: "linear" } }, "age", "Int64", context.upstreamNames)
    expect(withExpression).toEqual({ age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } })
    expect(Object.keys(addTerm(withExpression, "age", "Int64", context.upstreamNames))).toEqual(["age", "age_sq", "age_sq2"])
    expect(Object.keys(addTerm({ age: { type: "linear" } }, "age", "Int64", new Set(["age_sq"])))).toEqual(["age", "age_sq2"])
    const target = addTerm({ region: { type: "categorical" } }, "region", "String", context.upstreamNames)
    const frequency = addTerm(target, "region", "String", context.upstreamNames)
    expect(frequency).toEqual({
      region: { type: "categorical" },
      region_te: { type: "target_encoding", variable: "region" },
      region_fe: { type: "frequency_encoding", variable: "region" },
    })
    expect(addTerm(frequency, "region", "String", context.upstreamNames)).toBe(frequency)
  })

  it("type switches keep only the new type's keys and a monotone spline defaults to increasing", () => {
    const terms: Terms = { age: { type: "bs", df: 5, degree: 2, monotonicity: "increasing", knots: [1, 2] } }
    expect(switchNativeType(terms, "age", "ns")).toEqual({ age: { type: "ns", df: 5, knots: [1, 2] } })
    expect(switchNativeType(terms, "age", "linear")).toEqual({ age: { type: "linear", monotonicity: "increasing" } })
    expect(switchNativeType({ age: { type: "linear" } }, "age", "ms")).toEqual({ age: { type: "ms", monotonicity: "increasing" } })
    expect(switchNativeType(terms, "age", "categorical")).toEqual({ age: { type: "categorical" } })
    expect(switchAdditionalType(
      { region_te: { type: "target_encoding", variable: "region", prior_weight: 10, n_permutations: 3 } },
      "region_te", "region", "String", "frequency_encoding",
    )).toEqual({ ok: true, terms: { region_te: { type: "frequency_encoding", variable: "region" } } })
    expect(switchAdditionalType({ inc: { type: "expression", expr: "income ** 2" } }, "inc", "income", "Float64", "target_encoding"))
      .toEqual({ ok: false, reason: "Encodings are available for integer, boolean, and categorical features." })
    expect(switchAdditionalType(
      { region_te: { type: "target_encoding", variable: "region" }, region_fe: { type: "frequency_encoding", variable: "region" } },
      "region_te", "region", "String", "frequency_encoding",
    )).toEqual({ ok: false, reason: "This feature already has a frequency encoding." })
  })

  it("df, k, and knots replace each other and clearing a field deletes only that field", () => {
    const terms: Terms = { age: { type: "bs", df: 6, boundary_knots: [0, 90], degree: 2 } }
    expect(setTermField(terms, "age", "k", 8)).toEqual({ age: { type: "bs", k: 8, boundary_knots: [0, 90], degree: 2 } })
    expect(setTermField(terms, "age", "knots", [30, 60])).toEqual({ age: { type: "bs", knots: [30, 60], boundary_knots: [0, 90], degree: 2 } })
    expect(setTermField({ age: { type: "bs", k: 8 } }, "age", "df", 5)).toEqual({ age: { type: "bs", df: 5 } })
    const cleared = setTermField(terms, "age", "degree", undefined)
    expect(cleared).toEqual({ age: { type: "bs", df: 6, boundary_knots: [0, 90] } })
    expect(splineMode(cleared.age)).toBe("fixed")
  })

  it("reference level and levels are exclusive", () => {
    expect(setTermField({ region: { type: "categorical", levels: ["a"] } }, "region", "reference", "b"))
      .toEqual({ region: { type: "categorical", reference: "b" } })
    expect(setTermField({ region: { type: "categorical", reference: "b" } }, "region", "levels", ["a", "c"]))
      .toEqual({ region: { type: "categorical", levels: ["a", "c"] } })
    expect(setTermField({ region: { type: "categorical", reference: "b" } }, "region", "reference", undefined))
      .toEqual({ region: { type: "categorical" } })
  })

  it("spline mode transitions write df as the larger of 5 and degree + 1 and remove k and knots", () => {
    expect(setSplineMode({ age: { type: "bs", k: 12, degree: 5 } }, "age", "fixed")).toEqual({ age: { type: "bs", degree: 5, df: 6 } })
    expect(setSplineMode({ age: { type: "ns", knots: [1, 2] } }, "age", "fixed")).toEqual({ age: { type: "ns", df: 5 } })
    expect(setSplineMode({ age: { type: "bs", df: 6, knots: [1], k: 9, boundary_knots: [0, 3] } }, "age", "auto"))
      .toEqual({ age: { type: "bs", k: 9, boundary_knots: [0, 3] } })
    expect(setSplineMode({ bad: null } as unknown as Terms, "bad", "auto")).toEqual({ bad: null })
  })

  it("renames a term unless the name is empty, taken, or an upstream column", () => {
    const terms: Terms = { age: { type: "linear" }, age_sq: { type: "expression", expr: "age ** 2" } }
    expect(renameTerm(terms, "age_sq", " age_squared ", context.upstreamNames)).toEqual({
      ok: true,
      terms: { age: { type: "linear" }, age_squared: { type: "expression", expr: "age ** 2" } },
    })
    expect(renameTerm(terms, "age_sq", "", context.upstreamNames)).toEqual({ ok: false, reason: "Give the term a name." })
    expect(renameTerm(terms, "age_sq", "age", context.upstreamNames)).toEqual({ ok: false, reason: "age is already a term." })
    expect(renameTerm(terms, "age_sq", "income", context.upstreamNames)).toEqual({ ok: false, reason: "income is a column; term names must not be columns." })
  })

  it("sets an expression only inside the grammar over numeric eligible columns", () => {
    const terms: Terms = { age_sq: { type: "expression", expr: "age ** 2", monotonicity: "increasing" } }
    expect(setExpression(terms, "age_sq", " age * income ", context)).toEqual({
      ok: true,
      terms: { age_sq: { type: "expression", expr: "age * income", monotonicity: "increasing" } },
    })
    expect(setExpression(terms, "age_sq", "log(age)", context)).toEqual({ ok: false, reason: EXPRESSION_GRAMMAR })
    expect(setExpression(terms, "age_sq", "height ** 2", context)).toEqual({ ok: false, reason: "height is not in the upstream data." })
    expect(setExpression(terms, "age_sq", "region * 2", context)).toEqual({ ok: false, reason: "region is a categorical column; expressions need numbers." })
    expect(setExpression(terms, "age_sq", "y * 2", context)).toEqual({ ok: false, reason: "y is the target column." })
    expect(setExpression({}, "income", "income ** 2", context)).toEqual({ ok: false, reason: "An expression cannot be named after a column it reads." })
  })

  it("repairs a malformed entry with a valid native fit and removes only the named term", () => {
    expect(repairMalformedTerm({ age: null } as unknown as Terms, "age", "ms")).toEqual({ age: { type: "ms", monotonicity: "increasing" } })
    expect(removeTerm({ age: { type: "linear" }, region: { type: "categorical" } }, "age")).toEqual({ region: { type: "categorical" } })
  })

  it("Fit all with defaults adds only missing terms for eligible columns", () => {
    const terms: Terms = { age: { type: "bs", df: 4 }, age_sq: { type: "expression", expr: "age ** 2" } }
    expect(fitAllWithDefaults(terms, context)).toEqual({
      age: { type: "bs", df: 4 },
      age_sq: { type: "expression", expr: "age ** 2" },
      income: { type: "linear" },
      region: { type: "categorical" },
      flag: { type: "categorical" },
      "annual mileage": { type: "linear" },
      constructor: { type: "linear" },
    })
  })
})

describe("parameter contract", () => {
  it.each<[TermSpec, string[]]>([
    [{ type: "bs", df: 3 }, ["df must be an integer from 4 to 20."]],
    [{ type: "bs", df: 2, degree: 1 }, []],
    [{ type: "ns", df: 1 }, ["df must be an integer from 2 to 20."]],
    [{ type: "ms", k: 21 }, ["k must be an integer from 2 to 20."]],
    [{ type: "bs", df: 5, k: 8 }, ["Set only one of df, k, or knots (found df, k)."]],
    [{ type: "bs", degree: 6 }, ["Degree must be an integer from 1 to 5."]],
    [{ type: "bs", knots: [2, 1] }, ["Knots must be 1 to 20 finite, strictly increasing numbers."]],
    [{ type: "bs", knots: [1, 5], boundary_knots: [2, 9] }, ["Boundary knots must enclose every knot."]],
    [{ type: "ns", boundary_knots: [3, 3] }, ["Boundary knots must be exactly two finite, increasing numbers."]],
    [{ type: "linear", monotonicity: "flat" }, ["Monotonicity must be increasing or decreasing."]],
    [{ type: "target_encoding", prior_weight: -1, n_permutations: 101 }, ["Prior weight must be Auto or a non-negative number.", "Permutations must be an integer from 1 to 100."]],
    [{ type: "categorical", levels: ["a"], reference: "b" }, ["Set levels or a reference level, not both."]],
  ])("reports the backend's refusals for %j", (spec, issues) => {
    expect(termSpecIssues(spec)).toEqual(issues)
  })

  it("names penalised smooth splines and monotone constraints", () => {
    const terms: Terms = {
      age: { type: "bs" },
      income: { type: "ns", k: 6 },
      fixed: { type: "bs", df: 5 },
      knotted: { type: "ms", knots: [1] },
    }
    const interactions: InteractionSpec[] = [
      { factors: ["age", "region"], specs: { age: { type: "ns" } }, include_main: true },
      { factors: ["income", "region"], specs: { income: { type: "bs", df: 4 } }, include_main: true },
    ]
    expect(penalisedSmoothTerms(terms, interactions)).toEqual(["age", "income", "Interaction 1 age"])
    expect(monotoneConstraintTerms({
      a: { type: "linear", monotonicity: "increasing" },
      b: { type: "expression", expr: "a ** 2", monotonicity: "decreasing" },
      c: { type: "bs", df: 5, monotonicity: "increasing" },
      d: { type: "ms" },
      e: { type: "linear" },
    })).toEqual(["a", "b", "c"])
  })
})

describe("interaction resolution", () => {
  it("resolves a slot as override, single inheritable main effect, or dtype default", () => {
    expect(resolveSlot({ income: { type: "bs", df: 4 } }, "income", "Float64", { type: "linear" }))
      .toEqual({ kind: "override", spec: { type: "linear" } })
    expect(resolveSlot({ income: { type: "bs", df: 4, degree: 2 } }, "income", "Float64", undefined))
      .toEqual({ kind: "main", spec: { type: "bs", df: 4, degree: 2 }, key: "income" })
    expect(resolveSlot({ region_te: { type: "target_encoding", variable: "region", n_permutations: 6 } }, "region", "String", undefined))
      .toEqual({ kind: "main", spec: { type: "target_encoding", n_permutations: 6 }, key: "region_te" })
    expect(resolveSlot({}, "flag", "Boolean", undefined)).toEqual({ kind: "default", spec: { type: "categorical" } })
    expect(resolveSlot({}, "age", "Int64", undefined)).toEqual({ kind: "default", spec: { type: "linear" } })
  })

  it.each<[Terms, string, string]>([
    [{ income: { type: "ms" } }, "income", "Monotone splines cannot be used inside interactions"],
    [{ income: { type: "linear", monotonicity: "increasing" } }, "income", "Monotonicity is not applied inside interactions"],
    [{ income: { type: "bs", df: 5, monotonicity: "decreasing" } }, "income", "Monotonicity is not applied inside interactions"],
    [{ region: { type: "categorical", levels: ["a"] } }, "region", "Level restrictions and reference levels are not applied inside interactions"],
    [{ region: { type: "categorical", reference: "a" } }, "region", "Level restrictions and reference levels are not applied inside interactions"],
    [{ region: { type: "frequency_encoding" } }, "region", "Frequency encoding cannot be used inside a product interaction"],
    [{ region: { type: "categorical" }, region_te: { type: "target_encoding", variable: "region" } }, "region", "This feature has several main-effect terms; choose a fit for the interaction"],
  ])("inherited levels or monotonicity require an explicit slot fit (%j)", (terms, column, reason) => {
    expect(resolveSlot(terms, column, dtypeOf(column), undefined)).toEqual({ kind: "blocked", reason })
  })

  it("slot menus match the resolution rules", () => {
    const linear: TermSpec = { type: "linear" }
    const encoded: TermSpec = { type: "target_encoding" }
    expect(values(slotFitOptions({}, "income", "Float64", [linear]))).toEqual(["linear", "bs", "ns"])
    expect(values(slotFitOptions({}, "age", "Int64", [linear]))).toEqual(["linear", "categorical", "bs", "ns", "target_encoding"])
    expect(slotFitOptions({ region: { type: "categorical" } }, "region", "String", [linear]))
      .toEqual([{ value: "main", label: "As main (Categorical)" }, { value: "categorical", label: "Categorical" }])
    expect(values(slotFitOptions({ income: { type: "linear" } }, "income", "Float64", [linear]))).toEqual(["main", "linear", "bs", "ns"])
    // Categorical never re-types a numeric main effect; target encoding may sit over one.
    expect(values(slotFitOptions({ age: { type: "linear" } }, "age", "Int64", [linear]))).toEqual(["main", "linear", "bs", "ns", "target_encoding"])
    expect(values(slotFitOptions({ income: { type: "ms" } }, "income", "Float64", [linear]))).toEqual(["linear", "bs", "ns"])
    expect(values(slotFitOptions({ region_te: { type: "target_encoding", variable: "region" } }, "region", "String", [linear])))
      .toEqual(["main", "categorical", "target_encoding"])
    // Product target encoding: one encoded factor with Linear partners.
    expect(values(slotFitOptions({}, "income", "Float64", [encoded]))).toEqual(["linear"])
    expect(slotFitOptions({}, "income", "Float64", [encoded, encoded])).toEqual([])
    expect(slotFitOptions({}, "income", "Float64", [encoded, { type: "categorical" }])).toEqual([])
    expect(slotFitOptions({ region: { type: "categorical" } }, "region", "String", [encoded])).toEqual([])
    expect(values(slotFitOptions({}, "region", "String", [{ type: "bs" }]))).toEqual(["categorical"])
  })

  it("offers interaction modes by dtype class and factor-set usage", () => {
    const modes = (interactions: InteractionSpec[], index = 0, terms: Terms = {}) =>
      values(interactionEncodingOptions(interactions, index, terms, dtypeOf))
    expect(modes([{ factors: ["", ""], include_main: true }])).toEqual(["product", "target_encoding", "frequency_encoding"])
    expect(modes([{ factors: ["age", "region"], include_main: true }])).toEqual(["product", "target_encoding", "frequency_encoding"])
    expect(modes([{ factors: ["income", "region"], include_main: true }])).toEqual(["product"])
    expect(modes([{ factors: ["income", "region"], encoding: "target_encoding", include_main: true }])).toEqual(["product", "target_encoding"])
    const used: InteractionSpec[] = [
      { factors: ["flag", "region"], include_main: false },
      { factors: ["region", "flag"], encoding: "target_encoding", include_main: false },
      { factors: ["region", "flag"], encoding: "frequency_encoding", include_main: false },
    ]
    expect(modes(used, 0)).toEqual(["product"])
    expect(modes(used, 1)).toEqual(["target_encoding"])
    expect(modes(used, 2)).toEqual(["frequency_encoding"])
  })

  it("marks duplicates per encoding mode and factor set in any order", () => {
    expect(duplicateInteractionIndexes([
      { factors: ["age", "region"], include_main: true },
      { factors: ["region", "age"], include_main: true },
      { factors: ["region", "age"], encoding: "target_encoding", include_main: true },
      { factors: ["age", ""], include_main: true },
    ])).toEqual(new Set([1]))
  })

  it("materialises one main effect per column that its include-main cards agree on", () => {
    const design = simulateInteractionDesign({}, [
      { factors: ["income", "region"], include_main: true },
      { factors: ["income", "flag"], include_main: true },
    ], dtypeOf)
    expect(design.materialised).toEqual(new Map([
      ["income", { cards: [0, 1], spec: { type: "linear" } }],
      ["region", { cards: [0], spec: { type: "categorical" } }],
      ["flag", { cards: [1], spec: { type: "categorical" } }],
    ]))
    expect(design.cardIssues).toEqual(new Map())
  })

  it("disagreeing materialisations are shown on every card involved, in either order", () => {
    const spline: InteractionSpec = { factors: ["income", "region"], specs: { income: { type: "bs", df: 4 } }, include_main: true }
    const plain: InteractionSpec = { factors: ["income", "flag"], include_main: true }
    for (const cards of [[spline, plain], [plain, spline]]) {
      const design = simulateInteractionDesign({}, cards, dtypeOf)
      expect(design.materialised.has("income")).toBe(false)
      expect([...design.cardIssues.keys()]).toEqual([0, 1])
      expect(design.cardIssues.get(0)?.[0]).toMatch(/^Interactions 1, 2 add different main effects for income \(.+\); add a main term for income or give them the same fit$/)
    }
  })

  it("registers product target encodings and refuses encodings or categorical fits over incompatible mains", () => {
    const registered = simulateInteractionDesign({}, [
      { factors: ["region", "income"], specs: { region: { type: "target_encoding" } }, include_main: false },
    ], dtypeOf)
    expect(registered.registeredEncodings).toEqual(new Map([["region", [0]]]))
    expect(simulateInteractionDesign({ region: { type: "categorical" } }, [
      { factors: ["region", "income"], specs: { region: { type: "target_encoding" } }, include_main: true },
    ], dtypeOf).cardIssues.get(0)).toEqual(["Target encoding over the categorical main effect of region is collinear with its indicators"])
    const materialise: InteractionSpec = { factors: ["age", "region"], include_main: true }
    const retype: InteractionSpec = { factors: ["age", "flag"], specs: { age: { type: "categorical" } }, include_main: false }
    for (const [cards, offender] of [[[materialise, retype], 1], [[retype, materialise], 0]] as const) {
      expect(simulateInteractionDesign({}, [...cards], dtypeOf).cardIssues)
        .toEqual(new Map([[offender, ["A categorical fit for age would re-type its Linear main effect"]]]))
    }
  })

  it("tags feature rows by how interactions put them in the model", () => {
    const interactions: InteractionSpec[] = [
      { factors: ["income", "region"], include_main: true },
      { factors: ["income", "flag"], include_main: true },
      { factors: ["age", "region"], specs: { age: { type: "target_encoding" } }, include_main: false },
    ]
    const terms: Terms = { region: { type: "categorical" }, x2: { type: "expression", expr: "annual_x ** 2" } }
    const design = simulateInteractionDesign(terms, interactions, dtypeOf)
    const membership = modelMembership(terms, interactions, context)
    expect(featureTag("income", false, membership, design)).toBe("Main effect from Interactions 1, 2 (Linear)")
    expect(featureTag("flag", false, membership, design)).toBe("Main effect from Interaction 2 (Categorical)")
    expect(featureTag("age", false, membership, design)).toBe("Target encoding from Interaction 3")
    expect(featureTag("region", true, membership, design)).toBeNull()
    expect(featureTag("constructor", false, membership, design)).toBe("Not in model")
    const noMain = simulateInteractionDesign({}, [{ factors: ["income", "flag"], include_main: false }], dtypeOf)
    const noMainMembership = modelMembership({}, [{ factors: ["income", "flag"], include_main: false }], context)
    expect(featureTag("income", false, noMainMembership, noMain)).toBe("Interaction only")
    const expression: Terms = { inc2: { type: "expression", expr: "age * income" } }
    expect(featureTag("income", false, modelMembership(expression, [], context), simulateInteractionDesign(expression, [], dtypeOf)))
      .toBe("In an expression")
  })

  it("explains malformed interaction entries", () => {
    expect(interactionEntryIssue({ factors: ["age", ""], include_main: true })).toBeNull()
    expect(interactionEntryIssue(null)).toBe("This interaction is not an object.")
    expect(interactionEntryIssue({ factors: ["age"], weight: 2 })).toBe("This interaction has unknown settings: weight.")
    expect(interactionEntryIssue({ factors: "age" })).toBe("This interaction has no list of features.")
    expect(interactionEntryIssue({ factors: [], specs: [] })).toBe("This interaction's fits are not an object.")
    expect(interactionEntryIssue({ factors: ["age", "income"], specs: { age: null } }))
      .toBe("The fit for age in this interaction has no fit type.")
    expect(interactionEntryIssue({ factors: ["age", "income"], specs: { age: { df: 4 } } }))
      .toBe("The fit for age in this interaction has no fit type.")
    expect(interactionEntryIssue({ factors: [], include_main: "yes" })).toBe("Include main effects must be true or false.")
    expect(interactionEntryIssue({ factors: [], encoding: "hash" })).toBe("Unsupported interaction encoding hash.")
  })
})

describe("interaction transitions", () => {
  it("writes factors and overrides exactly", () => {
    const base = addInteraction([])
    expect(base).toEqual([{ factors: ["", ""], include_main: true }])
    const picked = pickSlotColumn(base, 0, 0, "age")
    expect(picked).toEqual([{ factors: ["age", ""], include_main: true }])
    const overridden = setSlotOverride(picked, 0, "age", { type: "bs", df: 4 })
    expect(overridden).toEqual([{ factors: ["age", ""], specs: { age: { type: "bs", df: 4 } }, include_main: true }])
    expect(setSlotOverride(overridden, 0, "age", null)).toEqual(picked)
    expect(pickSlotColumn(overridden, 0, 0, "income")).toEqual([{ factors: ["income", ""], include_main: true }])
    expect(addSlot(picked, 0)).toEqual([{ factors: ["age", "", ""], include_main: true }])
    expect(removeSlot(addSlot(overridden, 0), 0, 0)).toEqual([{ factors: ["", ""], include_main: true }])
    expect(removeSlot(picked, 0, 0)).toEqual(picked)
    expect(setIncludeMain(picked, 0, false)).toEqual([{ factors: ["age", ""], include_main: false }])
    expect(removeInteraction([...picked, ...base], 0)).toEqual(base)
  })

  it("a mode change keeps only the factors and Include main effects", () => {
    const initial: InteractionSpec[] = [{
      factors: ["age", "region"],
      specs: { age: { type: "bs" } },
      prior_weight: 2,
      n_permutations: 5,
      include_main: true,
    }]
    const encoded = setInteractionEncoding(initial, 0, "target_encoding")
    expect(encoded).toEqual([{ factors: ["age", "region"], include_main: true, encoding: "target_encoding" }])
    expect(setInteractionField(encoded, 0, "prior_weight", 0)).toEqual([{ ...encoded[0], prior_weight: 0 }])
    expect(setInteractionField(encoded, 0, "prior_weight", undefined)).toEqual(encoded)
    expect(setInteractionEncoding(encoded, 0, "product")).toEqual([{ factors: ["age", "region"], include_main: true }])
    expect(setInteractionEncoding(encoded, 0, "target_encoding")).toEqual(encoded)
  })
})
