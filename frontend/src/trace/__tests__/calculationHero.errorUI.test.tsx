/**
 * Phase 2 Wave 5 package 5C — review item #85.
 *
 * `CalculationHero.tsx` contains several paths where it silently renders
 * nothing (or a misleading empty label) when data is missing. Silent nulls
 * hide misconfiguration: the user sees an empty pane with no indication
 * that a backend error occurred, that a calculation was expected but not
 * produced, or that a waterfall build failed.
 *
 * This test suite pins the desired behaviour for each "error" null branch:
 * render a visible, accessible error UI with a specific message. It also
 * defends the legitimate "empty" nulls that should stay (e.g. raw source
 * data rows, opaque-with-result, and the a11y sentinel IIFE that returns
 * null when a branch result is not found verbatim in the branch text).
 *
 * Intentional failure mode (TDD): these tests are written BEFORE the
 * production fix and so must FAIL until:
 *   1. `WaterfallErrorAlert` is extracted as a shared component.
 *   2. The `isOpaque && !calculation` path is changed to render an error
 *      UI instead of a misleading "computed" label.
 *   3. The existing `renderUnifiedBox` null-calculation alert keeps its
 *      explicit message so the dev can't accidentally reduce it to an
 *      empty string later.
 */

import { describe, it, expect, afterEach } from "vitest"
import { render, screen, cleanup, within } from "@testing-library/react"
import CalculationHero from "../CalculationHero"
import type { CalculationHeroProps } from "../CalculationHero"
import WaterfallErrorAlert from "../WaterfallErrorAlert"

// ---------------------------------------------------------------------------
// Factories — mirror the style used in panels/__tests__/CalculationHero.test.tsx
// so expectations are consistent with the existing suite.
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
// Error-silent null branches — these MUST render a visible error UI.
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 error-silent null branches must surface visible errors", () => {
  afterEach(cleanup)

  // -------------------------------------------------------------------------
  // Case A: arithmetic expression present, calculation missing.
  // Previously the default `renderUnifiedBox` silently short-circuited with
  // `return null`. The current code shows an alert; this test pins the
  // alert down with an explicit message substring so the dev can't stub it
  // with an empty string.
  // -------------------------------------------------------------------------
  it("A: arithmetic expression present but calculation missing \u2014 renders accessible alert with explicit message", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "base_premium * age_factor",
            expression_type: "arithmetic",
          }),
          calculation: null,
        })}
      />,
    )

    // Must be announced via role=alert (WCAG: ARIA live region for errors).
    const alert = screen.getByRole("alert")
    expect(alert).toBeInTheDocument()

    // Message must be non-empty AND describe the missing-calculation state.
    // We pin specific substrings so the dev cannot silently stub the copy
    // with an empty string and still pass the suite.
    expect(alert.textContent).toBeTruthy()
    expect(alert.textContent?.length ?? 0).toBeGreaterThan(10)
    expect(alert).toHaveTextContent(/calculation/i)
    expect(alert).toHaveTextContent(/not available|missing|unavailable/i)
  })

  // -------------------------------------------------------------------------
  // Case B: opaque expression present, calculation missing.
  // Today this renders an italicised "computed" label regardless of whether
  // calculation data exists — a silent fallback. If the backend said the
  // expression is opaque but produced no calculation, that's a real error
  // the user needs to see.
  // -------------------------------------------------------------------------
  it("B: opaque expression with missing calculation \u2014 renders alert, NOT a silent 'computed' label", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text: "",
            expression_type: "opaque",
            referenced_columns: [],
          }),
          calculation: null,
        })}
      />,
    )

    // The page MUST contain an accessible error announcement.
    const alerts = screen.queryAllByRole("alert")
    expect(alerts.length).toBeGreaterThanOrEqual(1)

    // Of those alerts, at least one must carry an explicit missing-data
    // message. Pinning the substring prevents silent stubbing.
    const hasExplicitMessage = alerts.some((el) => {
      const txt = el.textContent ?? ""
      return (
        /calculation/i.test(txt) &&
        /(not available|missing|unavailable)/i.test(txt) &&
        txt.length > 10
      )
    })
    expect(hasExplicitMessage).toBe(true)
  })

  // -------------------------------------------------------------------------
  // Case C: backend reported a waterfall build error. StepCard owns the
  // shared alert, so CalculationHero must not duplicate it.
  // -------------------------------------------------------------------------
  it("C: backend waterfall error \u2014 leaves alert ownership to StepCard", () => {
    const backendError = "Waterfall build failed: non-multiplicative operator encountered"
    render(
      <CalculationHero
        {...makeProps({
          waterfall: {
            error: backendError,
            error_type: "WaterfallBuildError",
          } as unknown as CalculationHeroProps["waterfall"],
        })}
      />,
    )

    // The parent StepCard renders the alert once; the hero stays quiet.
    expect(screen.queryByText(new RegExp(backendError.slice(0, 30)))).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })
})

