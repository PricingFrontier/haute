import { describe, it, expect, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { FeaturesTab } from "../FeaturesTab"
import { makeTrainResult } from "../../../test-utils/factories"

afterEach(cleanup)

describe("FeaturesTab", () => {
  it("shows empty state when no feature importance data", () => {
    const result = makeTrainResult({ feature_importance: [] })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("No feature importance data available")).toBeInTheDocument()
  })

  it("shows the top 20 by default and exposes all rows and hidden rows through search", () => {
    const features = Array.from({ length: 25 }, (_, i) => ({
      feature: `feat_${i}`,
      importance: 25 - i,
    }))
    const result = makeTrainResult({ feature_importance: features })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("feat_0")).toBeInTheDocument()
    expect(screen.getByText("feat_19")).toBeInTheDocument()
    expect(screen.queryByText("feat_24")).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Search importance features"), {
      target: { value: "feat_24" },
    })
    expect(screen.getByText("feat_24")).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText("Features shown"), { target: { value: "all" } })
    fireEvent.change(screen.getByLabelText("Search importance features"), { target: { value: "" } })
    expect(screen.getByText("feat_24")).toBeInTheDocument()
  })

  it("shows feature count summary", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "a", importance: 10 },
        { feature: "b", importance: 5 },
      ],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("2 matching features")).toBeInTheDocument()
  })

  it("shows singular 'feature' for single feature", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "only_one", importance: 10 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("1 matching feature")).toBeInTheDocument()
  })

  it("shows type switcher when loss importance is available", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 10 }],
      feature_importance_loss: [{ feature: "x", importance: 7 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("Prediction")).toBeInTheDocument()
    expect(screen.getByText("Loss")).toBeInTheDocument()
  })

  it("does not show type switcher when only prediction data exists", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 10 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.queryByText("Prediction")).not.toBeInTheDocument()
    expect(screen.queryByText("Loss")).not.toBeInTheDocument()
  })

  it("switching to Loss tab shows loss features", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "pred_feat", importance: 10 }],
      feature_importance_loss: [{ feature: "loss_feat", importance: 7 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("pred_feat")).toBeInTheDocument()

    fireEvent.click(screen.getByText("Loss"))
    expect(screen.getByText("loss_feat")).toBeInTheDocument()
  })

  it("keeps signed loss values and puts negative values opposite positive values", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "prediction", importance: 1 }],
      feature_importance_loss: [
        { feature: "harmful", importance: -2 },
        { feature: "helpful", importance: 3 },
      ],
    })
    render(<FeaturesTab result={result} />)
    fireEvent.click(screen.getByRole("button", { name: "Loss" }))
    expect(screen.getByText("-2.0")).toBeInTheDocument()
    expect(screen.getByText("+3.0")).toBeInTheDocument()
    expect(screen.getByLabelText("harmful: -2.0")).toBeInTheDocument()
    expect(screen.getByLabelText("helpful: +3.0")).toBeInTheDocument()
  })

  it("keeps the magnitude reference stable while searching", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "largest", importance: 100 },
        { feature: "small", importance: 10 },
      ],
    })
    render(<FeaturesTab result={result} />)
    const before = screen.getByLabelText("small: 10.0").querySelector("div")
    expect(before).toHaveStyle({ width: "10%" })

    fireEvent.change(screen.getByLabelText("Search importance features"), {
      target: { value: "small" },
    })
    const after = screen.getByLabelText("small: 10.0").querySelector("div")
    expect(after).toHaveStyle({ width: "10%" })
  })

  it("shows SHAP tab when shap_summary is available", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "x", importance: 10 }],
      shap_summary: [{ feature: "shap_feat", mean_abs_shap: 5 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("SHAP")).toBeInTheDocument()

    fireEvent.click(screen.getByText("SHAP"))
    expect(screen.getByText("shap_feat")).toBeInTheDocument()
  })

  it("offers the SHAP beeswarm only for a result with beeswarm rows", () => {
    const shap_summary = [{ feature: "x", mean_abs_shap: 5 }]
    render(<FeaturesTab result={makeTrainResult({ shap_summary })} />)
    expect(screen.queryByText("SHAP beeswarm")).not.toBeInTheDocument()
    cleanup()

    render(
      <FeaturesTab
        result={makeTrainResult({
          shap_summary,
          shap_beeswarm: [
            { feature: "x", kind: "numeric", shap_values: [0.1], values: [1], value_ranks: [null] },
          ],
        })}
      />,
    )
    expect(screen.getByRole("button", { name: "SHAP beeswarm" })).toBeInTheDocument()
  })

  it("replaces the search and Features shown controls with the beeswarm's scope", () => {
    const names = Array.from({ length: 25 }, (_, i) => `feat_${i}`)
    const result = makeTrainResult({
      feature_importance: names.map((feature, i) => ({ feature, importance: 25 - i })),
      shap_summary: names.map((feature, i) => ({ feature, mean_abs_shap: 25 - i })),
      shap_beeswarm: names.slice(0, 20).map((feature) => ({
        feature,
        kind: "numeric" as const,
        shap_values: [0.1, -0.2, 0.3],
        values: [1, 2, 3],
        value_ranks: [0, 0.5, 1],
      })),
    })
    render(<FeaturesTab result={result} />)

    fireEvent.click(screen.getByRole("button", { name: "SHAP beeswarm" }))

    expect(screen.queryByLabelText("Search importance features")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Features shown")).not.toBeInTheDocument()
    expect(screen.getByText("Top 20 of 25 features by mean |SHAP|, 3 sampled rows.")).toBeInTheDocument()
    expect(screen.getAllByTestId("shap-beeswarm-feature")).toHaveLength(20)

    fireEvent.click(screen.getByRole("button", { name: "Prediction" }))
    expect(screen.getByLabelText("Search importance features")).toBeInTheDocument()
  })

  it("displays importance values", () => {
    const result = makeTrainResult({
      feature_importance: [{ feature: "age", importance: 25.3 }],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("25.3")).toBeInTheDocument()
  })

  it("preserves small signed importances instead of rounding them to zero", () => {
    render(
      <FeaturesTab
        result={makeTrainResult({
          feature_importance: [{ feature: "small", importance: -0.0031 }],
        })}
      />,
    )
    expect(screen.getByText("-0.0031")).toBeInTheDocument()
    expect(screen.getByLabelText("small: -0.0031")).toBeInTheDocument()
  })

  it("displays rank numbers", () => {
    const result = makeTrainResult({
      feature_importance: [
        { feature: "a", importance: 10 },
        { feature: "b", importance: 5 },
      ],
    })
    render(<FeaturesTab result={result} />)
    expect(screen.getByText("1")).toBeInTheDocument()
    expect(screen.getByText("2")).toBeInTheDocument()
  })
})
