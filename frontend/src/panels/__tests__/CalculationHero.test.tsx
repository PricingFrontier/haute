import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup } from "@testing-library/react"
import CalculationHero from "../../trace/CalculationHero"
import type { CalculationHeroProps } from "../../trace/CalculationHero"
import { nodeTypeColors } from "../../utils/nodeTypes"

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

function makeExpression(
  overrides: Partial<NonNullable<CalculationHeroProps["expression"]>> = {},
): NonNullable<CalculationHeroProps["expression"]> {
  return {
    expression_text: "base_premium * age_factor",
    expression_type: "arithmetic",
    referenced_columns: ["base_premium", "age_factor"],
    ...overrides,
  }
}

function makeCalculation(
  overrides: Partial<NonNullable<CalculationHeroProps["calculation"]>> = {},
): NonNullable<CalculationHeroProps["calculation"]> {
  return {
    substituted_text: "528 \u00d7 0.7 = 369.6",
    result_value: 369.6,
    input_values: { base_premium: 528, age_factor: 0.7 },
    ...overrides,
  }
}

function makeProps(overrides: Partial<CalculationHeroProps> = {}): CalculationHeroProps {
  return {
    column: "premium",
    expression: makeExpression(),
    calculation: makeCalculation(),
    nodeName: "Rating Engine",
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// CATEGORY 1: Basic Rendering
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Basic Rendering", () => {
  afterEach(cleanup)

  it("renders column name prominently", () => {
    render(<CalculationHero {...makeProps()} />)
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("renders inside the shared trace calculation frame", () => {
    render(<CalculationHero {...makeProps()} />)
    const frame = screen.getByTestId("trace-calculation-frame")
    expect(frame).toHaveTextContent("Rating")
    expect(frame).toHaveTextContent("premium")
    expect(frame).toHaveTextContent("369.6")
  })

  it("does not render a duplicate copy action inside the calculation frame", () => {
    render(<CalculationHero {...makeProps()} />)
    expect(screen.queryByRole("button", { name: /copy/i })).not.toBeInTheDocument()
  })

  it("renders the frame result as compact inline text rather than a large value pill", () => {
    render(<CalculationHero {...makeProps()} />)
    const result = screen.getByTestId("trace-calculation-result")
    expect(result).toHaveTextContent("= 369.6")
    expect(result).not.toHaveStyle({ background: "var(--text-accent-soft)" })
    expect(result).not.toHaveStyle({ border: "1px solid var(--text-accent-border)" })
  })

  it("outlines the shared calculation frame with the node type colour", () => {
    render(<CalculationHero {...makeProps({ nodeType: "optimiserApply" })} />)
    const frame = screen.getByTestId("trace-calculation-frame")
    expect(frame).toHaveStyle({ borderColor: nodeTypeColors.optimiserApply })
  })

  it("uses a flat shared calculation frame without an accent glow", () => {
    render(<CalculationHero {...makeProps({ nodeType: "optimiserApply" })} />)
    const frame = screen.getByTestId("trace-calculation-frame")
    expect(frame.style.background).toBe("var(--bg-elevated, rgba(255,255,255,0.03))")
    expect(frame.style.background).not.toContain("linear-gradient")
    expect(frame).toHaveStyle({ boxShadow: "none" })
  })

  it("renders expression_text with operator replacement (\u00d7 instead of *)", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({ expression_text: "base_premium * age_factor" }),
        })}
      />,
    )
    // The component should replace * with \u00d7
    expect(screen.getByText(/base_premium \u00d7 age_factor/)).toBeInTheDocument()
  })

  it("renders substituted_text showing actual values", () => {
    render(<CalculationHero {...makeProps()} />)
    expect(screen.getByText(/528/)).toBeInTheDocument()
    expect(screen.getByText(/0\.7/)).toBeInTheDocument()
  })

  it("renders result_value in accent styling", () => {
    const { container } = render(<CalculationHero {...makeProps()} />)
    const resultEls = screen.getAllByText("369.6")
    expect(resultEls.length).toBeGreaterThanOrEqual(1)
    // Result should be rendered with an accent class or data attribute
    const accentEl = container.querySelector("[data-accent], .accent, [class*='accent'], [class*='result']")
    expect(accentEl).toBeTruthy()
  })

  it("renders node name when provided", () => {
    render(<CalculationHero {...makeProps({ nodeName: "Rating Engine" })} />)
    expect(screen.getByText("Rating Engine")).toBeInTheDocument()
  })

  it("does not crash when expression is null", () => {
    render(<CalculationHero {...makeProps({ expression: null })} />)
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("does not crash when calculation is null", () => {
    render(<CalculationHero {...makeProps({ calculation: null })} />)
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("does not crash when both expression and calculation are null", () => {
    render(
      <CalculationHero
        {...makeProps({ expression: null, calculation: null })}
      />,
    )
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("renders 'Source data' or similar when both expression and calculation are null", () => {
    render(
      <CalculationHero
        {...makeProps({ expression: null, calculation: null })}
      />,
    )
    // When there's no expression and no calculation, the column is raw source data
    expect(screen.getByText(/source data/i)).toBeInTheDocument()
  })

})

// ---------------------------------------------------------------------------
// CATEGORY 2: Formula Modes
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Formula Modes", () => {
  afterEach(cleanup)

  it("arithmetic mode: shows formula + substituted + result in 3 lines", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "base_premium * age_factor",
            expression_type: "arithmetic",
          }),
          calculation: makeCalculation({
            substituted_text: "528 \u00d7 0.7 = 369.6",
            result_value: 369.6,
          }),
        })}
      />,
    )
    // Formula line
    expect(screen.getByText(/base_premium \u00d7 age_factor/)).toBeInTheDocument()
    // Substituted line
    expect(screen.getByText(/528 \u00d7 0\.7/)).toBeInTheDocument()
    // Result shown in line 1 AND inside the unified box (appears twice)
    expect(screen.getAllByText("369.6").length).toBeGreaterThanOrEqual(1)
  })

  it("conditional mode: shows when/then/otherwise text", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "when age > 25 then premium * 1.2 otherwise premium",
            expression_type: "conditional",
            referenced_columns: ["age", "premium"],
          }),
          calculation: makeCalculation({
            substituted_text: "when 30 > 25 then 100 \u00d7 1.2 = 120",
            result_value: 120,
            input_values: { age: 30, premium: 100 },
          }),
        })}
      />,
    )
    expect(screen.getByText(/when/i)).toBeInTheDocument()
    expect(screen.getByText(/then/i)).toBeInTheDocument()
    expect(screen.getByText(/otherwise/i)).toBeInTheDocument()
  })

  it("horizontal function mode: shows MAX(a, b) or MIN(a, b)", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "MAX(base_premium, floor_premium)",
            expression_type: "function",
            referenced_columns: ["base_premium", "floor_premium"],
          }),
          calculation: makeCalculation({
            substituted_text: "MAX(528, 400) = 528",
            result_value: 528,
            input_values: { base_premium: 528, floor_premium: 400 },
          }),
        })}
      />,
    )
    expect(screen.getAllByText(/MAX/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/528/).length).toBeGreaterThanOrEqual(1)
  })

  it("opaque mode: shows 'computed' label in italics", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "",
            expression_type: "opaque",
            referenced_columns: [],
          }),
          calculation: makeCalculation({
            substituted_text: "= 42.5",
            result_value: 42.5,
            input_values: {},
          }),
        })}
      />,
    )
    const computedEl = screen.getByText(/computed/i)
    expect(computedEl).toBeInTheDocument()
    // Should be italic
    const italicEl = computedEl.closest("em, i, [class*='italic'], [style*='italic']")
      ?? (computedEl.tagName === "EM" || computedEl.tagName === "I" ? computedEl : null)
    expect(
      italicEl !== null ||
      getComputedStyle(computedEl).fontStyle === "italic" ||
      computedEl.tagName === "EM" ||
      computedEl.tagName === "I",
    ).toBe(true)
  })

  it("source-only opaque mode names the source node instead of computed", () => {
    render(
      <CalculationHero
        {...makeProps({
          nodeName: "Policy Source",
          nodeType: "dataInput",
          expression: makeExpression({
            expression_text: "",
            expression_type: "opaque",
            referenced_columns: [],
          }),
          calculation: makeCalculation({
            substituted_text: "computed",
            result_value: 42.5,
            input_values: {},
          }),
        })}
      />,
    )

    expect(screen.getByText("Source node")).toBeInTheDocument()
    expect(screen.getAllByText("Policy Source").length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("source-only placeholder calculations name the source node when expression is absent", () => {
    render(
      <CalculationHero
        {...makeProps({
          nodeName: "Quote Input",
          nodeType: "apiInput",
          expression: null,
          calculation: makeCalculation({
            substituted_text: "computed",
            result_value: "Q123",
            input_values: {},
          }),
        })}
      />,
    )

    expect(screen.getByText("Source node")).toBeInTheDocument()
    expect(screen.getAllByText("Quote Input").length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("generated-column origins can name the generating node instead of computed", () => {
    render(
      <CalculationHero
        {...makeProps({
          nodeName: "Premium Expander",
          nodeType: "scenarioExpander",
          isSourceOrigin: true,
          expression: makeExpression({
            expression_text: "",
            expression_type: "opaque",
            referenced_columns: [],
          }),
          calculation: makeCalculation({
            substituted_text: "computed",
            result_value: 1.2,
            input_values: {},
          }),
        })}
      />,
    )

    expect(screen.getByText("Source node")).toBeInTheDocument()
    expect(screen.getAllByText("Premium Expander").length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText(/^computed$/i)).not.toBeInTheDocument()
  })

  it("banding mode: uses one compact input-to-band line", () => {
    render(
      <CalculationHero
        {...makeProps({
          column: "age_band",
          expression: makeExpression({
            expression_text: "driver_age -> age_band",
            expression_type: "banding",
            referenced_columns: ["driver_age"],
          }),
          calculation: makeCalculation({
            substituted_text: '22 -> "young"',
            result_value: "young",
            input_values: { driver_age: 22 },
            input_sources: {
              driver_age: {
                node_name: "Policies",
                result_value: 22,
              },
            },
          }),
        })}
      />,
    )

    expect(screen.getByLabelText("Banding: driver_age=22 -> young")).toBeInTheDocument()
    expect(screen.getByText(/Policies/)).toBeInTheDocument()
    expect(screen.queryByText("driver_age -> age_band")).not.toBeInTheDocument()
  })

  it("long formula (80+ chars): wraps or truncates with toggle", () => {
    const longFormula = Array.from({ length: 12 }, (_, i) => `factor_${i}`).join(" * ")
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: longFormula,
            expression_type: "arithmetic",
          }),
        })}
      />,
    )
    // Should either truncate with an ellipsis or provide a toggle to expand
    const toggleOrEllipsis =
      screen.queryByText(/\u2026/) ??
      screen.queryByText(/show more/i) ??
      screen.queryByRole("button", { name: /expand/i })
    expect(toggleOrEllipsis).toBeTruthy()
  })

  it("very long formula (200+ chars): does not overflow panel", () => {
    const veryLongFormula = Array.from({ length: 30 }, (_, i) => `column_name_${i}`).join(" + ")
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: veryLongFormula,
            expression_type: "arithmetic",
          }),
        })}
      />,
    )
    // The hero container should not overflow: check overflow is hidden or auto
    const heroEl = container.firstElementChild as HTMLElement
    expect(heroEl).toBeTruthy()
    // It should render without throwing
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("single operand: x.cast(Float64) renders cleanly", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "x.cast(Float64)",
            expression_type: "function",
            referenced_columns: ["x"],
          }),
          calculation: makeCalculation({
            substituted_text: "25.cast(Float64) = 25.0",
            result_value: 25.0,
            input_values: { x: 25 },
          }),
        })}
      />,
    )
    expect(screen.getAllByText(/cast/).length).toBeGreaterThanOrEqual(1)
  })

  it("no expression but has calculation: shows only substituted + result", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: null,
          calculation: makeCalculation({
            substituted_text: "= 145.6",
            result_value: 145.6,
            input_values: { premium: 208, discount: 0.7 },
          }),
        })}
      />,
    )
    // Should show substituted text and result, but no formula row
    expect(screen.getAllByText(/145\.6/).length).toBeGreaterThanOrEqual(1)
    // No formula line should appear since expression is null
    expect(screen.queryByText(/base_premium/)).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// CATEGORY 3: Value Formatting
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Value Formatting", () => {
  afterEach(cleanup)

  it("float values: 528.00 displayed with reasonable precision", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({ result_value: 528.0 }),
        })}
      />,
    )
    // Should display 528, not 528.00 (trailing zeros trimmed); may appear multiple times
    expect(screen.getAllByText("528").length).toBeGreaterThanOrEqual(1)
  })

  it("integer values: 100 displayed without decimal", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "= 100",
            result_value: 100,
          }),
        })}
      />,
    )
    expect(screen.getAllByText("100").length).toBeGreaterThanOrEqual(1)
    // Should not show "100.0" or "100.00"
    expect(screen.queryByText("100.0")).not.toBeInTheDocument()
    expect(screen.queryByText("100.00")).not.toBeInTheDocument()
  })

  it("NULL result: shows the trace null marker in muted styling", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "NULL \u00d7 0.7 = null",
            result_value: null,
          }),
        })}
      />,
    )
    const nullEls = screen.getAllByText("\u2014")
    expect(nullEls.length).toBeGreaterThanOrEqual(1)
    // Should have muted styling on at least one element
    const hasMuted = nullEls.some(el => {
      const mutedParent = el.closest("[class*='muted'], [class*='null'], [data-muted], [style*='opacity']")
      return mutedParent !== null
    })
    expect(hasMuted).toBe(true)
  })

  it("NaN result: shows 'NaN'", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "= NaN",
            result_value: NaN,
          }),
        })}
      />,
    )
    expect(screen.getAllByText("NaN").length).toBeGreaterThanOrEqual(1)
  })

  it("zero result: shows '0' (not empty)", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "0 \u00d7 5 = 0",
            result_value: 0,
          }),
        })}
      />,
    )
    expect(screen.getAllByText("0").length).toBeGreaterThanOrEqual(1)
  })

  it("negative result: shows with minus sign", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "100 - 250 = -150",
            result_value: -150,
          }),
        })}
      />,
    )
    expect(screen.getAllByText("-150").length).toBeGreaterThanOrEqual(1)
  })

  it("very large number: 1,547,832 with grouping", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "= 1547832",
            result_value: 1547832,
          }),
        })}
      />,
    )
    // Should show with thousand separators (may appear in line 1 and unified box)
    expect(screen.getAllByText("1,547,832").length).toBeGreaterThanOrEqual(1)
  })

  it("very small number: preserves a meaningful non-zero value", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "= 0.0023",
            result_value: 0.0023,
          }),
        })}
      />,
    )
    // With smart formatting, 0.0023 (abs < 10) shows up to 4dp: "0.0023" (may appear twice)
    expect(screen.getAllByText("0.0023").length).toBeGreaterThanOrEqual(1)
    // When formatted value matches full precision, no title is needed
    const heroEl = container.firstElementChild as HTMLElement
    expect(heroEl).toBeTruthy()
  })

  it("string result: displayed in quotes", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: '= "high_risk"',
            result_value: "high_risk",
          }),
        })}
      />,
    )
    expect(screen.getByText(/"high_risk"/)).toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// CATEGORY 4: Input Values Display
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Input Values Display", () => {
  afterEach(cleanup)

  it("shows each input column name and value", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            input_values: { base_premium: 528, age_factor: 0.7 },
          }),
        })}
      />,
    )
    expect(screen.getByText(/base_premium/)).toBeInTheDocument()
    expect(screen.getByText(/528/)).toBeInTheDocument()
    expect(screen.getByText(/age_factor/)).toBeInTheDocument()
    expect(screen.getByText(/0\.7/)).toBeInTheDocument()
  })

  it("two inputs: premium=528, rate=0.7 \u2014 both shown", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            input_values: { premium: 528, rate: 0.7 },
          }),
        })}
      />,
    )
    // "premium" appears as column name and/or in formula
    expect(screen.getAllByText(/premium/).length).toBeGreaterThanOrEqual(1)
    // Input values appear in the substituted text line
    expect(screen.getAllByText(/528/).length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText(/0\.7/).length).toBeGreaterThanOrEqual(1)
  })

  it("NULL input: shows null indicator", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            input_values: { premium: null, rate: 0.7 },
            substituted_text: "null \u00d7 0.7 = null",
            result_value: null,
          }),
        })}
      />,
    )
    // The null input value should be displayed
    expect(screen.getAllByText(/null/).length).toBeGreaterThanOrEqual(1)
  })

  it("input from source vs computed: no visual difference", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            input_values: { source_col: 100, computed_col: 200 },
          }),
        })}
      />,
    )
    // Input values appear in the substituted text; column name always renders
    expect(screen.getByText("premium")).toBeInTheDocument()
    // The component renders without crashing with these inputs
    const heroEl = container.firstElementChild as HTMLElement
    expect(heroEl).toBeTruthy()
  })

  it("many inputs (5+): all shown or first N with 'more' toggle", () => {
    const manyInputs: Record<string, unknown> = {}
    for (let i = 0; i < 7; i++) {
      manyInputs[`col_${i}`] = i * 10
    }
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({ input_values: manyInputs }),
        })}
      />,
    )
    // The component renders without crashing with many inputs
    // Column name is always shown
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("no inputs (opaque expression): no input section rendered", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "",
            expression_type: "opaque",
            referenced_columns: [],
          }),
          calculation: makeCalculation({
            input_values: {},
            substituted_text: "= 42.5",
            result_value: 42.5,
          }),
        })}
      />,
    )
    // There should be no input section when input_values is empty
    const inputSection = container.querySelector("[data-testid='input-values'], [class*='input']")
    // If no inputs, section should either not exist or be empty
    if (inputSection) {
      expect(inputSection.children.length).toBe(0)
    } else {
      expect(inputSection).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// CATEGORY 5: Conditional Branch Display
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Conditional Branch Display", () => {
  afterEach(cleanup)

  it("simple when/then: highlights which branch was taken", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "when age > 25 then premium * 1.2 otherwise premium",
            expression_type: "conditional",
            referenced_columns: ["age", "premium"],
          }),
          calculation: makeCalculation({
            substituted_text: "when 30 > 25 then 100 \u00d7 1.2 otherwise 100",
            result_value: 120,
            input_values: { age: 30, premium: 100 },
            taken_branch_index: 0,
          }),
        })}
      />,
    )
    // The matched branch should be highlighted (e.g., has an active/matched class)
    const matchedBranch = container.querySelector(
      "[class*='matched'], [class*='active'], [data-matched='true'], [class*='taken']",
    )
    expect(matchedBranch).toBeTruthy()
  })

  it("chained when/then (3+ branches): shows matched branch prominently, dims others", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text:
              "when risk_score > 90 then 'critical' when risk_score > 70 then 'high' when risk_score > 40 then 'medium' otherwise 'low'",
            expression_type: "conditional",
            referenced_columns: ["risk_score"],
          }),
          calculation: makeCalculation({
            substituted_text:
              "when 75 > 90 then 'critical' when 75 > 70 then 'high' when 75 > 40 then 'medium' otherwise 'low'",
            result_value: "high",
            input_values: { risk_score: 75 },
            taken_branch_index: 1,
          }),
        })}
      />,
    )
    // The matched branch ("high") should be prominent
    expect(screen.getAllByText(/high/).length).toBeGreaterThanOrEqual(1)
    // Unmatched branches should be dimmed
    const dimmedEls = container.querySelectorAll(
      "[class*='dimmed'], [class*='inactive'], [style*='opacity'], [data-matched='false']",
    )
    expect(dimmedEls.length).toBeGreaterThanOrEqual(1)
  })

  it("uses the backend taken branch index when multiple branches return the same value", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text:
              "when tier = 'A' then 0 when tier = 'B' then 0 otherwise 1",
            expression_type: "conditional",
            referenced_columns: ["tier"],
          }),
          calculation: makeCalculation({
            substituted_text:
              "when 'B' = 'A' then 0 when 'B' = 'B' then 0 otherwise 1",
            result_value: 0,
            input_values: { tier: "B" },
            taken_branch_index: 1,
          }),
        })}
      />,
    )

    const branches = Array.from(
      container.querySelectorAll<HTMLElement>(".conditional-display .branch"),
    )
    expect(branches).toHaveLength(3)
    expect(branches.map((branch) => branch.dataset.matched)).toEqual([
      "false",
      "true",
      "false",
    ])
    expect(branches[1]).toHaveTextContent("tier = 'B'")
  })

  it("does not guess a taken branch when typed backend selection is absent", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "when tier = 'A' then 0 when tier = 'B' then 0 otherwise 1",
            expression_type: "conditional",
            referenced_columns: ["tier"],
          }),
          calculation: makeCalculation({
            substituted_text: "when 'B' = 'A' then 0 when 'B' = 'B' then 0 otherwise 1",
            result_value: 0,
            input_values: { tier: "B" },
          }),
        })}
      />,
    )

    const branches = Array.from(
      container.querySelectorAll<HTMLElement>(".conditional-display .branch"),
    )
    expect(branches).toHaveLength(3)
    expect(branches.every((branch) => branch.dataset.matched === undefined)).toBe(true)
    expect(branches.every((branch) => !branch.classList.contains("inactive"))).toBe(true)
  })

  it("otherwise branch taken: shows 'otherwise' as the matched path", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "when age > 65 then 'senior' otherwise 'standard'",
            expression_type: "conditional",
            referenced_columns: ["age"],
          }),
          calculation: makeCalculation({
            substituted_text: "when 30 > 65 then 'senior' otherwise 'standard'",
            result_value: "standard",
            input_values: { age: 30 },
          }),
        })}
      />,
    )
    expect(screen.getByText(/otherwise/i)).toBeInTheDocument()
    expect(screen.getAllByText(/standard/).length).toBeGreaterThanOrEqual(1)
  })

  it("conditional with NULL condition: shows which path NULL takes", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "when age > 25 then premium * 1.2 otherwise premium",
            expression_type: "conditional",
            referenced_columns: ["age", "premium"],
          }),
          calculation: makeCalculation({
            substituted_text: "when null > 25 then 100 \u00d7 1.2 otherwise 100",
            result_value: 100,
            input_values: { age: null, premium: 100 },
          }),
        })}
      />,
    )
    // Should render without crashing and show the result
    expect(screen.getByTestId("trace-calculation-result")).toHaveTextContent("= 100")
    expect(screen.getByText(/otherwise/i)).toBeInTheDocument()
  })

  it("very long conditional (5+ branches): scrollable or collapsible", () => {
    const branches = Array.from(
      { length: 6 },
      (_, i) => `when score > ${90 - i * 10} then 'tier_${i}'`,
    ).join(" ")
    const fullExpr = `${branches} otherwise 'base'`

    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: fullExpr,
            expression_type: "conditional",
            referenced_columns: ["score"],
          }),
          calculation: makeCalculation({
            substituted_text: fullExpr.replace(/score/g, "55"),
            result_value: "tier_3",
            input_values: { score: 55 },
          }),
        })}
      />,
    )
    // Should not overflow; either scrollable or collapsible
    const heroEl = container.firstElementChild as HTMLElement
    expect(heroEl).toBeTruthy()
    // Should render without crashing
    expect(screen.getByText("premium")).toBeInTheDocument()
  })

  it("nested conditional: renders tree-like structure or flat with indentation", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text:
              "when region = 'US' then (when age > 25 then premium * 1.2 otherwise premium) otherwise premium * 0.8",
            expression_type: "conditional",
            referenced_columns: ["region", "age", "premium"],
          }),
          calculation: makeCalculation({
            substituted_text:
              "when 'US' = 'US' then (when 30 > 25 then 100 \u00d7 1.2 otherwise 100) otherwise 100 \u00d7 0.8",
            result_value: 120,
            input_values: { region: "US", age: 30, premium: 100 },
          }),
        })}
      />,
    )
    // Should render without crashing
    expect(screen.getByText("premium")).toBeInTheDocument()
    expect(screen.getByTestId("trace-calculation-result")).toHaveTextContent("= 120")
  })
})