// ---------------------------------------------------------------------------
// Legitimate-empty null branches — these MUST remain (no regression).
//
// The map:
//   - isNullBoth (no expression, no calculation): raw source column.
//     Not an error — column is upstream source data.
//   - isOpaque WITH a valid calculation: the backend chose to obscure the
//     expression but a result exists. "computed" label is the intended UX.
//   - A conditional without typed branch evidence: the branches remain
//     visible, but none is guessed from result text.
// ---------------------------------------------------------------------------

describe("CalculationHero \u2014 legitimate-empty null branches must stay legit-empty", () => {
  afterEach(cleanup)

  it("D: no expression + no calculation (raw source column) \u2014 shows 'source data', NOT an error alert", () => {
    render(
      <CalculationHero
        {...makeProps({
          expression: null,
          calculation: null,
        })}
      />,
    )

    // The hint text for a source column must be present.
    expect(screen.getByText(/source data/i)).toBeInTheDocument()

    // And there must be NO error alert — this is not a misconfiguration.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  })

  it("E: opaque expression WITH calculation \u2014 shows 'computed' label without error alert", () => {
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

    // The opaque label should still render.
    expect(screen.getByText(/computed/i)).toBeInTheDocument()

    // And no error alert — this is the intended UX when a result exists.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()

    // Result value should still surface in line 1.
    expect(screen.getAllByText(/42\.5/).length).toBeGreaterThanOrEqual(1)
  })

  it("F: conditional without typed branch evidence stays valid and unselected", () => {
    // Conditional expression whose textual branches do not establish the
    // selected path. Absence of typed backend evidence is not itself an
    // enrichment error, and the UI must not guess.
    const { container } = render(
      <CalculationHero
        {...makeProps({
          expression: makeExpression({
            expression_text:
              "when age > 25 then 'senior_discount' otherwise 'standard_rate'",
            expression_type: "conditional",
            referenced_columns: ["age"],
          }),
          calculation: makeCalculation({
            // substituted branches contain textual labels only; result
            // value 999.99 is NOT present verbatim in branch text.
            substituted_text:
              "when 30 > 25 then 'senior_discount' otherwise 'standard_rate'",
            result_value: 999.99,
            input_values: { age: 30 },
          }),
        })}
      />,
    )

    // No error alert — missing branch-selection evidence is not a failure.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()

    // The conditional branches themselves must still render.
    expect(screen.getByText(/when/i)).toBeInTheDocument()
    expect(screen.getByText(/otherwise/i)).toBeInTheDocument()

    // Sanity: conditional UI root exists.
    expect(container.querySelector(".conditional-display")).toBeTruthy()
  })
})

// ---------------------------------------------------------------------------
// WaterfallErrorAlert component — minimal error UI that must exist.
//
// Tests pin the minimum contract: renders the passed message, uses an
// accessible role, and does not collapse empty strings silently.
// ---------------------------------------------------------------------------

describe("WaterfallErrorAlert component contract", () => {
  afterEach(cleanup)

  it("renders the passed error message verbatim", () => {
    const message = "Waterfall could not be computed for node 'rating_engine'"
    render(<WaterfallErrorAlert error={message} />)
    expect(screen.getByText(message)).toBeInTheDocument()
  })

  it("has role=alert for accessible error announcement", () => {
    render(<WaterfallErrorAlert error="something broke" />)
    const alert = screen.getByRole("alert")
    expect(alert).toBeInTheDocument()
    expect(alert.textContent).toContain("something broke")
  })

  it("does not hide an empty error string silently \u2014 still renders a non-empty fallback", () => {
    // Defensive: if the backend ever hands us an empty string we should
    // still show SOMETHING so the user knows the waterfall failed. An
    // empty error UI would itself be a silent null.
    render(<WaterfallErrorAlert error="" />)
    const alert = screen.getByRole("alert")
    expect(alert).toBeInTheDocument()
    // The container text must not be empty — component renders its own
    // "waterfall error" header/prefix in addition to the message.
    expect((alert.textContent ?? "").trim().length).toBeGreaterThan(0)
  })

  it("surfaces long error messages in full (no truncation collapse)", () => {
    const longMessage = "A".repeat(200) + " — waterfall aborted mid-step"
    render(<WaterfallErrorAlert error={longMessage} />)
    const alert = screen.getByRole("alert")
    // We search within the alert to avoid matching truncation ellipses
    // elsewhere on the page.
    expect(within(alert).getByText(/waterfall aborted mid-step/)).toBeInTheDocument()
  })
})
