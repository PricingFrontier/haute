/**
 * Tests for the GLM-specific UI components:
 *   - GLMTargetConfig (family, link, offset, metrics)
 *   - GLMTermsConfig (terms pane routing)
 *   - GLMRegularizationConfig (type toggle, alpha, CV folds, L1 ratio)
 *   - GLMCoefficientsTab (sortable coefficients table)
 *   - GLMRelativitiesTab (bar chart with sort modes)
 *   - ModellingConfig GLM routing (algorithm picker, GLM panel rendering)
 *   - SummaryTab GLM fit statistics
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { GLMTargetConfig } from "../modelling/GLMTargetConfig"
import { GLMRegularizationConfig } from "../modelling/GLMRegularizationConfig"
import { GLMCoefficientsTab } from "../modelling/GLMCoefficientsTab"
import { GLMRelativitiesTab } from "../modelling/GLMRelativitiesTab"
import { SummaryTab } from "../modelling/SummaryTab"
import ModellingConfig from "../ModellingConfig"
import { GraphProvider } from "../GraphContext"
import useNodeResultsStore from "../../stores/useNodeResultsStore"
import useSettingsStore from "../../stores/useSettingsStore"
import type { TrainResult } from "../../stores/useNodeResultsStore"
import { makeTrainResult as makeCanonicalTrainResult } from "../../test-utils/factories"

// ── Mocks ────────────────────────────────────────────────────────

vi.mock("../../api/client", () => ({
  trainModel: vi.fn(() => new Promise(() => {})),
  estimateTrainingRam: vi.fn(() => new Promise(() => {})),
  // The train section mounts the destination selector, whose store slice
  // fetches the inventory on mount.
  getMlflowDestinations: vi.fn(() => Promise.resolve({
    mlflow_installed: true,
    mlflow_importable: true,
    destinations: [
      { key: "databricks", configured: false, destination: "", config_source: "", detail: "", probed: false, ok: false, category: "" },
      { key: "server", configured: false, destination: "", config_source: "", detail: "", probed: false, ok: false, category: "" },
      { key: "local", configured: true, destination: "C:/proj/mlruns", config_source: "default", detail: "", probed: false, ok: false, category: "" },
    ],
    detail: "",
  })),
  // GLMTargetConfig narrows errors with `instanceof ApiError`, so the mock
  // must export a real class or the instanceof check throws.
  ApiError: class ApiError extends Error {},
}))

vi.mock("../../api/dispersion", () => ({
  runDispersionEstimate: vi.fn(() => new Promise(() => {})),
}))

vi.mock("../../utils/buildGraph", () => ({
  buildGraph: vi.fn(() => ({ nodes: [], edges: [], preamble: "" })),
}))

vi.mock("../modelling/TrainingProgress", () => ({
  TrainingProgress: () => <div data-testid="training-progress" />,
}))

vi.mock("../modelling/MlflowExportSection", () => ({
  MlflowExportSection: () => <div data-testid="mlflow-export" />,
}))

// ── Shared helpers ───────────────────────────────────────────────

const defaultColumns = [
  { name: "claim_count", dtype: "Int64" },
  { name: "age", dtype: "Float64" },
  { name: "region", dtype: "Utf8" },
  { name: "exposure", dtype: "Float64" },
  { name: "severity", dtype: "Float64" },
]

function makeGlmCoefficients() {
  return [
    { feature: "(Intercept)", coefficient: -1.234, std_error: 0.05, z_value: -24.68, p_value: 0.0001, significance: "***" },
    { feature: "age", coefficient: 0.012, std_error: 0.003, z_value: 4.0, p_value: 0.0001, significance: "***" },
    { feature: "region_B", coefficient: 0.156, std_error: 0.08, z_value: 1.95, p_value: 0.051, significance: "." },
    { feature: "region_C", coefficient: -0.089, std_error: 0.09, z_value: -0.99, p_value: 0.322, significance: "" },
  ]
}

function makeGlmRelativities() {
  return [
    { feature: "(Intercept)", relativity: 0.291, ci_lower: 0.265, ci_upper: 0.32 },
    { feature: "age", relativity: 1.012, ci_lower: 1.006, ci_upper: 1.018 },
    { feature: "region_B", relativity: 1.169, ci_lower: 0.998, ci_upper: 1.37 },
    { feature: "region_C", relativity: 0.915, ci_lower: 0.766, ci_upper: 1.093 },
  ]
}

function makeTrainResult(overrides: Partial<TrainResult> = {}): TrainResult {
  return makeCanonicalTrainResult({
    final_test_metrics: { gini: 0.35, poisson_deviance: 1.42 },
    feature_importance: [
      { feature: "age", importance: 0.6 },
      { feature: "region", importance: 0.4 },
    ],
    model_path: "/models/glm_model.rsglm",
    ...overrides,
  })
}

beforeEach(() => {
  useNodeResultsStore.setState({ trainJobs: {}, trainResults: {} })
  useSettingsStore.setState({
    mlflow: {
      status: "pending",
      installed: null,
      importable: null,
      destinations: [],
      detail: "",
    },
    openSections: {},
  })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

// ═════════════════════════════════════════════════════════════════
// GLMTargetConfig
// ═════════════════════════════════════════════════════════════════

describe("GLMTargetConfig", () => {
  const baseConfig = { _nodeId: "n1", algorithm: "glm", target: "claim_count", weight: "exposure", family: "poisson" }
  const onUpdate = vi.fn()

  beforeEach(() => onUpdate.mockReset())

  it("renders target and weight dropdowns", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText("Target column")).toBeTruthy()
    expect(screen.getByText("Weight column (optional)")).toBeTruthy()
  })

  it("renders all backend-accepted family buttons", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    // Every family the backend _VALID_GLM_LINKS validates. Quasi-Poisson's
    // dispersion is estimated from Pearson residuals (no user parameter);
    // Neg. Binomial is offered now its theta gate exists — an explicit theta
    // is required before training, so RustyStats can never refuse the fit.
    for (const label of [
      "Poisson", "Gamma", "Tweedie", "Gaussian", "Binomial", "Quasi-Poisson",
      "Neg. Binomial",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy()
    }
  })

  it("selecting Neg. Binomial sets family, canonical link and count metrics", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "Neg. Binomial" }))
    expect(onUpdate).toHaveBeenCalledWith({
      family: "negbinomial",
      link: "",
      metrics: ["gini", "poisson_deviance"],
    })
  })

  it("selecting Quasi-Poisson sets family, canonical link and count metrics", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "Quasi-Poisson" }))
    expect(onUpdate).toHaveBeenCalledWith({
      family: "quasipoisson",
      link: "",
      metrics: ["gini", "poisson_deviance"],
    })
  })

  it("clicking a family updates family, link, and metrics", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "Gamma" }))
    expect(onUpdate).toHaveBeenCalledWith({
      family: "gamma",
      link: "",
      metrics: ["gini", "rmse"],
    })
  })

  it("gates Tweedie variance power when family=tweedie and the value is null", () => {
    const { unmount } = render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.queryByText(/Variance power/)).toBeNull()
    unmount()

    render(<GLMTargetConfig config={{ ...baseConfig, family: "tweedie", var_power: null }} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText(/Variance power/)).toBeTruthy()
    expect(screen.getByRole("button", { name: /Set variance power/ })).toBeTruthy()
    expect(screen.queryByRole("slider")).toBeNull()
  })

  it("shows the theta field only when family=negbinomial, empty by default", () => {
    // The gate starts from an empty field: an unselected theta must LOOK
    // unselected — faking a value here would show a chosen dispersion the
    // config doesn't actually have (RustyStats has no default to fall back on).
    const { unmount } = render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.queryByText(/Dispersion theta/)).toBeNull()
    unmount()

    render(<GLMTargetConfig config={{ ...baseConfig, family: "negbinomial" }} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText(/Dispersion theta/)).toBeTruthy()
    const input = screen.getByPlaceholderText("e.g. 1.5") as HTMLInputElement
    expect(input.value).toBe("")
  })

  it("typing a theta updates the config", () => {
    render(<GLMTargetConfig config={{ ...baseConfig, family: "negbinomial" }} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.change(screen.getByPlaceholderText("e.g. 1.5"), { target: { value: "2.5" } })
    expect(onUpdate).toHaveBeenCalledWith("theta", 2.5)
  })

  it("clearing the theta field unsets it (re-arms the gate)", () => {
    render(<GLMTargetConfig config={{ ...baseConfig, family: "negbinomial", theta: 2.5 }} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.change(screen.getByPlaceholderText("e.g. 1.5"), { target: { value: "" } })
    expect(onUpdate).toHaveBeenCalledWith("theta", null)
  })

  it("estimate button profiles theta and fills the field for the user", async () => {
    // The estimate is an explicit user action: the resolved value lands in
    // the editable field via onUpdate — never applied silently.
    const onEstimateDispersion = vi.fn(() => Promise.resolve(2.4487))
    render(
      <GLMTargetConfig
        config={{ ...baseConfig, family: "negbinomial" }}
        onUpdate={onUpdate}
        columns={defaultColumns}
        onEstimateDispersion={onEstimateDispersion}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Estimate from data" }))
    expect(onEstimateDispersion).toHaveBeenCalledWith("theta")
    await vi.waitFor(() => expect(onUpdate).toHaveBeenCalledWith("theta", 2.4487))
  })

  it("estimate failure surfaces the error instead of filling a value", async () => {
    const onEstimateDispersion = vi.fn(() => Promise.reject(new Error("no converged fit")))
    render(
      <GLMTargetConfig
        config={{ ...baseConfig, family: "negbinomial" }}
        onUpdate={onUpdate}
        columns={defaultColumns}
        onEstimateDispersion={onEstimateDispersion}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Estimate from data" }))
    await vi.waitFor(() => expect(screen.getByText(/Estimation failed: no converged fit/)).toBeTruthy())
    expect(onUpdate).not.toHaveBeenCalled()
  })

  it("hides the estimate buttons when no estimation handler is provided", () => {
    render(<GLMTargetConfig config={{ ...baseConfig, family: "negbinomial" }} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.queryByRole("button", { name: "Estimate from data" })).toBeNull()
  })

  it("tweedie variance-power gate offers estimation too", async () => {
    const onEstimateDispersion = vi.fn(() => Promise.resolve(1.47))
    render(
      <GLMTargetConfig
        config={{ ...baseConfig, family: "tweedie" }}
        onUpdate={onUpdate}
        columns={defaultColumns}
        onEstimateDispersion={onEstimateDispersion}
      />,
    )
    fireEvent.click(screen.getByRole("button", { name: "Estimate from data" }))
    expect(onEstimateDispersion).toHaveBeenCalledWith("var_power")
    await vi.waitFor(() => expect(onUpdate).toHaveBeenCalledWith("var_power", 1.47))
  })

  it("renders link function buttons with auto default", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByRole("button", { name: /auto \(log\)/ })).toBeTruthy()
  })

  it("clicking non-canonical link sets link override", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "identity" }))
    expect(onUpdate).toHaveBeenCalledWith("link", "identity")
  })

  it("renders offset column dropdown with link-function help", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByText(/Offset column/)).toBeTruthy()
    // The link-function framing is carried by the hover help.
    expect(screen.getByTestId("offset-help")).toBeTruthy()
  })

  it("renders intercept checkbox checked by default", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    const checkbox = screen.getByRole("checkbox")
    expect(checkbox).toBeTruthy()
    expect((checkbox as HTMLInputElement).checked).toBe(true)
  })

  it("unchecking intercept calls onUpdate", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("checkbox"))
    expect(onUpdate).toHaveBeenCalledWith("intercept", false)
  })

  it("renders GLM metrics toggle buttons", () => {
    render(<GLMTargetConfig config={baseConfig} onUpdate={onUpdate} columns={defaultColumns} />)
    expect(screen.getByRole("button", { name: "Gini" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "RMSE" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Poisson Dev." })).toBeTruthy()
    expect(screen.getByRole("button", { name: "R²" })).toBeTruthy()
  })

  it("clicking a metric toggles it in/out", () => {
    const config = { ...baseConfig, metrics: ["gini", "poisson_deviance"] }
    render(<GLMTargetConfig config={config} onUpdate={onUpdate} columns={defaultColumns} />)
    fireEvent.click(screen.getByRole("button", { name: "RMSE" }))
    expect(onUpdate).toHaveBeenCalledWith("metrics", ["gini", "poisson_deviance", "rmse"])
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMRegularizationConfig
// ═════════════════════════════════════════════════════════════════

describe("GLMRegularizationConfig", () => {
  const onUpdate = vi.fn()

  beforeEach(() => onUpdate.mockReset())

  it("shows controls beneath a noninteractive Regularization heading", () => {
    render(<GLMRegularizationConfig config={{}} onUpdate={onUpdate} />)
    const heading = screen.getByRole("heading", { name: "Regularization" })
    expect(heading.closest("button")).toBeNull()
    expect(screen.getByRole("button", { name: "None" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Ridge" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Lasso" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "Elastic Net" })).toBeTruthy()
  })

  it("clicking Ridge sets regularization", () => {
    render(<GLMRegularizationConfig config={{}} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByRole("button", { name: "Ridge" }))
    expect(onUpdate).toHaveBeenCalledWith("regularization", "ridge")
  })

  it("clicking None clears regularization", () => {
    render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByRole("button", { name: "None" }))
    expect(onUpdate).toHaveBeenCalledWith("regularization", null)
  })

  it("shows alpha input when regularization is active", () => {
    render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    expect(screen.getByText(/Alpha/)).toBeTruthy()
  })

  it("L1 ratio controls only appear for elastic_net", () => {
    const { unmount } = render(<GLMRegularizationConfig config={{ regularization: "ridge" }} onUpdate={onUpdate} />)
    expect(screen.queryByText(/L1 ratio/)).toBeNull()
    unmount()

    render(<GLMRegularizationConfig config={{ regularization: "elastic_net", l1_ratio: null }} onUpdate={onUpdate} />)
    expect(screen.getAllByText(/L1 ratio/).length).toBeGreaterThan(0)
  })

  it("elastic_net gates the L1 ratio until it is set explicitly", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net" }} onUpdate={onUpdate} />)
    // Unset: the mix must be chosen — a prompt and both collapse shortcuts.
    expect(screen.getByRole("button", { name: /Set L1 ratio mix/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Fit Ridge \(0\)/ })).toBeTruthy()
    expect(screen.getByRole("button", { name: /Fit LASSO \(1\)/ })).toBeTruthy()
  })

  it("Fit Ridge / Fit LASSO shortcuts set an explicit L1 ratio", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net" }} onUpdate={onUpdate} />)
    fireEvent.click(screen.getByRole("button", { name: /Fit Ridge \(0\)/ }))
    expect(onUpdate).toHaveBeenCalledWith("l1_ratio", 0)
    fireEvent.click(screen.getByRole("button", { name: /Fit LASSO \(1\)/ }))
    expect(onUpdate).toHaveBeenCalledWith("l1_ratio", 1)
  })

  it("a set L1 ratio shows the mix slider, not the prompt", () => {
    render(<GLMRegularizationConfig config={{ regularization: "elastic_net", l1_ratio: 0.3 }} onUpdate={onUpdate} />)
    expect(screen.queryByRole("button", { name: /Set L1 ratio mix/ })).toBeNull()
    expect(screen.getByText("0.30")).toBeTruthy()
  })

  it("shows active badge when regularization is set", () => {
    render(<GLMRegularizationConfig config={{ regularization: "lasso" }} onUpdate={onUpdate} />)
    expect(screen.getByText("lasso")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMCoefficientsTab
// ═════════════════════════════════════════════════════════════════

describe("GLMCoefficientsTab", () => {
  it("renders coefficient table with correct columns", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText(/^Term/)).toBeTruthy()
    expect(screen.getByText(/^Estimate/)).toBeTruthy()
    expect(screen.getByText(/^Std\. Error/)).toBeTruthy()
    // "z" column — use the th element directly
    const headers = document.querySelectorAll("th")
    const headerTexts = Array.from(headers).map(h => h.textContent!.trim())
    expect(headerTexts).toContain("z")
    expect(headerTexts.some(h => h.startsWith("Pr(>|z|)"))).toBe(true)
    expect(screen.getByText("Sig.")).toBeTruthy()
  })

  it("renders all coefficient rows", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("(Intercept)")).toBeTruthy()
    expect(screen.getByText("age")).toBeTruthy()
    expect(screen.getByText("region_B")).toBeTruthy()
    expect(screen.getByText("region_C")).toBeTruthy()
  })

  it("shows significance stars", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // There should be *** entries and . entries
    const stars = screen.getAllByText("***")
    expect(stars.length).toBe(2) // intercept and age
    expect(screen.getByText(".")).toBeTruthy()
  })

  it("shows significance legend", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("Signif. codes:")).toBeTruthy()
    expect(screen.getByText("4 terms")).toBeTruthy()
  })

  it("clicking column header sorts data", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // Default sort: by p_value asc. Click "Term" to sort by name
    fireEvent.click(screen.getByText("Term"))
    const rows = document.querySelectorAll("tbody tr")
    const firstCell = rows[0].querySelector("td")!.textContent
    expect(firstCell).toBe("(Intercept)")
  })

  it("clicking same column header reverses sort direction", () => {
    const result = makeTrainResult({ glm_coefficients: makeGlmCoefficients() })
    render(<GLMCoefficientsTab result={result} />)
    // Click "Estimate" twice — after first click the text includes sort indicator
    fireEvent.click(screen.getByText(/^Estimate/))
    fireEvent.click(screen.getByText(/^Estimate/))
    // Should now be descending (largest first)
    const rows = document.querySelectorAll("tbody tr")
    const firstVal = rows[0].querySelectorAll("td")[1].textContent
    // region_B has 0.156 — the largest
    expect(firstVal).toBe("0.156000")
  })

  it("shows empty state when no coefficients", () => {
    const result = makeTrainResult({ glm_coefficients: [] })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("No coefficient data available")).toBeTruthy()
  })

  it("formats very small p-values in scientific notation", () => {
    const result = makeTrainResult({
      glm_coefficients: [
        { feature: "test", coefficient: 1, std_error: 0.001, z_value: 1000, p_value: 0.00001, significance: "***" },
      ],
    })
    render(<GLMCoefficientsTab result={result} />)
    expect(screen.getByText("1.00e-5")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// GLMRelativitiesTab
// ═════════════════════════════════════════════════════════════════

describe("GLMRelativitiesTab", () => {
  it("renders relativity bars for all terms", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("(Intercept)")).toBeTruthy()
    expect(screen.getByText("age")).toBeTruthy()
    expect(screen.getByText("region_B")).toBeTruthy()
    expect(screen.getByText("region_C")).toBeTruthy()
  })

  it("shows sort mode buttons", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByRole("button", { name: "By deviation" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "By value" })).toBeTruthy()
    expect(screen.getByRole("button", { name: "A–Z" })).toBeTruthy()
  })

  it("default sort is by deviation (largest first)", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    // Intercept has relativity 0.291, so deviation |0.291 - 1| = 0.709 is largest
    const firstLabel = document.querySelectorAll(".truncate")[0].textContent
    expect(firstLabel).toBe("(Intercept)")
  })

  it("clicking A–Z sorts alphabetically", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    fireEvent.click(screen.getByRole("button", { name: "A–Z" }))
    const labels = Array.from(document.querySelectorAll(".truncate")).map(el => el.textContent)
    expect(labels[0]).toBe("(Intercept)")
    expect(labels[1]).toBe("age")
    expect(labels[2]).toBe("region_B")
    expect(labels[3]).toBe("region_C")
  })

  it("displays relativity values", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("0.291")).toBeTruthy()
    expect(screen.getByText("1.012")).toBeTruthy()
    expect(screen.getByText("1.169")).toBeTruthy()
    expect(screen.getByText("0.915")).toBeTruthy()
  })

  it("shows legend with baseline info and CI note when CIs present", () => {
    const result = makeTrainResult({ glm_relativities: makeGlmRelativities() })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("Baseline = 1.0 (center line)")).toBeTruthy()
    expect(screen.getByText("— CI whiskers")).toBeTruthy()
    expect(screen.getByText("4 terms")).toBeTruthy()
  })

  it("hides CI note when no CI data", () => {
    const rows = makeGlmRelativities().map(r => ({ feature: r.feature, relativity: r.relativity }))
    const result = makeTrainResult({ glm_relativities: rows })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.queryByText("— CI whiskers")).toBeNull()
  })

  it("shows empty state when no relativities", () => {
    const result = makeTrainResult({ glm_relativities: [] })
    render(<GLMRelativitiesTab result={result} />)
    expect(screen.getByText("No relativity data available")).toBeTruthy()
  })
})

// ═════════════════════════════════════════════════════════════════
// SummaryTab — GLM fit statistics
// ═════════════════════════════════════════════════════════════════

describe("SummaryTab (GLM extensions)", () => {
  it("shows GLM fit statistics card when present", () => {
    const result = makeTrainResult({
      glm_fit_statistics: { aic: 5432.1, bic: 5478.9, deviance: 4200.3, null_deviance: 5100.0 },
    })
    render(<SummaryTab result={result} />)
    expect(screen.getByText("Fit statistics")).toBeTruthy()
    expect(screen.getByText("aic")).toBeTruthy()
    expect(screen.getByText("5432.1000")).toBeTruthy()
    expect(screen.getByText("bic")).toBeTruthy()
    expect(screen.getByText("5478.9000")).toBeTruthy()
  })

  it("hides fit statistics when not present", () => {
    const result = makeTrainResult()
    render(<SummaryTab result={result} />)
    expect(screen.queryByText("Fit statistics")).toBeNull()
  })

  it("shows regularization info when present", () => {
    const result = makeTrainResult({
      glm_regularization_path: { selected_alpha: 0.001234, n_nonzero: 12 },
    })
    render(<SummaryTab result={result} />)
    expect(screen.getByText("Regularization")).toBeTruthy()
    expect(screen.getByText("Alpha")).toBeTruthy()
    expect(screen.getByText("0.001234")).toBeTruthy()
    expect(screen.getByText("Non-zero coefficients")).toBeTruthy()
    expect(screen.getByText("12")).toBeTruthy()
  })

  it("hides regularization when no path info", () => {
    const result = makeTrainResult()
    render(<SummaryTab result={result} />)
    // "Regularization" appears as a header in GLMRegularizationConfig but not in SummaryTab
    expect(screen.queryByText("Alpha")).toBeNull()
    expect(screen.queryByText("Non-zero coefficients")).toBeNull()
  })
})

// ═════════════════════════════════════════════════════════════════
// ModellingConfig — GLM routing
// ═════════════════════════════════════════════════════════════════

describe("ModellingConfig (GLM routing)", () => {
  it("algorithm picker shows GLM option", () => {
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1" }}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    expect(screen.getByText("GLM")).toBeTruthy()
    expect(screen.getByText(/Generalised linear model/)).toBeTruthy()
  })

  it("clicking GLM sets algorithm", () => {
    const onUpdate = vi.fn()
    render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={{ _nodeId: "n1" }}
          onUpdate={onUpdate}
          upstreamColumns={defaultColumns}
        />
      </GraphProvider>,
    )
    fireEvent.click(screen.getByText("GLM"))
    expect(onUpdate).toHaveBeenCalledWith({
      algorithm: "glm",
      evaluation: expect.objectContaining({
        schema_version: 1,
        strategy: "random",
      }),
    })
  })

  it("GLM config routes Rustystats target settings and regularization to the Target pane", () => {
    const config = {
      _nodeId: "n1",
      algorithm: "glm",
      target: "claim_count",
      weight: "exposure",
      family: "poisson",
    }
    const { rerender } = render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={config}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
          activePane="target"
        />
      </GraphProvider>,
    )
    expect(screen.getByLabelText("Selected algorithm")).toHaveTextContent(/^Algorithm Rustystats$/)
    expect(screen.getByText("Target & Weight")).toBeTruthy()
    expect(screen.getByText("Family")).toBeTruthy()
    expect(screen.getByText("Regularization")).toBeTruthy()
    expect(screen.getByRole("button", { name: "Ridge" })).toBeVisible()
    expect(screen.queryByText("Fit all with defaults")).toBeNull()

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={config}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
          activePane="features"
        />
      </GraphProvider>,
    )
    expect(screen.getByText("Features")).toBeTruthy()
    expect(screen.getByText("Fit all with defaults")).toBeTruthy()
    expect(screen.queryByText("Family")).toBeNull()
    expect(screen.queryByText("Regularization")).toBeNull()

    // Should NOT have CatBoost-specific sections
    expect(screen.queryByRole("button", { name: "regression" })).toBeNull()
    expect(screen.queryByRole("button", { name: "classification" })).toBeNull()
  })

  it("GLM config routes the shared split and train sections", () => {
    const config = {
      _nodeId: "n1",
      algorithm: "glm",
      target: "claim_count",
      weight: "exposure",
      family: "poisson",
      terms: { age: { type: "linear" } },
    }
    const { rerender } = render(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={config}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
          activePane="split"
        />
      </GraphProvider>,
    )
    expect(screen.getByText("How is the data structured?")).toBeTruthy()
    expect(screen.queryByRole("button", { name: /Train Model/ })).toBeNull()

    rerender(
      <GraphProvider allNodes={[]} edges={[]}>
        <ModellingConfig
          config={config}
          onUpdate={vi.fn()}
          upstreamColumns={defaultColumns}
          activePane="train"
        />
      </GraphProvider>,
    )
    expect(screen.getByRole("button", { name: /Train Model/ })).toBeTruthy()
    expect(screen.queryByText("How is the data structured?")).toBeNull()
  })
})