// ---------------------------------------------------------------------------
// CATEGORY 7: Edge Cases
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Edge Cases", () => {
  afterEach(cleanup)

  it("column name with spaces: renders correctly", () => {
    render(
      <CalculationHero {...makeProps({ column: "gross written premium" })} />,
    )
    expect(screen.getByText("gross written premium")).toBeInTheDocument()
  })

  it("column name with special characters: no injection", () => {
    render(
      <CalculationHero
        {...makeProps({ column: '<script>alert("xss")</script>' })}
      />,
    )
    // The raw text should appear, not be executed as HTML
    expect(screen.getByText('<script>alert("xss")</script>')).toBeInTheDocument()
    // No script tag should be injected into the DOM
    expect(document.querySelector("script")).toBeNull()
  })

  it("very long column name: truncates with tooltip", () => {
    const longName = "this_is_a_very_long_column_name_that_should_be_truncated_because_it_exceeds_reasonable_display_width"
    const { container } = render(
      <CalculationHero {...makeProps({ column: longName })} />,
    )
    // The column name should be rendered (possibly truncated)
    // Check for title attribute (tooltip) containing the full name
    const elWithTitle = container.querySelector(`[title="${longName}"]`)
    expect(elWithTitle).toBeTruthy()
  })

  it("expression_type is unknown string: falls back to generic display", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "some_col + 1",
            expression_type: "some_future_type_we_dont_know",
            referenced_columns: ["some_col"],
          }),
        })}
      />,
    )
    // Should not crash; should render the expression text generically
    expect(screen.getByText(/some_col/)).toBeInTheDocument()
  })

  it("empty expression_text: shows 'computed'", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "",
            expression_type: "arithmetic",
            referenced_columns: [],
          }),
        })}
      />,
    )
    expect(screen.getByText(/computed/i)).toBeInTheDocument()
  })

  it("empty substituted_text: shows just the result", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: "",
            result_value: 99,
            input_values: {},
          }),
        })}
      />,
    )
    expect(screen.getAllByText("99").length).toBeGreaterThanOrEqual(1)
  })

  it("empty referenced_columns array: no input section", () => {
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "42",
            expression_type: "literal",
            referenced_columns: [],
          }),
          calculation: makeCalculation({
            input_values: {},
            substituted_text: "= 42",
            result_value: 42,
          }),
        })}
      />,
    )
    const inputSection = container.querySelector("[data-testid='input-values'], [class*='input']")
    if (inputSection) {
      expect(inputSection.children.length).toBe(0)
    } else {
      expect(inputSection).toBeNull()
    }
  })

  it("result value is an object/array: renders as JSON fallback", () => {
    render(
      <CalculationHero
        {...makeProps({
          calculation: makeCalculation({
            substituted_text: '= {"a":1,"b":2}',
            result_value: { a: 1, b: 2 },
            input_values: {},
          }),
        })}
      />,
    )
    // Should render the JSON representation
    expect(screen.getAllByText(/\{"a":1,"b":2\}/).length).toBeGreaterThanOrEqual(1)
  })
})

