import { cleanup, fireEvent, render, screen, within } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import type { EbmTerm } from "../../../api/types"
import { makeTrainResult } from "../../../test-utils/factories"
import { EBMInteractionsConfig } from "../EBMInteractionsConfig"
import { EBMTermsTab } from "../EBMTermsTab"

const columns = [
  { name: "claims", dtype: "Float64" },
  { name: "age", dtype: "Float64" },
  { name: "region", dtype: "String" },
  { name: "income", dtype: "Float64" },
]

function renderInteractions(config: Record<string, unknown>) {
  const onUpdate = vi.fn(() => ({ ok: true as const }))
  render(
    <EBMInteractionsConfig
      config={{ algorithm: "ebm", target: "claims", ...config }}
      onUpdate={onUpdate}
      columns={columns}
    />,
  )
  return onUpdate
}

describe("EBMInteractionsConfig (MOD-F04)", () => {
  afterEach(cleanup)

  it("switches between an automatic count and explicit pairs, keeping other params", () => {
    const onUpdate = renderInteractions({ params: { max_rounds: 500, interactions: 3 } })
    expect(screen.getByLabelText("Maximum interaction count")).toHaveValue(3)
    fireEvent.change(screen.getByLabelText("Maximum interaction count"), { target: { value: "7" } })
    expect(onUpdate).toHaveBeenLastCalledWith("params", { max_rounds: 500, interactions: 7 })
    fireEvent.click(screen.getByRole("radio", { name: "Choose pairs" }))
    expect(onUpdate).toHaveBeenLastCalledWith("params", { max_rounds: 500, interactions: [] })
  })

  it("edits explicit pairs from the included features and refuses monotone ones", () => {
    const onUpdate = renderInteractions({
      params: { max_rounds: 500, interactions: [["age", "region"]] },
      monotone_constraints: { income: 1 },
      exclude: [],
    })
    const pair = screen.getByRole("group", { name: "Interaction 1" })
    const second = within(pair).getByLabelText("Interaction 1 feature 2")
    const options = within(second).getAllByRole("option").map((option) => option.textContent)
    // The target is a role column, never a feature.
    expect(options).toEqual(["Choose a feature", "age", "region", "income (monotone)"])
    expect(within(second).getByRole("option", { name: "income (monotone)" })).toBeDisabled()
    fireEvent.change(second, { target: { value: "age" } })
    expect(onUpdate).toHaveBeenLastCalledWith("params", {
      max_rounds: 500,
      interactions: [["age", "age"]],
    })
    fireEvent.click(screen.getByRole("button", { name: "Add interaction" }))
    expect(onUpdate).toHaveBeenLastCalledWith("params", {
      max_rounds: 500,
      interactions: [["age", "region"], ["", ""]],
    })
    fireEvent.click(screen.getByRole("button", { name: "Remove interaction 1" }))
    expect(onUpdate).toHaveBeenLastCalledWith("params", { max_rounds: 500, interactions: [] })
  })

  it("keeps a saved pair naming a column that is no longer a feature visible and repairable", () => {
    renderInteractions({ params: { max_rounds: 500, interactions: [["age", "gone"]] } })
    expect(screen.getByLabelText("Interaction 1 feature 2")).toHaveValue("gone")
    expect(screen.getByRole("option", { name: "gone (not a feature)" })).toBeInTheDocument()
  })
})

const TERMS: EbmTerm[] = [
  {
    term: "age",
    features: ["age"],
    kind: "main",
    importance: 0.4,
    axes: [{ feature: "age", type: "continuous", labels: ["Missing", "< 30", ">= 30"], cuts: [30] }],
    scores: [0.05, -0.2, 0.3],
  },
  {
    term: "region",
    features: ["region"],
    kind: "main",
    importance: 0.3,
    axes: [{ feature: "region", type: "nominal", labels: ["Missing", "east", "north"] }],
    scores: [0.1, -0.25, 0.4],
  },
  {
    term: "region & age",
    features: ["region", "age"],
    kind: "interaction",
    importance: 0.1,
    axes: [
      { feature: "region", type: "nominal", labels: ["Missing", "east", "north"] },
      { feature: "age", type: "continuous", labels: ["Missing", "< 30"] },
    ],
    scores: [[0, 0.01], [0.02, 0.03], [0.04, 0.05]],
  },
]

describe("EBMTermsTab (MOD-F04)", () => {
  afterEach(cleanup)

  it("shows the most important term's shape with its missing bin, labelled as term scores", () => {
    render(<EBMTermsTab result={makeTrainResult({ ebm_terms: TERMS })} />)
    expect(screen.getByRole("heading", { name: "age" })).toBeInTheDocument()
    expect(screen.getByText(/Main effect/)).toBeInTheDocument()
    expect(screen.getByText("Missing values score 0.05")).toBeInTheDocument()
    expect(screen.getByRole("img", { name: "Shape function for age" })).toBeInTheDocument()
    expect(screen.getByText(/Additive term scores on the model's link scale/)).toBeInTheDocument()
    expect(screen.queryByText(/SHAP/)).toBeNull()
  })

  it("draws a category shape and an interaction surface as one two-feature term", () => {
    render(<EBMTermsTab result={makeTrainResult({ ebm_terms: TERMS })} />)
    fireEvent.click(screen.getByText("region", { selector: "button *, button" }))
    const shape = screen.getByRole("img", { name: "Shape function for region" })
    expect(within(shape).getByText("Missing")).toBeInTheDocument()
    expect(within(shape).getByText("north")).toBeInTheDocument()

    fireEvent.click(screen.getByText("region & age", { selector: "button *, button" }))
    expect(screen.getByText(/Pairwise interaction/)).toBeInTheDocument()
    const surface = screen.getByRole("table", { name: "Interaction surface for region & age" })
    // The longer axis (region, 3 labels) runs down the rows.
    const rows = within(surface).getAllByRole("row")
    expect(rows).toHaveLength(4)
    expect(within(rows[3]).getByRole("rowheader")).toHaveTextContent("north")
    expect(within(rows[3]).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "0.04",
      "0.05",
    ])
  })

  it("states plainly when a result has no EBM terms", () => {
    render(<EBMTermsTab result={makeTrainResult({ ebm_terms: [] })} />)
    expect(screen.getByText("No EBM terms available")).toBeInTheDocument()
  })
})