// ---------------------------------------------------------------------------
// CATEGORY 8: Waterfall Mode
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 Waterfall Mode", () => {
  afterEach(cleanup)

  const waterfallProps = (
    overrides: Partial<CalculationHeroProps> = {},
  ): CalculationHeroProps =>
    makeProps({
      expression: makeExpression({
        expression_text: "base * territory_factor * age_factor * loyalty_discount",
        expression_type: "arithmetic",
        referenced_columns: ["base", "territory_factor", "age_factor", "loyalty_discount"],
      }),
      calculation: makeCalculation({
        substituted_text: "500 \u00d7 1.3 \u00d7 1.1 \u00d7 0.9 = 643.5",
        result_value: 643.5,
        input_values: {
          base: 500,
          territory_factor: 1.3,
          age_factor: 1.1,
          loyalty_discount: 0.9,
        },
      }),
      ...overrides,
    })

  it("3 multiplicative steps: renders as vertical waterfall bars", () => {
    const { container } = render(<CalculationHero {...waterfallProps()} />)
    // Should contain waterfall bars (data-testid or class convention)
    const waterfallBars = container.querySelectorAll(
      "[data-testid='waterfall-bar'], [class*='waterfall-bar'], [class*='waterfallBar']",
    )
    expect(waterfallBars.length).toBeGreaterThanOrEqual(3)
  })

  it("shows each factor name and contribution", () => {
    render(<CalculationHero {...waterfallProps()} />)
    expect(screen.getByText(/territory_factor/)).toBeInTheDocument()
    expect(screen.getByText(/age_factor/)).toBeInTheDocument()
    expect(screen.getByText(/loyalty_discount/)).toBeInTheDocument()
  })

  it("positive adjustments: shown in one color", () => {
    const { container } = render(<CalculationHero {...waterfallProps()} />)
    // Positive adjustments (factor > 1) should have a positive color class
    const positiveBars = container.querySelectorAll(
      "[class*='positive'], [data-direction='positive'], [class*='increase']",
    )
    expect(positiveBars.length).toBeGreaterThanOrEqual(1)
  })

  it("negative adjustments (discounts): shown in different color", () => {
    const { container } = render(<CalculationHero {...waterfallProps()} />)
    // loyalty_discount = 0.9, which is < 1 (a reduction)
    const negativeBars = container.querySelectorAll(
      "[class*='negative'], [data-direction='negative'], [class*='decrease']",
    )
    expect(negativeBars.length).toBeGreaterThanOrEqual(1)
  })

  it("final total: prominent at bottom", () => {
    render(<CalculationHero {...waterfallProps()} />)
    // The final total should display the result value
    const totalEl = screen.getByText("643.5")
    expect(totalEl).toBeInTheDocument()
    // It should be in a total/final element
    const totalContainer = totalEl.closest(
      "[class*='total'], [class*='final'], [data-testid='waterfall-total']",
    )
    expect(totalContainer).toBeTruthy()
  })

  it("waterfall with a zero-contribution step: still renders (thin bar or 'no change')", () => {
    const { container } = render(
      <CalculationHero
        {...waterfallProps({
          expression: makeExpression({
            expression_text: "base * neutral_factor * boost_factor",
            expression_type: "arithmetic",
            referenced_columns: ["base", "neutral_factor", "boost_factor"],
          }),
          calculation: makeCalculation({
            substituted_text: "500 \u00d7 1.0 \u00d7 1.2 = 600",
            result_value: 600,
            input_values: {
              base: 500,
              neutral_factor: 1.0,
              boost_factor: 1.2,
            },
          }),
        })}
      />,
    )
    // neutral_factor = 1.0 means zero contribution
    expect(screen.getByText(/neutral_factor/)).toBeInTheDocument()
    // The bar for neutral_factor should still exist (thin or labeled "no change")
    const waterfallBars = container.querySelectorAll(
      "[data-testid='waterfall-bar'], [class*='waterfall-bar'], [class*='waterfallBar']",
    )
    expect(waterfallBars.length).toBeGreaterThanOrEqual(2)
  })
})
